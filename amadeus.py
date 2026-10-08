"""
Provedor Amadeus Self-Service (10.000 creditos/mes gratis).

Usa duas chamadas:
  - Cheapest Date Search (/v1/shopping/flight-dates) opcional p/ achar o
    dia mais barato do mes.
  - Flight Offers Search (/v2/shopping/flight-offers) para pegar o voo real
    (companhia, duracao, preco) e o link de busca.

Sem pagamento. Precisa apenas de:
  - AMADEUS_CLIENT_ID  (API Key)
  - AMADEUS_CLIENT_SECRET

Cadastro e chaves: https://developers.amadeus.com
"""

import logging
import re

import requests

log = logging.getLogger("voos-bot")

TOKEN_URL = "https://test.api.amadeus.com/v1/security/oauth2/token"
API_BASE = "https://test.api.amadeus.com/v1"


def obter_token(client_id: str, client_secret: str) -> str:
    """Token OAuth2 do Amadeus (vale ~30 min; uma por rodada e suficiente)."""
    resp = requests.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        timeout=30,
    )
    resp.raise_for_status()
    dados = resp.json()
    token = dados.get("access_token")
    if not token:
        raise RuntimeError("Amadeus: resposta sem access_token.")
    return token


def _chamar(url: str, token: str, params: dict) -> dict:
    resp = requests.get(
        f"{API_BASE}{url}",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    resp.raise_for_status()
    dados = resp.json()
    if dados.get("errors"):
        detalhe = dados["errors"][0].get("detail", "erro desconhecido")
        raise RuntimeError(f"Amadeus: {detalhe}")
    return dados


def datas_mais_baratas(client_id: str, client_secret: str,
                       origem: str, destino: str, depart: str, ret) -> list[dict]:
    """Melhores precos por dia (ida e volta) no periodo do mes solicitado."""
    token = obter_token(client_id, client_secret)
    base = depart.strftime("%Y-%m-%d")
    datos = _chamar(
        "/shopping/flight-dates",
        token,
        {
            "origin": origem,
            "destination": destino,
            "departureDate": base,
            "returnDate": ret.strftime("%Y-%m-%d"),
            "nonStop": "false",
            "currencyCode": "BRL",
        },
    )
    itens = datos.get("data") or []
    saida = []
    for item in itens:
        p = item.get("price") or {}
        total = _flutuar(p.get("total"))
        tipico = _flutuar(p.get("typical"))
        if not total:
            continue
        saida.append({
            "data": item.get("departureDate"),
            "preco": int(round(total)),
            "preco_tipico": int(round(tipico)) if tipico else None,
        })
    return saida


def ofertas(client_id: str, client_secret: str,
            origem: str, destino: str, depart, ret,
            max_preco: str = "") -> list[dict]:
    """Flight Offers Search: lista de ofertas ida e volta filtrando por maxPrice."""
    token = obter_token(client_id, client_secret)
    params = {
        "originLocationCode": origem,
        "destinationLocationCode": destino,
        "departureDate": depart.strftime("%Y-%m-%d"),
        "returnDate": ret.strftime("%Y-%m-%d"),
        "adults": "1",
        "currencyCode": "BRL",
        "nonStop": "false",
    }
    if max_preco:
        params["maxPrice"] = max_preco
    dados = _chamar("/shopping/flight-offers", token, params)
    return dados.get("data") or []


def _flutuar(valor) -> float:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return 0.0


def _pt_para_segundos(dur: str) -> int:
    """Converte 'PT11H45M' em segundos."""
    m = re.search(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", dur or "")
    if not m:
        return 0
    h = int(m.group(1) or 0)
    mi = int(m.group(2) or 0)
    s = int(m.group(3) or 0)
    return h * 3600 + mi * 60 + s


NOMES_CIA = {
    "LA": "LATAM", "JJ": "LATAM", "TAM": "LATAM", "PZ": "LATAM",
    "G3": "GOL", "O6": "GOL", "AD": "Azul",
    "IB": "Iberia", "TP": "TAP Air Portugal", "AF": "Air France",
    "KL": "KLM", "LH": "Lufthansa", "BA": "British Airways",
    "LX": "SWISS", "OS": "Austrian", "SN": "Brussels Airlines",
    "SK": "SAS", "AY": "Finnair", "AZ": "ITA Airways",
    "CM": "Copa Airlines", "AA": "American Airlines", "UA": "United",
    "DL": "Delta", "AC": "Air Canada", "EK": "Emirates",
    "QR": "Qatar Airways", "TK": "Turkish Airlines", "NH": "ANA",
    "JL": "Japan Airlines", "AM": "AeroMéxico", "AV": "Avianca",
    "AR": "Aerolíneas Argentinas", "VY": "Vueling", "FR": "Ryanair",
    "U2": "easyJet", "W6": "Wizz Air", "UX": "Air Europa",
    "CX": "Cathay Pacific", "SQ": "Singapore Airlines",
    "KE": "Korean Air", "EK": "Emirates", "ET": "Ethiopian",
    "MS": "EgyptAir", "HA": "Hawaiian", "AS": "Alaska",
}


def nome_companhia(codigo: str) -> str:
    """Converte codigo IATA (LA, G3, AD...) em nome amigavel."""
    return NOMES_CIA.get(str(codigo).upper(), str(codigo).upper())