
import asyncio
import logging
import os
import requests
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from supabase import create_client, Client

from telegram import (
    Bot,
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from shopee import (
    buscar_produto_por_link,
    ShopeeAPIError,
)

from mercadolivre import (
    buscar_produto_por_link as buscar_produto_mercadolivre,
    MercadoLivreAPIError,
)


# ============================================================
# CONFIGURAÇÃO
# ============================================================

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_TOKEN",
    "",
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    "",
).strip()

# Encaminhamento opcional de ofertas para o canal WhatsApp via WAHA.
WAHA_URL = os.getenv("WAHA_URL", "https://raposa-waha.onrender.com").strip().rstrip("/")
WAHA_API_KEY = os.getenv("WAHA_API_KEY", "").strip()
WAHA_CHANNEL_ID = os.getenv("WAHA_CHANNEL_ID", "").strip()
WHATSAPP_FORWARD_ENABLED = os.getenv("WHATSAPP_FORWARD_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}

TELEGRAM_ADMIN_ID = os.getenv(
    "TELEGRAM_ADMIN_ID",
    "",
).strip()

SUPABASE_URL = os.getenv(
    "SUPABASE_URL",
    "",
).strip()

SUPABASE_KEY = os.getenv(
    "SUPABASE_KEY",
    "",
).strip()

INTERVALO_MINUTOS = int(
    os.getenv(
        "INTERVALO_MINUTOS",
        "30",
    )
)

PORT = int(
    os.getenv(
        "PORT",
        "10000",
    )
)

MAX_LINKS_POR_ENVIO = 20
FILA_ORIGEM = "raposa-cacadora-bot"

# ============================================================
# LOG
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "raposa-cacadora | "
        "%(message)s"
    ),
)

logger = logging.getLogger(
    "raposa-cacadora"
)


# ============================================================
# SUPABASE
# ============================================================

supabase: Client | None = None


def iniciar_supabase():

    global supabase

    if not SUPABASE_URL:
        raise RuntimeError(
            "SUPABASE_URL não configurada."
        )

    if not SUPABASE_KEY:
        raise RuntimeError(
            "SUPABASE_KEY não configurada."
        )

    supabase = create_client(
        SUPABASE_URL,
        SUPABASE_KEY,
    )

    logger.info(
        "Supabase conectado."
    )


# ============================================================
# CONTROLE DO BOT
# ============================================================

bot_ativo = True

worker_task: asyncio.Task | None = None


# ============================================================
# SERVIDOR HTTP PARA O RENDER
# ============================================================

class HealthHandler(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8",
        )

        self.end_headers()

        self.wfile.write(
            b"Raposa Cacadora OK"
        )

    def log_message(
        self,
        format,
        *args,
    ):
        return


def iniciar_servidor_http():

    try:

        servidor = ThreadingHTTPServer(
            (
                "0.0.0.0",
                PORT,
            ),
            HealthHandler,
        )

        logger.info(
            "Servidor HTTP iniciado em 0.0.0.0:%d",
            PORT,
        )

        servidor.serve_forever()

    except Exception as erro:

        logger.exception(
            "Erro no servidor HTTP: %s",
            erro,
        )


# ============================================================
# CONFIGURAÇÃO
# ============================================================

def validar_configuracao():

    erros = []

    if not TELEGRAM_TOKEN:
        erros.append(
            "TELEGRAM_TOKEN não configurado."
        )

    if not TELEGRAM_CHAT_ID:
        erros.append(
            "TELEGRAM_CHAT_ID não configurado."
        )

    if not TELEGRAM_ADMIN_ID:
        erros.append(
            "TELEGRAM_ADMIN_ID não configurado."
        )

    if not SUPABASE_URL:
        erros.append(
            "SUPABASE_URL não configurada."
        )

    if not SUPABASE_KEY:
        erros.append(
            "SUPABASE_KEY não configurada."
        )

    if INTERVALO_MINUTOS < 1:
        erros.append(
            "INTERVALO_MINUTOS deve ser maior que 0."
        )

    if erros:

        for erro in erros:
            logger.error(erro)

        raise RuntimeError(
            "Configuração inválida."
        )

    try:

        int(TELEGRAM_ADMIN_ID)

    except ValueError:

        raise RuntimeError(
            "TELEGRAM_ADMIN_ID deve ser numérico."
        )

    logger.info(
        "Configuração validada."
    )

    logger.info(
        "Intervalo: %d minutos",
        INTERVALO_MINUTOS,
    )

    logger.info(
        "Porta HTTP: %d",
        PORT,
    )


# ============================================================
# AUTORIZAÇÃO
# ============================================================

def usuario_autorizado(
    update: Update,
) -> bool:

    if not update.effective_user:
        return False

    try:

        admin_id = int(
            TELEGRAM_ADMIN_ID
        )

    except ValueError:

        return False

    return (
        update.effective_user.id
        == admin_id
    )


# ============================================================
# EXTRAIR LINKS
# ============================================================

def extrair_links(
    texto: str,
) -> list[str]:

    if not texto:
        return []

    links = []

    for parte in texto.split():

        link = (
            parte
            .strip()
            .strip("<>")
            .strip()
        )

        while link and link[-1] in (
            ",",
            ".",
            ";",
            ")",
            "]",
            "}",
        ):
            link = link[:-1]

        if not (
            link.startswith("http://")
            or link.startswith("https://")
        ):
            continue

        link_lower = link.lower()

        if (
            "shopee.com.br" in link_lower
            or "s.shopee.com.br" in link_lower
            or "mercadolivre.com.br" in link_lower
            or "mercadolibre.com" in link_lower
            or "meli.la" in link_lower
        ):
            links.append(link)

    resultado = []
    vistos = set()

    for link in links:

        chave = link.strip()

        if chave not in vistos:

            vistos.add(chave)
            resultado.append(chave)

    return resultado


# ============================================================
# SUPABASE - INSERIR LINKS
# ============================================================

def inserir_links(
    links: list[str],
) -> tuple[int, int, list[str]]:

    if supabase is None:
        raise RuntimeError(
            "Supabase não inicializado."
        )

    adicionados = 0
    duplicados = 0
    erros = []

    for link in links:

        try:

            resposta = (
                supabase
                .table("produtos_fila")
                .insert(
                    {
                        "link": link,
                        "status": "pending",
                        "fila_origem": FILA_ORIGEM,
                    }
                )
                .execute()
            )

            if resposta.data:
                adicionados += 1

        except Exception as erro:

            mensagem_erro = str(
                erro
            )

            texto = mensagem_erro.lower()

            if (
                "duplicate" in texto
                or "unique" in texto
                or "23505" in mensagem_erro
            ):

                duplicados += 1

                logger.info(
                    "Link já existente: %s",
                    link,
                )

            else:

                logger.exception(
                    "Erro ao inserir link: %s",
                    link,
                )

                erros.append(link)

    return (
        adicionados,
        duplicados,
        erros,
    )


# ============================================================
# SUPABASE - BUSCAR PRÓXIMO PRODUTO
# ============================================================

def buscar_proximo_produto():

    if supabase is None:
        raise RuntimeError(
            "Supabase não inicializado."
        )

    resposta = (
        supabase
        .table("produtos_fila")
        .select("*")
        .eq("status", "pending")
        .eq("fila_origem", FILA_ORIGEM)
        .order("created_at", desc=False)
        .limit(1)
        .execute()
    )

    if not resposta.data:
        return None

    return resposta.data[0]


# ============================================================
# SUPABASE - MARCAR PROCESSANDO
# ============================================================

def marcar_processando(
    produto_id: int,
) -> bool:

    if supabase is None:
        return False

    agora = datetime.now(
        timezone.utc
    ).isoformat()

    resposta = (
        supabase
        .table("produtos_fila")
        .update(
            {
                "status": "processing",
                "processing_at": agora,
            }
        )
        .eq("id", produto_id)
        .eq("status", "pending")
        .execute()
    )

    return bool(
        resposta.data
    )


# ============================================================
# SUPABASE - MARCAR PUBLICADO
# ============================================================

def marcar_publicado(
    produto_id: int,
    produto: dict[str, Any],
    telegram_message_id: int | None,
):

    if supabase is None:
        return

    agora = datetime.now(
        timezone.utc
    ).isoformat()

    dados = {
        "status": "published",
        "product_name": (
            produto.get("productName")
            or "Produto"
        ),
        "shop_id": produto.get(
            "shopId"
        ),
        "item_id": produto.get(
            "itemId"
        ),
        "image_url": produto.get(
            "imageUrl"
        ),
        "telegram_message_id": (
            telegram_message_id
        ),
        "telegram_chat_id": (
            TELEGRAM_CHAT_ID
        ),
        "published_at": agora,
        "processing_at": None,
        "erro": None,
    }

    (
        supabase
        .table("produtos_fila")
        .update(dados)
        .eq("id", produto_id)
        .execute()
    )


# ============================================================
# SUPABASE - MARCAR ERRO
# ============================================================

def marcar_erro(
    produto_id: int,
    erro: str,
):

    if supabase is None:
        return

    resposta = (
        supabase
        .table("produtos_fila")
        .select("tentativas")
        .eq("id", produto_id)
        .limit(1)
        .execute()
    )

    tentativas = 0

    if resposta.data:

        tentativas = int(
            resposta.data[0].get(
                "tentativas",
                0,
            )
            or 0
        )

    tentativas += 1

    (
        supabase
        .table("produtos_fila")
        .update(
            {
                "status": "error",
                "tentativas": tentativas,
                "erro": erro[:2000],
                "processing_at": None,
            }
        )
        .eq("id", produto_id)
        .execute()
    )


# ============================================================
# SUPABASE - RECUPERAR PROCESSAMENTOS PRESOS
# ============================================================

def recuperar_processamentos_presos():

    if supabase is None:
        return

    limite = (
        datetime.now(
            timezone.utc
        )
        - timedelta(
            minutes=max(
                INTERVALO_MINUTOS * 2,
                20,
            )
        )
    ).isoformat()

    resposta = (
        supabase
        .table("produtos_fila")
        .select("id")
        .eq("status", "processing")
        .lt("processing_at", limite)
        .execute()
    )

    if not resposta.data:
        return

    for produto in resposta.data:

        produto_id = produto["id"]

        (
            supabase
            .table("produtos_fila")
            .update(
                {
                    "status": "pending",
                    "processing_at": None,
                }
            )
            .eq("id", produto_id)
            .eq("status", "processing")
            .execute()
        )

        logger.warning(
            "Produto %s recuperado de processing.",
            produto_id,
        )


# ============================================================
# NÚMEROS
# ============================================================

def numero(
    valor,
    padrao=0.0,
):

    try:

        if valor is None:
            return padrao

        texto = str(
            valor
        ).strip()

        if not texto:
            return padrao

        return float(
            texto.replace(
                ",",
                ".",
            )
        )

    except Exception:

        return padrao


def inteiro(
    valor,
    padrao=0,
):

    try:

        if valor is None:
            return padrao

        return int(
            float(valor)
        )

    except Exception:

        return padrao


# ============================================================
# MOEDA
# ============================================================

def moeda(
    valor,
):

    valor = numero(
        valor
    )

    texto = (
        f"{valor:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )

    return f"R$ {texto}"


# ============================================================
# VENDAS
# ============================================================

def formatar_vendas(
    vendas,
):

    vendas = inteiro(
        vendas
    )

    return (
        f"{vendas:,}"
        .replace(",", ".")
    )


# ============================================================
# MENSAGEM DO PRODUTO
# ============================================================

def montar_mensagem(
    produto,
    marketplace=None,
):
    nome = produto.get("productName") or "Produto"
    preco = numero(produto.get("price"))
    preco_min = numero(produto.get("priceMin"))
    desconto = numero(produto.get("priceDiscountRate"))
    avaliacao = numero(produto.get("ratingStar"))
    vendas = inteiro(produto.get("sales"))
    loja = produto.get("shopName") or "Loja Shopee"

    preco_atual = preco_min if preco_min > 0 else preco
    original = numero(produto.get("originalPrice"))

    if original > preco_atual > 0:
        preco_anterior = original
    elif desconto > 0 and desconto < 100 and preco_atual > 0:
        preco_anterior = preco_atual / (1 - desconto / 100)
    else:
        preco_anterior = preco_atual

    if marketplace == "mercadolivre":
        partes = [
            "🦊🔥 <b>ACHADINHO MERCADO LIVRE</b>\n",
            "\n",
            f"✨ <b>{nome}</b>\n",
            "\n",
            f"💥 <b>DE {moeda(preco_anterior)}</b>\n",
            f"🏷️ <b>POR {moeda(preco_atual)}</b>\n",
        ]

        if desconto > 0:
            partes.append(f"🟢 <b>{desconto:.0f}% DE DESCONTO</b>\n")

        if produto.get("couponActive"):
            coupon_code = html_escape(str(produto.get("couponCode") or "").strip())
            coupon_amount = numero(produto.get("couponDiscountAmount"))
            coupon_percentage = numero(produto.get("couponDiscountPercentage"))
            coupon_minimum = numero(produto.get("couponMinPurchaseAmount"))

            partes.append("\n")
            partes.append("🎟️ <b>CUPOM ATIVO</b>\n")
            if coupon_amount > 0:
                partes.append(f"💸 <b>{moeda(coupon_amount)} OFF</b>\n")
            elif coupon_percentage > 0:
                partes.append(f"🏷️ <b>{coupon_percentage:.0f}% OFF</b>\n")
            if coupon_code:
                partes.append(f"🔑 Código: <code>{coupon_code}</code>\n")
            else:
                partes.append("✨ Cupom automático para este anúncio\n")
            if coupon_minimum > 0:
                partes.append(f"🛒 Mínimo: <b>{moeda(coupon_minimum)}</b>\n")

        partes.extend([
            "\n",
            "━━━━━━━━━━━━━━━━━━\n",
            "\n",
            "🚨 <b>OFERTA POR TEMPO LIMITADO!</b>\n",
            "O preço pode mudar a qualquer momento.\n",
            "\n",
            "👇 <b>Aproveite enquanto estiver disponível!</b>",
        ])
        return "".join(partes)

    return (
        "🦊🔥 <b>ACHADINHO SHOPEE</b>\n"
        "\n"
        f"✨ <b>{nome}</b>\n"
        "\n"
        f"💥 <b>DE {moeda(preco_anterior)}</b>\n"
        f"🏷️ <b>POR {moeda(preco_atual)}</b>\n"
        f"🟢 <b>{desconto:.0f}% DE DESCONTO</b>\n"
        "\n"
        f"⭐ <b>{avaliacao:.1f}/5 de avaliação</b>\n"
        f"🔥 <b>{formatar_vendas(vendas)} vendas</b>\n"
        f"🏪 <b>{loja}</b>\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "🚨 <b>OFERTA POR TEMPO LIMITADO!</b>\n"
        "O preço pode mudar a qualquer momento.\n"
        "\n"
        "👇 <b>Aproveite enquanto estiver disponível!</b>"
    )

# ============================================================
# ENCAMINHAMENTO OPCIONAL PARA O CANAL WHATSAPP (WAHA)
# ============================================================

async def encaminhar_para_whatsapp(produto: dict[str, Any], mensagem_html: str, link_afiliado: str, *, historico: bool = False) -> bool:
    """Encaminha a oferta ao canal WhatsApp sem publicar novamente no Telegram."""
    if not historico and not WHATSAPP_FORWARD_ENABLED:
        return False
    if not WAHA_API_KEY or not WAHA_CHANNEL_ID:
        logger.warning("WhatsApp não enviado: configure WAHA_API_KEY e WAHA_CHANNEL_ID.")
        return False

    import re
    from html import unescape

    texto = unescape(re.sub(r"<[^>]+>", "", mensagem_html or "")).strip()
    if link_afiliado:
        texto = f"{texto}\n\n🛒 COMPRAR AGORA: {link_afiliado}"

    image_url = str(produto.get("imageUrl") or "").strip()

    def _enviar():
        headers = {"X-Api-Key": WAHA_API_KEY, "Content-Type": "application/json", "Accept": "application/json"}
        endpoint = f"{WAHA_URL}/api/sendImage" if image_url else f"{WAHA_URL}/api/sendText"
        payload = {"session": "default", "chatId": WAHA_CHANNEL_ID}
        if image_url:
            payload.update({
                "file": {"url": image_url, "mimetype": "image/jpeg", "filename": "oferta.jpg"},
                "caption": texto,
            })
        else:
            payload["text"] = texto

        resposta = requests.post(endpoint, headers=headers, json=payload, timeout=25)
        if not resposta.ok and image_url:
            logger.warning("WAHA recusou imagem (%s); tentando encaminhar apenas texto.", resposta.status_code)
            resposta = requests.post(
                f"{WAHA_URL}/api/sendText",
                headers=headers,
                json={"session": "default", "chatId": WAHA_CHANNEL_ID, "text": texto},
                timeout=25,
            )
        resposta.raise_for_status()
        try:
            dados = resposta.json()
        except Exception:
            dados = {}
        return resposta.status_code, str(dados.get("id") or dados.get("messageId") or "")

    try:
        status_code, message_id = await asyncio.to_thread(_enviar)
        logger.info("Oferta encaminhada ao canal WhatsApp via WAHA (HTTP %s).", status_code)
        return {"status_code": status_code, "message_id": message_id}
    except Exception:
        logger.exception("Falha ao encaminhar oferta ao WhatsApp.")
        return False


async def processar_lote_historico_whatsapp(bot: Bot, limite: int = 5):
    """Envia somente ofertas já publicadas no Telegram; nunca chama publicação do Telegram."""
    if supabase is None:
        await enviar_notificacao_admin(bot, "❌ Supabase não está conectado.")
        return
    if not WAHA_API_KEY or not WAHA_CHANNEL_ID:
        await enviar_notificacao_admin(
            bot,
            "⚠️ Lote histórico não iniciado: falta configurar WAHA_API_KEY no Render do raposa-cacadora-bot. Nenhuma oferta foi enviada."
        )
        return

    existentes = await asyncio.to_thread(
        lambda: supabase.table("whatsapp_backfill_log").select("produto_fila_id").eq("channel_id", WAHA_CHANNEL_ID).eq("status", "sent").execute()
    )
    enviados = {int(x["produto_fila_id"]) for x in (existentes.data or [])}

    resposta = await asyncio.to_thread(
        lambda: supabase.table("produtos_fila").select("id,link,product_name,image_url,published_at").eq("fila_origem", FILA_ORIGEM).eq("bot_id", FILA_ORIGEM).eq("status", "published").not_.is_("telegram_message_id", "null").order("published_at", desc=True).limit(250).execute()
    )
    candidatos = [x for x in (resposta.data or []) if int(x["id"]) not in enviados][:limite]
    if not candidatos:
        await enviar_notificacao_admin(bot, "ℹ️ Não há ofertas históricas pendentes de envio ao WhatsApp.")
        return

    ok = 0
    falhas = 0
    for item in candidatos:
        pid = int(item["id"])
        try:
            link = str(item.get("link") or "")
            lower = link.lower()
            if any(d in lower for d in ("mercadolivre.com.br", "mercadolibre.com", "meli.la")):
                produto = await asyncio.to_thread(buscar_produto_mercadolivre, link)
                marketplace = "mercadolivre"
            else:
                produto = await asyncio.to_thread(buscar_produto_por_link, link)
                marketplace = "shopee"
            if not produto:
                raise RuntimeError("O marketplace não retornou os dados do produto.")
            produto["marketplace"] = marketplace
            mensagem = montar_mensagem(produto, marketplace=marketplace)
            resultado = await encaminhar_para_whatsapp(produto, mensagem, link, historico=True)
            if not resultado:
                raise RuntimeError("O envio ao WhatsApp não foi confirmado.")
            await asyncio.to_thread(
                lambda pid=pid, mid=resultado.get("message_id", ""): supabase.table("whatsapp_backfill_log").upsert(
                    {"produto_fila_id": pid, "channel_id": WAHA_CHANNEL_ID, "status": "sent", "whatsapp_message_id": mid, "error": None, "sent_at": datetime.now(timezone.utc).isoformat()},
                    on_conflict="produto_fila_id"
                ).execute()
            )
            ok += 1
        except Exception as erro:
            falhas += 1
            logger.exception("Falha no lote histórico para produto fila #%s", pid)
            await asyncio.to_thread(
                lambda pid=pid, msg=str(erro)[:1000]: supabase.table("whatsapp_backfill_log").upsert(
                    {"produto_fila_id": pid, "channel_id": WAHA_CHANNEL_ID, "status": "error", "error": msg},
                    on_conflict="produto_fila_id"
                ).execute()
            )

    await enviar_notificacao_admin(
        bot,
        "📦 <b>LOTE HISTÓRICO WHATSAPP CONCLUÍDO</b>\n\n"
        f"✅ Enviados: <b>{ok}</b>\n"
        f"⚠️ Falhas: <b>{falhas}</b>\n"
        "📨 Somente WhatsApp foi utilizado; nenhuma nova postagem foi feita no Telegram.\n"
        "Use /whatsapp_lote para processar o próximo lote de 5."
    )


# ============================================================
# PUBLICAR PRODUTO
# ============================================================

async def publicar_produto(
    bot: Bot,
    produto: dict[str, Any],
    link_afiliado: str,
    marketplace=None,
):

    mensagem = montar_mensagem(
        produto,
        marketplace=marketplace,
    )

    image_url = (
        produto.get(
            "imageUrl"
        )
        or ""
    )

    teclado = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🛒 COMPRAR AGORA",
                    url=link_afiliado,
                )
            ]
        ]
    )

    if image_url:

        try:

            mensagem_enviada = (
                await bot.send_photo(
                    chat_id=TELEGRAM_CHAT_ID,
                    photo=image_url,
                    caption=mensagem,
                    parse_mode=ParseMode.HTML,
                    reply_markup=teclado,
                )
            )

            logger.info(
                "Produto publicado com imagem e botão."
            )

            await encaminhar_para_whatsapp(produto, mensagem, link_afiliado)

            return (
                True,
                mensagem_enviada.message_id,
            )

        except Exception as erro:

            logger.warning(
                "Falha ao enviar imagem: %s",
                erro,
            )

    try:

        mensagem_enviada = (
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=mensagem,
                parse_mode=ParseMode.HTML,
                reply_markup=teclado,
                disable_web_page_preview=True,
            )
        )

        logger.info(
            "Produto publicado somente como texto."
        )

        await encaminhar_para_whatsapp(produto, mensagem, link_afiliado)

        return (
            True,
            mensagem_enviada.message_id,
        )

    except Exception as erro:

        logger.exception(
            "Erro ao publicar produto: %s",
            erro,
        )

        return (
            False,
            None,
        )


# ============================================================
# NOTIFICAÇÃO PARA ADMIN
# ============================================================

async def enviar_notificacao_admin(
    bot: Bot,
    texto: str,
):

    try:

        await bot.send_message(
            chat_id=int(
                TELEGRAM_ADMIN_ID
            ),
            text=texto,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    except Exception as erro:

        logger.warning(
            "Não foi possível notificar admin: %s",
            erro,
        )


# ============================================================
# PROCESSAR PRODUTO
# ============================================================

async def processar_produto(
    bot: Bot,
    produto_fila: dict[str, Any],
):

    produto_id = produto_fila["id"]
    link = produto_fila["link"]

    logger.info(
        "=========================================="
    )

    logger.info(
        "Processando produto ID %s",
        produto_id,
    )

    logger.info(
        "Link: %s",
        link,
    )

    reservado = await asyncio.to_thread(
        marcar_processando,
        produto_id,
    )

    if not reservado:

        logger.info(
            "Produto %s não pôde ser reservado.",
            produto_id,
        )

        return False

    try:

        link_lower = str(link).lower()

        if any(
            dominio in link_lower
            for dominio in (
                "mercadolivre.com.br",
                "mercadolibre.com",
                "meli.la",
            )
        ):
            logger.info(
                "Detectado link do Mercado Livre."
            )

            produto = await asyncio.to_thread(
                buscar_produto_mercadolivre,
                link,
            )

            marketplace = "mercadolivre"

        else:
            logger.info(
                "Detectado link da Shopee."
            )

            produto = await asyncio.to_thread(
                buscar_produto_por_link,
                link,
            )

            marketplace = "shopee"

        if not produto:

            if marketplace == "mercadolivre":
                raise MercadoLivreAPIError(
                    "Produto não encontrado."
                )

            raise ShopeeAPIError(
                "Produto não encontrado."
            )

        produto["marketplace"] = marketplace

        logger.info(
            "Produto encontrado: %s",
            produto.get(
                "productName",
                "Produto",
            ),
        )

        sucesso, message_id = (
            await publicar_produto(
                bot=bot,
                produto=produto,
                link_afiliado=link,
                marketplace=marketplace,
            )
        )

        if not sucesso:

            raise RuntimeError(
                "Falha ao publicar no Telegram."
            )

        await asyncio.to_thread(
            marcar_publicado,
            produto_id,
            produto,
            message_id,
        )

        logger.info(
            "Produto %s marcado como publicado.",
            produto_id,
        )

        await enviar_notificacao_admin(
            bot,
            (
                "✅ <b>PRODUTO PUBLICADO</b>\n"
                "\n"
                f"📦 <b>{produto.get('productName', 'Produto')}</b>\n"
                f"🆔 Fila: <b>#{produto_id}</b>\n"
                f"📨 Mensagem: <b>#{message_id}</b>"
            ),
        )

        return True

    except (ShopeeAPIError, MercadoLivreAPIError) as erro:

        logger.error(
            "Erro da Shopee: %s",
            erro,
        )

        await asyncio.to_thread(
            marcar_erro,
            produto_id,
            str(erro),
        )

        await enviar_notificacao_admin(
            bot,
            (
                "❌ <b>ERRO AO PROCESSAR PRODUTO</b>\n"
                "\n"
                f"🆔 Fila: <b>#{produto_id}</b>\n"
                f"🔗 {link}\n"
                f"⚠️ <b>{str(erro)[:1500]}</b>"
            ),
        )

        return False

    except Exception as erro:

        logger.exception(
            "Erro ao processar produto %s",
            produto_id,
        )

        await asyncio.to_thread(
            marcar_erro,
            produto_id,
            str(erro),
        )

        await enviar_notificacao_admin(
            bot,
            (
                "❌ <b>ERRO AO PROCESSAR PRODUTO</b>\n"
                "\n"
                f"🆔 Fila: <b>#{produto_id}</b>\n"
                f"🔗 {link}\n"
                f"⚠️ <b>{str(erro)[:1500]}</b>"
            ),
        )

        return False


# ============================================================
# TECLADO PRINCIPAL
# ============================================================

def teclado_controle():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "▶️ INICIAR",
                    callback_data="bot_iniciar",
                ),
                InlineKeyboardButton(
                    "⏹️ STOP",
                    callback_data="bot_stop",
                ),
            ],
        ]
    )


# ============================================================
# /START
# ============================================================

async def comando_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not usuario_autorizado(update):
        return

    mensagem = (
        "🦊 <b>RAPOSA CAÇADORA</b>\n"
        "\n"
        "Envie um ou vários links da Shopee "
        "para colocar na fila.\n"
        "\n"
        f"📦 Máximo por envio: <b>{MAX_LINKS_POR_ENVIO}</b>\n"
        f"⏱️ Intervalo: <b>{INTERVALO_MINUTOS} minutos</b>\n"
        "\n"
        "O bot salva tudo no Supabase, então "
        "a fila continua mesmo se o Render reiniciar.\n"
        "\n"
        "Use os botões abaixo para controlar a publicação."
    )

    await update.message.reply_text(
        mensagem,
        parse_mode=ParseMode.HTML,
        reply_markup=teclado_controle(),
    )


# ============================================================
# /WHATSAPP_LOTE - PROCESSAR 5 OFERTAS HISTÓRICAS
# ============================================================

async def comando_whatsapp_lote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not usuario_autorizado(update):
        return
    if not WAHA_API_KEY or not WAHA_CHANNEL_ID:
        await update.message.reply_text(
            "⚠️ Ainda falta configurar WAHA_API_KEY no Render do raposa-cacadora-bot. Não enviei nada.",
            parse_mode=ParseMode.HTML,
        )
        return
    await update.message.reply_text(
        "📦 Lote histórico iniciado em segundo plano: até 5 ofertas antigas serão enviadas ao WhatsApp. O bot continuará recebendo novos links e não publicará essas ofertas novamente no Telegram.",
        parse_mode=ParseMode.HTML,
    )
    asyncio.create_task(processar_lote_historico_whatsapp(context.bot, limite=5))


# ============================================================
# /STATUS
# ============================================================

async def comando_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not usuario_autorizado(update):
        return

    if supabase is None:
        return

    try:

        resposta = (
            supabase
            .table("produtos_fila")
            .select("status")
            .execute()
        )

        registros = (
            resposta.data
            or []
        )

        total = len(
            registros
        )

        publicados = sum(
            1
            for item in registros
            if item.get("status")
            == "published"
        )

        pendentes = sum(
            1
            for item in registros
            if item.get("status")
            == "pending"
        )

        processando = sum(
            1
            for item in registros
            if item.get("status")
            == "processing"
        )

        erros = sum(
            1
            for item in registros
            if item.get("status")
            == "error"
        )

        estado = (
            "🟢 ATIVO"
            if bot_ativo
            else "🔴 PARADO"
        )

        mensagem = (
            "🦊 <b>RAPOSA CAÇADORA</b>\n"
            "\n"
            "📊 <b>STATUS DA FILA</b>\n"
            "\n"
            f"🤖 Bot: <b>{estado}</b>\n"
            f"📦 Total: <b>{total}</b>\n"
            f"✅ Publicados: <b>{publicados}</b>\n"
            f"⏳ Aguardando: <b>{pendentes}</b>\n"
            f"🔄 Processando: <b>{processando}</b>\n"
            f"❌ Erros: <b>{erros}</b>\n"
            "\n"
            f"⏱️ Intervalo: <b>{INTERVALO_MINUTOS} minutos</b>"
        )

        await update.message.reply_text(
            mensagem,
            parse_mode=ParseMode.HTML,
            reply_markup=teclado_controle(),
        )

    except Exception as erro:

        logger.exception(
            "Erro no comando /status"
        )

        await update.message.reply_text(
            f"❌ Erro ao consultar status:\n{erro}"
        )


# ============================================================
# /FILA
# ============================================================

async def comando_fila(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not usuario_autorizado(update):
        return

    if supabase is None:
        return

    try:

        resposta = (
            supabase
            .table("produtos_fila")
            .select(
                "id,link,product_name,status,created_at"
            )
            .order("id", desc=False)
            .limit(100)
            .execute()
        )

        registros = (
            resposta.data
            or []
        )

        if not registros:

            await update.message.reply_text(
                "🦊 A fila está vazia."
            )

            return

        linhas = [
            "🦊 <b>FILA DE PRODUTOS</b>",
            "",
        ]

        simbolos = {
            "pending": "⏳",
            "processing": "🔄",
            "published": "✅",
            "error": "❌",
        }

        for item in registros:

            simbolo = simbolos.get(
                item.get("status"),
                "❓",
            )

            nome = (
                item.get(
                    "product_name"
                )
                or "Aguardando processamento"
            )

            if len(nome) > 45:

                nome = (
                    nome[:42]
                    + "..."
                )

            linhas.append(
                f"{simbolo} #{item['id']} — {nome}"
            )

        texto = "\n".join(
            linhas
        )

        if len(texto) > 4000:

            texto = (
                texto[:3950]
                + "\n\n..."
            )

        await update.message.reply_text(
            texto,
            parse_mode=ParseMode.HTML,
        )

    except Exception as erro:

        logger.exception(
            "Erro no comando /fila"
        )

        await update.message.reply_text(
            f"❌ Erro ao consultar fila:\n{erro}"
        )


# ============================================================
# /ERROS
# ============================================================

async def comando_erros(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not usuario_autorizado(update):
        return

    if supabase is None:
        return

    try:

        resposta = (
            supabase
            .table("produtos_fila")
            .select(
                "id,link,erro,tentativas"
            )
            .eq("status", "error")
            .order("id", desc=False)
            .limit(20)
            .execute()
        )

        registros = (
            resposta.data
            or []
        )

        if not registros:

            await update.message.reply_text(
                "✅ Não existem produtos com erro."
            )

            return

        linhas = [
            "⚠️ <b>PRODUTOS COM ERRO</b>",
            "",
        ]

        for item in registros:

            erro = (
                item.get("erro")
                or "Erro desconhecido"
            )

            if len(erro) > 300:

                erro = (
                    erro[:297]
                    + "..."
                )

            linhas.append(
                f"❌ <b>#{item['id']}</b>"
            )

            linhas.append(
                f"Tentativas: {item.get('tentativas', 0)}"
            )

            linhas.append(
                f"Erro: {erro}"
            )

            linhas.append("")

        texto = "\n".join(
            linhas
        )

        if len(texto) > 4000:

            texto = (
                texto[:3950]
                + "\n\n..."
            )

        await update.message.reply_text(
            texto,
            parse_mode=ParseMode.HTML,
        )

    except Exception as erro:

        logger.exception(
            "Erro no comando /erros"
        )

        await update.message.reply_text(
            f"❌ Erro ao consultar erros:\n{erro}"
        )


# ============================================================
# /RETRY
# ============================================================

async def comando_retry(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not usuario_autorizado(update):
        return

    if supabase is None:
        return

    try:

        resposta = (
            supabase
            .table("produtos_fila")
            .update(
                {
                    "status": "pending",
                    "erro": None,
                    "processing_at": None,
                }
            )
            .eq("status", "error")
            .execute()
        )

        quantidade = len(
            resposta.data
            or []
        )

        await update.message.reply_text(
            (
                "🔄 <b>REPROCESSAMENTO</b>\n"
                "\n"
                f"✅ {quantidade} produto(s) "
                "voltaram para a fila.\n"
                "\n"
                "Use ▶️ INICIAR para liberar a publicação."
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=teclado_controle(),
        )

    except Exception as erro:

        logger.exception(
            "Erro no comando /retry"
        )

        await update.message.reply_text(
            f"❌ Erro ao reprocessar:\n{erro}"
        )


# ============================================================
# /STOP
# ============================================================

async def comando_stop(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    global bot_ativo

    if not usuario_autorizado(update):
        return

    bot_ativo = False

    logger.warning(
        "Publicação pausada pelo administrador."
    )

    await update.message.reply_text(
        (
            "⏹️ <b>PUBLICAÇÃO PARADA</b>\n"
            "\n"
            "Nenhum novo produto será publicado.\n"
            "Os produtos que já estão no Supabase "
            "continuam na fila.\n"
            "\n"
            "▶️ Use /iniciar ou o botão INICIAR "
            "para continuar."
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=teclado_controle(),
    )


# ============================================================
# /INICIAR
# ============================================================

async def comando_iniciar(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    global bot_ativo

    if not usuario_autorizado(update):
        return

    bot_ativo = True

    logger.info(
        "Publicação iniciada pelo administrador."
    )

    await update.message.reply_text(
        (
            "▶️ <b>PUBLICAÇÃO INICIADA</b>\n"
            "\n"
            "A Raposa Caçadora voltou a processar "
            "a fila do Supabase.\n"
            "\n"
            f"⏱️ Intervalo: <b>{INTERVALO_MINUTOS} minutos</b>"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=teclado_controle(),
    )


# ============================================================
# BOTÕES
# ============================================================

async def callback_controle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    global bot_ativo

    query = update.callback_query

    if query is None:
        return

    if not usuario_autorizado(update):

        await query.answer(
            "Você não está autorizado.",
            show_alert=True,
        )

        return

    await query.answer()

    if query.data == "bot_stop":

        bot_ativo = False

        logger.warning(
            "Publicação parada pelo botão STOP."
        )

        texto = (
            "⏹️ <b>PUBLICAÇÃO PARADA</b>\n"
            "\n"
            "A fila permanece salva no Supabase.\n"
            "Nenhum novo produto será publicado.\n"
            "\n"
            "▶️ Pressione INICIAR para continuar."
        )

    elif query.data == "bot_iniciar":

        bot_ativo = True

        logger.info(
            "Publicação iniciada pelo botão."
        )

        texto = (
            "▶️ <b>PUBLICAÇÃO INICIADA</b>\n"
            "\n"
            "A Raposa voltou a processar a fila.\n"
            "\n"
            f"⏱️ Intervalo: <b>{INTERVALO_MINUTOS} minutos</b>"
        )

    else:

        return

    try:

        await query.edit_message_text(
            texto,
            parse_mode=ParseMode.HTML,
            reply_markup=teclado_controle(),
        )

    except Exception:

        try:

            if query.message:

                await query.message.reply_text(
                    texto,
                    parse_mode=ParseMode.HTML,
                    reply_markup=teclado_controle(),
                )

        except Exception:

            pass


# ============================================================
# RECEBER LINKS PELO TELEGRAM
# ============================================================

async def receber_links(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not usuario_autorizado(update):
        return

    if update.message is None:
        return

    texto = (
        update.message.text
        or update.message.caption
        or ""
    )

    links = extrair_links(
        texto
    )

    if not links:

        await update.message.reply_text(
            (
                "⚠️ Não encontrei links da Shopee ou Mercado Livre.\n\n"
                "Envie um ou vários links, por exemplo:\n"
                "https://s.shopee.com.br/..."
            )
        )

        return

    if len(links) > MAX_LINKS_POR_ENVIO:

        await update.message.reply_text(
            (
                f"⚠️ Você enviou <b>{len(links)}</b> links.\n\n"
                f"O limite é <b>{MAX_LINKS_POR_ENVIO}</b> "
                "links por envio.\n"
                "\n"
                "Envie em grupos menores."
            ),
            parse_mode=ParseMode.HTML,
        )

        return

    try:

        (
            adicionados,
            duplicados,
            erros,
        ) = await asyncio.to_thread(
            inserir_links,
            links,
        )

        mensagem = (
            "🦊 <b>LINKS RECEBIDOS</b>\n"
            "\n"
            f"📥 Recebidos: <b>{len(links)}</b>\n"
            f"⏳ Adicionados à fila: <b>{adicionados}</b>\n"
            f"♻️ Já existentes: <b>{duplicados}</b>\n"
            f"❌ Erros: <b>{len(erros)}</b>\n"
            "\n"
            "Os links adicionados estão salvos no "
            "<b>Supabase</b> e aguardando publicação."
        )

        if bot_ativo:

            mensagem += (
                "\n\n"
                "🟢 A publicação está <b>ATIVA</b>."
            )

        else:

            mensagem += (
                "\n\n"
                "🔴 A publicação está <b>PARADA</b>."
            )

        await update.message.reply_text(
            mensagem,
            parse_mode=ParseMode.HTML,
            reply_markup=teclado_controle(),
        )

        if erros:

            await enviar_notificacao_admin(
                context.bot,
                (
                    "⚠️ <b>ERRO AO ADICIONAR LINKS</b>\n"
                    "\n"
                    f"Recebidos: {len(links)}\n"
                    f"Adicionados: {adicionados}\n"
                    f"Duplicados: {duplicados}\n"
                    f"Com erro: {len(erros)}"
                ),
            )

    except Exception as erro:

        logger.exception(
            "Erro ao adicionar links."
        )

        await update.message.reply_text(
            (
                "❌ <b>ERRO AO SALVAR LINKS</b>\n"
                "\n"
                f"{str(erro)[:2000]}"
            ),
            parse_mode=ParseMode.HTML,
        )


# ============================================================
# WORKER DA FILA
# ============================================================

async def worker_fila(
    bot: Bot,
):

    global bot_ativo

    logger.info(
        "Worker da fila iniciado."
    )

    while True:

        try:

            # ------------------------------------------------
            # STOP
            # ------------------------------------------------

            if not bot_ativo:

                await asyncio.sleep(2)

                continue

            # ------------------------------------------------
            # Recuperar produtos presos
            # ------------------------------------------------

            await asyncio.to_thread(
                recuperar_processamentos_presos,
            )

            # ------------------------------------------------
            # Buscar próximo produto
            # ------------------------------------------------

            produto = await asyncio.to_thread(
                buscar_proximo_produto,
            )

            if not produto:

                logger.info(
                    "Nenhum produto pendente. "
                    "Aguardando novos links..."
                )

                await asyncio.sleep(10)

                continue

            # ------------------------------------------------
            # Processar
            # ------------------------------------------------

            sucesso = await processar_produto(
                bot=bot,
                produto_fila=produto,
            )

            # ------------------------------------------------
            # STOP pode ter sido acionado
            # ------------------------------------------------

            if not bot_ativo:

                continue

            # ------------------------------------------------
            # Se publicou, aguarda intervalo
            # ------------------------------------------------

            if sucesso:

                logger.info(
                    "Aguardando %d minutos "
                    "para o próximo produto...",
                    INTERVALO_MINUTOS,
                )

                for _ in range(
                    INTERVALO_MINUTOS * 60
                ):

                    if not bot_ativo:
                        break

                    await asyncio.sleep(1)

            else:

                logger.warning(
                    "Produto apresentou erro. "
                    "Aguardando 30 segundos antes "
                    "de verificar a fila novamente."
                )

                for _ in range(30):

                    if not bot_ativo:
                        break

                    await asyncio.sleep(1)

        except asyncio.CancelledError:

            logger.info(
                "Worker da fila cancelado."
            )

            raise

        except Exception as erro:

            logger.exception(
                "Erro no worker da fila: %s",
                erro,
            )

            try:

                await enviar_notificacao_admin(
                    bot,
                    (
                        "🚨 <b>ERRO NO WORKER</b>\n"
                        "\n"
                        f"{str(erro)[:1500]}\n"
                        "\n"
                        "O worker tentará continuar automaticamente."
                    ),
                )

            except Exception:

                pass

            await asyncio.sleep(30)


# ============================================================
# INICIAR WORKER
# ============================================================

async def iniciar_worker(
    application: Application,
):

    global worker_task

    if worker_task is not None:

        if not worker_task.done():

            logger.info(
                "Worker já está em execução."
            )

            return

    worker_task = asyncio.create_task(
        worker_fila(
            application.bot
        )
    )

    logger.info(
        "Worker criado."
    )


# ============================================================
# POST INIT
# ============================================================

async def post_init(
    application: Application,
):

    try:

        me = await application.bot.get_me()

        logger.info(
            "Telegram conectado: @%s",
            me.username,
        )

    except Exception as erro:

        logger.exception(
            "Erro ao conectar Telegram: %s",
            erro,
        )

        raise

    await iniciar_worker(
        application
    )


# ============================================================
# POST SHUTDOWN
# ============================================================

async def post_shutdown(
    application: Application,
):

    global worker_task

    if worker_task is not None:

        if not worker_task.done():

            logger.info(
                "Cancelando worker..."
            )

            worker_task.cancel()

            try:

                await worker_task

            except asyncio.CancelledError:

                pass

    worker_task = None

    logger.info(
        "Worker da fila finalizado."
    )


# ============================================================
# MAIN
# ============================================================

def main():

    logger.info(
        "🦊 RAPOSA CAÇADORA iniciando..."
    )

    # --------------------------------------------------------
    # SERVIDOR HTTP DO RENDER
    # --------------------------------------------------------

    servidor_thread = threading.Thread(
        target=iniciar_servidor_http,
        daemon=True,
    )

    servidor_thread.start()

    # --------------------------------------------------------
    # CONFIGURAÇÃO
    # --------------------------------------------------------

    validar_configuracao()

    iniciar_supabase()

    # --------------------------------------------------------
    # RECUPERAR PROCESSAMENTOS PRESOS
    # --------------------------------------------------------

    recuperar_processamentos_presos()

    # --------------------------------------------------------
    # APLICAÇÃO TELEGRAM
    # --------------------------------------------------------

    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # --------------------------------------------------------
    # COMANDOS
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            comando_start,
        )
    )

    application.add_handler(
        CommandHandler(
            "status",
            comando_status,
        )
    )

    application.add_handler(
        CommandHandler(
            "whatsapp_lote",
            comando_whatsapp_lote,
        )
    )

    application.add_handler(
        CommandHandler(
            "fila",
            comando_fila,
        )
    )

    application.add_handler(
        CommandHandler(
            "erros",
            comando_erros,
        )
    )

    application.add_handler(
        CommandHandler(
            "retry",
            comando_retry,
        )
    )

    application.add_handler(
        CommandHandler(
            "stop",
            comando_stop,
        )
    )

    application.add_handler(
        CommandHandler(
            "iniciar",
            comando_iniciar,
        )
    )

    # --------------------------------------------------------
    # BOTÕES
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            callback_controle,
        )
    )

    # --------------------------------------------------------
    # RECEBER LINKS
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            receber_links,
        )
    )

    # --------------------------------------------------------
    # START POLLING
    # --------------------------------------------------------

    # Durante um deploy do Render, a instância antiga pode
    # permanecer ativa por alguns segundos enquanto a nova sobe.
    # Aguardamos antes de iniciar o polling do Telegram.

    atraso_polling = int(
        os.getenv(
            "TELEGRAM_POLLING_START_DELAY",
            "75",
        )
    )

    logger.info(
        "Aguardando %d segundos antes de iniciar o polling do Telegram...",
        atraso_polling,
    )

    import time

    time.sleep(
        atraso_polling
    )

    logger.info(
        "Bot Telegram iniciando polling..."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )
    
# ============================================================
# EXECUÇÃO
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        logger.info(
            "🦊 Raposa Caçadora encerrada."
        )

    except Exception as erro:

        logger.exception(
            "Erro fatal: %s",
            erro,
        )

        raise
