import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any

import requests


logger = logging.getLogger(
    "raposa-cacadora"
)


# ============================================================
# CONFIGURAÇÃO
# ============================================================

SHOPEE_APP_ID = os.getenv(
    "SHOPEE_APP_ID",
    ""
).strip()

SHOPEE_SECRET = os.getenv(
    "SHOPEE_SECRET",
    ""
).strip()

SHOPEE_GRAPHQL_URL = os.getenv(
    "SHOPEE_GRAPHQL_URL",
    "https://open-api.affiliate.shopee.com.br/graphql"
).strip()


# ============================================================
# ERRO
# ============================================================

class ShopeeAPIError(Exception):
    pass


# ============================================================
# QUERY PRODUCT OFFER V2
# ============================================================

PRODUCT_OFFER_QUERY = """
query {
    productOfferV2 {
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


# ============================================================
# ASSINATURA SHOPEE
# ============================================================

def gerar_assinatura(
    payload: str
) -> str:

    if not SHOPEE_APP_ID:
        raise ShopeeAPIError(
            "SHOPEE_APP_ID não configurado."
        )

    if not SHOPEE_SECRET:
        raise ShopeeAPIError(
            "SHOPEE_SECRET não configurado."
        )

    timestamp = int(
        time.time()
    )

    base_string = (
        SHOPEE_APP_ID
        + str(timestamp)
        + payload
    )

    assinatura = hmac.new(
        SHOPEE_SECRET.encode(
            "utf-8"
        ),
        base_string.encode(
            "utf-8"
        ),
        hashlib.sha256
    ).hexdigest()

    return (
        f"SHA256 Credential={SHOPEE_APP_ID},"
        f"Timestamp={timestamp},"
        f"Signature={assinatura}"
    )


# ============================================================
# CONSULTA GRAPHQL
# ============================================================

def consultar_shopee():

    payload_dict = {
        "query": PRODUCT_OFFER_QUERY
    }

    payload = json.dumps(
        payload_dict,
        separators=(
            ",",
            ":"
        ),
        ensure_ascii=False
    )

    authorization = gerar_assinatura(
        payload
    )

    headers = {
        "Content-Type": "application/json",
        "Authorization": authorization
    }

    try:

        resposta = requests.post(
            SHOPEE_GRAPHQL_URL,
            headers=headers,
            data=payload.encode(
                "utf-8"
            ),
            timeout=30
        )

    except requests.RequestException as erro:

        raise ShopeeAPIError(
            f"Erro de conexão com a Shopee: {erro}"
        ) from erro

    logger.info(
        "Shopee respondeu HTTP %s",
        resposta.status_code
    )

    if resposta.status_code != 200:

        raise ShopeeAPIError(
            "Shopee respondeu HTTP "
            f"{resposta.status_code}: "
            f"{resposta.text[:1000]}"
        )

    try:

        dados = resposta.json()

    except ValueError as erro:

        raise ShopeeAPIError(
            "Resposta da Shopee não é JSON válido."
        ) from erro

    if dados.get("errors"):

        raise ShopeeAPIError(
            "Erro retornado pela Shopee: "
            + json.dumps(
                dados["errors"],
                ensure_ascii=False
            )
        )

    return dados


# ============================================================
# BUSCAR OFERTAS
# ============================================================

def buscar_ofertas():

    dados = consultar_shopee()

    try:

        product_offer = (
            dados
            .get("data", {})
            .get("productOfferV2", {})
        )

        ofertas = product_offer.get(
            "nodes",
            []
        )

    except AttributeError as erro:

        raise ShopeeAPIError(
            "Formato inesperado na resposta "
            "da API da Shopee."
        ) from erro

    if not isinstance(
        ofertas,
        list
    ):

        raise ShopeeAPIError(
            "A Shopee não retornou uma lista "
            "de ofertas."
        )

    logger.info(
        "%d ofertas recebidas.",
        len(ofertas)
    )

    return ofertas


# ============================================================
# TESTE DIRETO
# ============================================================

if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | "
            "%(levelname)s | "
            "%(message)s"
        )
    )

    try:

        ofertas = buscar_ofertas()

        print(
            json.dumps(
                ofertas,
                ensure_ascii=False,
                indent=2
            )
        )

    except Exception as erro:

        logger.exception(
            "Erro ao consultar Shopee: %s",
            erro
        )
