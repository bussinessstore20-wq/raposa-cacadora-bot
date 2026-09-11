import os
import re
import html
import sqlite3
import logging
from urllib.parse import urlparse

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from shopee import (
    get_product_from_shopee,
    ShopeeAPIError,
)


# ============================================================
# CONFIGURAÇÃO
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

# Opcional:
# coloque os IDs dos usuários autorizados separados por vírgula.
#
# Exemplo:
# ADMIN_IDS=123456789,987654321
#
# Se deixar vazio, qualquer pessoa que encontrar o bot poderá
# tentar usar.
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "")


if not BOT_TOKEN:
    raise RuntimeError(
        "ERRO: BOT_TOKEN não foi configurado."
    )


if not CHANNEL_ID:
    raise RuntimeError(
        "ERRO: CHANNEL_ID não foi configurado."
    )


# Converte:
# "123,456,789"
#
# para:
# {123, 456, 789}
def load_admin_ids():

    if not ADMIN_IDS_RAW.strip():
        return set()

    ids = set()

    for value in ADMIN_IDS_RAW.split(","):

        value = value.strip()

        if not value:
            continue

        try:
            ids.add(int(value))
        except ValueError:
            logging.warning(
                "ADMIN_IDS contém valor inválido: %s",
                value
            )

    return ids


ADMIN_IDS = load_admin_ids()


# Banco SQLite
DB_FILE = "raposa.db"


# ============================================================
# LOG
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(name)s | "
        "%(message)s"
    ),
)

logger = logging.getLogger(
    "raposa-cacadora"
)


# ============================================================
# BANCO DE DADOS
# ============================================================

def init_db():

    conn = sqlite3.connect(DB_FILE)

    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            url TEXT UNIQUE NOT NULL,

            original_url TEXT,

            title TEXT,

            old_price TEXT,

            price TEXT,

            image_url TEXT,

            affiliate_link TEXT,

            created_at TIMESTAMP
                DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()

    conn.close()


def product_exists(url):

    conn = sqlite3.connect(DB_FILE)

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id
        FROM products
        WHERE url = ?
        """,
        (url,)
    )

    result = cursor.fetchone()

    conn.close()

    return result is not None


def save_product(product):

    conn = sqlite3.connect(DB_FILE)

    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT OR IGNORE INTO products
        (
            url,
            original_url,
            title,
            old_price,
            price,
            image_url,
            affiliate_link
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            product.get("url"),
            product.get("original_url"),
            product.get("title"),
            product.get("old_price"),
            product.get("price"),
            product.get("image_url"),
            product.get("affiliate_link"),
        )
    )

    conn.commit()

    conn.close()


# ============================================================
# AUTORIZAÇÃO
# ============================================================

def user_is_authorized(update: Update):

    # Se ADMIN_IDS não foi configurado,
    # não bloqueia ninguém.
    if not ADMIN_IDS:
        return True

    user = update.effective_user

    if not user:
        return False

    return user.id in ADMIN_IDS


async def authorization_error(update: Update):

    message = (
        "🔒 <b>Acesso restrito.</b>\n\n"
        "Você não possui autorização para "
        "usar a Raposa Caçadora."
    )

    if update.callback_query:

        await update.callback_query.answer(
            "Acesso negado.",
            show_alert=True
        )

    elif update.message:

        await update.message.reply_text(
            message,
            parse_mode="HTML"
        )


# ============================================================
# URL
# ============================================================

def extract_url(text):

    if not text:
        return None

    match = re.search(
        r"https?://[^\s<>]+",
        text
    )

    if not match:
        return None

    url = match.group(0)

    # Remove pontuação que possa ter vindo
    # junto com o link.
    url = url.rstrip(
        ".,;:!?)]}>\"'"
    )

    return url


def is_shopee_url(url):

    try:

        parsed = urlparse(url)

        domain = parsed.netloc.lower()

        # Aceita:
        #
        # shopee.com.br
        # www.shopee.com.br
        # s.shopee.com.br
        #
        # e outros subdomínios da Shopee.

        return (
            domain == "shopee.com.br"
            or domain.endswith(".shopee.com.br")
            or domain == "shopee.com"
            or domain.endswith(".shopee.com")
        )

    except Exception:

        return False


# ============================================================
# FORMATAÇÃO
# ============================================================

def clean_text(value):

    if value is None:
        return ""

    return str(value).strip()


def safe_html(value):

    return html.escape(
        clean_text(value)
    )


def create_caption(product):

    title = safe_html(
        product.get(
            "title",
            "Produto Shopee"
        )
    )

    old_price = safe_html(
        product.get(
            "old_price",
            ""
        )
    )

    price = safe_html(
        product.get(
            "price",
            ""
        )
    )

    # Monta o preço antigo somente
    # se ele existir.
    old_price_line = ""

    if old_price:

        old_price_line = (
            f"❌ De: <s>{old_price}</s>\n"
        )

    # Se houver preço atual.
    price_line = ""

    if price:

        price_line = (
            f"✅ <b>Por: {price} no Pix</b>\n"
        )

    caption = (
        f"🛍️ <b>{title}</b>\n\n"
        f"{old_price_line}"
        f"{price_line}\n"
        f"🔥 <b>ACHADINHO ENCONTRADO!</b>\n\n"
        f"🦊 <b>Raposa Caçadora</b>"
    )

    return caption


# ============================================================
# BOTÕES
# ============================================================

def create_confirmation_keyboard():

    keyboard = [

        [
            InlineKeyboardButton(
                "✅ PUBLICAR",
                callback_data="publish"
            ),

            InlineKeyboardButton(
                "❌ CANCELAR",
                callback_data="cancel"
            ),
        ]

    ]

    return InlineKeyboardMarkup(
        keyboard
    )


def create_buy_keyboard(url):

    keyboard = [

        [
            InlineKeyboardButton(
                "🛒 COMPRAR NA SHOPEE",
                url=url
            )
        ]

    ]

    return InlineKeyboardMarkup(
        keyboard
    )


# ============================================================
# /START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not user_is_authorized(update):

        await authorization_error(update)

        return

    message = """
🦊 <b>RAPOSA CAÇADORA</b>

Olá! 👋

Eu sou seu bot de achadinhos da Shopee.

🔗 <b>Como usar:</b>

Basta enviar um link da Shopee aqui.

Exemplo:

<code>https://s.shopee.com.br/SEU-LINK</code>

Eu vou:

1️⃣ Encontrar o produto
2️⃣ Pegar os dados
3️⃣ Montar o anúncio
4️⃣ Mostrar uma prévia
5️⃣ Você decide se publica

🛒 Depois é só clicar em <b>PUBLICAR</b>.
"""

    await update.message.reply_text(
        message,
        parse_mode="HTML"
    )


# ============================================================
# /HELP
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not user_is_authorized(update):

        await authorization_error(update)

        return

    message = """
🦊 <b>RAPOSA CAÇADORA</b>

<b>Comandos disponíveis:</b>

/start - iniciar o bot
/help - mostrar ajuda

<b>Uso:</b>

Envie um link da Shopee.

Exemplo:

<code>https://s.shopee.com.br/AAH3wuxvT6</code>

A Raposa irá preparar a oferta.
"""

    await update.message.reply_text(
        message,
        parse_mode="HTML"
    )


# ============================================================
# RECEBER LINK
# ============================================================

async def receive_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not user_is_authorized(update):

        await authorization_error(update)

        return

    if not update.message:

        return

    text = update.message.text or ""

    url = extract_url(text)

    if not url:

        await update.message.reply_text(
            "🦊 <b>Não encontrei um link.</b>\n\n"
            "Envie um link da Shopee.",
            parse_mode="HTML"
        )

        return

    if not is_shopee_url(url):

        await update.message.reply_text(
            "⚠️ <b>Esse não parece ser um link da Shopee.</b>\n\n"
            "Envie um link da Shopee.",
            parse_mode="HTML"
        )

        return

    # Verifica se já foi publicado.
    if product_exists(url):

        await update.message.reply_text(
            "⚠️ <b>Esse produto já foi publicado.</b>\n\n"
            "Não vou publicar o mesmo produto novamente.",
            parse_mode="HTML"
        )

        return

    # Mensagem de processamento.
    processing_message = await update.message.reply_text(
        "🦊 <b>A Raposa está caçando...</b> 🔎\n\n"
        "Aguarde um momento.",
        parse_mode="HTML"
    )

    try:

        # Consulta o módulo da Shopee.
        product = await get_product_from_shopee(
            url
        )

        if not product:

            raise Exception(
                "A API não retornou dados do produto."
            )

        # Garante que a URL original
        # fique registrada.
        product["original_url"] = url

        # Se o módulo não colocou "url",
        # usamos o link original.
        if not product.get("url"):

            product["url"] = (
                product.get("affiliate_link")
                or url
            )

        # Guarda temporariamente a oferta
        # para o botão PUBLICAR.
        context.user_data[
            "pending_product"
        ] = product

        caption = create_caption(
            product
        )

        keyboard = (
            create_confirmation_keyboard()
        )

        # Apaga a mensagem "caçando".
        try:

            await processing_message.delete()

        except Exception:

            pass

        image_url = product.get(
            "image_url"
        )

        # ----------------------------------------------------
        # COM IMAGEM
        # ----------------------------------------------------

        if image_url:

            try:

                await update.message.reply_photo(
                    photo=image_url,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=keyboard
                )

                return

            except Exception as image_error:

                logger.warning(
                    "Não foi possível enviar a imagem: %s",
                    image_error
                )

        # ----------------------------------------------------
        # SEM IMAGEM
        # ----------------------------------------------------

        await update.message.reply_text(
            caption,
            parse_mode="HTML",
            reply_markup=keyboard
        )

    except ShopeeAPIError as error:

        logger.exception(
            "Erro da API Shopee."
        )

        try:

            await processing_message.edit_text(
                "❌ <b>Erro na API da Shopee.</b>\n\n"
                f"<code>{safe_html(error)}</code>",
                parse_mode="HTML"
            )

        except Exception:

            await update.message.reply_text(
                "❌ Ocorreu um erro ao consultar "
                "a API da Shopee."
            )

    except Exception as error:

        logger.exception(
            "Erro processando produto."
        )

        try:

            await processing_message.edit_text(
                "❌ <b>Não consegui processar o produto.</b>\n\n"
                "Verifique o link e tente novamente.",
                parse_mode="HTML"
            )

        except Exception:

            await update.message.reply_text(
                "❌ Não consegui processar o produto."
            )


# ============================================================
# BOTÕES
# ============================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if not query:

        return

    # Verificação de segurança.
    if not user_is_authorized(update):

        await authorization_error(update)

        return

    await query.answer()

    product = context.user_data.get(
        "pending_product"
    )

    if not product:

        await query.edit_message_text(
            "⚠️ <b>Essa publicação expirou.</b>\n\n"
            "Envie o link novamente.",
            parse_mode="HTML"
        )

        return

    # ========================================================
    # CANCELAR
    # ========================================================

    if query.data == "cancel":

        context.user_data.pop(
            "pending_product",
            None
        )

        await query.edit_message_text(
            "❌ <b>Publicação cancelada.</b>\n\n"
            "🦊 A Raposa guardou esse achado.",
            parse_mode="HTML"
        )

        return

    # ========================================================
    # PUBLICAR
    # ========================================================

    if query.data == "publish":

        try:

            caption = create_caption(
                product
            )

            affiliate_link = (
                product.get("affiliate_link")
                or product.get("url")
                or product.get("original_url")
            )

            if not affiliate_link:

                raise Exception(
                    "Produto não possui link de compra."
                )

            keyboard = create_buy_keyboard(
                affiliate_link
            )

            image_url = product.get(
                "image_url"
            )

            # ------------------------------------------------
            # PUBLICAR COM IMAGEM
            # ------------------------------------------------

            if image_url:

                try:

                    await context.bot.send_photo(
                        chat_id=CHANNEL_ID,
                        photo=image_url,
                        caption=caption,
                        parse_mode="HTML",
                        reply_markup=keyboard
                    )

                except Exception as image_error:

                    logger.warning(
                        "Falha ao publicar imagem: %s",
                        image_error
                    )

                    # Se a imagem falhar,
                    # publica somente o texto.
                    await context.bot.send_message(
                        chat_id=CHANNEL_ID,
                        text=caption,
                        parse_mode="HTML",
                        reply_markup=keyboard
                    )

            # ------------------------------------------------
            # PUBLICAR SOMENTE TEXTO
            # ------------------------------------------------

            else:

                await context.bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=caption,
                    parse_mode="HTML",
                    reply_markup=keyboard
                )

            # Salva depois de publicar.
            save_product(
                product
            )

            # Limpa a oferta temporária.
            context.user_data.pop(
                "pending_product",
                None
            )

            # Atualiza a mensagem de confirmação.
            try:

                await query.edit_message_text(
                    "✅ <b>PUBLICADO COM SUCESSO!</b>\n\n"
                    "🦊 A Raposa Caçadora encontrou "
                    "mais um achadinho.\n\n"
                    "📢 O produto já foi enviado "
                    "para o canal.",
                    parse_mode="HTML"
                )

            except Exception:

                pass

        except Exception as error:

            logger.exception(
                "Erro ao publicar no canal."
            )

            error_text = safe_html(
                str(error)
            )

            try:

                await query.edit_message_text(
                    "❌ <b>Não consegui publicar.</b>\n\n"
                    "Verifique se:\n\n"
                    "• O bot é administrador do canal\n"
                    "• O CHANNEL_ID está correto\n"
                    "• O bot possui permissão para publicar\n\n"
                    f"<code>{error_text}</code>",
                    parse_mode="HTML"
                )

            except Exception:

                pass


# ============================================================
# CANCELAR COMANDO
# ============================================================

async def cancel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not user_is_authorized(update):

        await authorization_error(update)

        return

    context.user_data.pop(
        "pending_product",
        None
    )

    await update.message.reply_text(
        "❌ Publicação cancelada."
    )


# ============================================================
# ERROS
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.error(
        "Erro não tratado:",
        exc_info=context.error
    )


# ============================================================
# STARTUP
# ============================================================

def main():

    logger.info(
        "Inicializando banco de dados..."
    )

    init_db()

    logger.info(
        "Iniciando Raposa Caçadora..."
    )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # --------------------------------------------------------
    # COMANDOS
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CommandHandler(
            "help",
            help_command
        )
    )

    application.add_handler(
        CommandHandler(
            "cancel",
            cancel_command
        )
    )

    # --------------------------------------------------------
    # LINKS / TEXTOS
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            receive_link
        )
    )

    # --------------------------------------------------------
    # BOTÕES
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    # --------------------------------------------------------
    # ERROS
    # --------------------------------------------------------

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "🦊 Raposa Caçadora online!"
    )

    # Polling é adequado para rodar como
    # Background Worker no Render.
    application.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# EXECUÇÃO
# ============================================================

if __name__ == "__main__":

    main()
