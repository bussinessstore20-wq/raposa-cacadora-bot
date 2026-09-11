import os
import json
import time
import hashlib
import logging
from pathlib import Path

import requests
from telegram import Bot


# ============================================================
# 🦊 RAPOSA CAÇADORA
# Shopee Affiliate API -> Telegram
# ============================================================


# ============================================================
# CONFIGURAÇÕES
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SHOPEE_APP_ID = os.getenv("SHOPEE_APP_ID")
SHOPEE_SECRET = os.getenv("SHOPEE_SECRET")

SHOPEE_API_URL = (
    "https://open-api.affiliate.shopee.com.br/graphql"
)

PRODUTOS_POR_CICLO = int(
    os.getenv("PRODUTOS_POR_CICLO", "5")
)

INTERVALO_MINUTOS = int(
    os.getenv("INTERVALO_MINUTOS", "30")
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

ARQUIVO_ENVIADOS = Path(
    "produtos_enviados.json"
)


# ============================================================
# LOG
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

logger = logging.getLogger(
    "raposa-cacadora"
)


# ============================================================
# VALIDAR CONFIGURAÇÃO
# ============================================================

def validar_configuracao():

    obrigatorias = {
        "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
        "SHOPEE_APP_ID": SHOPEE_APP_ID,
        "SHOPEE_SECRET": SHOPEE_SECRET,
    }

    faltando = [
        nome
        for nome, valor in obrigatorias.items()
        if not valor
    ]

    if faltando:

        raise RuntimeError(
            "Variáveis de ambiente faltando: "
            + ", ".join(faltando)
        )

    logger.info(
        "Configuração validada."
    )


# ============================================================
# CONVERSÃO PARA NÚMERO
# ============================================================

def numero(valor):

    try:
        return float(valor)

    except (TypeError, ValueError):
        return 0.0


# ============================================================
# FORMATAÇÃO DE DINHEIRO
# ============================================================

def dinheiro(valor):

    valor = numero(valor)

    texto = f"{valor:,.2f}"

    texto = (
        texto
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )

    return f"R$ {texto}"


# ============================================================
# ASSINATURA SHOPEE
# ============================================================

def gerar_assinatura(
    payload,
    timestamp
):

    texto = (
        str(SHOPEE_APP_ID)
        + str(timestamp)
        + payload
        + str(SHOPEE_SECRET)
    )

    return hashlib.sha256(
        texto.encode("utf-8")
    ).hexdigest()


# ============================================================
# REQUISIÇÃO GRAPHQL
# ============================================================

def shopee_graphql(query):

    timestamp = int(
        time.time()
    )

    body = {
        "query": query
    }

    payload = json.dumps(
        body,
        ensure_ascii=False,
        separators=(",", ":")
    )

    assinatura = gerar_assinatura(
        payload,
        timestamp
    )

    headers = {
        "Content-Type": "application/json",
        "Authorization": (
            f"SHA256 Credential={SHOPEE_APP_ID}, "
            f"Timestamp={timestamp}, "
            f"Signature={assinatura}"
        )
    }

    logger.info(
        "Consultando API da Shopee..."
    )

    resposta = requests.post(
        SHOPEE_API_URL,
        data=payload.encode("utf-8"),
        headers=headers,
        timeout=40
    )

    logger.info(
        "Shopee respondeu HTTP %s",
        resposta.status_code
    )

    if resposta.status_code != 200:

        raise RuntimeError(
            "Erro HTTP da Shopee: "
            f"{resposta.status_code}\n"
            f"{resposta.text[:1000]}"
        )

    try:

        resultado = resposta.json()

    except ValueError:

        raise RuntimeError(
            "A Shopee não retornou JSON:\n"
            + resposta.text[:1000]
        )

    if resultado.get("errors"):

        raise RuntimeError(
            "Erro retornado pela Shopee:\n"
            + json.dumps(
                resultado["errors"],
                ensure_ascii=False,
                indent=2
            )
        )

    return resultado


# ============================================================
# BUSCAR OFERTAS
# ============================================================

def buscar_ofertas():

    query = """
    {
      productOfferV2(
        page: 1,
        limit: 20
      ) {
        nodes {
          productName
          itemId
          commissionRate
          commission
          price
          sales
          imageUrl
          shopName
          productLink
          offerLink
          periodStartTime
          periodEndTime
          priceMin
          priceMax
          productCatIds
          ratingStar
          priceDiscountRate
          shopId
          shopType
          sellerCommissionRate
          shopeeCommissionRate
        }

        pageInfo {
          page
          limit
          hasNextPage
          scrollId
        }
      }
    }
    """

    resultado = shopee_graphql(
        query
    )

    try:

        ofertas = (
            resultado
            ["data"]
            ["productOfferV2"]
            ["nodes"]
        )

    except (KeyError, TypeError):

        raise RuntimeError(
            "Resposta inesperada da Shopee:\n"
            + json.dumps(
                resultado,
                ensure_ascii=False,
                indent=2
            )
        )

    logger.info(
        "%d ofertas recebidas.",
        len(ofertas)
    )

    return ofertas


# ============================================================
# CALCULAR PREÇO ORIGINAL
# ============================================================

def calcular_preco_original(
    preco,
    desconto
):

    preco = numero(preco)
    desconto = numero(desconto)

    if preco <= 0:
        return None

    if desconto <= 0 or desconto >= 100:
        return None

    return preco / (
        1 - desconto / 100
    )


# ============================================================
# CARREGAR PRODUTOS JÁ ENVIADOS
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

        return set(
            str(item)
            for item in dados
        )

    except Exception as erro:

        logger.warning(
            "Erro lendo produtos_enviados.json: %s",
            erro
        )

        return set()


# ============================================================
# SALVAR PRODUTOS ENVIADOS
# ============================================================

def salvar_enviados(enviados):

    with open(
        ARQUIVO_ENVIADOS,
        "w",
        encoding="utf-8"
    ) as arquivo:

        json.dump(
            list(enviados),
            arquivo,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# FILTRAR E RANQUEAR PRODUTOS
# ============================================================

def filtrar_ofertas(ofertas):

    aprovadas = []

    for produto in ofertas:

        nome = produto.get(
            "productName"
        )

        image_url = produto.get(
            "imageUrl"
        )

        offer_link = produto.get(
            "offerLink"
        )

        if not nome:
            continue

        if not image_url:
            continue

        if not offer_link:
            continue

        preco = numero(
            produto.get("price")
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

        vendas = int(
            numero(
                produto.get("sales")
            )
        )

        comissao = numero(
            produto.get("commission")
        )

        if preco <= 0:
            continue

        if desconto < DESCONTO_MINIMO:
            continue

        if avaliacao < AVALIACAO_MINIMA:
            continue

        if vendas < VENDAS_MINIMAS:
            continue

        if comissao < COMISSAO_MINIMA:
            continue

        aprovadas.append(
            produto
        )

    # --------------------------------------------------------
    # RANKING
    # --------------------------------------------------------

    def pontuacao(produto):

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

        vendas = numero(
            produto.get("sales")
        )

        comissao = numero(
            produto.get("commission")
        )

        return (
            desconto * 3
            + avaliacao * 10
            + min(vendas, 10000) / 100
            + comissao
        )

    aprovadas.sort(
        key=pontuacao,
        reverse=True
    )

    logger.info(
        "%d produtos passaram nos filtros.",
        len(aprovadas)
    )

    return aprovadas


# ============================================================
# ID ÚNICO DO PRODUTO
# ============================================================

def obter_id_produto(produto):

    shop_id = produto.get(
        "shopId"
    )

    item_id = produto.get(
        "itemId"
    )

    if shop_id and item_id:

        return f"{shop_id}:{item_id}"

    offer_link = produto.get(
        "offerLink"
    )

    return offer_link


# ============================================================
# ESCAPAR HTML
# ============================================================

def escapar_html(texto):

    if texto is None:
        return ""

    return (
        str(texto)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ============================================================
# MONTAR PUBLICAÇÃO
# ============================================================

def montar_mensagem(produto):

    nome = escapar_html(
        produto.get(
            "productName",
            "Produto"
        )
    )

    loja = escapar_html(
        produto.get(
            "shopName",
            "Shopee"
        )
    )

    preco = numero(
        produto.get("price")
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

    vendas = int(
        numero(
            produto.get("sales")
        )
    )

    comissao = numero(
        produto.get(
            "commission"
        )
    )

    offer_link = produto.get(
        "offerLink",
        ""
    )

    preco_original = (
        calcular_preco_original(
            preco,
            desconto
        )
    )

    if preco_original:

        preco_texto = (
            f"❌ De: "
            f"<s>{dinheiro(preco_original)}</s>\n"
            f"✅ Por: "
            f"<b>{dinheiro(preco)}</b>"
        )

    else:

        preco_texto = (
            f"✅ Por: "
            f"<b>{dinheiro(preco)}</b>"
        )

    return (
        "🦊 <b>RAPOSA CAÇADORA</b>\n\n"

        f"🔥 <b>{nome}</b>\n\n"

        f"{preco_texto}\n\n"

        f"🏷️ <b>{desconto:.0f}% OFF</b>\n"
        f"⭐ Avaliação: <b>{avaliacao:.1f}</b>\n"
        f"🛍️ Loja: {loja}\n"
        f"📦 Vendas: <b>{vendas:,}</b>\n"
        f"💰 Comissão: "
        f"<b>{dinheiro(comissao)}</b>\n\n"

        "🛒 <b>COMPRE AQUI:</b>\n"
        f"{offer_link}\n\n"

        "⚡ <i>Aproveite enquanto estiver disponível!</i>"
    )


# ============================================================
# PUBLICAR PRODUTO
# ============================================================

def publicar_produto(
    bot,
    produto
):

    mensagem = montar_mensagem(
        produto
    )

    image_url = produto.get(
        "imageUrl"
    )

    if image_url:

        bot.send_photo(
            chat_id=TELEGRAM_CHAT_ID,
            photo=image_url,
            caption=mensagem,
            parse_mode="HTML"
        )

    else:

        bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=mensagem,
            parse_mode="HTML"
            )


# ============================================================
# EXECUTAR UM CICLO
# ============================================================

def executar_ciclo(bot):

    logger.info(
        "=========================================="
    )

    logger.info(
        "🦊 Iniciando novo ciclo..."
    )

    enviados = carregar_enviados()

    ofertas = buscar_ofertas()

    ofertas = filtrar_ofertas(
        ofertas
    )

    publicados = 0

    for produto in ofertas:

        if publicados >= PRODUTOS_POR_CICLO:
            break

        produto_id = obter_id_produto(
            produto
        )

        if not produto_id:
            continue

        produto_id = str(
            produto_id
        )

        if produto_id in enviados:

            logger.info(
                "Produto já enviado: %s",
                produto_id
            )

            continue

        try:

            logger.info(
                "Publicando: %s",
                produto.get(
                    "productName"
                )
            )

            publicar_produto(
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

            time.sleep(3)

        except Exception as erro:

            logger.exception(
                "Erro ao publicar produto: %s",
                erro
            )

    logger.info(
        "Ciclo finalizado. %d produtos publicados.",
        publicados
    )


# ============================================================
# FUNÇÃO PRINCIPAL
# ============================================================

def main():

    logger.info(
        "🦊 RAPOSA CAÇADORA iniciando..."
    )

    validar_configuracao()

    bot = Bot(
        token=TELEGRAM_TOKEN
    )

    while True:

        try:

            executar_ciclo(
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

        time.sleep(
            INTERVALO_MINUTOS * 60
        )


# ============================================================
# INICIAR
# ============================================================

if __name__ == "__main__":

    main()
