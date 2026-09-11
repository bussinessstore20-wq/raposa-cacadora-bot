import os
import time
import json
import hashlib
import requests


SHOPEE_APP_ID = os.getenv("SHOPEE_APP_ID")
SHOPEE_SECRET = os.getenv("SHOPEE_SECRET")

SHOPEE_API_URL = (
    "https://open-api.affiliate.shopee.com.br/graphql"
)


class ShopeeAPIError(Exception):
    pass


def generate_signature(payload: str, timestamp: int) -> str:
    """
    Gera a assinatura exigida pela Shopee.

    SHA256(
        AppId +
        Timestamp +
        Payload +
        Secret
    )
    """

    raw = (
        str(SHOPEE_APP_ID)
        + str(timestamp)
        + payload
        + str(SHOPEE_SECRET)
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def graphql_request(query: str):
    """
    Executa uma requisição GraphQL na Shopee.
    """

    if not SHOPEE_APP_ID:
        raise ShopeeAPIError(
            "SHOPEE_APP_ID não configurado."
        )

    if not SHOPEE_SECRET:
        raise ShopeeAPIError(
            "SHOPEE_SECRET não configurado."
        )

    body = {
        "query": query
    }

    # IMPORTANTE:
    # A assinatura usa o payload JSON exato.
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

    response = requests.post(
        SHOPEE_API_URL,
        data=payload.encode("utf-8"),
        headers=headers,
        timeout=30
    )

    if response.status_code != 200:

        raise ShopeeAPIError(
            f"HTTP {response.status_code}: "
            f"{response.text}"
        )

    result = response.json()

    if result.get("errors"):

        raise ShopeeAPIError(
            json.dumps(
                result["errors"],
                ensure_ascii=False
            )
        )

    return result.get("data", {})


def generate_affiliate_link(
    original_url: str,
    sub_ids=None
):
    """
    Converte uma URL da Shopee em um
    link rastreável de afiliado.
    """

    if sub_ids is None:
        sub_ids = [
            "telegram",
            "raposa-cacadora"
        ]

    sub_ids_graphql = ", ".join(
        f'"{sid}"'
        for sid in sub_ids
    )

    query = f"""
    mutation {{
        generateShortLink(
            input: {{
                originUrl: "{original_url}"
                subIds: [{sub_ids_graphql}]
            }}
        ) {{
            shortLink
        }}
    }}
    """

    data = graphql_request(query)

    result = data.get(
        "generateShortLink"
    )

    if not result:
        raise ShopeeAPIError(
            "A Shopee não retornou o shortLink."
        )

    return result["shortLink"]
