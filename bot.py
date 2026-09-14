import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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


# ============================================================
# INTERVALO
# ============================================================

INTERVALO_MINUTOS = int(
    os.getenv(
        "INTERVALO_MINUTOS",
        "10"
    )
)


# ============================================================
# LINKS MANUAIS
# ============================================================
#
# COLE SEUS LINKS AQUI.
#
# A ordem determina a ordem das publicações.
#
# ATÉ 20 LINKS.
#

LINKS_PRODUTOS = [

    "https://s.shopee.com.br/1qbmEw9Aek",

    # "https://s.shopee.com.br/SEU_LINK_02",
    # "https://s.shopee.com.br/SEU_LINK_03",
    # "https://s.shopee.com.br/SEU_LINK_04",
    # "https://s.shopee.com.br/SEU_LINK_05",
    # "https://s.shopee.com.br/SEU_LINK_06",
    # "https://s.shopee.com.br/SEU_LINK_07",
    # "https://s.shopee.com.br/SEU_LINK_08",
    # "https://s.shopee.com.br/SEU_LINK_09",
    # "https://s.shopee.com.br/SEU_LINK_10",
    # "https://s.shopee.com.br/SEU_LINK_11",
    # "https://s.shopee.com.br/SEU_LINK_12",
    # "https://s.shopee.com.br/SEU_LINK_13",
    # "https://s.shopee.com.br/SEU_LINK_14",
    # "https://s.shopee.com.br/SEU_LINK_15",
    # "https://s.shopee.com.br/SEU_LINK_16",
    # "https://s.shopee.com.br/SEU_LINK_17",
    # "https://s.shopee.com.br/SEU_LINK_18",
    # "https://s.shopee.com.br/SEU_LINK_19",
    # "https://s.shopee.com.br/SEU_LINK_20",
]


# ============================================================
# ESTADO
# ============================================================

ARQUIVO_ESTADO = Path(
    "fila_produtos.json"
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
    )
)

logger = logging.getLogger(
    "raposa-cacadora"
)


# ============================================================
# VALIDAÇÃO
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

    if INTERVALO_MINUTOS < 1:

        erros.append(
            "INTERVALO_MINUTOS deve ser maior que 0."
        )

    if len(LINKS_PRODUTOS) > 20:

        erros.append(
            "Máximo permitido: 20 links."
        )

    links_validos = []

    for link in LINKS_PRODUTOS:

        link = str(
            link
        ).strip()

        if not link:
            continue

        if not (
            link.startswith(
                "http://"
            )
            or link.startswith(
                "https://"
            )
        ):

            erros.append(
                f"Link inválido: {link}"
            )

        else:

            links_validos.append(
                link
            )

    if not links_validos:

        erros.append(
            "Nenhum link configurado."
        )

    if erros:

        for erro in erros:

            logger.error(
                erro
            )

        raise RuntimeError(
            "Configuração inválida."
        )

    logger.info(
        "Configuração validada."
    )

    logger.info(
        "Total de links: %d",
        len(links_validos)
    )

    logger.info(
        "Intervalo: %d minutos",
        INTERVALO_MINUTOS
    )


# ============================================================
# ESTADO DA FILA
# ============================================================

def carregar_indice():

    if not ARQUIVO_ESTADO.exists():

        logger.info(
            "Nenhum estado anterior encontrado."
        )

        return 0

    try:

        with open(
            ARQUIVO_ESTADO,
            "r",
            encoding="utf-8"
        ) as arquivo:

            dados = json.load(
                arquivo
            )

        indice = int(
            dados.get(
                "indice",
                0
            )
        )

        logger.info(
            "Estado carregado. "
            "Próximo índice: %d",
            indice
        )

        return indice

    except Exception as erro:

        logger.warning(
            "Não foi possível carregar "
            "o estado: %s",
            erro
        )

        return 0


def salvar_indice(
    indice
):

    temporario = Path(
        "fila_produtos.tmp"
    )

    try:

        with open(
            temporario,
            "w",
            encoding="utf-8"
        ) as arquivo:

            json.dump(
                {
                    "indice": indice
                },
                arquivo,
                ensure_ascii=False,
                indent=2
            )

        temporario.replace(
            ARQUIVO_ESTADO
        )

        logger.info(
            "Estado salvo. "
            "Próximo índice: %d",
            indice
        )

    except Exception as erro:

        logger.exception(
            "Erro ao salvar estado: %s",
            erro
        )

        raise


# ============================================================
# NÚMEROS
# ============================================================

def numero(
    valor,
    padrao=0.0
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
                "."
            )
        )

    except Exception:

        return padrao


def inteiro(
    valor,
    padrao=0
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
    valor
):

    valor = numero(
        valor
    )

    texto = (
        f"{valor:,.2f}"
        .replace(
            ",",
            "X"
        )
        .replace(
            ".",
            ","
        )
        .replace(
            "X",
            "."
        )
    )

    return f"R$ {texto}"


# ============================================================
# VENDAS
# ============================================================

def formatar_vendas(
    vendas
):

    vendas = inteiro(
        vendas
    )

    return (
        f"{vendas:,}"
        .replace(
            ",",
            "."
        )
    )


# ============================================================
# MENSAGEM
# ============================================================

def montar_mensagem(
    produto
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

    # --------------------------------------------------------
    # PREÇO ATUAL
    # --------------------------------------------------------

    preco_atual = preco

    if preco_min > 0:

        preco_atual = preco_min

    # --------------------------------------------------------
    # PREÇO ANTERIOR
    # --------------------------------------------------------

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

        preco_anterior = (
            preco_atual
        )

    # --------------------------------------------------------
    # MENSAGEM
    # --------------------------------------------------------

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
# PUBLICAR NO TELEGRAM
# ============================================================

async def publicar_produto(
    bot: Bot,
    produto: dict[str, Any],
    link_afiliado: str
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
                    url=link_afiliado
                )
            ]
        ]
    )

    # --------------------------------------------------------
    # TENTAR IMAGEM
    # --------------------------------------------------------

    if image_url:

        try:

            await bot.send_photo(
                chat_id=TELEGRAM_CHAT_ID,
                photo=image_url,
                caption=mensagem,
                parse_mode="HTML",
                reply_markup=teclado
            )

            logger.info(
                "Produto publicado com "
                "imagem e botão."
            )

            return True

        except Exception as erro:

            logger.warning(
                "Falha ao enviar imagem: %s",
                erro
            )

    # --------------------------------------------------------
    # FALLBACK: TEXTO
    # --------------------------------------------------------

    try:

        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=mensagem,
            parse_mode="HTML",
            reply_markup=teclado,
            disable_web_page_preview=True
        )

        logger.info(
            "Produto publicado somente "
            "como texto."
        )

        return True

    except Exception as erro:

        logger.exception(
            "Erro ao publicar no Telegram: %s",
            erro
        )

        return False


# ============================================================
# PROCESSAR UM LINK
# ============================================================

async def processar_link(
    bot: Bot,
    link: str,
    indice: int,
    total: int
):

    logger.info(
        "=========================================="
    )

    logger.info(
        "Produto %d/%d",
        indice + 1,
        total
    )

    logger.info(
        "Link: %s",
        link
    )

    # --------------------------------------------------------
    # BUSCAR PRODUTO
    # --------------------------------------------------------

    try:

        produto = await asyncio.to_thread(
            buscar_produto_por_link,
            link
        )

    except ShopeeAPIError as erro:

        logger.error(
            "Erro da Shopee: %s",
            erro
        )

        return False

    except Exception as erro:

        logger.exception(
            "Erro inesperado ao buscar produto: %s",
            erro
        )

        return False

    if not produto:

        logger.error(
            "Produto não encontrado."
        )

        return False

    # --------------------------------------------------------
    # NOME
    # --------------------------------------------------------

    logger.info(
        "Produto encontrado: %s",
        produto.get(
            "productName",
            "Produto"
        )
    )

    # --------------------------------------------------------
    # PUBLICAR
    # --------------------------------------------------------

    sucesso = await publicar_produto(
        bot=bot,
        produto=produto,
        link_afiliado=link
    )

    return sucesso


# ============================================================
# MAIN
# ============================================================

async def main():

    logger.info(
        "🦊 RAPOSA CAÇADORA iniciando..."
    )

    validar_configuracao()

    total = len(
        LINKS_PRODUTOS
    )

    indice = carregar_indice()

    # --------------------------------------------------------
    # PROTEGER ÍNDICE
    # --------------------------------------------------------

    if indice < 0:

        indice = 0

    if indice > total:

        indice = total

    # --------------------------------------------------------
    # FILA JÁ FINALIZADA
    # --------------------------------------------------------

    if indice >= total:

        logger.info(
            "=========================================="
        )

        logger.info(
            "Todos os %d produtos já foram publicados.",
            total
        )

        logger.info(
            "Não há mais produtos na fila."
        )

        return

    logger.info(
        "Começando pelo produto %d/%d.",
        indice + 1,
        total
    )

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    async with Bot(
        token=TELEGRAM_TOKEN
    ) as bot:

        try:

            me = await bot.get_me()

            logger.info(
                "Telegram conectado: @%s",
                me.username
            )

        except Exception as erro:

            logger.exception(
                "Não foi possível conectar "
                "ao Telegram: %s",
                erro
            )

            raise

        # ----------------------------------------------------
        # FILA
        # ----------------------------------------------------

        while indice < total:

            link = LINKS_PRODUTOS[
                indice
            ]

            sucesso = await processar_link(
                bot=bot,
                link=link,
                indice=indice,
                total=total
            )

            # ------------------------------------------------
            # SUCESSO
            # ------------------------------------------------

            if sucesso:

                indice += 1

                salvar_indice(
                    indice
                )

                logger.info(
                    "Produto publicado. "
                    "Progresso: %d/%d.",
                    indice,
                    total
                )

            # ------------------------------------------------
            # ERRO
            # ------------------------------------------------

            else:

                logger.warning(
                    "Produto %d falhou.",
                    indice + 1
                )

                logger.warning(
                    "O índice NÃO será avançado."
                )

                logger.info(
                    "Tentarei novamente em %d minutos.",
                    INTERVALO_MINUTOS
                )

            # ------------------------------------------------
            # FINALIZOU
            # ------------------------------------------------

            if indice >= total:

                break

            # ------------------------------------------------
            # ESPERA
            # ------------------------------------------------

            logger.info(
                "Aguardando %d minutos "
                "para o próximo produto...",
                INTERVALO_MINUTOS
            )

            await asyncio.sleep(
                INTERVALO_MINUTOS * 60
            )

    logger.info(
        "=========================================="
    )

    logger.info(
        "🦊 Fila finalizada."
    )

    logger.info(
        "Total: %d/%d produtos.",
        indice,
        total
    )


# ============================================================
# EXECUÇÃO
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        logger.info(
            "🦊 Raposa Caçadora encerrada."
        )
