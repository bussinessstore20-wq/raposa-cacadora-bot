import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from telegram import Bot

from shopee import (
    buscar_ofertas,
    ShopeeAPIError,
)


# ============================================================
# CONFIGURAÇÃO
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

INTERVALO_MINUTOS = int(
    os.getenv("INTERVALO_MINUTOS", "2")
)

PRODUTOS_POR_CICLO = int(
    os.getenv("PRODUTOS_POR_CICLO", "1")
)

DESCONTO_MINIMO = float(
    os.getenv("DESCONTO_MINIMO", "30")
)

AVALIACAO_MINIMA = float(
    os.getenv("AVALIACAO_MINIMA", "4.5")
)

VENDAS_MINIMAS = int(
    os.getenv("VENDAS_MINIMAS", "0")
)

COMISSAO_MINIMA = float(
    os.getenv("COMISSAO_MINIMA", "0")
)


# ============================================================
# ARQUIVO DE PRODUTOS ENVIADOS
# ============================================================

ARQUIVO_ENVIADOS = Path(
    "produtos_enviados.json"
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
# CONFIGURAÇÃO
# ============================================================

def validar_configuracao():

    erros = []

    if not TELEGRAM_TOKEN:
        erros.append(
            "TELEGRAM_TOKEN não configurado"
        )

    if not TELEGRAM_CHAT_ID:
        erros.append(
            "TELEGRAM_CHAT_ID não configurado"
        )

    if erros:

        for erro in erros:
            logger.error(erro)

        raise RuntimeError(
            "Configuração inválida."
        )

    logger.info(
        "Configuração validada."
    )


# ============================================================
# PRODUTOS ENVIADOS
# ============================================================

def carregar_enviados():

    if not ARQUIVO_ENVIADOS.exists():

        return set()

    try:

        with open(
            ARQUIVO_ENVIADOS,
            "r",
            encoding="utf-8"
        ) as arquivo:

            dados = json.load(
                arquivo
            )

        if not isinstance(
            dados,
            list
        ):

            return set()

        return {
            str(item)
            for item in dados
        }

    except Exception as erro:

        logger.warning(
            "Não foi possível carregar "
            "produtos_enviados.json: %s",
            erro
        )

        return set()


def salvar_enviados(enviados):

    temporario = Path(
        "produtos_enviados.tmp"
    )

    try:

        with open(
            temporario,
            "w",
            encoding="utf-8"
        ) as arquivo:

            json.dump(
                sorted(
                    enviados
                ),
                arquivo,
                ensure_ascii=False,
                indent=2
            )

        temporario.replace(
            ARQUIVO_ENVIADOS
        )

    except Exception as erro:

        logger.exception(
            "Erro ao salvar produtos enviados: %s",
            erro
        )


# ============================================================
# ID DO PRODUTO
# ============================================================

def obter_id_produto(
    produto: dict[str, Any]
):

    item_id = produto.get(
        "itemId"
    )

    if item_id is not None:

        return str(
            item_id
        )

    product_id = produto.get(
        "productId"
    )

    if product_id is not None:

        return str(
            product_id
        )

    return None


# ============================================================
# CONVERSÃO DE VALORES
# ============================================================

def numero(
    valor,
    padrao=0.0
):

    try:

        if valor is None:
            return padrao

        return float(
            str(valor).replace(
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

def moeda(valor):

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
# FILTRO DE OFERTAS
# ============================================================

def filtrar_ofertas(
    ofertas
):

    resultado = []

    for produto in ofertas:

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

        comissao = (
            numero(
                produto.get(
                    "commissionRate"
                )
            )
            * 100
        )

        if desconto < DESCONTO_MINIMO:
            continue

        if avaliacao < AVALIACAO_MINIMA:
            continue

        if vendas < VENDAS_MINIMAS:
            continue

        if comissao < COMISSAO_MINIMA:
            continue

        resultado.append(
            produto
        )

    return resultado


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

    offer_link = (
        produto.get(
            "offerLink"
        )
        or produto.get(
            "productLink"
        )
        or ""
    )

    # --------------------------------------------------------
    # Preço anterior aproximado
    # --------------------------------------------------------

    preco_atual = preco

    if preco_min > 0:
        preco_atual = preco_min

    if (
        desconto > 0
        and preco_atual > 0
    ):

        preco_anterior = (
            preco_atual
            / (1 - desconto / 100)
        )

    else:

        preco_anterior = (
            preco_atual
        )

    mensagem = (
        "🦊 <b>RAPOSA CAÇADORA</b>\n"
        "\n"
        f"🔥 <b>{nome}</b>\n"
        "\n"
        f"❌ De: <s>{moeda(preco_anterior)}</s>\n"
        f"✅ <b>Por: {moeda(preco_atual)}</b>\n"
    )

    if desconto > 0:

        mensagem += (
            f"\n🏷️ <b>{desconto:.0f}% OFF</b>"
        )

    mensagem += (
        f"\n⭐ Avaliação: {avaliacao:.1f}"
        f"\n📦 Vendas: {vendas:,}"
        f"\n🏪 Loja: {loja}"
        .replace(",", ".")
    )

    if offer_link:

        mensagem += (
            "\n\n"
            "🛒 <b>COMPRE AQUI:</b>\n"
            f"{offer_link}"
        )

    return mensagem


# ============================================================
# PUBLICAÇÃO NO TELEGRAM
# ============================================================

async def publicar_produto(
    bot: Bot,
    produto: dict[str, Any]
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

    if image_url:

        try:

            await bot.send_photo(
                chat_id=TELEGRAM_CHAT_ID,
                photo=image_url,
                caption=mensagem,
                parse_mode="HTML"
            )

            return

        except Exception as erro:

            logger.warning(
                "Não foi possível enviar "
                "a imagem. Enviando apenas texto. "
                "Erro: %s",
                erro
            )

    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=mensagem,
        parse_mode="HTML",
        disable_web_page_preview=False
    )


# ============================================================
# CICLO
# ============================================================

async def executar_ciclo(
    bot: Bot
):

    logger.info(
        "=========================================="
    )

    logger.info(
        "🦊 Iniciando novo ciclo..."
    )

    enviados = carregar_enviados()

    try:

        logger.info(
            "Consultando API da Shopee..."
        )

        ofertas = buscar_ofertas()

    except ShopeeAPIError as erro:

        logger.error(
            "Erro da API Shopee: %s",
            erro
        )

        return

    except Exception as erro:

        logger.exception(
            "Erro inesperado ao consultar Shopee: %s",
            erro
        )

        return

    if not ofertas:

        logger.warning(
            "Nenhuma oferta recebida."
        )

        return

    logger.info(
        "%d ofertas recebidas.",
        len(ofertas)
    )

    ofertas_filtradas = filtrar_ofertas(
        ofertas
    )

    logger.info(
        "%d produtos passaram nos filtros.",
        len(ofertas_filtradas)
    )

    publicados = 0

    for produto in ofertas_filtradas:

        if publicados >= PRODUTOS_POR_CICLO:
            break

        produto_id = obter_id_produto(
            produto
        )

        if not produto_id:

            logger.warning(
                "Produto sem itemId. Ignorando."
            )

            continue

        if produto_id in enviados:

            logger.info(
                "Produto já enviado: %s",
                produto_id
            )

            continue

        nome = (
            produto.get(
                "productName"
            )
            or "Produto"
        )

        logger.info(
            "Publicando: %s",
            nome
        )

        try:

            await publicar_produto(
                bot,
                produto
            )

            enviados.add(
                produto_id
            )

            salvar_enviados(
                enviados
            )

            publicados += 1

            await asyncio.sleep(
                3
            )

        except Exception as erro:

            logger.exception(
                "Erro ao publicar produto: %s",
                erro
            )

    logger.info(
        "Ciclo finalizado. "
        "%d produtos publicados.",
        publicados
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    logger.info(
        "🦊 RAPOSA CAÇADORA iniciando..."
    )

    validar_configuracao()

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

        while True:

            try:

                await executar_ciclo(
                    bot
                )

            except Exception as erro:

                logger.exception(
                    "Erro no ciclo: %s",
                    erro
                )

            logger.info(
                "Aguardando %d minutos...",
                INTERVALO_MINUTOS
            )

            await asyncio.sleep(
                INTERVALO_MINUTOS * 60
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
