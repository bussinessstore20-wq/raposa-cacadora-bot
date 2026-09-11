import os
import json
import time
import hashlib
import requests
import re


# ============================================================
# CONFIGURAÇÃO
# ============================================================

SHOPEE_APP_ID = os.getenv("SHOPEE_APP_ID")
SHOPEE_SECRET = os.getenv("SHOPEE_SECRET")

SHOPEE_API_URL = (
    "https://open-api.affiliate.shopee.com.br/graphql"
)


# ============================================================
# ERRO
# ============================================================

class ShopeeAPIError(Exception):
    pass


# ============================================================
# VALIDAÇÃO
# ============================================================

def validate_config():

    if not SHOPEE_APP_ID:
        raise ShopeeAPIError(
            "SHOPEE_APP_ID não configurado."
        )

    if not SHOPEE_SECRET:
        raise ShopeeAPIError(
            "SHOPEE_SECRET não configurado."
        )


# ============================================================
# ASSINATURA
# ============================================================

def generate_signature(
    payload,
    timestamp
):

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
# REQUEST GRAPHQL
# ============================================================

def graphql_request(
    query,
    variables=None
):

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

    headers = {
        "Authorization": (
            f"SHA256 "
            f"Credential={SHOPEE_APP_ID},"
            f"Timestamp={timestamp},"
            f"Signature={signature}"
        ),
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
            f"Erro de conexão: {error}"
        )

    if response.status_code != 200:

        raise ShopeeAPIError(
            f"HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    try:

        result = response.json()

    except ValueError:

        raise ShopeeAPIError(
            "A Shopee retornou JSON inválido."
        )

    if result.get("errors"):

        raise ShopeeAPIError(
            json.dumps(
                result["errors"],
                ensure_ascii=False,
                indent=2
            )
        )

    return result.get(
        "data",
        {}
    )


# ============================================================
# PRODUCT OFFER V2
# ============================================================

def product_offer_v2(
    page=1,
    limit=20,
    keyword=None
):

    """
    Consulta ofertas da Shopee.

    A query utiliza os campos que você mostrou
    no retorno real da sua API.
    """

    query = """
    query ProductOfferV2(
        $page: Int
        $limit: Int
        $keyword: String
    ) {
        productOfferV2(
            page: $page
            limit: $limit
            keyword: $keyword
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

    variables = {
        "page": page,
        "limit": limit,
    }

    if keyword:

        variables["keyword"] = keyword

    return graphql_request(
        query,
        variables
    )


# ============================================================
# FORMATAR PREÇO
# ============================================================

def format_price(value):

    if value is None:
        return ""

    try:

        number = float(value)

        return (
            f"R$ {number:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

    except (
        ValueError,
        TypeError
    ):

        return str(value)


# ============================================================
# EXTRAIR ITEM ID
# ============================================================

def extract_item_id(url):

    """
    Extrai o itemId de URLs normais da Shopee.

    Exemplo:

    https://shopee.com.br/product/1573388099/52514564881

    retorna:

    52514564881
    """

    patterns = [

        r"/product/\d+/(\d+)",

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
# PROCURAR PRODUTO NA LISTA
# ============================================================

def find_product_in_results(
    products,
    original_url
):

    original_item_id = extract_item_id(
        original_url
    )

    # --------------------------------------------------------
    # PRIMEIRA TENTATIVA:
    # procurar pelo itemId
    # --------------------------------------------------------

    if original_item_id:

        for product in products:

            if int(
                product.get("itemId", 0)
            ) == original_item_id:

                return product

    # --------------------------------------------------------
    # SEGUNDA TENTATIVA:
    # procurar pelo offerLink
    # --------------------------------------------------------

    original_url_clean = (
        original_url.rstrip("/")
    )

    for product in products:

        offer_link = (
            product.get("offerLink")
            or ""
        )

        if (
            offer_link.rstrip("/")
            == original_url_clean
        ):

            return product

    return None


# ============================================================
# TRANSFORMAR PRODUTO
# ============================================================

def normalize_product(
    product,
    original_url
):

    price = product.get(
        "price"
    )

    price_min = product.get(
        "priceMin"
    )

    price_max = product.get(
        "priceMax"
    )

    discount = product.get(
        "priceDiscountRate"
    )

    # --------------------------------------------------------
    # PREÇO
    # --------------------------------------------------------

    if price:

        current_price = price

    elif price_min:

        current_price = price_min

    else:

        current_price = price_max

    # --------------------------------------------------------
    # PREÇO ANTERIOR
    #
    # A API fornece percentual de desconto.
    # Podemos calcular uma aproximação.
    # --------------------------------------------------------

    old_price = None

    if (
        current_price
        and discount
        and float(discount) > 0
    ):

        try:

            current = float(
                current_price
            )

            discount_number = float(
                discount
            )

            old = (
                current
                /
                (1 - discount_number / 100)
            )

            old_price = old

        except (
            ValueError,
            TypeError,
            ZeroDivisionError
        ):

            old_price = None

    # --------------------------------------------------------
    # LINK
    # --------------------------------------------------------

    affiliate_link = (
        product.get("offerLink")
        or original_url
    )

    # --------------------------------------------------------
    # RETORNO
    # --------------------------------------------------------

    return {

        "url": affiliate_link,

        "original_url": original_url,

        "affiliate_link": affiliate_link,

        "title": (
            product.get("productName")
            or "Produto Shopee"
        ),

        "price": format_price(
            current_price
        ),

        "old_price": (
            format_price(old_price)
            if old_price
            else ""
        ),

        "image_url": (
            product.get("imageUrl")
        ),

        "shop_name": (
            product.get("shopName")
            or ""
        ),

        "item_id": (
            product.get("itemId")
        ),

        "discount": discount,

        "commission_rate": (
            product.get(
                "commissionRate"
            )
        ),

        "commission": (
            product.get(
                "commission"
            )
        ),

        "sales": (
            product.get(
                "sales"
            )
        ),

        "rating": (
            product.get(
                "ratingStar"
            )
        ),

        "product_link": (
            product.get(
                "productLink"
            )
        ),
    }


# ============================================================
# FUNÇÃO PRINCIPAL USADA PELO BOT
# ============================================================

async def get_product_from_shopee(
    url
):

    """
    Recebe um link da Shopee.

    Retorna:

    {
        title,
        price,
        old_price,
        image_url,
        affiliate_link,
        ...
    }
    """

    # --------------------------------------------------------
    # ITEM ID
    # --------------------------------------------------------

    item_id = extract_item_id(
        url
    )

    # --------------------------------------------------------
    # CASO O LINK JÁ SEJA UMA URL NORMAL
    # --------------------------------------------------------

    if item_id:

        # Consulta produtos.
        data = product_offer_v2(
            page=1,
            limit=20
        )

        offer_data = data.get(
            "productOfferV2",
            {}
        )

        products = offer_data.get(
            "nodes",
            []
        )

        product = find_product_in_results(
            products,
            url
        )

        if product:

            return normalize_product(
                product,
                url
            )

    # --------------------------------------------------------
    # LINK CURTO
    # --------------------------------------------------------
    #
    # Exemplo:
    #
    # https://s.shopee.com.br/AAH3wuxvT6
    #
    # Aqui não temos o itemId diretamente.
    #
    # Por isso precisamos consultar a API e
    # localizar o produto pelo offerLink.
    # --------------------------------------------------------

    data = product_offer_v2(
        page=1,
        limit=20
    )

    offer_data = data.get(
        "productOfferV2",
        {}
    )

    products = offer_data.get(
        "nodes",
        []
    )

    product = find_product_in_results(
        products,
        url
    )

    if not product:

        raise ShopeeAPIError(
            "Não encontrei esse produto no retorno "
            "atual da productOfferV2."
        )

    return normalize_product(
        product,
        url
    )
