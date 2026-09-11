import os
import json
import time
import hashlib
import logging
from pathlib import Path

import requests
from telegram import Bot


# ============================================================
# RAPOSA CAÇADORA
# Shopee Affiliate -> Telegram
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


# Quantidade de produtos publicados por ciclo
PRODUTOS_POR_CICLO = int(
    os.getenv("PRODUTOS_POR_CICLO", "5")
)

# Intervalo entre ciclos
INTERVALO_MINUTOS = int(
    os.getenv("INTERVALO_MINUTOS", "30")
)

# Filtros
DESCONTO_MINIMO = float(
    os.getenv("DESCONTO_MINIMO", "30")
)

AVALIACAO_MINIMA = float(
    os.getenv("AVALIACAO_MINIMA", "4.5")
)

COMISSAO_MINIMA = float(
    os.getenv("COMISSAO_MINIMA", "3")
)

VENDAS_MINIMAS = int(
    os.getenv("VENDAS_MINIMAS", "0")
)


# Arquivo usado para evitar produtos repetidos
ARQUIVO_ENVIADOS = Path(
    "produtos_enviados.json"
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
            "Variáveis de ambiente faltando: "
            + ", ".join(faltando)
        )


# ============================================================
# CONVERSÃO NUMÉRICA
# ============================================================

def numero(valor):

    try:
        return float(valor)

    except (TypeError, ValueError):

        return 0.0


# ============================================================
# DINHEIRO
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

def shopee_graphql(
    query
):

    timestamp = int(
        time.time()
    )

    body = {
        "query": query
    }

    # IMPORTANTE:
    # A assinatura precisa usar exatamente
    # o mesmo payload enviado no POST.
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
            "SHA256 "
            f"Credential={SHOPEE_APP_ID}, "
            f"Timestamp={timestamp}, "
            f"Signature={assinatura}"
        ),
    }

    response = requests.post(
        SHOPEE_API_URL,
        data=payload.encode("utf-8"),
        headers=headers,
        timeout=40
    )

    logger.info(
        "Shopee HTTP %s",
        response.status_code
    )

    if response.status_code != 200:

        raise RuntimeError(
            "Erro HTTP da Shopee: "
            f"{response.status_code}\n"
            f"{response.text[:1000]}"
        )

    try:

        resultado = response.json()

    except ValueError:

        raise RuntimeError(
            "A Shopee não retornou JSON:\n"
            + response.text[:1000]
        )

    if resultado.get("errors"):

        raise RuntimeError(
            "Erro da API Shopee:\n"
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
        limit: 20,
        sortType: 5
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

    logger.info(
        "Consultando ofertas da Shopee..."
    )

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
# PREÇO ORIGINAL ESTIMADO
# ============================================================

def calcular_preco_original(
    preco,
    desconto
):

    preco = numero(preco)
    desconto = numero(desconto)

    if preco <= 0:
        return None

    if desconto <= 0:
        return None

    if desconto >= 100:
        return None

    return preco / (
        1 - desconto / 100
    )


# ============================================================
# FILTRAR OFERTAS
# ============================================================

def filtrar_ofertas(
    ofertas
):

    aprovadas = []

    for produto in ofertas:

        nome = produto.get(
            "productName"
        )

        offer_link = produto.get(
            "offerLink"
        )

        image_url = produto.get(
            "imageUrl"
        )

        if not nome:
            continue

        if not offer_link:
            continue

        if not image_url:
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

        vendas = int(
            numero(
                produto.get("sales")
            )
        )

        # ----------------------------------------------------
        # FILTROS
        # ----------------------------------------------------

        if preco <= 0:
            continue

        if desconto < DESCONTO_MINIMO:
            continue

        if avaliacao < AVALIACAO_MINIMA:
            continue

        if comissao < COMISSAO_MINIMA:
            continue

        if vendas < VENDAS_MINIMAS:
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
            produto.get(
                "sales"
            )
        )

        comissao = numero(
            produto.get(
                "commission"
            )
        )

        # Peso maior para desconto,
        # avaliação e vendas.
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

    return aprovadas


# ============================================================
# ARQUIVO DE PRODUTOS ENVIADOS
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
            "Não consegui ler produtos_enviados.json: %s",
            erro
        )

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
# TEXTO DA PUBLICAÇÃO
# ============================================================

def montar_mensagem(
    produto
):

    nome = produto.get(
        "productName",
        "Produto"
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

    loja = produto.get(
        "shopName",
        "Shopee"
    )

    comissao = numero(
        produto.get(
            "commission"
        )
    )

    offer_link = produto.get(
        "offerLink"
    )

    preco_original = (
        calcular_preco_original(
            preco,
            desconto
        )
    )

    if preco_original:

        preco_linha = (
            f"❌ De: ~{dinheiro(preco_original)}~\n"
            f"✅ Por: *{dinheiro(preco)}*"
        )

    else:

        preco_linha = (
            f"✅ Por: *{dinheiro(preco)}*"
        )

    # Telegram MarkdownV2 é mais chato com
    # caracteres especiais. Para evitar problemas,
    # usamos HTML.
    mensagem = (
        "🦊 <b>RAPOSA CAÇADORA</b>\n\n"

        f"🔥 <b>{nome}</b>\n\n"

        f"{preco_linha}\n\n"

        f"🏷️ <b>{desconto:.0f}% OFF</b>\n"
        f"⭐ Avaliação: <b>{avaliacao:.1f}</b>\n"
        f"🛍️ Loja: {loja}\n"
        f"📦 Vendas: {vendas:,}\n"
        f"💰 Comissão estimada: "
        f"<b>{dinheiro(comissao)}</b>\n\n"

        f"🛒 <b>COMPRE AQUI:</b>\n"
        f"{offer_link}\n\n"

        "⚡ <i>Aproveite enquanto estiver disponível!</i>"
    )

    return mensagem


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
# TEXTO SEGURO
# ============================================================

def montar_mensagem_segura(
    produto
):

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

        preco_linha = (
            f"❌ De: <s>{dinheiro(preco_original)}</s>\n"
            f"✅ Por: <b>{dinheiro(preco)}</b>"
        )

    else:

        preco_linha = (
            f"✅ Por: <b>{dinheiro(preco)}</b>"
        )

    mensagem = (
        "🦊 <b>RAPOSA CAÇADORA</b>\n\n"

        f"🔥 <b>{nome}</b>\n\n"

        f"{preco_linha}\n\n"

        f"🏷️ <b>{desconto:.0f}% OFF</b>\n"
        f"⭐ Avaliação: <b>{avaliacao:.1f}</b>\n"
        f"🛍️ Loja: {loja}\n"
        f"📦 Vendas: <b>{vendas:,}</b>\n"
        f"💰 Comissão: <b>{dinheiro(comissao)}</b>\n\n"

        "🛒 <b>COMPRE AQUI:</b>\n"
        f"{offer_link}\n\n"

        "⚡ <i>Aproveite enquanto estiver disponível!</i>"
    )

    return mensagem


# ============================================================
# PUBLICAR NO TELEGRAM
# ============================================================

def publicar_produto(
    bot,
    produto
):

    mensagem = montar_mensagem_segura(
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
            parse_mode="HTML",
            disable_web_page_preview=False
        )


# ============================================================
# ID ÚNICO
# ============================================================

def obter_id_produto(
    produto
):

    item_id = produto.get(
        "itemId"
    )

    shop_id = produto.get(
        "shopId"
    )

    if item_id:

        return f"{shop_id}:{item_id}"

    return produto.get(
        "offerLink"
    )


# ============================================================
# EXECUTAR UM CICLO
# ============================================================

def executar_ciclo(
    bot
):

    logger.info(
        "======================================"
    )

    logger.info(
        "🦊 Iniciando novo ciclo..."
    )

    enviados = carregar_enviados()

    ofertas = buscar_ofertas()

    aprovadas = filtrar_ofertas(
        ofertas
    )

    logger.info(
        "%d ofertas aprovadas após filtros.",
        len(aprovadas)
    )

    publicados = 0

    for produto in aprovadas:

        if publicados >= PRODUTOS_POR_CICLO:
            break

        produto_id = obter_id_produto(
            produto
        )

        if not produto_id:
            continue

        if str(produto_id) in enviados:

            logger.info(
                "Já enviado: %s",
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
                str(produto_id)
            )

            salvar_enviados(
                enviados
            )

            publicados += 1

            # Evita mandar várias mensagens
            # praticamente ao mesmo tempo.
            time.sleep(3)

        except Exception as erro:

            logger.exception(
                "Erro ao publicar produto: %s",
                erro
            )

    logger.info(
        "Ciclo finalizado: %d publicados.",
        publicados
    )


# ============================================================
# LOOP PRINCIPAL
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
# START
# ============================================================

if __name__ == "__main__":

    main()
