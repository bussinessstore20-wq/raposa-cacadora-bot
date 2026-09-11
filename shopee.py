import hashlib
import json
import logging
import os
import time

import requests


logger = logging.getLogger(__name__)


SHOPEE_GRAPHQL_URL = os.getenv(
    "SHOPEE_GRAPHQL_URL",
    "https://open-api.affiliate.shopee.com.br/graphql",
)


PRODUCT_OFFER_QUERY = """
{
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


class ShopeeAPIError(Exception):
    """Erro relacionado à API de Afiliados da Shopee."""


def _obter_credenciais():
    app_id = os.getenv("SHOPEE_APP_ID")
    secret = os.getenv("SHOPEE_SECRET")

    if not app_id:
        raise ShopeeAPIError(
            "A variável SHOPEE_APP_ID não está configurada."
        )

    if not secret:
        raise ShopeeAPIError(
            "A variável SHOPEE_SECRET não está configurada."
        )

    return app_id.strip(), secret.strip()


def _criar_payload():
    """
    Cria exatamente o JSON que será enviado à Shopee.

    A assinatura deve ser calculada sobre o payload exato
    enviado no corpo da requisição.
    """

    payload = {
        "query": PRODUCT_OFFER_QUERY,
        "variables": {},
    }

    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _gerar_assinatura(app_id, secret, timestamp, payload):
    """
    Gera a assinatura da Shopee:

    SHA256(AppId + Timestamp + Payload + Secret)
    """

    texto_assinatura = (
        f"{app_id}{timestamp}{payload}{secret}"
    )

    assinatura = hashlib.sha256(
        texto_assinatura.encode("utf-8")
    ).hexdigest()

    return assinatura


def _consultar_api():
    app_id, secret = _obter_credenciais()

    payload = _criar_payload()

    timestamp = int(time.time())

    assinatura = _gerar_assinatura(
        app_id=app_id,
        secret=secret,
        timestamp=timestamp,
        payload=payload,
    )

    authorization = (
        f"SHA256 Credential={app_id}, "
        f"Timestamp={timestamp}, "
        f"Signature={assinatura}"
    )

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": authorization,
    }

    logger.info("Consultando API da Shopee...")

    try:
        response = requests.post(
            SHOPEE_GRAPHQL_URL,
            data=payload.encode("utf-8"),
            headers=headers,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise ShopeeAPIError(
            f"Erro de conexão com a Shopee: {exc}"
        ) from exc

    logger.info(
        "Shopee respondeu HTTP %s",
        response.status_code,
    )

    if response.status_code != 200:
        raise ShopeeAPIError(
            f"Shopee respondeu HTTP {response.status_code}: "
            f"{response.text[:1000]}"
        )

    try:
        resultado = response.json()
    except ValueError as exc:
        raise ShopeeAPIError(
            "A Shopee retornou uma resposta que não é JSON."
        ) from exc

    if resultado.get("errors"):
        erros = resultado["errors"]

        logger.error(
            "Erro GraphQL da Shopee: %s",
            json.dumps(
                erros,
                ensure_ascii=False,
            ),
        )

        raise ShopeeAPIError(
            "A API da Shopee retornou erros GraphQL: "
            + json.dumps(
                erros,
                ensure_ascii=False,
            )
        )

    return resultado


def buscar_ofertas():
    """
    Consulta a API da Shopee e retorna a lista de ofertas.

    Retorno:
        list[dict]
    """

    resultado = _consultar_api()

    data = resultado.get("data") or {}

    product_offer = data.get("productOfferV2") or {}

    ofertas = product_offer.get("nodes") or []

    page_info = product_offer.get("pageInfo") or {}

    logger.info(
        "%s ofertas recebidas.",
        len(ofertas),
    )

    logger.debug(
        "PageInfo da Shopee: %s",
        page_info,
    )

    return ofertas
