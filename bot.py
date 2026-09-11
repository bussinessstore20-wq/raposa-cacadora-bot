import os
import json
import time
import hashlib
import logging
import requests

from telegram import Bot


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


# ============================================================
# CONFIGURAÇÃO DOS FILTROS
# ============================================================

# Só publica produtos com desconto igual ou maior que isso.
DESCONTO_MINIMO = float(
    os.getenv("DESCONTO_MINIMO", "30")
)

# Só publica produtos com avaliação igual ou maior que isso.
AVALIACAO_MINIMA = float(
    os.getenv("AVALIACAO_MINIMA", "4.5")
)

# Comissão mínima em reais.
COMISSAO_MINIMA = float(
    os.getenv("COMISSAO_MINIMA", "3")
)

# Quantos produtos serão publicados por ciclo.
PRODUTOS_POR_CICLO = int(
    os.getenv("PRODUTOS_POR_CICLO", "3")
)


# ============================================================
# LOG
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(
    "raposa-cacadora"
)


# ============================================================
# VALIDAÇÃO
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
            "Variáveis não configuradas: "
            + ", ".join(faltando)
        )


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
# GRAPHQL SHOPEE
# ============================================================

def shopee_request(
    query
):

    timestamp = int(
        time.time()
    )

    body = {
        "query": query
    }

    payload = json.dumps(
        body,
        separators=(",", ":"),
        ensure_ascii=False
    )

    assinatura = gerar_assinatura(
        payload,
        timestamp
    )

    headers = {
        "Authorization": (
            f"SHA256 "
            f"Credential={SHOPEE_APP_ID},"
            f"Timestamp={timestamp},"
            f"Signature={assinatura}"
        ),
        "Content-Type": "application/json",
    }

    response = requests.post(
        SHOPEE_API_URL,
        data=payload.encode("utf-8"),
        headers=headers,
        timeout=30
    )

    if response.status_code != 200:

        raise RuntimeError(
            f"Erro Shopee HTTP "
            f"{response.status_code}: "
            f"{response.text[:500]}"
        )

    resultado = response.json()

    if resultado.get("errors"):

        raise RuntimeError(
            json.dumps(
                resultado["errors"],
                ensure_ascii=False
            )
        )

    return resultado


# ============================================================
# BUSCAR OFERTAS
# ============================================================

def buscar_ofertas():

    query = """
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

    logger.info(
        "Consultando ofertas da Shopee..."
    )

    resultado = shopee_request(
        query
    )

    try:

        produtos = (
            resultado
            ["data"]
            ["productOfferV2"]
            ["nodes"]
        )

    except KeyError:

        raise RuntimeError(
            "Resposta da Shopee não possui "
            "productOfferV2.nodes."
        )

    logger.info(
        "%s produtos recebidos.",
        len(produtos)
    )

    return produtos


# ============================================================
# CONVERTER NÚMERO
# ============================================================

def numero(valor):

    try:
        return float(valor)

    except (
        ValueError,
        TypeError
    ):

        return 0.0


# ============================================================
# FILTRAR PRODUTOS
# ============================================================

def filtrar_produtos(
    produtos
):

    aprovados = []

    for produto in produtos:

        nome = produto.get(
            "productName"
        )

        if not nome:
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

        comissao = numero(
            produto.get(
                "commission"
            )
        )

        # ----------------------------------------------------
        # FILTROS
        # ----------------------------------------------------

        if desconto < DESCONTO_MINIMO:
            continue

        if avaliacao < AVALIACAO_MINIMA:
            continue

        if comissao < COMISSAO_MINIMA:
            continue

        if preco <= 0:
            continue

        if not produto.get("imageUrl"):
            continue

        if not produto.get("offerLink"):
            continue

        aprovados.append(
            produto
        )

    # --------------------------------------------------------
    # MELHORES PRIMEIRO
    # --------------------------------------------------------

    aprovados.sort(
        key=lambda produto: (
            numero(
                produto.get(
                    "commission"
                )
            ),
            numero(
                produto.get(
                    "priceDiscountRate"
                )
            ),
            numero(
                produto.get(
                    "ratingStar"
                )
            )
        ),
        reverse=True
    )

    return aprovados


# ============================================================
# FORMATAR DINHEIRO
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
# CALCULAR PREÇO ANTIGO
# ============================================================

def calcular_preco_antigo(
    preco,
    desconto
):

    preco = numero(
        preco
    )

    desconto = numero(
        desconto
    )

    if (
        preco <= 0
        or desconto <= 0
        or desconto >= 100
    ):

        return None

    return (
        preco
        /
        (1 - desconto / 100)
    )


# ============================================================
# MONTAR PUBLICAÇÃO
# ============================================================

def montar_mensagem(
    produto
):

    nome = produto.get(
        "productName",
        "Produto Shopee"
    )

    preco = numero(
        produto.get("price")
    )

    desconto = numero(
        produto.get(
            "priceDiscountRate"
        )
    )

    avaliacao = produto.get(
        "ratingStar"
    )

    vendas = produto.get(
        "sales"
    )

    loja = produto.get(
        "shopName",
        ""
    )

    comissao = numero(
        produto.get(
            "commission"
        )
    )

    preco_antigo = (
        calcular_preco_antigo(
            preco,
            desconto
        )
    )

    # --------------------------------------------------------
    # PREÇO ANTIGO
    # --------------------------------------------------------

    if preco_antigo:

        linha_preco = (
            f"❌ De: "
            f"~{dinheiro(preco_antigo)}~\n"
            f"✅ Por: "
            f"*{dinheiro(preco)}*"
        )

    else:

        linha_preco = (
            f"✅ Por: "
            f"*{dinheiro(preco)}*"
        )

    # --------------------------------------------------------
    # MENSAGEM
    # --------------------------------------------------------

    mensagem = (
        f"🦊 *RAPOSA CAÇADORA*\n\n"

        f"🔥 *{nome}*\n\n"

        f"{linha_preco}\n\n"

        f"🏷️ *{desconto:.0f}% OFF*\n"
        f"⭐ Avaliação: *{avaliacao}*\n"
        f"🛍️ Loja: {loja}\n"
        f"💰 Comissão: *{dinheiro(comissao)}*\n\n"

        f"🛒 *COMPRE AQUI:*\n"
        f"{produto.get('offerLink')}\n\n"

        f"⚡ Aproveite enquanto estiver disponível!"
    )

    return mensagem


# ============================================================
# ENVIAR TELEGRAM
# ============================================================

def enviar_produto(
    bot,
    produto
):

    mensagem = montar_mensagem(
        produto
    )

    imagem = produto.get(
        "imageUrl"
    )

    logger.info(
        "Publicando: %s",
        produto.get("productName")
    )

    if imagem:

        bot.send_photo(
            chat_id=TELEGRAM_CHAT_ID,
            photo=imagem,
            caption=mensagem,
            parse_mode="Markdown"
        )

    else:

        bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=mensagem,
            parse_mode="Markdown"
        )


# ============================================================
# IDENTIFICADOR DO PRODUTO
# ============================================================

def id_produto(
    produto
):

    return str(
        produto.get(
            "itemId"
        )
    )


# ============================================================
# CONTROLE DE DUPLICADOS
# ============================================================

ARQUIVO_ENVIADOS = (
    "produtos_enviados.json"
)


def carregar_enviados():

    if not os.path.exists(
        ARQUIVO_ENVIADOS
    ):

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

    except Exception:

        return set()


def salvar_enviados(
    enviados
):

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
# EXECUTAR
# ============================================================

def executar():

    logger.info(
        "🦊 Iniciando Raposa Caçadora..."
    )

    validar_configuracao()

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    bot = Bot(
        token=TELEGRAM_TOKEN
    )

    # --------------------------------------------------------
    # PRODUTOS JÁ ENVIADOS
    # --------------------------------------------------------

    enviados = carregar_enviados()

    # --------------------------------------------------------
    # BUSCAR SHOPEE
    # --------------------------------------------------------

    produtos = buscar_ofertas()

    # --------------------------------------------------------
    # FILTRAR
    # --------------------------------------------------------

    aprovados = filtrar_produtos(
        produtos
    )

    logger.info(
        "%s produtos aprovados.",
        len(aprovados)
    )

    publicados = 0

    # --------------------------------------------------------
    # PUBLICAR
    # --------------------------------------------------------

    for produto in aprovados:

        if publicados >= PRODUTOS_POR_CICLO:
            break

        identificador = id_produto(
            produto
        )

        if identificador in enviados:

            logger.info(
                "Produto já enviado: %s",
                identificador
            )

            continue

        try:

            enviar_produto(
                bot,
                produto
            )

            enviados.add(
                identificador
            )

            salvar_enviados(
                enviados
            )

            publicados += 1

            time.sleep(2)

        except Exception as erro:

            logger.exception(
                "Erro ao publicar produto: %s",
                erro
            )

    logger.info(
        "Ciclo terminado. "
        "%s produtos publicados.",
        publicados
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    executar()
