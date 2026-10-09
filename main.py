"""
Bot de oportunidades de passagens aereas + ideias de passeio (GetYourGuide).

Fonte de voos: Amadeus (se as chaves estiverem configuradas) com fallback
para SerpAPI (widget Google Flights) — busca de ida e volta para um aeroporto
de origem x uma lista de destinos. Compara o menor preco achado com o
preco-medio da rota (price_insights.typical_price_range) e so envia quando
a oferta esta igual ou abaixo da media. A roteacao de origem/destino roda
gratis no GitHub Actions, respeitando a cota free de cada API.

Modos de execucao:

    python main.py             -> roda uma rodada de busca (usado no GitHub Actions)
    python main.py --polling   -> fica ouvindo o Telegram (uso local/teste)
    python main.py --teste     -> envia uma mensagem de exemplo para o chat

Toda a configuracao vem de variaveis de ambiente (ou de um .env na mesma
pasta). Veja o README para a lista completa e como obter cada token.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import unicodedata
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import amadeus

from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import Update

# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache"
CACHE_FILE = CACHE_DIR / "deals.json"
OFERTAS_FILE = CACHE_DIR / "ofertas.json"
ARQUIVO_ENV = BASE_DIR / ".env"
FORMATO_CACHE = 2
QTD_OFERTAS_SITE = 60

log = logging.getLogger("voos-bot")


def _env(chave: str, padrao: str = "") -> str:
    return os.environ.get(chave, padrao).strip()


def _env_int(chave: str, padrao: int) -> int:
    try:
        return int(os.environ.get(chave, str(padrao)))
    except (TypeError, ValueError):
        return padrao


def _env_bool(chave: str, padrao: bool) -> bool:
    valor = _env(chave)
    if not valor:
        return padrao
    return valor.lower() in ("1", "true", "sim", "yes", "on")


def _carregar_dotenv() -> None:
    """Le o .env sem depender de python-dotenv (formato CHAVE=VALOR)."""
    if not ARQUIVO_ENV.exists():
        return
    for linha in ARQUIVO_ENV.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        os.environ.setdefault(chave.strip(), valor.strip().strip("'\""))


def _token_obrigatorio(chave: str) -> str:
    valor = _env(chave)
    if not valor:
        raise RuntimeError(
            f"{chave} nao configurado. Defina no .env ou como Secret do GitHub."
        )
    return valor


# ---------------------------------------------------------------------------
# Dicionario de cidades: ideias de roteiro + slugs do GetYourGuide.
# `gyg` = (slug, id) para montar https://www.getyourguide.com/SLUG-lID/
# Quando o id nao e confiavel, deixamos so o link de busca (?q=).
# ---------------------------------------------------------------------------

ROTEIROS = {
    "MIAMI": {
        "cidade": "Miami",
        "gyg": ("miami", 169),
        "ideias": [
            "Little Havana e Wynwood Walls (arte de rua e cultura cubana)",
            "Passeio de lancha pelas mansões e pelo bairro Art Deco",
            "Tour pelos Everglades com barco a ar e jacarés",
        ],
    },
    "FORT LAUDERDALE": {
        "cidade": "Fort Lauderdale",
        "gyg": ("fort-lauderdale", 52),
        "ideias": [
            "Cruzeiro pelos canais de Las Olas (a Veneza americana)",
            "Passeio de barco para ver mansões à beira-água",
            "Praias de Fort Lauderdale e região de Miami",
        ],
    },
    "ORLANDO": {
        "cidade": "Orlando",
        "gyg": ("orlando", 4226),
        "ideias": [
            "Parques temáticos (Universal, Disney, SeaWorld)",
            "Kennedy Space Center — visite a base da NASA",
            "Outlets de Orlando e passeio pela International Drive",
        ],
    },
    "NOVA YORK": {
        "cidade": "Nova York",
        "gyg": ("new-york", 59),
        "ideias": [
            "Empire State Building ou Top of the Rock ao pôr do sol",
            "Central Park, Metropolitan Museum e Broadway",
            "Estátua da Liberdade + ferry por Ellis Island",
        ],
    },
    "PARIS": {
        "cidade": "Paris",
        "gyg": ("paris", 16),
        "ideias": [
            "Torre Eiffel + cruzeiro noturno no Rio Sena",
            "Museu do Louvre e passeio por Montmartre",
            "Day trip para o Palácio de Versalhes",
        ],
    },
    "LISBOA": {
        "cidade": "Lisboa",
        "gyg": ("lisbon", 307),
        "ideias": [
            "Elétrico 28, Alfama e miradouros",
            "Sintra: Palácio Nacional da Pena + Quinta da Regaleira",
            "Noite de fado com pastel de nata",
        ],
    },
    "MADRI": {
        "cidade": "Madri",
        "gyg": ("madrid", 35),
        "ideias": [
            "Palácio Real, Plaza Mayor e Gran Vía",
            "Museu do Prado ou Reina Sofía (Guernica)",
            "Day trip para Toledo ou Segóvia",
        ],
    },
    "BARCELONA": {
        "cidade": "Barcelona",
        "gyg": ("barcelona", 14),
        "ideias": [
            "Sagrada Família e Park Güell",
            "Las Ramblas, Bairro Gótico e mercado La Boqueria",
            "Day trip para a Costa Brava ou vinícolas",
        ],
    },
    "LONDRES": {
        "cidade": "Londres",
        "gyg": ("london", 17),
        "ideias": [
            "London Eye, Big Ben e Palácio de Buckingham",
            "Torre de Londres + Tower Bridge",
            "Day trip para Stonehenge ou Oxford",
        ],
    },
    "ROMA": {
        "cidade": "Roma",
        "gyg": ("rome", 33),
        "ideias": [
            "Coliseu + Fórum Romano + Palatino",
            "Vaticano: Basílica e Capela Sistina",
            "Fontana di Trevi, Panteão e bairro Trastevere",
        ],
    },
    "MILAO": {
        "cidade": "Milão",
        "gyg": ("milan", 712),
        "ideias": [
            "Duomo e Galleria Vittorio Emanuele II",
            "\"A Última Ceia\" (Santa Maria delle Grazie)",
            "Day trip para o Lago de Como",
        ],
    },
    "BUENOS AIRES": {
        "cidade": "Buenos Aires",
        "gyg": ("buenos-aires", 34),
        "ideias": [
            "Show de tango + tour pelo bairro de La Boca",
            "Teatro Colón, Recoleta e Palermo",
            "Day trip para o Delta do Tigre",
        ],
    },
    "SANTIAGO": {
        "cidade": "Santiago",
        "gyg": ("santiago", 913),
        "ideias": [
            "Day trip para Valparaíso e Viña del Mar",
            "Observatório Sky Costanera (vista 360°)",
            "Vinícolas do Vale de Maipo com degustação",
        ],
    },
    "CUSCO": {
        "cidade": "Cusco",
        "gyg": ("cusco", 381),
        "ideias": [
            "Machu Picchu + Vale Sagrado dos Incas",
            "Sacsayhuamán e centro histórico de Cusco",
            "Trekking na Montanha Arco-Íris (Vinicunca)",
        ],
    },
    "LIMA": {
        "cidade": "Lima",
        "gyg": ("lima", 9),
        "ideias": [
            "Huaca Pucllana e Museu Larco",
            "Miraflores e a Costa Verde de paraglider",
            "Tour gastronômico (ceviche e pisco sour)",
        ],
    },
    "BOGOTA": {
        "cidade": "Bogotá",
        "gyg": ("bogota", 12),
        "ideias": [
            "La Candelaria + teleférico de Monserrate",
            "Museu do Ouro",
            "Day trip para a Catedral de Sal em Zipaquirá",
        ],
    },
    "CANCUN": {
        "cidade": "Cancún",
        "gyg": ("cancun", 859),
        "ideias": [
            "Day trip para Chichén Itzá (7 maravilhas)",
            "Isla Mujeres, Xcaret ou Holbox",
            "Tulum + cenotes com mergulho",
        ],
    },
    "PUNTA CANA": {
        "cidade": "Punta Cana",
        "gyg": ("punta-cana", 937),
        "ideias": [
            "Isla Saona ou catamarã + snorkel",
            "Buggies pela selva tropical",
            "Praias de Bávaro e Bavaro Adventure Park",
        ],
    },
    "RIO DE JANEIRO": {
        "cidade": "Rio de Janeiro",
        "ideias": [
            "Pão de Açúcar + Cristo Redentor",
            "Praias de Copacabana/Ipanema e mirante do Arpoador",
            "Lapa, Santa Teresa e trilha do Morro Dois Irmãos",
        ],
    },
    "SALVADOR": {
        "cidade": "Salvador",
        "ideias": [
            "Pelourinho + Igreja do Bonfim",
            "Praias do Farol da Barra e Itapuã",
            "Day trip para a Ilha dos Frades",
        ],
    },
    "FORTALEZA": {
        "cidade": "Fortaleza",
        "ideias": [
            "Passeio de jangada pelas falésias de Cumbuco",
            "Day trip para Jericoacoara (Jeri)",
            "Beach Park ou Beach Village",
        ],
    },
    "RECIFE": {
        "cidade": "Recife",
        "ideias": [
            "Porto de Galinhas + piscinas naturais",
            "Olinda: centro histórico e frevo",
            "Catamarã no Recife Antigo e Boa Viagem",
        ],
    },
    "NATAL": {
        "cidade": "Natal",
        "ideias": [
            "Pipa: falésias e Praia dos Golfinhos",
            "Buggy pelas dunas de Genipabu",
            "Passeio em Maracajaú (águas calmas)",
        ],
    },
    "AMESTERDA": {
        "cidade": "Amesterdã",
        "gyg": ("amsterdam", 19),
        "ideias": [
            "Cruzeiro pelos canais do Jordaan",
            "Museu Van Gogh e Rijksmuseum",
            "Day trip para os moinhos de Zaanse Schans",
        ],
    },
    "PORTO": {
        "cidade": "Porto",
        "ideias": [
            "Passeio de barco pelo Rio Douro e cais da Ribeira",
            "Vinícolas do Vale do Douro com degustação",
            "Torre dos Clérigos e Livraria Lello",
        ],
    },
    "MONTE GO BAY": {
        "cidade": "Montego Bay",
        "ideias": [
            "Rafting no rio Martha Brae",
            "Dunn's River Falls e Rose Hall",
            "Passeio de catamarã para Doctor's Cave Beach",
        ],
    },
    "CURACAO": {
        "cidade": "Curaçao",
        "ideias": [
            "Passeio pelas casas coloridas de Willemstad",
            "Klein Curaçao ou praias de Mambo Beach",
            "Playa Kenepa e parque Shete Boka",
        ],
    },
    "LOS ANGELES": {
        "cidade": "Los Angeles",
        "gyg": ("los-angeles", 75),
        "ideias": [
            "Hollywood, Walk of Fame e Griffith Observatory",
            "Universal Studios Hollywood",
            "Malibu e Santa Monica de carro/bicicleta",
        ],
    },
    "TOQUIO": {
        "cidade": "Tóquio",
        "gyg": ("tokyo", 81),
        "ideias": [
            "Shibuya Crossing, Harajuku e Asakusa",
            "Day trip para o Monte Fuji",
            "Tour gastronômico e mercados (Tsukiji/Ueno)",
        ],
    },
    "OSAKA": {
        "cidade": "Osaka",
        "gyg": ("osaka", 16),
        "ideias": [
            "Castelo de Osaka e Dotonbori",
            "Day trip para Kyoto (templos e santuários)",
            "Universal Studios Japan",
        ],
    },
    "FLORIANOPOLIS": {
        "cidade": "Florianópolis",
        "ideias": [
            "Lagoa da Conceição e ilha do Campeche",
            "Praias do Leste (Mole, Joaquina) e dunas",
            "Costa da Lagoa e trilhas da Lagoinha do Leste",
        ],
    },
}

IDEIAS_GENERICAS = [
    "Tours guiados a pé pelo centro histórico",
    "Passeio de barco e/ou transporte pela cidade",
    "Ingressos para as atrações principais (museus, mirantes, parques)",
]

# Companhia brasileira (foco do bot) -> site oficial para "comprar direto".
SITES_COMPANHIAS = {
    "gol": "https://www.voegol.com.br",
    "azul": "https://www.voeazul.com.br",
    "latam": "https://www.latamairlines.com/br",
    "latam airlines": "https://www.latamairlines.com/br",
    "tam": "https://www.latamairlines.com/br",
}

COMPANHIAS_BR = ["gol", "azul", "latam", "latam airlines", "tam"]

# IATA -> nome bonito da cidade para exibir nas mensagens.
CIDADES_POR_IATA = {
    "MIA": "Miami", "FLL": "Fort Lauderdale", "PBI": "West Palm Beach",
    "JFK": "Nova York", "LGA": "Nova York", "EWR": "Nova York",
    "MCO": "Orlando", "CDG": "Paris", "ORY": "Paris",
    "LIS": "Lisboa", "MAD": "Madri", "BCN": "Barcelona",
    "LHR": "Londres", "LGW": "Londres", "FCO": "Roma",
    "MXP": "Milão", "EZE": "Buenos Aires", "AEP": "Buenos Aires",
    "SCL": "Santiago", "CUZ": "Cusco", "LIM": "Lima",
    "BOG": "Bogotá", "CUN": "Cancún", "PUJ": "Punta Cana",
    "GIG": "Rio de Janeiro", "SSA": "Salvador", "FOR": "Fortaleza",
    "REC": "Recife", "NAT": "Natal", "CWB": "Curitiba",
    "POA": "Porto Alegre", "BSB": "Brasília", "FLN": "Florianópolis",
    "AMS": "Amesterdã", "OPO": "Porto", "MBJ": "Montego Bay",
    "CUR": "Curaçao", "LAX": "Los Angeles", "HND": "Tóquio",
    "NRT": "Tóquio", "KIX": "Osaka",
}

# Pool de destinos monitorados. A rotacao abaixo escolhe uma janela pequena
# por rodada para nao estourar a cota gratuita do SerpAPI.
# Foco: Europa | Brasil | Japao | Caribe | EUA
POOL_PADRAO = "MIA,FLL,JFK,MCO,LAX,CDG,LIS,MAD,BCN,LHR,FCO,MXP,AMS,OPO,NRT,HND,KIX,CUN,PUJ,MBJ,CUR,GIG,SSA,FOR,REC,NAT,FLN"

NOMES_ORIGENS = {
    "GRU": "Guarulhos", "CGH": "Congonhas", "VCP": "Viracopos",
    "GIG": "Rio de Janeiro", "BSB": "Brasília", "CWB": "Curitiba",
    "POA": "Porto Alegre", "FLN": "Florianópolis",
}


# ---------------------------------------------------------------------------
# Utilitarios
# ---------------------------------------------------------------------------

def _escapar(texto: str) -> str:
    """Escapa caracteres com significado especial no parse_mode HTML."""
    return (
        str(texto)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _limpar_texto(texto: str) -> str:
    """Caixa alta e sem acentos, para casar com as chaves de ROTEIROS."""
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().upper()


def _para_int(valor) -> int:
    """Converte o preco que a API manda ('R$ 1.529', 1529, '$314') em int."""
    if isinstance(valor, (int, float)):
        return int(valor)
    m = re.search(r"[\d.,]+", str(valor or ""))
    if not m:
        return 0
    txt = m.group(0).replace(".", "").replace(",", "")
    try:
        return int(txt)
    except ValueError:
        return 0


def _formatar_preco(valor: int) -> str:
    return f"R$ {valor:,.0f}".replace(",", ".")


def _formatar_duracao(segundos: int) -> str:
    segundos = int(segundos or 0)
    h, m = divmod(segundos // 60, 60)
    if h and m:
        return f"{h}h{m:02d}min"
    if h:
        return f"{h}h"
    return f"{m}min"


def _formatar_data(dia) -> str:
    """date/str '2026-11-14' -> '14 nov 2026'."""
    if isinstance(dia, date):
        d = dia
    else:
        try:
            d = datetime.strptime(str(dia), "%Y-%m-%d").date()
        except ValueError:
            return str(dia)
    meses_pt = ["", "jan", "fev", "mar", "abr", "mai", "jun",
                "jul", "ago", "set", "out", "nov", "dez"]
    return f"{d.day} {meses_pt[d.month]} {d.year}"


# ---------------------------------------------------------------------------
# Cache (dedupe) — evita bombardear o chat com a mesma oferta.
# ---------------------------------------------------------------------------

def _ler_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            dados = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if dados.get("formato") == FORMATO_CACHE:
                return dados
        except (ValueError, OSError):
            log.warning("Cache corrompido; iniciando do zero.")
    return {"formato": FORMATO_CACHE, "vistos": {}}


def _salvar_cache(cache: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache["atualizado"] = datetime.now(timezone.utc).isoformat()
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _ler_ofertas() -> list[dict]:
    if OFERTAS_FILE.exists():
        try:
            dados = json.loads(OFERTAS_FILE.read_text(encoding="utf-8"))
            if isinstance(dados, list):
                return dados
        except (ValueError, OSError):
            log.warning("ofertas.json corrompido; iniciando do zero.")
    return []


def _registrar_oferta(deal: dict) -> None:
    """Guarda a oferta enviada para a pagina do GitHub Pages (dedupe por chave)."""
    ofertas = _ler_ofertas()
    chave = _chave_deal(deal)
    ofertas = [o for o in ofertas if o.get("chave") != chave]
    ofertas.insert(0, {
        "chave": chave,
        "enviado_em": datetime.now(timezone.utc).isoformat(),
        "origem": deal["origem"],
        "iata": deal["iata"],
        "cidade": deal["cidade"],
        "data_out": deal["data_out"],
        "data_ret": deal["data_ret"],
        "tipo": deal["tipo"],
        "preco": deal["preco"],
        "preco_media": deal.get("preco_media"),
        "companhias": deal["companhias"],
        "principal": deal["principal"],
        "brasileira": deal["brasileira"],
        "duracao": deal["duracao"],
        "escalas": deal["escalas"],
        "url": deal["url"],
    })
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    OFERTAS_FILE.write_text(
        json.dumps(ofertas[:QTD_OFERTAS_SITE], ensure_ascii=False, indent=2), encoding="utf-8")


def _chave_deal(deal: dict) -> str:
    return f"{deal['origem']}_{deal['iata']}_{deal['data_out']}_{deal['data_ret']}"


def _vale_enviar(cache: dict, deal: dict) -> bool:
    """Envia se nunca viu ou se o preco caiu pelo menos DROP_MINIMO."""
    visto = (cache["vistos"] or {}).get(_chave_deal(deal))
    if visto is None:
        return True
    anterior = int(visto.get("melhor", 0) or 0)
    if anterior <= 0:
        return True
    queda = (anterior - deal["preco"]) / anterior
    return queda >= _env_float("DROP_MINIMO", 0.05)


def _env_float(chave: str, padrao: float) -> float:
    try:
        return float(os.environ.get(chave, str(padrao)))
    except (TypeError, ValueError):
        return padrao


def _registrar(cache: dict, deal: dict) -> None:
    vistos = cache.setdefault("vistos", {})
    chave = _chave_deal(deal)
    ant = vistos.get(chave, {})
    vistos[chave] = {
        "preco": deal["preco"],
        "melhor": min(int(ant.get("preco", deal["preco"])), deal["preco"]),
        "vezes": int(ant.get("vezes", 0)) + 1,
        "ultimo": datetime.now(timezone.utc).isoformat(),
    }
    if len(vistos) > 2500:
        for k in sorted(vistos, key=lambda k: vistos[k].get("ultimo", ""))[:500]:
            vistos.pop(k, None)


# ---------------------------------------------------------------------------
# SerpAPI (Google Flights)
# ---------------------------------------------------------------------------

def _sessao() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=3, backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET"]),
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def _datas_busca() -> tuple[date, date]:
    """Janela de datas: OUTBOUND_DATE/RETURN_DATE, senao MES_ALVO, senao o proximo mes."""
    out = _env("OUTBOUND_DATE")
    ret = _env("RETURN_DATE")
    mes = _env("MES_ALVO")
    if out:
        try:
            d_out = datetime.strptime(out, "%Y-%m-%d").date()
        except ValueError:
            raise RuntimeError(f"OUTBOUND_DATE invalida: {out} (use AAAA-MM-DD)")
        d_ret = datetime.strptime(ret, "%Y-%m-%d").date() if ret else d_out + timedelta(days=10)
        return d_out, d_ret
    if mes:
        try:
            ano, m = (int(x) for x in mes.split("-"))
            base = date(ano, m, 5)
        except (ValueError, IndexError):
            raise RuntimeError(f"MES_ALVO invalido: {mes} (use AAAA-MM)")
        return base, date(ano, m, 15)
    base = (datetime.now(timezone.utc).date() + timedelta(days=31)).replace(day=5)
    return base, base.replace(day=15)


def _buscar_voo(origem: str, destino: str, d_out: date, d_ret: date, sessao) -> dict:
    """Busca ida e volta (ou so ida) e devolve a resposta crua da SerpAPI."""
    tipo = _env_int("TIPO_VOO", 1)
    params = {
        "engine": "google_flights",
        "api_key": _token_obrigatorio("SERPAPI_KEY"),
        "departure_id": origem,
        "arrival_id": destino,
        "hl": "pt-br",
        "gl": "br",
        "currency": "BRL",
        "type": str(tipo),
        "stops": _env("MAX_ESCALAS", "2"),
    }
    if tipo == 2:
        params["outbound_date"] = d_out.isoformat()
    else:
        params["outbound_date"] = d_out.isoformat()
        params["return_date"] = d_ret.isoformat()
    max_preco = _env("MAX_PRECO", "8000")
    if max_preco:
        params["max_price"] = max_preco

    log.info("Buscando %s -> %s (%s ate %s)", origem, destino, d_out, d_ret)
    resposta = sessao.get("https://serpapi.com/search.json", params=params, timeout=90)
    resposta.raise_for_status()
    dados = resposta.json()
    erro = dados.get("error")
    if isinstance(erro, str):
        if "hasn't returned any results" in erro or "no results" in erro.lower():
            log.info("Google Flights sem resultados p/ %s -> %s (%s).", origem, destino, erro)
            return {}
        raise RuntimeError(erro)
    return dados


def _e_brasileira(nome: str) -> bool:
    return _limpar_texto(nome) in {_limpar_texto(c) for c in COMPANHIAS_BR}


def _extrair_deal(dados: dict, origem: str, destino: str,
                  d_out: date, d_ret: date, tipo: int) -> dict | None:
    """Monta uma oferta a partir do voo mais barato + preco medio da rota."""
    melhores = dados.get("best_flights") or []
    if not melhores:
        return None
    voo = min(melhores, key=lambda b: _para_int(b.get("price")))
    preco = _para_int(voo.get("price"))
    if preco <= 0:
        return None

    trechos = voo.get("flights") or []
    companhias = []
    for s in trechos:
        a = s.get("airline")
        if isinstance(a, dict):
            n = str(a.get("name") or "").strip()
        else:
            n = str(a or "").strip()
        if n and n not in companhias:
            companhias.append(n)
    brasileiras = [c for c in companhias if _e_brasileira(c)]
    principal = _limpar_texto(companhias[0]) if companhias else ""
    escalas = max(0, len(trechos) - 1)

    pi = dados.get("price_insights") or {}
    tipico = pi.get("typical_price_range") or []
    preco_media = _para_int(tipico[0]) if tipico else None

    cidade = CIDADES_POR_IATA.get(destino, destino)
    url = (dados.get("search_metadata") or {}).get("google_flights_url")

    return {
        "origem": origem,
        "iata": destino,
        "cidade": cidade,
        "data_out": d_out.isoformat(),
        "data_ret": d_ret.isoformat() if tipo == 1 else "",
        "tipo": tipo,
        "preco": preco,
        "preco_media": preco_media,
        "companhias": companhias,
        "principal": principal,
        "brasileira": bool(brasileiras),
        "duracao": _formatar_duracao(voo.get("total_duration")),
        "escalas": escalas,
        "url": url or _url_google_flights(d_out, d_ret, origem, destino, cidade),
    }


def _url_google_flights(d_out: date, d_ret: date, origem: str, destino: str, cidade: str) -> str:
    q = f"{origem} to {cidade} on {d_out.isoformat()}"
    if d_ret:
        q += f" return {d_ret.isoformat()}"
    return ("https://www.google.com/travel/flights?" +
            urllib.parse.urlencode({"hl": "pt-br", "gl": "br", "curr": "BRL", "q": q}))


def _url_site() -> str:
    """URL do GitHub Pages com as ultimas promocoes."""
    slug = _env("USER_REPO") or "gsantos622-stack/voos-bot"
    return f"https://{slug.replace('/', '.')}/"


# ---------------------------------------------------------------------------
# Amadeus (fonte v2, 10.000 creditos/mes gratis)
# ---------------------------------------------------------------------------

def _credenciais_amadeus() -> tuple[str, str] | None:
    cid = _env("AMADEUS_CLIENT_ID")
    sec = _env("AMADEUS_CLIENT_SECRET")
    if cid and sec:
        return cid, sec
    return None


def _fonte_voo() -> str:
    """serpapi | amadeus | ambos (amadeus primeiro, serpapi de reserva)."""
    fonte = _env("FONTE_VOO").lower()
    if fonte in ("serpapi", "amadeus", "ambos"):
        return fonte
    if _credenciais_amadeus():
        return "ambos"
    return "serpapi"


def _extrair_deal_amadeus(oferta: dict, origem: str, destino: str,
                          d_out: date, d_ret: date, tipo: int) -> dict | None:
    """Converte uma oferta do Flight Offers Search no formato unificado."""
    preco_obj = oferta.get("price") or {}
    preco = int(_para_int_float(preco_obj.get("grandTotal") or preco_obj.get("total")) + 0.5)
    if preco <= 0:
        return None

    itinerarios = oferta.get("itineraries") or []
    if not itinerarios:
        return None
    itin = itinerarios[0]
    trechos = itin.get("segments") or []
    escalas = max(0, len(trechos) - 1)

    companhias = []
    for s in trechos:
        codigo = str(s.get("carrierCode") or "").strip()
        if codigo:
            nome = amadeus.nome_companhia(codigo)
            if nome not in companhias:
                companhias.append(nome)
    if not companhias:
        for cd in (oferta.get("validatingAirlineCodes") or []):
            nome = amadeus.nome_companhia(cd)
            if nome not in companhias:
                companhias.append(nome)

    brasileiras = [c for c in companhias if _e_brasileira(c)]
    principal = _limpar_texto(companhias[0]) if companhias else ""
    duracao = amadeus._pt_para_segundos(itin.get("duration"))
    cidade = CIDADES_POR_IATA.get(destino, destino)

    return {
        "origem": origem,
        "iata": destino,
        "cidade": cidade,
        "data_out": d_out.isoformat(),
        "data_ret": d_ret.isoformat() if tipo == 1 else "",
        "tipo": tipo,
        "preco": preco,
        "preco_media": None,
        "companhias": companhias,
        "principal": principal,
        "brasileira": bool(brasileiras),
        "duracao": _formatar_duracao(duracao),
        "escalas": escalas,
        "url": _url_google_flights(d_out, d_ret, origem, destino, cidade),
    }


def _para_int_float(valor) -> float:
    if isinstance(valor, (int, float)):
        return float(valor)
    s = str(valor or "").strip()
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")   # milhar ponto + decimal virgula
    elif "," in s:
        s = s.replace(",", ".")                    # so decimal virgula
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else 0.0


def _deal_amadeus(creds, origem: str, destino: str, d_out: date, d_ret: date,
                  tipo: int) -> dict | None:
    """Busca no Amadeus e devolve a oferta unificada (ou None)."""
    max_preco = _env("MAX_PRECO")
    ofertas = amadeus.ofertas(
        creds[0], creds[1], origem, destino, d_out, d_ret, max_preco
    )
    if not ofertas:
        return None
    oferta = min(ofertas, key=lambda o: _para_int_float(
        (o.get("price") or {}).get("grandTotal") or (o.get("price") or {}).get("total")))
    return _extrair_deal_amadeus(oferta, origem, destino, d_out, d_ret, tipo)


def _obter_deal(origem: str, destino: str, d_out: date, d_ret: date,
                tipo: int, sessao) -> dict | None:
    """Dispara a fonte primaria (+ fallback) e devolve 1 oferta por destino."""
    fonte = _fonte_voo()
    creds = _credenciais_amadeus()

    if fonte in ("amadeus", "ambos") and creds:
        try:
            deal = _deal_amadeus(creds, origem, destino, d_out, d_ret, tipo)
            if deal:
                log.info("Fonte: Amadeus (%s -> %s)", origem, destino)
                return deal
        except Exception as e:  # noqa: BLE001
            log.exception("Amadeus falhou para %s -> %s", origem, destino)
            if fonte == "amadeus":
                raise
            log.warning("Fallback para SerpAPI.")

    if fonte in ("serpapi", "ambos"):
        dados = _buscar_voo(origem, destino, d_out, d_ret, sessao)
        deal = _extrair_deal(dados, origem, destino, d_out, d_ret, tipo)
        if deal:
            log.info("Fonte: SerpAPI (%s -> %s)", origem, destino)
        return deal
    return None


# ---------------------------------------------------------------------------
# GetYourGuide
# ---------------------------------------------------------------------------

def _gyg_partner() -> str:
    pid = _env("GETYOURGUIDE_PARTNER_ID")
    return f"&partner_id={pid}&psrc=partner" if pid else ""


def _resumo_roteiro(deal: dict) -> tuple[list[str], str]:
    """Devolve (ideias, link do GetYourGuide para o destino)."""
    chave_nome = _limpar_texto(deal["cidade"])
    chave_iata = deal["iata"].upper()
    entrada = ROTEIROS.get(chave_nome) or ROTEIROS.get(chave_iata)

    cidade_link = urllib.parse.quote(deal["cidade"])
    link_pesquisa = f"https://www.getyourguide.com/s/?q={cidade_link}{_gyg_partner()}"

    if not entrada:
        return IDEIAS_GENERICAS, link_pesquisa

    ideias = entrada["ideias"]
    slug, gid = entrada.get("gyg", (None, None))
    if slug and gid:
        sufixo = f"?partner_id={_env('GETYOURGUIDE_PARTNER_ID')}" if _env("GETYOURGUIDE_PARTNER_ID") else ""
        link_destino = f"https://www.getyourguide.com/{slug}-l{gid}/{sufixo}"
    else:
        link_destino = link_pesquisa
    return ideias, link_destino


# ---------------------------------------------------------------------------
# Formatação da mensagem
# ---------------------------------------------------------------------------

def _montar_mensagem(deal: dict) -> str:
    ideias, link_gyg = _resumo_roteiro(deal)
    origem_nome = NOMES_ORIGENS.get(deal["origem"], deal["origem"])

    rotulo_data = (f"{_formatar_data(deal['data_out'])} → "
                   f"{_formatar_data(deal['data_ret'])}" if deal.get("data_ret")
                   else _formatar_data(deal["data_out"]))
    rotulo_preco = "ida e volta" if deal["tipo"] == 1 else "ida"

    site = SITES_COMPANHIAS.get(deal["principal"].lower())
    compra = f'\n<a href="{site}">🛒 Comprar direto no site</a>' if site else ""

    selo = ""
    if deal.get("preco_media"):
        selo = f' · <i>média: a partir de {_formatar_preco(deal["preco_media"])}</i>'

    trecho = "direto" if deal["escalas"] == 0 else f"{deal['escalas']} escala(s)"

    partes = [
        f"✈️ <b>PROMOÇÃO DE PASSAGEM</b>\n",
        f"🛫 <b>{_escapar(origem_nome)} ({deal['origem']}) → "
        f"{_escapar(deal['cidade'])} ({deal['iata']})</b>",
        f"📅 {_escapar(rotulo_data)} · {rotulo_preco}",
    ]
    if deal.get("companhias"):
        partes.append(f"🏢 {_escapar(' · '.join(deal['companhias']))}")
    partes.append(f"⏱️ {deal.get('duracao', '—')} · {trecho}")
    partes.append(f"💵 <b>{_formatar_preco(deal['preco'])}</b>{selo}\n")
    partes.append(f"🔗 <a href=\"{deal['url']}\">Ver oferta no Google Flights</a>{compra}\n")
    partes.append("━━━━━━━━━━━━━━━")
    partes.append(f"🗺️ <b>Roteiro & Passeios — {_escapar(deal['cidade'])}</b>")
    for ideia in ideias[:3]:
        partes.append(f"• {_escapar(ideia)}")
    partes.append(f"🎟️ <a href=\"{link_gyg}\">Buscar passeios no GetYourGuide</a>\n")
    partes.append("— sent by <i>Voos BR + GetYourGuide</i>")

    return "\n".join(partes)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def _chats() -> list[str]:
    valor = _token_obrigatorio("TELEGRAM_CHAT_ID")
    return [c.strip() for c in valor.replace(";", ",").split(",") if c.strip()]


async def _enviar_deal(app, deal: dict) -> None:
    texto = _montar_mensagem(deal)
    for chat in _chats():
        try:
            await app.bot.send_message(
                chat_id=chat,
                text=texto,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            log.info("Enviada oferta %s -> %s (R$ %s) para %s",
                     deal["origem"], deal["iata"], deal["preco"], chat)
            await asyncio.sleep(1.2)
        except Exception as e:  # noqa: BLE001
            log.error("Falha ao enviar para %s: %s", chat, e)


def _notificar_erro(resumo: str) -> None:
    token = _env("TELEGRAM_TOKEN")
    chat = _env("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={
                "chat_id": chat,
                "text": f"⚠️ Falha na rodada de voos:\n{_escapar(resumo)[:700]}",
                "parse_mode": "HTML",
            },
            timeout=20,
        )
    except Exception:  # noqa: BLE001
        log.exception("Nao consegui nem notificar o erro via Telegram.")


# ---------------------------------------------------------------------------
# Rotacao de origens e destinos (respeita a cota gratis do SerpAPI)
# ---------------------------------------------------------------------------

def _indice_rodada() -> int:
    """Numero sequencial de rodadas (a cada 6h, em UTC)."""
    return int(datetime.now(timezone.utc).timestamp() // 21600)


def _origens_do_dia() -> list[str]:
    """Escolhe qual aeroporto buscar nesta rodada pela rotacao diaria.

    ORIGENS_ROTACAO lista a sequencia do dia (ex.: "GRU,GRU,CGH,GRU").
    Posicoes alem do tamanho da lista ficam de repouso (economizam a cota).
    """
    rotacao = [o.strip().upper() for o in _env("ORIGENS_ROTACAO", "GRU,GRU,CGH").split(",") if o.strip()]
    fixa = _env("ORIGEM_FIXA")
    if fixa:
        return [fixa.upper()]

    hora_utc = datetime.now(timezone.utc).hour
    posicao = hora_utc // 6
    if posicao >= len(rotacao):
        if _env_bool("FORCAR_BUSCA", False) and rotacao:
            log.info("Rodada em repouso, mas FORCAR_BUSCA esta ligado: forçando %s.", rotacao[0])
            return [rotacao[0]]
        log.info("Rodada %s da rotacao em repouso (cota gratis).", posicao)
        return []
    origem = rotacao[posicao]
    log.info("Rotacao[%s] = %s", posicao, origem)
    return [origem]


def _destinos_do_dia() -> list[str]:
    """Destinos desta rodada: DESTINOS_FOCADOS (sobrepoe) ou janela do pool."""
    focados = _env("DESTINOS_FOCADOS")
    if focados:
        lista = [d.strip().upper() for d in focados.replace(";", ",").split(",") if d.strip()]
        if not lista:
            raise RuntimeError("DESTINOS_FOCADOS vazio.")
        log.info("Destinos focados para esta rodada: %s", ", ".join(lista))
        return lista

    pool = [d.strip().upper() for d in _env("DESTINOS_POOL", POOL_PADRAO).split(",") if d.strip()]
    if not pool:
        raise RuntimeError("DESTINOS_POOL vazio. Defina a lista de destinos.")
    n_por_rodada = max(1, _env_int("DESTINOS_POR_EXECUCAO", 4))
    inicio = (_indice_rodada() * n_por_rodada) % len(pool)
    janela = []
    for i in range(n_por_rodada):
        janela.append(pool[(inicio + i) % len(pool)])
    log.info("Destinos desta rodada: %s", ", ".join(janela))
    return janela


# ---------------------------------------------------------------------------
# Fluxos de execucao
# ---------------------------------------------------------------------------

async def _processar_origem(app, origem: str, sessao, cache: dict) -> tuple[int, list[dict]]:
    """Busca os destinos da rodada para uma origem e envia o que vale a pena."""
    destinos = _destinos_do_dia()
    d_out, d_ret = _datas_busca()
    tipo = _env_int("TIPO_VOO", 1)

    so_br = _env_bool("SO_COMPANHIAS_BR", True)
    permitir_outras = _env_bool("PERMITIR_OUTRAS", True)

    achados: list[dict] = []
    for destino in destinos:
        try:
            deal = _obter_deal(origem, destino, d_out, d_ret, tipo, sessao)
            if not deal:
                log.info("Sem oferta para %s -> %s.", origem, destino)
                continue
            # Filtro de companhia brasileira.
            if so_br and not deal["brasileira"] and not permitir_outras:
                log.info("%s -> %s ignorado: sem cia brasileira.", origem, destino)
                continue
            # Promocao = preco igual ou abaixo da media da rota (se conhecida).
            if deal["preco_media"] and deal["preco"] > deal["preco_media"] and _env_bool("SO_PROMOCAO", True):
                log.info("%s -> %s: R$ %s acima da media R$ %s; ignorado.",
                         origem, destino, deal["preco"], deal["preco_media"])
                continue
            achados.append(deal)
        except Exception as e:  # noqa: BLE001
            log.exception("Erro na busca %s -> %s", origem, destino)
            _notificar_erro(f"{origem} -> {destino}: {type(e).__name__}: {e}")

    novos = [d for d in achados if _vale_enviar(cache, d)]
    top = sorted(novos, key=lambda d: d["preco"])[:_env_int("MAX_MENSAGENS", 5)]
    for deal in top:
        await _enviar_deal(app, deal)
        _registrar(cache, deal)
        _registrar_oferta(deal)

    log.info("%s: %d destinos avaliados, %d envios.", origem, len(achados), len(top))
    return len(top), achados


async def _rodar_uma_vez() -> None:
    """Modo principal: uma rodada de busca e envio (GitHub Actions)."""
    app = Application.builder().token(_token_obrigatorio("TELEGRAM_TOKEN")).build()
    await app.initialize()
    cache = _ler_cache()
    origens = _origens_do_dia()
    sessao = _sessao()
    enviadas = 0
    total = 0
    try:
        for origem in origens:
            n, achados = await _processar_origem(app, origem, sessao, cache)
            enviadas += n
            total += len(achados)
        _salvar_cache(cache)
        if _env_bool("ENVIAR_RESUMO", True) and (enviadas or total):
            resumo = (
                f"📊 <b>Rodada concluída</b> — {enviadas} oferta(s) enviada(s)"
                f" (origem: {', '.join(origens) or 'repouso'})."
                f"\n\n🌐 Ultimas promocoes: {_url_site()}"
            )
            for chat in _chats():
                try:
                    await app.bot.send_message(chat_id=chat, text=resumo, parse_mode="HTML")
                except Exception:  # noqa: BLE001
                    pass
    finally:
        await app.shutdown()
    log.info("Fim da rodada: %d envios de %d ofertas totais.", enviadas, total)


# --- Comandos do modo polling ----------------------------------------------

async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    texto = (
        "👋 Ola! Eu monitoro promoções de passagens saindo do Brasil e mando "
        "ideias de passeio (GetYourGuide) para os destinos.\n\n"
        "Comandos:\n"
        "/status — configuração e últimos números\n"
        "/ultimas — últimas ofertas já enviadas\n"
        "/rodada — roda uma busca agora (gasta a cota da API!)"
    )
    await context.bot.send_message(chat_id=update.effective_chat.id, text=texto)


async def _cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cache = _ler_cache()
    texto = (
        "<b>Configuração</b>\n"
        f"Fonte de voos: <code>{_fonte_voo()}</code>\n"
        f"Origem (rotacao): <code>{_env('ORIGENS_ROTACAO', 'GRU,GRU,CGH')}</code>\n"
        f"Destinos por rodada: <code>{_env('DESTINOS_POR_EXECUCAO', '4')}</code>\n"
        f"Pool de destinos: <code>{_env('DESTINOS_POOL', POOL_PADRAO)[:60]}</code>\n"
        f"Preço máximo: <code>R$ {_env('MAX_PRECO', '8000')}</code>\n"
        f"Ofertas no cache: <code>{len(cache.get('vistos') or {})}</code>\n"
        f"Última atualização: <code>{cache.get('atualizado', '—')}</code>"
    )
    await context.bot.send_message(chat_id=update.effective_chat.id, text=texto, parse_mode="HTML")


async def _cmd_ultimas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cache = _ler_cache()
    vistos = cache.get("vistos") or {}
    sair = []
    for chave, info in sorted(vistos.items(), key=lambda kv: kv[1].get("ultimo", ""), reverse=True)[:8]:
        sair.append(f"• <code>{chave}</code> — {_formatar_preco(info.get('preco', 0))} (x{info.get('vezes', 1)})")
    texto = "\n".join(sair) if sair else "Nenhuma oferta no cache ainda."
    await context.bot.send_message(chat_id=update.effective_chat.id, text=texto, parse_mode="HTML")


async def _cmd_rodada(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Rodando uma busca agora (isso gasta cota da API)…")
    sessao = _sessao()
    cache = _ler_cache()
    app = context.application
    total = 0
    for origem in _origens_do_dia():
        try:
            n, _ = await _processar_origem(app, origem, sessao, cache)
            total += n
        except Exception as e:  # noqa: BLE001
            await update.message.reply_text(f"⚠️ Erro em {origem}: {e}")
    _salvar_cache(cache)
    await update.message.reply_text(f"Rodada concluída: {total} oferta(s) enviada(s).")


def _rodar_polling() -> None:
    app = Application.builder().token(_token_obrigatorio("TELEGRAM_TOKEN")).build()
    app.add_handler(CommandHandler("start", _cmd_start))
    app.add_handler(CommandHandler("status", _cmd_status))
    app.add_handler(CommandHandler("ultimas", _cmd_ultimas))
    app.add_handler(CommandHandler("rodada", _cmd_rodada))
    log.info("Bot no modo polling (Ctrl+C para parar).")
    app.run_polling(allowed_updates=["message"])


# --- Teste -----------------------------------------------------------------

async def _enviar_teste() -> None:
    exemplo = {
        "origem": "GRU",
        "iata": "MIA",
        "cidade": "Miami",
        "data_out": (datetime.now(timezone.utc).date() + timedelta(days=31)).replace(day=5).isoformat(),
        "data_ret": (datetime.now(timezone.utc).date() + timedelta(days=31)).replace(day=15).isoformat(),
        "tipo": 1,
        "preco": 2350,
        "preco_media": 4000,
        "companhias": ["LATAM"],
        "principal": "LATAM",
        "brasileira": True,
        "duracao": "9h00min",
        "escalas": 0,
        "url": "https://www.google.com/travel/flights?hl=pt-br&gl=br&curr=BRL&q=GRU%20to%20Miami",
    }
    app = Application.builder().token(_token_obrigatorio("TELEGRAM_TOKEN")).build()
    await app.initialize()
    try:
        await _enviar_deal(app, exemplo)
    finally:
        await app.shutdown()
    log.info("Mensagem de teste enviada.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bot de promoções de voos BR")
    parser.add_argument("--polling", action="store_true", help="modo long-polling (fica ouvindo o Telegram)")
    parser.add_argument("--teste", action="store_true", help="envia uma mensagem de exemplo")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    _carregar_dotenv()

    try:
        if args.teste:
            asyncio.run(_enviar_teste())
        elif args.polling:
            _rodar_polling()
        else:
            asyncio.run(_rodar_uma_vez())
    except KeyboardInterrupt:
        log.info("Encerrado pelo usuário.")
    except Exception as e:  # noqa: BLE001
        log.exception("Falha na execução")
        try:
            _notificar_erro(f"{type(e).__name__}: {e}")
        except Exception:  # noqa: BLE001
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()