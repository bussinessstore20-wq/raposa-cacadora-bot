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

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_TOKEN",
    ""
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
).strip()


# Quantos produtos publicar em cada ciclo
PRODUTOS_POR_CICLO = int(
    os.getenv(
        "PRODUTOS_POR_CICLO",
        "1"
    )
)


# Tempo entre os ciclos
INTERVALO_MINUTOS = int(
    os.getenv(
        "INTERVALO_MINUTOS",
        "2"
    )
)


# Filtros
DESCONTO_MINIMO = float(
    os.getenv(
        "DESCONTO_MINIMO",
        "30"
    )
)

AVALIACAO_MINIMA = float(
    os.getenv(
        "AVALIACAO_MINIMA",
        "4.5"
    )
)

VENDAS_MINIMAS = int(
    os.getenv(
        "VENDAS_MINIMAS",
        "0"
    )
)

COMISSAO_MINIMA = float(
    os.getenv(
        "COMISSAO_MINIMA",
        "0"
    )
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
# VALIDAÇÃO DA CONFIGURAÇÃO
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

    if PRODUTOS_POR_CICLO < 1:
        erros.append(
            "PRODUTOS_POR_CICLO deve ser maior que 0"
        )

    if INTERVALO_MINUTOS < 1:
        erros.append(
            "INTERVALO_MINUTOS deve ser maior que 0"
        )

    if DESCONTO_MINIMO < 0:
        erros.append(
            "DESCONTO_MINIMO não pode ser negativo"
        )

    if AVALIACAO_MINIMA < 0:
        erros.append(
            "AVALIACAO_MINIMA não pode ser negativa"
        )

    if VENDAS_MINIMAS < 0:
        erros.append(
            "VENDAS_MINIMAS não pode ser negativa"
        )

    if COMISSAO_MINIMA < 0:
        erros.append(
            "COMISSAO_MINIMA não pode ser negativa"
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
        "Produtos por ciclo: %d",
        PRODUTOS_POR_CICLO
    )

    logger.info(
        "Intervalo: %d minutos",
        INTERVALO_MINUTOS
    )

    logger.info(
        "Desconto mínimo: %.0f%%",
        DESCONTO_MINIMO
    )

    logger.info(
        "Avaliação mínima: %.1f",
        AVALIACAO_MINIMA
    )

    logger.info(
        "Vendas mínimas: %d",
        VENDAS_MINIMAS
    )

    logger.info(
        "Comissão mínima: %.1f%%",
        COMISSAO_MINIMA
    )


# ============================================================
# PRODUTOS ENVIADOS
# ============================================================

def carregar_enviados():

    if not ARQUIVO_ENVIADOS.exists():

        logger.info(
            "Arquivo de produtos enviados "
            "ainda não existe."
        )

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

            logger.warning(
                "produtos_enviados.json "
                "não contém uma lista."
            )

            return set()

        enviados = {
            str(item)
            for item in dados
        }

        logger.info(
            "%d produtos já registrados "
            "como enviados.",
            len(enviados)
        )

        return enviados

    except Exception as erro:

        logger.warning(
            "Não foi possível carregar "
            "produtos_enviados.json: %s",
            erro
        )

        return set()


def salvar_enviados(
    enviados
):

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
                sorted(enviados),
                arquivo,
                ensure_ascii=False,
                indent=2
            )

        temporario.replace(
            ARQUIVO_ENVIADOS
        )

        logger.info(
            "Lista de produtos enviados "
            "salva. Total: %d",
            len(enviados)
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
# CONVERSÃO DE NÚMEROS
# ============================================================

def numero(
    valor,
    padrao=0.0
):

    try:

        if valor is None:
            return padrao

        texto = str(valor).strip()

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
# FORMATAÇÃO DE VENDAS
# ============================================================

def formatar_vendas(
    vendas
):

    numero_vendas = inteiro(
        vendas
    )

    return f"{numero_vendas:,}".replace(
        ",",
        "."
    )


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

        # A API retorna commissionRate
        # como decimal.
        #
        # Exemplo:
        # "0.46" = 46%
        #
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
# MONTAGEM DA MENSAGEM
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

    # IMPORTANTE:
    # Primeiro usamos offerLink.
    #
    # O productLink só é usado como
    # último recurso caso offerLink
    # não exista.
    offer_link = (
        produto.get(
            "offerLink"
        )
        or ""
    )

    if not offer_link:

        offer_link = (
            produto.get(
                "productLink"
            )
            or ""
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
        f"🔥 <b>{nome}</b>\n"
        "\n"
        f"❌ De: <s>{moeda(preco_anterior)}</s>\n"
        f"✅ <b>Por: {moeda(preco_atual)}</b>\n"
        "\n"
        f"🏷️ <b>{desconto:.0f}% OFF</b>\n"
        f"⭐ Avaliação: {avaliacao:.1f}\n"
        f"📦 Vendas: {formatar_vendas(vendas)}\n"
        f"🏪 Loja: {loja}"
    )

    if offer_link:

        mensagem += (
            "\n\n"
            "🛒 <b>COMPRE AQUI:</b>\n"
            f"{offer_link}"
        )

    else:

        logger.warning(
            "Produto sem offerLink/productLink: %s",
            nome
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

    # --------------------------------------------------------
    # TENTAR PUBLICAR IMAGEM + TEXTO
    # --------------------------------------------------------

    if image_url:

        try:

            await bot.send_photo(
                chat_id=TELEGRAM_CHAT_ID,
                photo=image_url,
                caption=mensagem,
                parse_mode="HTML"
            )

            logger.info(
                "Produto publicado com "
                "imagem com sucesso."
            )

            return True

        except Exception as erro:

            logger.warning(
                "Não foi possível enviar "
                "a imagem. Tentando enviar "
                "apenas texto. Erro: %s",
                erro
            )

    # --------------------------------------------------------
    # FALLBACK: SOMENTE TEXTO
    # --------------------------------------------------------

    try:

        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=mensagem,
            parse_mode="HTML",
            disable_web_page_preview=False
        )

        logger.info(
            "Produto publicado somente "
            "como texto."
        )

        return True

    except Exception as erro:

        logger.exception(
            "Erro ao enviar mensagem "
            "para o Telegram: %s",
            erro
        )

        return False


# ============================================================
# EXECUTAR CICLO
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

    # --------------------------------------------------------
    # CONSULTAR SHOPEE
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # VERIFICAR RESULTADO
    # --------------------------------------------------------

    if not ofertas:

        logger.warning(
            "Nenhuma oferta recebida."
        )

        return

    logger.info(
        "%d ofertas recebidas.",
        len(ofertas)
    )

    # --------------------------------------------------------
    # APLICAR FILTROS
    # --------------------------------------------------------

    ofertas_filtradas = filtrar_ofertas(
        ofertas
    )

    logger.info(
        "%d produtos passaram nos filtros.",
        len(ofertas_filtradas)
    )

    if not ofertas_filtradas:

        logger.info(
            "Nenhum produto atende aos "
            "filtros neste ciclo."
        )

        return

    # --------------------------------------------------------
    # PUBLICAR
    # --------------------------------------------------------

    publicados = 0

    for produto in ofertas_filtradas:

        # ----------------------------------------------------
        # LIMITE DE PRODUTOS POR CICLO
        # ----------------------------------------------------

        if publicados >= PRODUTOS_POR_CICLO:

            break

        # ----------------------------------------------------
        # IDENTIFICAR PRODUTO
        # ----------------------------------------------------

        produto_id = obter_id_produto(
            produto
        )

        if not produto_id:

            logger.warning(
                "Produto sem itemId. Ignorando."
            )

            continue

        # ----------------------------------------------------
        # VERIFICAR DUPLICIDADE
        # ----------------------------------------------------

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

        logger.info(
            "itemId: %s",
            produto_id
        )

        # ----------------------------------------------------
        # PUBLICAR NO TELEGRAM
        # ----------------------------------------------------

        sucesso = await publicar_produto(
            bot,
            produto
        )

        # ----------------------------------------------------
        # SÓ MARCAR COMO ENVIADO
        # SE O TELEGRAM CONFIRMAR SUCESSO
        # ----------------------------------------------------

        if sucesso:

            enviados.add(
                produto_id
            )

            salvar_enviados(
                enviados
            )

            publicados += 1

            logger.info(
                "Produto publicado com sucesso. "
                "Total neste ciclo: %d/%d",
                publicados,
                PRODUTOS_POR_CICLO
            )

            # ------------------------------------------------
            # PEQUENA PAUSA ENTRE PUBLICAÇÕES
            # ------------------------------------------------

            if publicados < PRODUTOS_POR_CICLO:

                await asyncio.sleep(
                    3
                )

    # --------------------------------------------------------
    # FINAL DO CICLO
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # VALIDAR CONFIGURAÇÃO
    # --------------------------------------------------------

    validar_configuracao()

    # --------------------------------------------------------
    # CONECTAR AO TELEGRAM
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
        # LOOP PRINCIPAL
        # ----------------------------------------------------

        while True:

            try:

                await executar_ciclo(
                    bot
                )

            except Exception as erro:

                logger.exception(
                    "Erro inesperado no ciclo: %s",
                    erro
                )

            # ------------------------------------------------
            # ESPERA ANTES DO PRÓXIMO CICLO
            # ------------------------------------------------

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
