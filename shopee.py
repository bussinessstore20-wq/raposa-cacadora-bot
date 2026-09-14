import hashlib
import json
import logging
import os
import re
import time
from urllib.parse import urlparse, parse_qs

import requests


logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURAÇÃO
# ============================================================

SHOPEE_GRAPHQL_URL = os.getenv(
    "SHOPEE_GRAPHQL_URL",
    "https://open-api.affiliate.shopee.com.br/graphql",
)


# ============================================================
# ERRO DA API
# ============================================================

class ShopeeAPIError(Exception):
    """Erro relacionado à API de Afiliados da Shopee."""


# ============================================================
# CREDENCIAIS
# ============================================================

def _obter_credenciais():

    app_id = os.getenv(
        "SHOPEE_APP_ID"
    )

    secret = os.getenv(
        "SHOPEE_SECRET"
    )

    if not app_id:
        raise ShopeeAPIError(
            "A variável SHOPEE_APP_ID "
            "não está configurada."
        )

    if not secret:
        raise ShopeeAPIError(
            "A variável SHOPEE_SECRET "
            "não está configurada."
        )

    return (
        app_id.strip(),
        secret.strip()
    )


# ============================================================
# RESOLVER SHORT LINK
# ============================================================

def resolver_link(
    link: str
):

    logger.info(
        "Resolvendo link da Shopee: %s",
        link
    )

    try:

        response = requests.get(
            link,
            allow_redirects=True,
            timeout=30,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/130.0.0.0 "
                    "Safari/537.36"
                )
            }
        )

    except requests.RequestException as erro:

        raise ShopeeAPIError(
            f"Erro ao resolver link da Shopee: {erro}"
        ) from erro

    if response.status_code >= 400:

        raise ShopeeAPIError(
            f"Shopee retornou HTTP "
            f"{response.status_code} "
            f"ao resolver o link."
        )

    url_final = response.url

    logger.info(
        "URL final resolvida: %s",
        url_final
    )

    return url_final


# ============================================================
# EXTRAIR SHOP ID / ITEM ID
# ============================================================

def extrair_ids_da_url(
    url: str
):

    logger.info(
        "Extraindo IDs da URL..."
    )

    parsed = urlparse(
        url
    )

    caminho = parsed.path

    # --------------------------------------------------------
    # FORMATO:
    #
    # https://shopee.com.br/produto-i.123456.789012
    # --------------------------------------------------------

    match = re.search(
        r"-i\.(\d+)\.(\d+)",
        caminho
    )

    if match:

        shop_id = match.group(1)
        item_id = match.group(2)

        logger.info(
            "IDs encontrados: "
            "shopId=%s | itemId=%s",
            shop_id,
            item_id
        )

        return (
            shop_id,
            item_id
        )

    # --------------------------------------------------------
    # FORMATO:
    #
    # https://shopee.com.br/product/123456/789012
    # --------------------------------------------------------

    match = re.search(
        r"/product/(\d+)/(\d+)",
        caminho
    )

    if match:

        shop_id = match.group(1)
        item_id = match.group(2)

        logger.info(
            "IDs encontrados: "
            "shopId=%s | itemId=%s",
            shop_id,
            item_id
        )

        return (
            shop_id,
            item_id
        )

    # --------------------------------------------------------
    # FORMATO:
    #
    # ?shopid=123456&itemid=789012
    # --------------------------------------------------------

    parametros = parse_qs(
        parsed.query
    )

    shop_values = (
        parametros.get(
            "shopid"
        )
        or parametros.get(
            "shopId"
        )
    )

    item_values = (
        parametros.get(
            "itemid"
        )
        or parametros.get(
            "itemId"
        )
    )

    if shop_values and item_values:

        shop_id = shop_values[0]
        item_id = item_values[0]

        logger.info(
            "IDs encontrados nos parâmetros: "
            "shopId=%s | itemId=%s",
            shop_id,
            item_id
        )

        return (
            shop_id,
            item_id
        )

    raise ShopeeAPIError(
        "Não foi possível encontrar "
        "shopId/itemId na URL final da Shopee."
    )


# ============================================================
# ASSINATURA
# ============================================================

def _gerar_assinatura(
    app_id,
    secret,
    timestamp,
    payload
):

    texto_assinatura = (
        f"{app_id}"
        f"{timestamp}"
        f"{payload}"
        f"{secret}"
    )

    return hashlib.sha256(
        texto_assinatura.encode(
            "utf-8"
        )
    ).hexdigest()


# ============================================================
# GRAPHQL
# ============================================================

def _graphql(
    query: str,
    variables: dict
):

    app_id, secret = (
        _obter_credenciais()
    )

    payload_obj = {
        "query": query,
        "variables": variables
    }

    payload = json.dumps(
        payload_obj,
        ensure_ascii=False,
        separators=(
            ",",
            ":"
        )
    )

    timestamp = int(
        time.time()
    )

    assinatura = _gerar_assinatura(
        app_id=app_id,
        secret=secret,
        timestamp=timestamp,
        payload=payload
    )

    authorization = (
        f"SHA256 "
        f"Credential={app_id}, "
        f"Timestamp={timestamp}, "
        f"Signature={assinatura}"
    )

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": authorization,
    }

    try:

        response = requests.post(
            SHOPEE_GRAPHQL_URL,
            data=payload.encode(
                "utf-8"
            ),
            headers=headers,
            timeout=30
        )

    except requests.RequestException as erro:

        raise ShopeeAPIError(
            f"Erro de conexão com a Shopee: "
            f"{erro}"
        ) from erro

    logger.info(
        "Shopee respondeu HTTP %d",
        response.status_code
    )

    if response.status_code != 200:

        raise ShopeeAPIError(
            f"Shopee respondeu HTTP "
            f"{response.status_code}: "
            f"{response.text[:1000]}"
        )

    try:

        resultado = response.json()

    except ValueError as erro:

        raise ShopeeAPIError(
            "A Shopee retornou uma resposta "
            "que não é JSON."
        ) from erro

    if resultado.get("errors"):

        logger.error(
            "Erros GraphQL: %s",
            json.dumps(
                resultado["errors"],
                ensure_ascii=False
            )
        )

        raise ShopeeAPIError(
            "A API da Shopee retornou "
            "erros GraphQL."
        )

    return resultado


# ============================================================
# QUERY DE PRODUTO
# ============================================================

PRODUCT_QUERY = """
query ProductOffer(
    $shopId: Int,
    $itemId: Int,
    $page: Int,
    $limit: Int
) {
    productOfferV2(
        shopId: $shopId,
        itemId: $itemId,
        page: $page,
        limit: $limit
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
        }
    }
}
"""


# ============================================================
# BUSCAR PRODUTO POR SHOP ID + ITEM ID
# ============================================================

def buscar_produto_por_ids(
    shop_id,
    item_id
):

    logger.info(
        "Consultando produto: "
        "shopId=%s | itemId=%s",
        shop_id,
        item_id
    )

    try:

        shop_id_int = int(
            shop_id
        )

        item_id_int = int(
            item_id
        )

    except ValueError as erro:

        raise ShopeeAPIError(
            "shopId/itemId inválidos."
        ) from erro

    resultado = _graphql(
        query=PRODUCT_QUERY,
        variables={
            "shopId": shop_id_int,
            "itemId": item_id_int,
            "page": 1,
            "limit": 1
        }
    )

    data = (
        resultado.get(
            "data"
        )
        or {}
    )

    product_offer = (
        data.get(
            "productOfferV2"
        )
        or {}
    )

    nodes = (
        product_offer.get(
            "nodes"
        )
        or []
    )

    if not nodes:

        logger.warning(
            "Nenhum produto encontrado "
            "para shopId=%s itemId=%s",
            shop_id,
            item_id
        )

        return None

    produto = nodes[0]

    logger.info(
        "Produto encontrado: %s",
        produto.get(
            "productName"
        )
    )

    return produto


# ============================================================
# BUSCAR PRODUTO POR LINK
# ============================================================

def buscar_produto_por_link(
    link: str
):

    if not link:
        raise ShopeeAPIError(
            "Link vazio."
        )

    link = link.strip()

    # --------------------------------------------------------
    # 1. Resolver short link
    # --------------------------------------------------------

    url_final = resolver_link(
        link
    )

    # --------------------------------------------------------
    # 2. Extrair IDs
    # --------------------------------------------------------

    shop_id, item_id = (
        extrair_ids_da_url(
            url_final
        )
    )

    # --------------------------------------------------------
    # 3. Consultar produto
    # --------------------------------------------------------

    produto = buscar_produto_por_ids(
        shop_id=shop_id,
        item_id=item_id
    )

    if not produto:

        raise ShopeeAPIError(
            "O produto não foi encontrado "
            "na API de Afiliados."
        )

    # --------------------------------------------------------
    # 4. GARANTIR O LINK DE AFILIADO
    #
    # O botão usará o link que você colocou
    # manualmente.
    # --------------------------------------------------------

    produto["manualAffiliateLink"] = link

    return produto
