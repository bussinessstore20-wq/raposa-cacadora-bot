import os
import json
import time
import hashlib
import requests


# ============================================================
# CONFIGURAÇÃO
# ============================================================

SHOPEE_APP_ID = os.getenv("SHOPEE_APP_ID")
SHOPEE_SECRET = os.getenv("SHOPEE_SECRET")

SHOPEE_API_URL = (
    "https://open-api.affiliate.shopee.com.br/graphql"
)


# ============================================================
# ERRO PERSONALIZADO
# ============================================================

class ShopeeAPIError(Exception):
    pass


# ============================================================
# VALIDAÇÃO
# ============================================================

def validate_config():

    if not SHOPEE_APP_ID:
        raise ShopeeAPIError(
            "SHOPEE_APP_ID não configurado no Render."
        )

    if not SHOPEE_SECRET:
        raise ShopeeAPIError(
            "SHOPEE_SECRET não configurado no Render."
        )


# ============================================================
# ASSINATURA
# ============================================================

def generate_signature(payload, timestamp):

    raw = (
        str(SHOPEE_APP_ID)
        + str(timestamp)
        + payload
        + str(SHOPEE_SECRET)
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


# ============================================================
# GRAPHQL
# ============================================================

def graphql_request(query, variables=None):

    validate_config()

    body = {
        "query": query
    }

    if variables is not None:
        body["variables"] = variables

    payload = json.dumps(
        body,
        separators=(",", ":"),
        ensure_ascii=False
    )

    timestamp = int(time.time())

    signature = generate_signature(
        payload,
        timestamp
    )

    authorization = (
        f"SHA256 "
        f"Credential={SHOPEE_APP_ID},"
        f"Timestamp={timestamp},"
        f"Signature={signature}"
    )

    headers = {
        "Authorization": authorization,
        "Content-Type": "application/json",
    }

    try:

        response = requests.post(
            SHOPEE_API_URL,
            data=payload.encode("utf-8"),
            headers=headers,
            timeout=30
        )

    except requests.RequestException as error:

        raise ShopeeAPIError(
            f"Erro de conexão com a Shopee: {error}"
        )

    if response.status_code != 200:

        raise ShopeeAPIError(
            f"Shopee retornou HTTP "
            f"{response.status_code}: "
            f"{response.text[:500]}"
        )

    try:

        result = response.json()

    except ValueError:

        raise ShopeeAPIError(
            "A Shopee retornou uma resposta inválida."
        )

    if result.get("errors"):

        raise ShopeeAPIError(
            json.dumps(
                result["errors"],
                ensure_ascii=False,
                indent=2
            )
        )

    return result.get("data", {})


# ============================================================
# GERAR LINK DE AFILIADO
# ============================================================

def generate_affiliate_link(original_url):

    """
    Gera um link curto/rastreável de afiliado.

    OBS:
    O formato exato da mutation deve ser confirmado
    no Explorer da conta da Shopee caso a API da sua
    conta utilize uma versão diferente.
    """

    mutation = """
    mutation GenerateShortLink(
        $originUrl: String!
    ) {
        generateShortLink(
            input: {
                originUrl: $originUrl
            }
        ) {
            shortLink
        }
    }
    """

    variables = {
        "originUrl": original_url
    }

    data = graphql_request(
        mutation,
        variables
    )

    result = data.get(
        "generateShortLink"
    )

    if not result:

        raise ShopeeAPIError(
            "A API não retornou o link de afiliado."
        )

    short_link = result.get(
        "shortLink"
    )

    if not short_link:

        raise ShopeeAPIError(
            "A Shopee não retornou shortLink."
        )

    return short_link


# ============================================================
# CONSULTAR PRODUTO
# ============================================================

def get_product_offer(
    product_url=None,
    item_id=None
):

    """
    Consulta ofertas de produtos.

    IMPORTANTE:

    A estrutura GraphQL disponível para a sua conta pode
    variar. Por isso, se a Shopee retornar erro de campo,
    usamos o Explorer oficial para ajustar a query.
    """

    query = """
    query ProductOffer(
        $itemId: Int
    ) {
        productOfferV2(
            itemId: $itemId
        ) {
            nodes {
                itemId
                productName
                productLink
                offerLink
                imageUrl
                priceMin
                priceMax
                commissionRate
                commission
            }
        }
    }
    """

    variables = {
        "itemId": item_id
    }

    data = graphql_request(
        query,
        variables
    )

    result = data.get(
        "productOfferV2"
    )

    if not result:

        raise ShopeeAPIError(
            "A API não retornou productOfferV2."
        )

    nodes = result.get(
        "nodes",
        []
    )

    if not nodes:

        raise ShopeeAPIError(
            "Nenhum produto encontrado."
        )

    return nodes


# ============================================================
# EXTRAIR ITEM ID
# ============================================================

def extract_item_id(url):

    """
    Tenta encontrar o item_id em URLs da Shopee.

    Exemplos possíveis:

    /product/123456/789012345
    /123456/789012345
    """

    import re

    patterns = [

        # /product/shopid/itemid
        r"/product/\d+/(\d+)",

        # /shopid/itemid
        r"/\d+/(\d+)",

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            url
        )

        if match:

            return int(
                match.group(1)
            )

    return None


# ============================================================
# TRANSFORMAR PRODUTO
# ============================================================

def normalize_product(
    product,
    original_url,
    affiliate_link
):

    title = (
        product.get("productName")
        or "Produto Shopee"
    )

    image_url = (
        product.get("imageUrl")
    )

    offer_link = (
        product.get("offerLink")
        or affiliate_link
        or original_url
    )

    price_min = product.get(
        "priceMin"
    )

    price_max = product.get(
        "priceMax"
    )

    # Se houver somente um preço.
    if price_min is not None:

        price = price_min

    elif price_max is not None:

        price = price_max

    else:

        price = None

    # Formatação simples.
    if isinstance(price, (int, float)):

        price_formatted = (
            f"R$ {price:.2f}"
            .replace(".", ",")
        )

    elif price is not None:

        price_formatted = str(price)

    else:

        price_formatted = ""

    return {

        "url": offer_link,

        "original_url": original_url,

        "affiliate_link": offer_link,

        "title": title,

        "price": price_formatted,

        "old_price": "",

        "image_url": image_url,

        "commission_rate": product.get(
            "commissionRate"
        ),

        "commission": product.get(
            "commission"
        ),

        "item_id": product.get(
            "itemId"
        ),
    }


# ============================================================
# FUNÇÃO PRINCIPAL
# ============================================================

async def get_product_from_shopee(
    url
):

    """
    Função usada pelo bot.py.

    Fluxo:

    1. Recebe link.
    2. Tenta descobrir item_id.
    3. Consulta a API.
    4. Gera link afiliado.
    5. Retorna produto padronizado.
    """

    # --------------------------------------------------------
    # 1. ITEM ID
    # --------------------------------------------------------

    item_id = extract_item_id(
        url
    )

    if not item_id:

        raise ShopeeAPIError(
            "Não consegui identificar o ID do produto "
            "nesse link da Shopee."
        )

    # --------------------------------------------------------
    # 2. CONSULTAR API
    # --------------------------------------------------------

    products = get_product_offer(
        product_url=url,
        item_id=item_id
    )

    if not products:

        raise ShopeeAPIError(
            "Nenhum produto encontrado."
        )

    product = products[0]

    # --------------------------------------------------------
    # 3. LINK DE AFILIADO
    # --------------------------------------------------------

    affiliate_link = generate_affiliate_link(
        url
    )

    # --------------------------------------------------------
    # 4. NORMALIZAR
    # --------------------------------------------------------

    return normalize_product(
        product=product,
        original_url=url,
        affiliate_link=affiliate_link
    )
