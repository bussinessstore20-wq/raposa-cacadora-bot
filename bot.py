
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


# Pipeline temporária para processamento manual de carrosséis Instagram.
import instagram_pipeline as instagram_pipeline
from manus import detalhar_tarefa, enviar_mensagem_tarefa

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
WAHA_TEXT_ONLY_PREVIEW = os.getenv("WAHA_TEXT_ONLY_PREVIEW", "false").strip().lower() in {"1", "true", "yes", "on"}

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

# Controle independente do lote histórico do WhatsApp.
whatsapp_backfill_task: asyncio.Task | None = None
whatsapp_backfill_stop_requested = False
normal_product_in_progress = False


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

async def encaminhar_para_whatsapp(produto: dict[str, Any], mensagem_html: str, link_afiliado: str, *, historico: bool = False, produto_fila_id: int | None = None) -> bool:
    """Encaminha ao WhatsApp o mesmo texto publicado no Telegram, sem anexar foto."""
    if not historico and not WHATSAPP_FORWARD_ENABLED:
        return False
    if not WAHA_API_KEY or not WAHA_CHANNEL_ID:
        logger.warning("WhatsApp não enviado: configure WAHA_API_KEY e WAHA_CHANNEL_ID.")
        return False

    import re
    from html import unescape

    # Remove apenas a marcação HTML do Telegram; mantém texto, emojis e quebras.
    texto = unescape(re.sub(r"<[^>]+>", "", mensagem_html or "")).strip()
    if link_afiliado:
        # Evita duplicar a URL se ela já estiver presente no corpo da mensagem.
        if link_afiliado not in texto:
            texto = f"{texto}\n\n🛒 COMPRAR AGORA: {link_afiliado}"

    def _enviar():
        headers = {"X-Api-Key": WAHA_API_KEY, "Content-Type": "application/json", "Accept": "application/json"}
        payload = {
            "session": "default",
            "chatId": WAHA_CHANNEL_ID,
            "text": texto,
            "linkPreview": True,
        }
        ultima_excecao = None
        for tentativa in range(1, 4):
            try:
                resposta = requests.post(
                    f"{WAHA_URL}/api/sendText",
                    headers=headers,
                    json=payload,
                    timeout=25,
                )
                resposta.raise_for_status()
                try:
                    dados = resposta.json()
                except Exception:
                    dados = {}
                return resposta.status_code, str(dados.get("id") or dados.get("messageId") or "")
            except requests.RequestException as erro:
                ultima_excecao = erro
                status = getattr(getattr(erro, "response", None), "status_code", None)
                # Erros de sessão/configuração (4xx, exceto 408/429) não melhoram com repetição.
                if status is not None and 400 <= status < 500 and status not in (408, 429):
                    raise
                if tentativa < 3:
                    espera = (2, 5)[tentativa - 1]
                    logger.warning("Falha temporária WAHA no envio (tentativa %s/3; HTTP %s). Nova tentativa em %ss.", tentativa, status or "sem resposta", espera)
                    import time
                    time.sleep(espera)
        raise ultima_excecao

    try:
        status_code, message_id = await asyncio.to_thread(_enviar)
        logger.info("Texto da oferta encaminhado ao WhatsApp sem imagem (HTTP %s).", status_code)
        if produto_fila_id is not None and supabase is not None:
            try:
                await asyncio.to_thread(lambda: supabase.table("whatsapp_backfill_log").upsert(
                    {"produto_fila_id": produto_fila_id, "channel_id": WAHA_CHANNEL_ID, "status": "sent", "whatsapp_message_id": message_id, "error": None, "sent_at": datetime.now(timezone.utc).isoformat()},
                    on_conflict="produto_fila_id"
                ).execute())
            except Exception:
                logger.exception("WhatsApp enviado, mas não foi possível registrar confirmação do produto #%s.", produto_fila_id)
        return {"status_code": status_code, "message_id": message_id}
    except Exception as erro:
        logger.exception("Falha ao encaminhar oferta ao WhatsApp após tentativas automáticas.")
        if produto_fila_id is not None and supabase is not None:
            try:
                await asyncio.to_thread(lambda: supabase.table("whatsapp_backfill_log").upsert(
                    {"produto_fila_id": produto_fila_id, "channel_id": WAHA_CHANNEL_ID, "status": "error", "error": str(erro)[:1000]},
                    on_conflict="produto_fila_id"
                ).execute())
            except Exception:
                logger.exception("Não foi possível registrar a falha do WhatsApp para o produto #%s.", produto_fila_id)
        return False


async def processar_lote_historico_whatsapp(bot: Bot, limite: int = 5):
    """Envia ofertas já publicadas no Telegram ao WhatsApp, com imagem e pausa controlável."""
    global whatsapp_backfill_stop_requested, normal_product_in_progress

    import gc

    # Aguarda terminar qualquer produto novo iniciado antes do lote histórico.
    while normal_product_in_progress and not whatsapp_backfill_stop_requested:
        await asyncio.sleep(1)
    if whatsapp_backfill_stop_requested:
        await enviar_notificacao_admin(bot, "⏹️ Lote histórico cancelado antes de iniciar.")
        return

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
    interrompido = False
    for item in candidatos:
        if whatsapp_backfill_stop_requested:
            interrompido = True
            logger.warning("Lote histórico interrompido pelo STOP antes do próximo produto.")
            break

        pid = int(item["id"])
        produto = None
        try:
            link = str(item.get("link") or "")
            lower = link.lower()
            marketplace = "mercadolivre" if any(d in lower for d in ("mercadolivre.com.br", "mercadolibre.com", "meli.la")) else "shopee"
            try:
                if marketplace == "mercadolivre":
                    produto = await asyncio.to_thread(buscar_produto_mercadolivre, link)
                else:
                    produto = await asyncio.to_thread(buscar_produto_por_link, link)
            except Exception:
                logger.exception("Não foi possível consultar novamente o produto histórico #%s; usando os dados salvos na fila.", pid)
                produto = None

            if whatsapp_backfill_stop_requested:
                interrompido = True
                logger.warning("Lote histórico interrompido antes do envio do produto #%s.", pid)
                break

            if produto:
                produto["marketplace"] = marketplace
                mensagem = montar_mensagem(produto, marketplace=marketplace)
                # Se a consulta não retornou imagem, aproveita a imagem salva na publicação do Telegram.
                if not produto.get("imageUrl"):
                    produto["imageUrl"] = str(item.get("image_url") or "").strip()
            else:
                nome_salvo = str(item.get("product_name") or "Oferta")
                imagem_salva = str(item.get("image_url") or "").strip()
                produto = {"productName": nome_salvo, "imageUrl": imagem_salva, "marketplace": marketplace}
                titulo = "MERCADO LIVRE" if marketplace == "mercadolivre" else "SHOPEE"
                mensagem = f"🦊🔥 ACHADINHO {titulo}\n\n✨ {nome_salvo}\n\n🚨 Oferta publicada anteriormente; preço e disponibilidade podem ter mudado."
                logger.warning("Usando nome e imagem salvos para o produto histórico #%s.", pid)

            # Preserva a imagem como no Telegram; WAHA recebe mídia + legenda.
            resultado = await encaminhar_para_whatsapp(produto, mensagem, link, historico=True)
            if not resultado:
                raise RuntimeError("O envio ao WhatsApp não foi confirmado; confira a sessão e a chave WAHA nos logs do serviço.")
            await asyncio.to_thread(
                lambda pid=pid, mid=resultado.get("message_id", ""): supabase.table("whatsapp_backfill_log").upsert(
                    {"produto_fila_id": pid, "channel_id": WAHA_CHANNEL_ID, "status": "sent", "whatsapp_message_id": mid, "error": None, "sent_at": datetime.now(timezone.utc).isoformat()},
                    on_conflict="produto_fila_id"
                ).execute()
            )
            ok += 1

            # Dá tempo para o WAHA concluir o envio da mídia e estabilizar antes do próximo.
            # A pausa pode ser interrompida pelo botão STOP.
            for _ in range(30):
                if whatsapp_backfill_stop_requested:
                    interrompido = True
                    break
                await asyncio.sleep(1)
            if interrompido:
                logger.warning("Lote histórico pausado depois do envio confirmado do produto #%s.", pid)
                break
        except Exception as erro:
            falhas += 1
            logger.exception("Falha no lote histórico para produto fila #%s", pid)
            await asyncio.to_thread(
                lambda pid=pid, msg=str(erro)[:1000]: supabase.table("whatsapp_backfill_log").upsert(
                    {"produto_fila_id": pid, "channel_id": WAHA_CHANNEL_ID, "status": "error", "error": msg},
                    on_conflict="produto_fila_id"
                ).execute()
            )
            # Falhou um envio: interrompe o lote para não disparar várias requisições
            # contra o WAHA quando ele está devolvendo 502/503. O próximo lote pode
            # ser iniciado manualmente depois que o serviço estiver saudável.
            interrompido = True
            logger.error("Lote histórico encerrado após a primeira falha para proteger o WAHA; corrija/aguarde o serviço antes de tentar novamente.")
            break
        finally:
            # Libera referências do processo Python do bot. A RAM do WAHA é separada;
            # a pausa entre envios também reduz a pressão sobre o serviço de mídia.
            item = None
            produto = None
            gc.collect()

    if falhas:
        status_lote = "⚠️ <b>LOTE PAUSADO APÓS FALHA NO WHATSAPP</b>"
        orientacao_lote = "Não reinicie ainda: verifique/aguarde o WAHA voltar a responder sem erro 502/503."
    elif interrompido:
        status_lote = "⏹️ <b>LOTE INTERROMPIDO PELO STOP</b>"
        orientacao_lote = "Use /whatsapp_lote quando quiser continuar."
    else:
        status_lote = "📦 <b>LOTE HISTÓRICO WHATSAPP CONCLUÍDO</b>"
        orientacao_lote = "Use /whatsapp_lote para processar o próximo lote quando quiser continuar."
    await enviar_notificacao_admin(
        bot,
        f"{status_lote}\n\n"
        f"✅ Enviados: <b>{ok}</b>\n"
        f"⚠️ Falhas: <b>{falhas}</b>\n"
        "🖼️ O lote mantém as imagens sempre que houver URL disponível.\n"
        "📨 Somente WhatsApp foi utilizado; nenhuma nova postagem foi feita no Telegram.\n"
        f"{orientacao_lote}"
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

            resultado_whatsapp = await encaminhar_para_whatsapp(produto, mensagem, link_afiliado, produto_fila_id=produto.get("_fila_id"))
            if not resultado_whatsapp:
                await enviar_notificacao_admin(bot, f"⚠️ <b>WHATSAPP NÃO CONFIRMADO</b>\n\nOferta #{produto.get('_fila_id', '?')} publicada no Telegram, mas o envio ao WhatsApp falhou após 3 tentativas. A falha foi registrada para conferência. Verifique se a sessão WAHA está ativa.")

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

        resultado_whatsapp = await encaminhar_para_whatsapp(produto, mensagem, link_afiliado, produto_fila_id=produto.get("_fila_id"))
        if not resultado_whatsapp:
            await enviar_notificacao_admin(bot, f"⚠️ <b>WHATSAPP NÃO CONFIRMADO</b>\n\nOferta #{produto.get('_fila_id', '?')} publicada no Telegram, mas o envio ao WhatsApp falhou após 3 tentativas. A falha foi registrada para conferência. Verifique se a sessão WAHA está ativa.")

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
        produto["_fila_id"] = produto_id

        # Se o STOP foi acionado enquanto consultávamos o marketplace,
        # devolve o item à fila em vez de publicar depois da pausa.
        if not bot_ativo:
            if supabase is not None:
                await asyncio.to_thread(
                    lambda: supabase.table("produtos_fila").update(
                        {"status": "pending", "processing_at": None}
                    ).eq("id", produto_id).eq("status", "processing").execute()
                )
            logger.info("STOP detectado antes da publicação; produto %s voltou para a fila.", produto_id)
            return False

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
        [[
            InlineKeyboardButton("▶️ INICIAR FILA NOVA", callback_data="bot_iniciar"),
            InlineKeyboardButton("⏹️ STOP FILA NOVA", callback_data="bot_stop"),
        ]]
    )


def teclado_lote_historico():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⏹️ STOP LOTES ANTIGOS", callback_data="whatsapp_lote_stop")
    ]])


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

async def comando_whatsapp_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envia um único produto já publicado ao WhatsApp, sem republicar no Telegram."""
    if not usuario_autorizado(update):
        return
    if update.message is None:
        return
    if supabase is None:
        await update.message.reply_text("❌ Supabase não está conectado. Nenhum envio foi feito.")
        return
    if not WAHA_API_KEY or not WAHA_CHANNEL_ID:
        await update.message.reply_text("⚠️ WAHA_API_KEY ou WAHA_CHANNEL_ID não configurada. Nenhum envio foi feito.")
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Use: /whatsapp_id 426\nEnvie apenas um ID por vez.")
        return

    produto_id = int(context.args[0])
    try:
        resposta = await asyncio.to_thread(
            lambda: supabase.table("produtos_fila")
                .select("id,link,product_name,image_url,status,fila_origem,bot_id,telegram_message_id")
                .eq("id", produto_id)
                .eq("fila_origem", FILA_ORIGEM)
                .eq("bot_id", FILA_ORIGEM)
                .eq("status", "published")
                .not_.is_("telegram_message_id", "null")
                .limit(1)
                .execute()
        )
        if not resposta.data:
            await update.message.reply_text(
                f"⚠️ O registro #{produto_id} não foi encontrado como publicado pelo Raposa Caçadora Bot. Nada foi enviado."
            )
            return
        item = resposta.data[0]

        registro_log = await asyncio.to_thread(
            lambda: supabase.table("whatsapp_backfill_log")
                .select("status,whatsapp_message_id")
                .eq("produto_fila_id", produto_id)
                .eq("channel_id", WAHA_CHANNEL_ID)
                .limit(1)
                .execute()
        )
        logs = registro_log.data or []
        if logs and logs[0].get("status") in ("sent", "sending"):
            await update.message.reply_text(
                f"🛡️ O produto #{produto_id} já está marcado como {logs[0].get('status')} para este WhatsApp. Não vou reenviar para evitar duplicidade."
            )
            return

        # Reserva o ID antes da chamada ao WAHA para bloquear comandos repetidos.
        await asyncio.to_thread(
            lambda: supabase.table("whatsapp_backfill_log").upsert(
                {
                    "produto_fila_id": produto_id,
                    "channel_id": WAHA_CHANNEL_ID,
                    "status": "sending",
                    "error": None,
                },
                on_conflict="produto_fila_id"
            ).execute()
        )

        link = str(item.get("link") or "").strip()
        nome = str(item.get("product_name") or "Oferta").strip()
        lower = link.lower()
        marketplace = "mercadolivre" if any(d in lower for d in ("mercadolivre.com.br", "mercadolibre.com", "meli.la")) else "shopee"
        titulo = "MERCADO LIVRE" if marketplace == "mercadolivre" else "SHOPEE"
        produto = {
            "productName": nome,
            "imageUrl": str(item.get("image_url") or "").strip(),
            "marketplace": marketplace,
        }
        mensagem = (
            f"🦊🔥 ACHADINHO {titulo}\n\n"
            f"✨ {nome}\n\n"
            "🚨 Oferta publicada anteriormente; preço e disponibilidade podem ter mudado."
        )
        resultado = await encaminhar_para_whatsapp(produto, mensagem, link, historico=True)
        if not resultado:
            raise RuntimeError("WAHA não confirmou o envio. Confira a sessão e os logs antes de tentar novamente.")

        await asyncio.to_thread(
            lambda: supabase.table("whatsapp_backfill_log").upsert(
                {
                    "produto_fila_id": produto_id,
                    "channel_id": WAHA_CHANNEL_ID,
                    "status": "sent",
                    "whatsapp_message_id": resultado.get("message_id", ""),
                    "error": None,
                    "sent_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="produto_fila_id"
            ).execute()
        )
        await update.message.reply_text(
            f"✅ Produto #{produto_id} enviado ao WhatsApp.\n\n{nome}\n{link}\n\nID WAHA: {resultado.get('message_id') or 'não informado'}"
        )
    except Exception as erro:
        logger.exception("Erro no comando /whatsapp_id para produto #%s", produto_id)
        try:
            await asyncio.to_thread(
                lambda: supabase.table("whatsapp_backfill_log").upsert(
                    {
                        "produto_fila_id": produto_id,
                        "channel_id": WAHA_CHANNEL_ID,
                        "status": "error",
                        "error": str(erro)[:1000],
                    },
                    on_conflict="produto_fila_id"
                ).execute()
            )
        except Exception:
            logger.exception("Não foi possível registrar falha de envio do produto #%s", produto_id)
        await update.message.reply_text(
            f"❌ Não consegui confirmar o envio do produto #{produto_id}. Confira os logs do serviço antes de tentar novamente.\n\nErro: {str(erro)[:500]}"
        )


async def comando_whatsapp_lote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global whatsapp_backfill_task, whatsapp_backfill_stop_requested

    if not usuario_autorizado(update):
        return
    if not WAHA_API_KEY or not WAHA_CHANNEL_ID:
        await update.message.reply_text(
            "⚠️ Ainda falta configurar WAHA_API_KEY no Render do raposa-cacadora-bot. Não enviei nada.",
            parse_mode=ParseMode.HTML,
        )
        return
    if whatsapp_backfill_task is not None and not whatsapp_backfill_task.done():
        await update.message.reply_text(
            "⏳ Já existe um lote histórico em execução. Use STOP LOTES ANTIGOS para pará-lo; a fila nova tem controle separado.",
            parse_mode=ParseMode.HTML,
            reply_markup=teclado_lote_historico(),
        )
        return

    whatsapp_backfill_stop_requested = False
    await update.message.reply_text(
        "📦 Lote histórico iniciado: até 5 ofertas antigas serão enviadas ao WhatsApp COM IMAGEM quando disponível, com pausa de 30 segundos entre envios.\n\n⏹️ Use STOP LOTES ANTIGOS para parar somente este lote. A fila nova tem STOP separado; links novos ficam aguardando até o histórico terminar.",
        parse_mode=ParseMode.HTML,
        reply_markup=teclado_lote_historico(),
    )
    whatsapp_backfill_task = asyncio.create_task(
        processar_lote_historico_whatsapp(context.bot, limite=5)
    )


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

    global bot_ativo, whatsapp_backfill_stop_requested

    if not usuario_autorizado(update):
        return

    bot_ativo = False

    logger.warning("STOP da fila nova acionado; o lote histórico não foi alterado.")

    await update.message.reply_text(
        (
            "⏹️ <b>PUBLICAÇÃO PARADA</b>\n"
            "\n"
            "Somente a fila de novos links foi pausada.\n"
            "Os produtos permanecem salvos no Supabase.\n"
            "O lote de links antigos no WhatsApp não é afetado.\n\n"
            "▶️ Use /iniciar ou INICIAR FILA NOVA para continuar."
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

    global bot_ativo, whatsapp_backfill_stop_requested

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

    if query.data == "whatsapp_lote_stop":

        whatsapp_backfill_stop_requested = True
        logger.warning("STOP exclusivo do lote histórico acionado; fila nova não foi alterada.")
        texto = (
            "⏹️ <b>LOTE DE LINKS ANTIGOS PARANDO</b>\n\n"
            "Somente o lote histórico do WhatsApp foi sinalizado para parar. "
            "A fila de novos links continua no estado em que estava."
        )
        teclado_resposta = teclado_lote_historico()

    elif query.data == "bot_stop":

        bot_ativo = False
        logger.warning("STOP exclusivo da fila nova acionado; lote histórico não foi alterado.")
        texto = (
            "⏹️ <b>FILA DE NOVOS LINKS PAUSADA</b>\n\n"
            "Os novos produtos permanecem salvos no Supabase. "
            "O lote de links antigos no WhatsApp não foi afetado.\n\n"
            "▶️ Use INICIAR FILA NOVA para retomar."
        )
        teclado_resposta = teclado_controle()

    elif query.data == "bot_iniciar":

        bot_ativo = True

        logger.info(
            "Publicação iniciada pelo botão."
        )

        texto = (
            "▶️ <b>FILA DE NOVOS LINKS INICIADA</b>\n"
            "\n"
            "A Raposa voltou a processar a fila nova.\n"
            "O lote histórico do WhatsApp não foi alterado.\n\n"
            f"⏱️ Intervalo: <b>{INTERVALO_MINUTOS} minutos</b>"
        )
        teclado_resposta = teclado_controle()

    else:

        return

    try:

        await query.edit_message_text(
            texto,
            parse_mode=ParseMode.HTML,
            reply_markup=teclado_resposta,
        )

    except Exception:

        try:

            if query.message:

                await query.message.reply_text(
                    texto,
                    parse_mode=ParseMode.HTML,
                    reply_markup=teclado_resposta,
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

    global bot_ativo, whatsapp_backfill_task, normal_product_in_progress

    logger.info(
        "Worker da fila iniciado."
    )

    while True:

        try:

            # ------------------------------------------------
            # Controles independentes
            # ------------------------------------------------

            if not bot_ativo:
                await asyncio.sleep(2)
                continue

            # Enquanto o lote histórico roda, novos links podem ser recebidos
            # e salvos, mas a fila nova não processa produtos.
            if whatsapp_backfill_task is not None and not whatsapp_backfill_task.done():
                await asyncio.sleep(1)
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

            # O lote histórico pode ter sido iniciado enquanto a consulta
            # ao Supabase estava aguardando; não comece um produto novo nesse caso.
            if whatsapp_backfill_task is not None and not whatsapp_backfill_task.done():
                await asyncio.sleep(1)
                continue

            # ------------------------------------------------
            # Processar
            # ------------------------------------------------

            normal_product_in_progress = True
            try:
                sucesso = await processar_produto(
                    bot=bot,
                    produto_fila=produto,
                )
            finally:
                normal_product_in_progress = False

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

    # Inicialização temporária e restrita ao lote histórico #24.
    if os.getenv("INSTAGRAM_BATCH24_AUTOSTART", "false").strip().lower() in {"1", "true", "yes", "on"}:
        asyncio.create_task(_iniciar_lote24_automatico(application.bot))



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
# LOTE INSTAGRAM TEMPORÁRIO — SOMENTE SOB COMANDO ADMINISTRATIVO
# ============================================================

INSTAGRAM_TEMP_BOT_ID = os.getenv("BOT_ID", "raposa-cacadora").strip()
lote24_poll_task = None

async def _acompanhar_tarefa_manus(post_id: int, task_id: str, bot: Bot):
    """Consulta o Manus sem depender do webhook da conta Render suspensa."""
    import time
    limite = time.monotonic() + 60 * 60
    ultimo_status = ""
    while time.monotonic() < limite:
        try:
            data = await asyncio.to_thread(detalhar_tarefa, task_id)
            task = data.get("task") or {}
            status = str(task.get("status") or "").strip().lower()
            stop_reason = str(task.get("stop_reason") or "").strip().lower()
            if status != ultimo_status:
                logger.info("Lote Instagram #%s: tarefa Manus status=%s stop_reason=%s", post_id, status, stop_reason)
                ultimo_status = status
            if status in {"stopped", "completed", "finished", "done"} or stop_reason:
                ok, mensagem = await asyncio.to_thread(
                    instagram_pipeline.processar_webhook_manus,
                    supabase,
                    {"event_type": "task_stopped", "task_detail": task},
                )
                logger.info("Resultado Manus lote #%s: ok=%s mensagem=%s", post_id, ok, mensagem)
                await enviar_notificacao_admin(
                    bot,
                    f"🦊 <b>LOTE #{post_id} — RETORNO DO MANUS</b>\n\n{mensagem}\n\nConfira a prévia no Telegram antes de aprovar.",
                )
                return
        except Exception:
            logger.exception("Falha ao consultar tarefa Manus do lote #%s; nova tentativa em 15s.", post_id)
        await asyncio.sleep(15)
    await enviar_notificacao_admin(
        bot,
        f"⚠️ <b>LOTE #{post_id}</b>\n\nO acompanhamento automático atingiu 60 minutos. A tarefa Manus pode continuar ativa; confira o painel do Manus antes de iniciar novamente.",
    )


async def _iniciar_lote24_automatico(bot: Bot):
    """Inicia apenas o lote #24 uma vez, mantendo aprovação manual antes da publicação."""
    global lote24_poll_task
    if supabase is None:
        logger.error("Autostart lote #24 abortado: Supabase desconectado.")
        return
    try:
        rows = await asyncio.to_thread(
            lambda: supabase.table("instagram_posts")
                .select("id,status,bot_id,manus_task_id")
                .eq("id", 24).eq("bot_id", INSTAGRAM_TEMP_BOT_ID)
                .limit(1).execute().data or []
        )
        if not rows:
            logger.error("Autostart lote #24 abortado: registro não encontrado para bot_id=%s.", INSTAGRAM_TEMP_BOT_ID)
            return
        post=rows[0]
        if str(post.get("status") or "").lower() != "pending":
            logger.info("Autostart lote #24 ignorado: status atual=%s; nenhum novo task será criado.", post.get("status"))
            return
        if lote24_poll_task is not None and not lote24_poll_task.done():
            logger.info("Autostart lote #24 ignorado: acompanhamento já em execução.")
            return
        iniciou = await asyncio.to_thread(instagram_pipeline.processar_lote_se_pronto, supabase, 24)
        rows_atual = await asyncio.to_thread(
            lambda: supabase.table("instagram_posts")
                .select("status,error,manus_task_id")
                .eq("id", 24).eq("bot_id", INSTAGRAM_TEMP_BOT_ID)
                .limit(1).execute().data or []
        )
        atual=rows_atual[0] if rows_atual else {}
        task_id=str(atual.get("manus_task_id") or "").strip()
        if not iniciou or not task_id:
            logger.error("Autostart lote #24 não criou tarefa Manus. status=%s erro=%s", atual.get("status"), atual.get("error"))
            await enviar_notificacao_admin(bot, f"❌ <b>LOTE #24 NÃO INICIADO</b>\n\n{str(atual.get('error') or 'Confira os logs do serviço.')[:800]}")
            return
        lote24_poll_task=asyncio.create_task(_acompanhar_tarefa_manus(24, task_id, bot))
        logger.info("Autostart: lote #24 enviado ao Manus, task_id=%s; aguardando resultado e aprovação.", task_id)
        await enviar_notificacao_admin(bot, f"🦊 <b>LOTE #24 ENVIADO AO MANUS</b>\n\n🆔 Tarefa: <code>{task_id}</code>\nAcompanharei o processamento e enviarei a prévia para aprovação. Nenhuma publicação será feita sem aprovação.")
    except Exception:
        logger.exception("Falha no autostart do lote #24.")

async def comando_lote24(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global lote24_poll_task
    if not usuario_autorizado(update):
        return
    if update.message is None:
        return
    if supabase is None:
        await update.message.reply_text("❌ Supabase não está conectado. Não iniciei o lote.")
        return
    try:
        post_rows = await asyncio.to_thread(
            lambda: supabase.table("instagram_posts")
                .select("id,status,bot_id,manus_task_id")
                .eq("id", 24)
                .eq("bot_id", INSTAGRAM_TEMP_BOT_ID)
                .limit(1).execute().data or []
        )
        if not post_rows:
            await update.message.reply_text(
                f"❌ Lote #24 não encontrado para bot_id={INSTAGRAM_TEMP_BOT_ID}. Confira BOT_ID no Render."
            )
            return
        post = post_rows[0]
        status = str(post.get("status") or "").lower()
        if status != "pending":
            await update.message.reply_text(
                f"⚠️ Lote #24 está com status <b>{status}</b>, não pending. Não vou criar outra tarefa Manus nem duplicar processamento.",
                parse_mode=ParseMode.HTML,
            )
            return
        if lote24_poll_task is not None and not lote24_poll_task.done():
            await update.message.reply_text("⏳ Já existe um acompanhamento de tarefa Manus em execução.")
            return
        iniciou = await asyncio.to_thread(
            instagram_pipeline.processar_lote_se_pronto,
            supabase,
            24,
        )
        if not iniciou:
            atual = await asyncio.to_thread(
                lambda: supabase.table("instagram_posts")
                    .select("status,error")
                    .eq("id", 24).eq("bot_id", INSTAGRAM_TEMP_BOT_ID)
                    .limit(1).execute().data or []
            )
            detalhes = atual[0] if atual else {}
            await update.message.reply_text(
                f"❌ Não consegui iniciar o lote #24. Status: {detalhes.get('status','desconhecido')}. Erro: {str(detalhes.get('error') or 'verifique os logs')[:700]}"
            )
            return
        atualizado = await asyncio.to_thread(
            lambda: supabase.table("instagram_posts")
                .select("manus_task_id,status")
                .eq("id", 24).eq("bot_id", INSTAGRAM_TEMP_BOT_ID)
                .limit(1).execute().data or []
        )
        task_id = str((atualizado[0] if atualizado else {}).get("manus_task_id") or "").strip()
        if not task_id:
            await update.message.reply_text("⚠️ O lote foi reservado, mas o Manus não retornou task_id. Confira o status do lote no Supabase antes de tentar novamente.")
            return
        lote24_poll_task = asyncio.create_task(_acompanhar_tarefa_manus(24, task_id, context.bot))
        await update.message.reply_text(
            f"✅ Lote #24 enviado ao Manus.\n\n🆔 Tarefa: <code>{task_id}</code>\n⏳ Vou acompanhar o processamento e enviar a prévia para sua aprovação no Telegram.\n\n🚫 Nenhuma publicação será feita sem sua aprovação.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as erro:
        logger.exception("Falha ao iniciar lote Instagram #24")
        await update.message.reply_text(f"❌ Falha ao iniciar lote #24: {str(erro)[:900]}")

async def callback_carrossel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None:
        return
    if not usuario_autorizado(update):
        await query.answer("Você não está autorizado.", show_alert=True)
        return
    try:
        acao, raw_post_id = query.data.split(":", 1)
        post_id = int(raw_post_id)
    except Exception:
        await query.answer("Botão inválido.", show_alert=True)
        return
    if supabase is None:
        await query.answer("Supabase não está conectado.", show_alert=True)
        return
    try:
        rows = await asyncio.to_thread(
            lambda: supabase.table("instagram_posts")
                .select("id,manus_task_id,status,bot_id")
                .eq("id", post_id).eq("bot_id", INSTAGRAM_TEMP_BOT_ID)
                .limit(1).execute().data or []
        )
        if not rows:
            await query.answer("Carrossel não encontrado.", show_alert=True)
            return
        post = rows[0]
        task_id = str(post.get("manus_task_id") or "").strip()
        status = str(post.get("status") or "").lower()
        if not task_id:
            await query.answer("Tarefa Manus não vinculada.", show_alert=True)
            return
        if status in {"approved", "rejected", "published"}:
            await query.answer(f"Já está com status {status}.", show_alert=True)
            return
        aprovado = acao == "carousel_approve"
        instrucao = (
            f"O carrossel #{post_id} foi APROVADO pelo administrador no Telegram. Continue o fluxo e publique no Instagram conforme as instruções originais."
            if aprovado else
            f"O carrossel #{post_id} foi REPROVADO pelo administrador no Telegram. Não publique este carrossel. Encerre o fluxo."
        )
        await asyncio.to_thread(enviar_mensagem_tarefa, task_id, instrucao)
        await asyncio.to_thread(
            lambda: supabase.table("instagram_posts")
                .update({"status": "approved" if aprovado else "rejected", "updated_at": datetime.now(timezone.utc).isoformat()})
                .eq("id", post_id).eq("bot_id", INSTAGRAM_TEMP_BOT_ID).execute()
        )
        await query.answer("Decisão registrada.")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        if query.message:
            await query.message.reply_text(
                f"🦊 Carrossel #{post_id}: {'APROVADO' if aprovado else 'REPROVADO'}. {'O Manus continuará o fluxo.' if aprovado else 'O Manus foi instruído a não publicar.'}"
            )
    except Exception as erro:
        logger.exception("Erro na decisão do carrossel #%s", post_id)
        await query.answer(f"Falha ao registrar decisão: {str(erro)[:150]}", show_alert=True)


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

    application.add_handler(CommandHandler("lote24", comando_lote24))
    application.add_handler(CallbackQueryHandler(callback_carrossel, pattern=r"^carousel_(approve|reject):"))

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
            "whatsapp_id",
            comando_whatsapp_id,
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
