import os
import re
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

# ============================================================
# CONFIGURAÇÃO
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não configurado.")

if not CHANNEL_ID:
    raise RuntimeError("CHANNEL_ID não configurado.")

DB_FILE = "raposa.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("raposa-cacadora")


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
            title TEXT,
            old_price TEXT,
            price TEXT,
            image_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


def product_exists(url):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id FROM products WHERE url = ?",
        (url,)
    )

    result = cursor.fetchone()

    conn.close()

    return result is not None


def save_product(product):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT OR IGNORE INTO products
        (url, title, old_price, price, image_url)
        VALUES (?, ?, ?, ?, ?)
    """, (
        product["url"],
        product["title"],
        product["old_price"],
        product["price"],
        product["image_url"],
    ))

    conn.commit()
    conn.close()


# ============================================================
# UTILIDADES
# ============================================================

def is_shopee_url(text):
    try:
        parsed = urlparse(text)

        domain = parsed.netloc.lower()

        return (
            "shopee.com.br" in domain
            or "shopee.com" in domain
        )

    except Exception:
        return False


def extract_url(text):
    match = re.search(
        r"https?://[^\s]+",
        text
    )

    if not match:
        return None

    return match.group(0).rstrip(".,)")


# ============================================================
# PRODUTO
# ============================================================

async def get_product_from_shopee(url):
    """
    V1:

    Aqui ficará o módulo responsável por obter os dados reais
    do produto da Shopee.

    Por enquanto usamos dados de demonstração para testar
    TODO o fluxo do Telegram.

    Depois substituímos somente esta função pela integração
    escolhida para obter os dados da Shopee.
    """

    return {
        "url": url,
        "title": "Conjunto Alfaiataria Calça e Colete Feminino Cintura Alta Elegante Social",
        "old_price": "R$ 89,99",
        "price": "R$ 85,50",
        "image_url": None,
    }


# ============================================================
# MENSAGEM
# ============================================================

def create_caption(product):

    return f"""🛍️ <b>{product['title']}</b>

❌ De: <s>{product['old_price']}</s>
✅ <b>Por: {product['price']} no Pix</b>

🔥 <b>ACHADINHO ENCONTRADO!</b>

🦊 <b>Raposa Caçadora</b>"""


def create_buy_button(url):

    keyboard = [
        [
            InlineKeyboardButton(
                "🛒 COMPRAR NA SHOPEE",
                url=url
            )
        ]
    ]

    return InlineKeyboardMarkup(keyboard)


def create_confirmation_buttons():

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

    return InlineKeyboardMarkup(keyboard)


# ============================================================
# /start
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    message = """🦊 <b>Raposa Caçadora</b>

Olá! 👋

Envie um link da Shopee e eu preparo o achadinho para você.

🔗 <b>Exemplo:</b>

https://s.shopee.com.br/SEU-LINK

Depois eu mostro uma prévia e você decide se quer publicar no canal."""

    await update.message.reply_text(
        message,
        parse_mode="HTML"
    )


# ============================================================
# /help
# ============================================================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    message = """🦊 <b>Raposa Caçadora</b>

<b>Comandos:</b>

/start - iniciar o bot
/help - ajuda

<b>Como usar:</b>

Basta enviar um link da Shopee.

A Raposa Caçadora irá preparar a publicação para você."""

    await update.message.reply_text(
        message,
        parse_mode="HTML"
    )


# ============================================================
# RECEBER LINK
# ============================================================

async def receive_link(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message:
        return

    text = update.message.text or ""

    url = extract_url(text)

    if not url:
        await update.message.reply_text(
            "🦊 Não encontrei nenhum link na mensagem.\n\n"
            "Envie um link da Shopee."
        )
        return

    if not is_shopee_url(url):
        await update.message.reply_text(
            "⚠️ Esse link não parece ser da Shopee.\n\n"
            "Envie um link da Shopee."
        )
        return

    if product_exists(url):

        await update.message.reply_text(
            "⚠️ Esse produto já foi processado anteriormente."
        )

        return

    await update.message.reply_text(
        "🦊 <b>Caçando o produto...</b> 🔎",
        parse_mode="HTML"
    )

    try:

        product = await get_product_from_shopee(url)

        context.user_data["pending_product"] = product

        caption = create_caption(product)

        keyboard = create_confirmation_buttons()

        if product.get("image_url"):

            await update.message.reply_photo(
                photo=product["image_url"],
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard
            )

        else:

            await update.message.reply_text(
                caption,
                parse_mode="HTML",
                reply_markup=keyboard
            )

    except Exception as e:

        logger.exception(e)

        await update.message.reply_text(
            "❌ Não consegui processar esse produto.\n\n"
            "Tente novamente."
        )


# ============================================================
# BOTÕES
# ============================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    product = context.user_data.get("pending_product")

    if not product:

        await query.edit_message_text(
            "⚠️ Esse anúncio expirou.\n\n"
            "Envie o link novamente."
        )

        return

    # --------------------------------------------------------
    # CANCELAR
    # --------------------------------------------------------

    if query.data == "cancel":

        context.user_data.pop(
            "pending_product",
            None
        )

        await query.edit_message_text(
            "❌ Publicação cancelada."
        )

        return

    # --------------------------------------------------------
    # PUBLICAR
    # --------------------------------------------------------

    if query.data == "publish":

        try:

            caption = create_caption(product)

            keyboard = create_buy_button(
                product["url"]
            )

            if product.get("image_url"):

                await context.bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=product["image_url"],
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=keyboard
                )

            else:

                await context.bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=caption,
                    parse_mode="HTML",
                    reply_markup=keyboard
                )

            save_product(product)

            context.user_data.pop(
                "pending_product",
                None
            )

            await query.edit_message_text(
                "✅ <b>Publicado com sucesso!</b>\n\n"
                "🦊 A Raposa Caçadora encontrou mais um achadinho.",
                parse_mode="HTML"
            )

        except Exception as e:

            logger.exception(e)

            await query.edit_message_text(
                "❌ Não consegui publicar no canal.\n\n"
                "Verifique se o bot é administrador do canal "
                "e se o CHANNEL_ID está correto."
            )


# ============================================================
# ERROS
# ============================================================

async def error_handler(update, context):

    logger.error(
        "Erro no bot:",
        exc_info=context.error
    )


# ============================================================
# MAIN
# ============================================================

def main():

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            receive_link
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "🦊 Raposa Caçadora iniciada!"
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
