import asyncio
import logging
import os
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from supabase import create_client, Client

from telegram import (
    Update,
    Bot,
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


# ============================================================
# INTERVALO
# ============================================================

INTERVALO_MINUTOS = int(
    os.getenv(
        "INTERVALO_MINUTOS",
        "10",
    )
)


# ============================================================
# PORTA DO RENDER
# ============================================================

PORT = int(
    os.getenv(
        "PORT",
        "10000",
    )
)


# ============================================================
# CONFIGURAÇÃO DA FILA
# ============================================================

NOME_TABELA = "produtos_fila"

# Se um produto ficar processing por mais que esse tempo,
# consideramos que o processo morreu e devolvemos para pending.
TIMEOUT_PROCESSING_MINUTOS = 30


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
            "SUPABASE_URL não configurado."
        )

    if not SUPABASE_KEY:
        raise RuntimeError(
            "SUPABASE_KEY não configurado."
        )

    supabase = create_client(
        SUPABASE_URL,
        SUPABASE_KEY,
    )

    logger.info(
        "Supabase conectado."
    )


def obter_supabase() -> Client:
    if supabase is None:
        raise RuntimeError(
            "Supabase ainda não foi inicializado."
        )

    return supabase


# ============================================================
# SERVIDOR HTTP PARA O RENDER
# ============================================================

class HealthHandler(
    BaseHTTPRequestHandler
):

    def do_GET(
        self,
    ):
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
            "SUPABASE_URL não configurado."
        )

    if not SUPABASE_KEY:
        erros.append(
            "SUPABASE_KEY não configurado."
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
            "TELEGRAM_ADMIN_ID precisa ser numérico."
        )

    logger.info(
        "Configuração validada."
    )

    logger.info(
        "Intervalo: %d minutos",
        INTERVALO_MINUTOS,
    )


# ============================================================
# ADMIN
# ============================================================

def usuario_e_admin(
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


async def verificar_admin(
    update: Update,
) -> bool:

    if usuario_e_admin(update):
        return True

    if update.effective_message:

        await update.effective_message.reply_text(
            "⛔ Você não tem permissão para "
            "administrar a Raposa Caçadora."
        )

    return False


# ============================================================
# EXTRAIR LINKS
# ============================================================

PADRAO_URL = re.compile(
    r"https?://[^\s<>\"]+",
    re.IGNORECASE,
)


def extrair_links(
    texto: str,
) -> list[str]:

    encontrados = PADRAO_URL.findall(
        texto or ""
    )

    links = []

    for link in encontrados:

        link = link.strip()

        # Remove pontuação comum que pode vir no final.
        link = link.rstrip(
            ".,;:!?)]}>\"'"
        )

        if not link:
            continue

        link_lower = link.lower()

        if (
            "shopee.com.br" not in link_lower
            and "s.shopee.com.br" not in link_lower
        ):
            continue

        if link not in links:
            links.append(link)

    return links


# ============================================================
# SUPABASE - FUNÇÕES DE BANCO
# ============================================================

def buscar_links_existentes(
    links: list[str],
) -> set[str]:

    if not links:
        return set()

    cliente = obter_supabase()

    resposta = (
        cliente
        .table(NOME_TABELA)
        .select("link")
        .in_("link", links)
        .execute()
    )

    registros = resposta.data or []

    return {
        str(item["link"])
        for item in registros
        if item.get("link")
    }


def inserir_links(
    links: list[str],
) -> tuple[int, int]:

    if not links:
        return 0, 0

    existentes = buscar_links_existentes(
        links
    )

    novos = [
        link
        for link in links
        if link not in existentes
    ]

    if not novos:
        return 0, len(existentes)

    registros = [
        {
            "link": link,
            "status": "pending",
            "tentativas": 0,
        }
        for link in novos
    ]

    cliente = obter_supabase()

    try:

        (
            cliente
            .table(NOME_TABELA)
            .insert(registros)
            .execute()
        )

    except Exception as erro:

        logger.exception(
            "Erro ao inserir links no Supabase: %s",
            erro,
        )

        # Pode ter ocorrido uma corrida de inserção.
        # Verificamos novamente os existentes.
        existentes_depois = buscar_links_existentes(
            links
        )

        novos_reais = [
            link
            for link in links
            if link in existentes_depois
        ]

        if len(novos_reais) == len(links):

            return 0, len(existentes_depois)

        raise

    return len(novos), len(existentes)


def recuperar_processamentos_presos():

    cliente = obter_supabase()

    agora = datetime.now(
        timezone.utc
    )

    limite = agora.timestamp() - (
        TIMEOUT_PROCESSING_MINUTOS * 60
    )

    limite_dt = datetime.fromtimestamp(
        limite,
        tz=timezone.utc,
    ).isoformat()

    try:

        (
            cliente
            .table(NOME_TABELA)
            .update(
                {
                    "status": "pending",
                    "processing_at": None,
                }
            )
            .eq(
                "status",
                "processing",
            )
            .lt(
                "processing_at",
                limite_dt,
            )
            .execute()
        )

        logger.info(
            "Verificação de produtos presos concluída."
        )

    except Exception as erro:

        logger.exception(
            "Erro ao recuperar produtos presos: %s",
            erro,
        )


def obter_proximo_produto():

    cliente = obter_supabase()

    resposta = (
        cliente
        .table(NOME_TABELA)
        .select("*")
        .eq(
            "status",
            "pending",
        )
        .order(
            "created_at",
            desc=False,
        )
        .order(
            "id",
            desc=False,
        )
        .limit(1)
        .execute()
    )

    registros = resposta.data or []

    if not registros:
        return None

    return registros[0]


def marcar_processing(
    produto_id: int,
) -> bool:

    cliente = obter_supabase()

    agora = datetime.now(
        timezone.utc
    ).isoformat()

    resposta = (
        cliente
        .table(NOME_TABELA)
        .update(
            {
                "status": "processing",
                "processing_at": agora,
            }
        )
        .eq(
            "id",
            produto_id,
        )
        .eq(
            "status",
            "pending",
        )
        .execute()
    )

    registros = resposta.data or []

    return bool(registros)


def marcar_publicado(
    produto_id: int,
    produto: dict[str, Any],
    telegram_message_id: int | None = None,
):

    cliente = obter_supabase()

    agora = datetime.now(
        timezone.utc
    ).isoformat()

    dados = {
        "status": "published",
        "processing_at": None,
        "published_at": agora,
        "erro": None,
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
        "image_url": (
            produto.get("imageUrl")
            or None
        ),
    }

    if telegram_message_id is not None:
        dados[
            "telegram_message_id"
        ] = telegram_message_id

    (
        cliente
        .table(NOME_TABELA)
        .update(dados)
        .eq(
            "id",
            produto_id,
        )
        .execute()
    )

    logger.info(
        "Produto %d marcado como published.",
        produto_id,
    )


def marcar_erro(
    produto_id: int,
    erro: str,
):

    cliente = obter_supabase()

    try:

        (
            cliente
            .table(NOME_TABELA)
            .update(
                {
                    "status": "error",
                    "processing_at": None,
                    "erro": erro[:2000],
                }
            )
            .eq(
                "id",
                produto_id,
            )
            .execute()
        )

    except Exception:

        logger.exception(
            "Erro ao salvar status de erro "
            "do produto %d.",
            produto_id,
        )


def incrementar_tentativas(
    produto_id: int,
):

    cliente = obter_supabase()

    resposta = (
        cliente
        .table(NOME_TABELA)
        .select("tentativas")
        .eq(
            "id",
            produto_id,
        )
        .limit(1)
        .execute()
    )

    registros = resposta.data or []

    if not registros:
        return

    atual = int(
        registros[0].get(
            "tentativas",
            0,
        )
        or 0
    )

    (
        cliente
        .table(NOME_TABELA)
        .update(
            {
                "tentativas": atual + 1,
            }
        )
        .eq(
            "id",
            produto_id,
        )
        .execute()
    )


def obter_ultimo_publicado():

    cliente = obter_supabase()

    resposta = (
        cliente
        .table(NOME_TABELA)
        .select(
            "published_at"
        )
        .eq(
            "status",
            "published",
        )
        .not_.is_(
            "published_at",
            "null",
        )
        .order(
            "published_at",
            desc=True,
        )
        .limit(1)
        .execute()
    )

    registros = resposta.data or []

    if not registros:
        return None

    return registros[0].get(
        "published_at"
    )


def obter_contagem_status():

    cliente = obter_supabase()

    resultado = {}

    for status in (
        "pending",
        "processing",
        "published",
        "error",
    ):

        resposta = (
            cliente
            .table(NOME_TABELA)
            .select(
                "id",
                count="exact",
            )
            .eq(
                "status",
                status,
            )
            .execute()
        )

        resultado[status] = (
            resposta.count or 0
        )

    resultado["total"] = sum(
        resultado.values()
    )

    return resultado
