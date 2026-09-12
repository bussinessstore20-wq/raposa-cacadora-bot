import hashlib
import json
import logging
import os
import time

import requests


logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURAÇÃO
# ============================================================

SHOPEE_GRAPHQL_URL = os.getenv(
    "SHOPEE_GRAPHQL_URL",
    "https://open-api.affiliate.shopee.com.br/graphql",
)


# Quantidade de produtos por página
SHOPEE_PAGE_LIMIT = int(
    os.getenv(
        "SHOPEE_PAGE_LIMIT",
        "20"
    )
)


# Quantidade máxima de páginas consultadas
# em cada ciclo.
#
# 5 páginas x 20 produtos = até 100 produtos.
MAX_PAGINAS = int(
    os.getenv(
        "MAX_PAGINAS",
        "5"
    )
)


# ============================================================
# QUERY GRAPHQL
# ============================================================

PRODUCT_OFFER_QUERY = """
query ProductOffers(
    $page: Int,
    $limit: Int
) {
    productOfferV2(
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
# PAYLOAD
# ============================================================

def _criar_payload(
    page: int,
    limit: int
):

    payload = {
        "query": PRODUCT_OFFER_QUERY,
        "variables": {
            "page": page,
            "limit": limit
        }
    }

    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(
            ",",
            ":"
        )
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

    assinatura = hashlib.sha256(
        texto_assinatura.encode(
            "utf-8"
        )
    ).hexdigest()

    return assinatura


# ============================================================
# CONSULTAR UMA PÁGINA
# ============================================================

def _consultar_pagina(
    page: int,
    limit: int
):

    app_id, secret = (
        _obter_credenciais()
    )

    payload = _criar_payload(
        page=page,
        limit=limit
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
        "Shopee página %d respondeu HTTP %d",
        page,
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

    # --------------------------------------------------------
    # ERROS GRAPHQL
    # --------------------------------------------------------

    if resultado.get("errors"):

        erros = resultado.get(
            "errors"
        )

        logger.error(
            "Erro GraphQL da Shopee: %s",
            json.dumps(
                erros,
                ensure_ascii=False
            )
        )

        raise ShopeeAPIError(
            "A API da Shopee retornou "
            "erros GraphQL: "
            + json.dumps(
                erros,
                ensure_ascii=False
            )
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

    page_info = (
        product_offer.get(
            "pageInfo"
        )
        or {}
    )

    return (
        nodes,
        page_info
    )


# ============================================================
# BUSCAR OFERTAS COM PAGINAÇÃO
# ============================================================

def buscar_ofertas():

    logger.info(
        "Iniciando busca paginada "
        "de ofertas da Shopee..."
    )

    todas_ofertas = []

    pagina = 1

    while pagina <= MAX_PAGINAS:

        logger.info(
            "Consultando página %d/%d "
            "(limite: %d produtos)...",
            pagina,
            MAX_PAGINAS,
            SHOPEE_PAGE_LIMIT
        )

        try:

            ofertas, page_info = (
                _consultar_pagina(
                    page=pagina,
                    limit=SHOPEE_PAGE_LIMIT
                )
            )

        except ShopeeAPIError:

            # Se a primeira página falhar,
            # não temos ofertas para trabalhar.
            #
            # Se páginas posteriores falharem,
            # também interrompemos a consulta
            # para evitar trabalhar com uma
            # paginação incompleta sem avisar.
            raise

        quantidade = len(
            ofertas
        )

        logger.info(
            "Página %d: %d ofertas recebidas.",
            pagina,
            quantidade
        )

        todas_ofertas.extend(
            ofertas
        )

        has_next_page = bool(
            page_info.get(
                "hasNextPage",
                False
            )
        )

        pagina_atual = page_info.get(
            "page"
        )

        logger.info(
            "PageInfo: page=%s, "
            "hasNextPage=%s",
            pagina_atual,
            has_next_page
        )

        # ----------------------------------------------------
        # NÃO HÁ MAIS PÁGINAS
        # ----------------------------------------------------

        if not has_next_page:

            logger.info(
                "Não existem mais páginas "
                "disponíveis."
            )

            break

        # ----------------------------------------------------
        # PÁGINA VAZIA
        # ----------------------------------------------------

        if quantidade == 0:

            logger.info(
                "Página %d vazia. "
                "Encerrando paginação.",
                pagina
            )

            break

        pagina += 1

    # ========================================================
    # REMOVER DUPLICADOS
    # ========================================================

    ofertas_unicas = []

    ids_vistos = set()

    for oferta in todas_ofertas:

        item_id = oferta.get(
            "itemId"
        )

        if item_id is None:

            # Mantém produtos sem itemId.
            # O bot.py decidirá depois
            # se pode publicá-los.
            ofertas_unicas.append(
                oferta
            )

            continue

        item_id = str(
            item_id
        )

        if item_id in ids_vistos:

            continue

        ids_vistos.add(
            item_id
        )

        ofertas_unicas.append(
            oferta
        )

    logger.info(
        "Paginação finalizada: "
        "%d ofertas recebidas em até %d páginas.",
        len(todas_ofertas),
        MAX_PAGINAS
    )

    logger.info(
        "Após remover duplicados: "
        "%d ofertas únicas.",
        len(ofertas_unicas)
    )

    return ofertas_unicas
