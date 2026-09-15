import asyncio
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
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
    filters,
)

from shopee import (
    buscar_produto_por_link,
    ShopeeAPIError,
)


# ============================================================
# CONFIGURAÇÃO
# ============================================================

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_TOKEN",
    ""
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
).strip()

TELEGRAM_ADMIN_ID = os.getenv(
    "TELEGRAM_ADMIN_ID",
    ""
).strip()

SUPABASE_URL = os.getenv(
    "SUPABASE_URL",
    ""
).strip()

SUPABASE_KEY = os.getenv(
    "SUPABASE_KEY",
    ""
).strip()

INTERVALO_MINUTOS = int(
    os.getenv(
        "INTERVALO_MINUTOS",
        "10",
    )
)

PORT = int(
    os.getenv(
        "PORT",
        "10000",
    )
)


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

    servidor = HTTPServer(
        (
            "0.0.0.0",
            PORT,
        ),
        HealthHandler,
    )

    logger.info(
        "Servidor HTTP iniciado na porta %d",
        PORT,
    )

    servidor.serve_forever()


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

        link = parte.strip()

        if not (
            link.startswith("http://")
            or link.startswith("https://")
        ):
            continue

        if (
            "shopee.com.br" in link
            or "s.shopee.com.br" in link
        ):
            links.append(link)

    # Remove duplicados mantendo a ordem.
    resultado = []

    vistos = set()

    for link in links:

        if link not in vistos:

            vistos.add(link)
            resultado.append(link)

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

            # Violação do índice UNIQUE.
            if (
                "duplicate"
                in mensagem_erro.lower()
                or "unique"
                in mensagem_erro.lower()
                or "23505"
                in mensagem_erro
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

                erros.append(
                    link
                )

    return (
        adicionados,
        duplicados,
        erros,
    )


# ============================================================
# SUPABASE - BUSCAR PRÓXIMO
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

    # Busca tentativa atual.
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
):

    nome = (
        produto.get(
            "productName"
        )
        or "Produto"
    )

    preco = numero(
        produto.get(
            "price"
        )
    )

    preco_min = numero(
        produto.get(
            "priceMin"
        )
    )

    desconto = numero(
        produto.get(
            "priceDiscountRate"
        )
    )

    avaliacao = numero(
        produto.get(
            "ratingStar"
        )
    )

    vendas = inteiro(
        produto.get(
            "sales"
        )
    )

    loja = (
        produto.get(
            "shopName"
        )
        or "Loja Shopee"
    )

    preco_atual = preco

    if preco_min > 0:
        preco_atual = preco_min

    if (
        desconto > 0
        and desconto < 100
        and preco_atual > 0
    ):

        preco_anterior = (
            preco_atual
            / (
                1
                - desconto / 100
            )
        )

    else:

        preco_anterior = preco_atual

    mensagem = (
        "🦊 <b>RAPOSA CAÇADORA</b>\n"
        "\n"
        "🔥 <b>OFERTA EM DESTAQUE</b>\n"
        "\n"
        f"✨ <b>{nome}</b>\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"❌ De: <s>{moeda(preco_anterior)}</s>\n"
        f"💰 <b>Por apenas: {moeda(preco_atual)}</b>\n"
        f"🏷️ <b>{desconto:.0f}% OFF</b>\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"⭐ <b>{avaliacao:.1f}</b>/5 de avaliação\n"
        f"📦 <b>{formatar_vendas(vendas)}</b> vendas\n"
        f"🏪 <b>{loja}</b>\n"
        "\n"
        "🚨 <b>Preço sujeito a alteração.</b>\n"
        "⚡ Aproveite enquanto estiver disponível!"
    )

    return mensagem


# ============================================================
# PUBLICAR PRODUTO
# ============================================================

async def publicar_produto(
    bot: Bot,
    produto: dict[str, Any],
    link_afiliado: str,
):

    mensagem = montar_mensagem(
        produto
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

    # --------------------------------------------------------
    # Reservar produto
    # --------------------------------------------------------

    reservado = await asyncio.to_thread(
        marcar_processando,
        produto_id,
    )

    if not reservado:

        logger.info(
            "Produto %s não pôde ser reservado. "
            "Provavelmente outro processo assumiu.",
            produto_id,
        )

        return False

    try:

        produto = await asyncio.to_thread(
            buscar_produto_por_link,
            link,
        )

        if not produto:

            raise ShopeeAPIError(
                "Produto não encontrado."
            )

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

        return True

    except ShopeeAPIError as erro:

        logger.error(
            "Erro da Shopee: %s",
            erro,
        )

        await asyncio.to_thread(
            marcar_erro,
            produto_id,
            str(erro),
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

        return False


# ============================================================
# NOTIFICAR ADMIN
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
        )

    except Exception as erro:

        logger.warning(
            "Não foi possível notificar admin: %s",
            erro,
        )


# ============================================================
# COMANDO /STATUS
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

        mensagem = (
            "🦊 <b>RAPOSA CAÇADORA</b>\n"
            "\n"
            "📊 <b>STATUS DA FILA</b>\n"
            "\n"
            f"📦 Total: <b>{total}</b>\n"
            f"✅ Publicados: <b>{publicados}</b>\n"
            f"⏳ Aguardando: <b>{pendentes}</b>\n"
            f"🔄 Processando: <b>{processando}</b>\n"
            f"❌ Erros: <b>{erros}</b>\n"
            "\n"
            f"⏱️ Intervalo: <b>{INTERVALO_MINUTOS} minutos</b>\n"
        )

        await update.message.reply_text(
            mensagem,
            parse_mode=ParseMode.HTML,
        )

    except Exception as erro:

        logger.exception(
            "Erro no comando /status"
        )

        await update.message.reply_text(
            f"❌ Erro ao consultar status:\n{erro}"
        )


# ============================================================
# COMANDO /FILA
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
                "id,product_name,status,created_at"
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

            # Evita mensagens enormes.
            if len(nome) > 45:
                nome = nome[:42] + "..."

            linhas.append(
                f"{simbolo} #{item['id']} — {nome}"
            )

        await update.message.reply_text(
            "\n".join(linhas),
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
# COMANDO /ERROS
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
                erro = erro[:297] + "..."

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

        await update.message.reply_text(
            "\n".join(linhas),
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
# COMANDO /RETRY
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
            "🔄 <b>REPROCESSAMENTO</b>\n"
            "\n"
            f"✅ {quantidade} produto(s) "
            "voltaram para a fila.",
                )

    except Exception as erro:

        logger.exception(
            "Erro no comando /retry"
        )

        await update.message.reply_text(
            f"❌ Erro ao reprocessar:\n{erro}"
        )


# ============================================================
# RECEBER LINKS ENVIADOS PELO ADMIN
# ============================================================

async def receber_links(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not usuario_autorizado(update):
        return

    if not update.message:
        return

    texto = update.message.text or ""

    links = extrair_links(texto)

    if not links:

        await update.message.reply_text(
            "⚠️ Nenhum link da Shopee foi encontrado."
        )

        return

    logger.info(
        "Recebidos %d link(s) pelo Telegram.",
        len(links),
    )

    try:

        adicionados, duplicados, erros = (
            await asyncio.to_thread(
                inserir_links,
                links,
            )
        )

        linhas = [
            "🦊 <b>RAPOSA CAÇADORA</b>",
            "",
            "📥 <b>LINKS RECEBIDOS</b>",
            "",
            f"📦 Recebidos: <b>{len(links)}</b>",
            f"⏳ Adicionados à fila: <b>{adicionados}</b>",
            f"♻️ Já existentes: <b>{duplicados}</b>",
            f"❌ Erros: <b>{len(erros)}</b>",
            "",
        ]

        if adicionados:
            linhas.append(
                "✅ Os links foram recebidos e estão "
                "aguardando publicação."
            )

        if erros:

            linhas.append("")
            linhas.append(
                "⚠️ Links que apresentaram erro:"
            )

            for link in erros:

                if len(link) > 80:
                    link = link[:77] + "..."

                linhas.append(
                    f"• {link}"
                )

        await update.message.reply_text(
            "\n".join(linhas),
            parse_mode=ParseMode.HTML,
        )

        # ----------------------------------------------------
        # Notificação administrativa adicional
        # ----------------------------------------------------

        await enviar_notificacao_admin(
            context.bot,
            (
                "🦊 <b>FILA ATUALIZADA</b>\n"
                "\n"
                f"📥 Links recebidos: <b>{len(links)}</b>\n"
                f"⏳ Adicionados: <b>{adicionados}</b>\n"
                f"♻️ Duplicados: <b>{duplicados}</b>\n"
                f"❌ Erros: <b>{len(erros)}</b>\n"
                "\n"
                "Os links adicionados estão aguardando "
                "publicação automática."
            ),
        )

    except Exception as erro:

        logger.exception(
            "Erro ao inserir links recebidos."
        )

        await update.message.reply_text(
            "❌ <b>ERRO AO ADICIONAR LINKS</b>\n"
            "\n"
            f"{erro}",
            parse_mode=ParseMode.HTML,
        )


# ============================================================
# COMANDO /ADICIONAR
# ============================================================

async def comando_adicionar(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not usuario_autorizado(update):
        return

    if not update.message:
        return

    texto = " ".join(
        context.args
    )

    if not texto:

        await update.message.reply_text(
            "📥 Envie o comando assim:\n\n"
            "<code>/adicionar https://s.shopee.com.br/...</code>",
            parse_mode=ParseMode.HTML,
        )

        return

    links = extrair_links(
        texto
    )

    if not links:

        await update.message.reply_text(
            "⚠️ Nenhum link válido da Shopee encontrado."
        )

        return

    try:

        adicionados, duplicados, erros = (
            await asyncio.to_thread(
                inserir_links,
                links,
            )
        )

        mensagem = (
            "🦊 <b>LINKS ADICIONADOS</b>\n"
            "\n"
            f"📥 Recebidos: <b>{len(links)}</b>\n"
            f"⏳ Na fila: <b>{adicionados}</b>\n"
            f"♻️ Duplicados: <b>{duplicados}</b>\n"
            f"❌ Erros: <b>{len(erros)}</b>\n"
        )

        if adicionados:

            mensagem += (
                "\n✅ Os produtos estão aguardando "
                "publicação automática."
            )

        await update.message.reply_text(
            mensagem,
            parse_mode=ParseMode.HTML,
        )

    except Exception as erro:

        logger.exception(
            "Erro no comando /adicionar."
        )

        await update.message.reply_text(
            f"❌ Erro ao adicionar links:\n{erro}"
        )


# ============================================================
# COMANDO /AJUDA
# ============================================================

async def comando_ajuda(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not usuario_autorizado(update):
        return

    mensagem = (
        "🦊 <b>RAPOSA CAÇADORA</b>\n"
        "\n"
        "📋 <b>COMANDOS</b>\n"
        "\n"
        "📊 /status — mostra o status da fila\n"
        "📋 /fila — mostra os produtos\n"
        "⚠️ /erros — mostra os produtos com erro\n"
        "🔄 /retry — coloca os erros novamente na fila\n"
        "📥 /adicionar — adiciona um link manualmente\n"
        "❓ /ajuda — mostra esta mensagem\n"
        "\n"
        "💡 Você também pode simplesmente enviar "
        "20 ou mais links em uma única mensagem."
    )

    await update.message.reply_text(
        mensagem,
        parse_mode=ParseMode.HTML,
    )


# ============================================================
# TRABALHADOR DA FILA
# ============================================================

async def trabalhador_fila(
    bot: Bot,
):

    logger.info(
        "🦊 Trabalhador da fila iniciado."
    )

    while True:

        try:

            # ------------------------------------------------
            # Recuperar produtos que ficaram presos em
            # processing após reinício do Render.
            # ------------------------------------------------

            await asyncio.to_thread(
                recuperar_processamentos_presos
            )

            # ------------------------------------------------
            # Buscar próximo produto.
            # ------------------------------------------------

            produto = await asyncio.to_thread(
                buscar_proximo_produto
            )

            if not produto:

                logger.info(
                    "Nenhum produto pendente. "
                    "Aguardando novos links..."
                )

                await asyncio.sleep(
                    30
                )

                continue

            # ------------------------------------------------
            # Processar
            # ------------------------------------------------

            sucesso = await processar_produto(
                bot=bot,
                produto_fila=produto,
            )

            # ------------------------------------------------
            # Sucesso
            # ------------------------------------------------

            if sucesso:

                logger.info(
                    "Produto publicado. "
                    "Próxima publicação em %d minutos.",
                    INTERVALO_MINUTOS,
                )

                await enviar_notificacao_admin(
                    bot,
                    (
                        "✅ <b>PRODUTO PUBLICADO</b>\n"
                        "\n"
                        f"📦 ID da fila: <b>{produto['id']}</b>\n"
                        "\n"
                        f"⏱️ Próximo produto em "
                        f"<b>{INTERVALO_MINUTOS} minutos</b>."
                    ),
                )

                # ------------------------------------------------
                # Aguardar somente depois de publicar com sucesso.
                # ------------------------------------------------

                await asyncio.sleep(
                    INTERVALO_MINUTOS * 60
                )

            # ------------------------------------------------
            # Erro
            # ------------------------------------------------

            else:

                logger.warning(
                    "Produto %s apresentou erro.",
                    produto["id"],
                )

                await enviar_notificacao_admin(
                    bot,
                    (
                        "❌ <b>ERRO AO PUBLICAR PRODUTO</b>\n"
                        "\n"
                        f"📦 ID da fila: <b>{produto['id']}</b>\n"
                        f"🔗 {produto['link']}\n"
                        "\n"
                        "⚠️ O produto foi marcado como erro.\n"
                        "Use /erros para consultar.\n"
                        "Use /retry para tentar novamente."
                    ),
                )

                # ------------------------------------------------
                # Pequena pausa para evitar loop rápido de erros.
                # ------------------------------------------------

                await asyncio.sleep(
                    30
                )

        except asyncio.CancelledError:

            logger.info(
                "Trabalhador da fila encerrado."
            )

            raise

        except Exception as erro:

            logger.exception(
                "Erro inesperado no trabalhador da fila."
            )

            try:

                await enviar_notificacao_admin(
                    bot,
                    (
                        "🚨 <b>ERRO NO TRABALHADOR</b>\n"
                        "\n"
                        f"{str(erro)[:2000]}\n"
                        "\n"
                        "🔄 O sistema continuará tentando."
                    ),
                )

            except Exception:
                pass

            await asyncio.sleep(
                30
            )


# ============================================================
# POST INIT DO TELEGRAM
# ============================================================

async def post_init(
    application: Application,
):

    logger.info(
        "Telegram Application iniciado."
    )

    # --------------------------------------------------------
    # Criar tarefa permanente da fila.
    # --------------------------------------------------------

    bot = application.bot

    application.bot_data[
        "worker_task"
    ] = asyncio.create_task(
        trabalhador_fila(
            bot
        )
    )

    logger.info(
        "Trabalhador da fila iniciado pelo Telegram."
    )


# ============================================================
# POST SHUTDOWN
# ============================================================

async def post_shutdown(
    application: Application,
):

    tarefa = application.bot_data.get(
        "worker_task"
    )

    if tarefa:

        logger.info(
            "Encerrando trabalhador da fila..."
        )

        tarefa.cancel()

        try:

            await tarefa

        except asyncio.CancelledError:

            pass


# ============================================================
# CONSTRUIR APPLICATION
# ============================================================

def criar_application():

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
            "status",
            comando_status,
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
            "adicionar",
            comando_adicionar,
        )
    )

    application.add_handler(
        CommandHandler(
            "ajuda",
            comando_ajuda,
        )
    )

    # --------------------------------------------------------
    # LINKS ENVIADOS DIRETAMENTE
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            receber_links,
        )
    )

    return application


# ============================================================
# MAIN
# ============================================================

def main():

    logger.info(
        "=========================================="
    )

    logger.info(
        "🦊 RAPOSA CAÇADORA iniciando..."
    )

    # --------------------------------------------------------
    # Validar configuração
    # --------------------------------------------------------

    validar_configuracao()

    # --------------------------------------------------------
    # Supabase
    # --------------------------------------------------------

    iniciar_supabase()

    # --------------------------------------------------------
    # Recuperar produtos que eventualmente ficaram presos
    # antes de iniciar o bot.
    # --------------------------------------------------------

    try:

        recuperar_processamentos_presos()

    except Exception as erro:

        logger.warning(
            "Não foi possível recuperar "
            "processamentos presos: %s",
            erro,
        )

    # --------------------------------------------------------
    # Servidor HTTP para Render
    # --------------------------------------------------------

    servidor_thread = threading.Thread(
        target=iniciar_servidor_http,
        daemon=True,
    )

    servidor_thread.start()

    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    application = criar_application()

    logger.info(
        "=========================================="
    )

    logger.info(
        "Bot Telegram iniciando..."
    )

    # --------------------------------------------------------
    # Run polling
    # --------------------------------------------------------

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
