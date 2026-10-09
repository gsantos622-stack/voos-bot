"""Rover de milhas: varre feeds publicos de sites que curam ofertas de milhas,
filtra emissao de passagens (Smiles/LATAM Pass/Azul/etc.) e envia no Telegram.

Fontes: feeds RSS oficiais de curadores publicos (sem scraping de login/ToS).
Nao usa cota do SerpAPI.

    python milhas.py            -> busca, envia as novas e grava cache/milhas.json
    python milhas.py --listar   -> so mostra o que achou (nao envia)
"""

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from xml.etree import ElementTree

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache"
MILHAS_FILE = CACHE_DIR / "milhas.json"
ARQUIVO_ENV = BASE_DIR / ".env"
QTD_MILHAS_SITE = 40

FEEDS = {
    "Passageiro de Primeira": "https://passageirodeprimeira.com/feed/",
    "Melhores Destinos": "https://melhoresdestinos.com.br/feed/",
}

# Precisa citar programa/milhas E ter contexto de emissao de passagem.
RE_MILHAS = re.compile(r"(milhas|pontos|smiles|latam\s*pass|azul\s*fidelidade|avios)", re.I)
RE_EMISSAO = re.compile(
    r"(disponibilidad|emitir|emiss[aã]o|emita|resgat|resgate|voe\b|voo\b|trecho|"
    r"a partir de|milhas \+ taxas|pontos \+ taxas|ida e volta)", re.I)
# Ruido que nao e emissao de passagem.
RE_EXCLUIR = re.compile(
    r"(hotel|hospedagem|cashback|aluguel|alug[áa]vel de carro|iphone|curso|"
    r"universidade das milhas|assinantes do clube|compra de pontos|"
    r"cart[ãa]o|\bades[ãa]o\b|conta nomad|revolut|shell box|seguro viagem|"
    r"^como\b)", re.I)
RE_TAGS = re.compile(r"<[^>]+>")


def _env(chave: str, padrao: str = "") -> str:
    return os.environ.get(chave, padrao).strip()


def _carregar_dotenv() -> None:
    if not ARQUIVO_ENV.exists():
        return
    for linha in ARQUIVO_ENV.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        os.environ.setdefault(chave.strip(), valor.strip())


def _texto(bruto: str) -> str:
    return re.sub(r"\s+", " ", unescape(RE_TAGS.sub(" ", bruto or ""))).strip()


def _data_br(dt: datetime | None) -> str:
    return dt.strftime("%d/%m/%Y %H:%M") if dt else ""


def _ler_cache() -> list[dict]:
    if MILHAS_FILE.exists():
        try:
            dados = json.loads(MILHAS_FILE.read_text(encoding="utf-8"))
            if isinstance(dados, list):
                return dados
        except (ValueError, OSError):
            pass
    return []


def _salvar_cache(itens: list[dict]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    MILHAS_FILE.write_text(
        json.dumps(itens[:QTD_MILHAS_SITE], ensure_ascii=False, indent=2), encoding="utf-8")


def _baixar(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "voos-bot/1.0 (+github)"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        return resp.read()


def _coletar_feed(nome: str, url: str) -> list[dict]:
    try:
        raiz = ElementTree.fromstring(_baixar(url))
    except Exception as e:  # noqa: BLE001
        print(f"[aviso] feed '{nome}' falhou: {type(e).__name__}: {e}")
        return []

    achados = []
    for item in raiz.iter("item"):
        titulo = _texto((item.findtext("title") or ""))
        link = (item.findtext("link") or "").strip()
        descricao = _texto(item.findtext("description") or "")
        bruto_data = item.findtext("pubDate") or ""
        try:
            pub = parsedate_to_datetime(bruto_data)
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pub = None

        alvo = f"{titulo} {descricao}"
        if not (RE_MILHAS.search(alvo) and RE_EMISSAO.search(alvo)):
            continue
        if RE_EXCLUIR.search(titulo):
            continue
        if not link:
            continue

        achados.append({
            "link": link,
            "titulo": titulo,
            "resumo": descricao[:260],
            "fonte": nome,
            "publicado": pub.isoformat() if pub else "",
        })
    return achados


def coletar() -> list[dict]:
    horas = int(_env("MILHAS_HORAS", "30") or "30")
    limite = datetime.now(timezone.utc) - timedelta(hours=horas)

    itens: list[dict] = []
    vistos: set[str] = set()
    for nome, url in FEEDS.items():
        for it in _coletar_feed(nome, url):
            if it["link"] in vistos:
                continue
            if it["publicado"]:
                try:
                    if datetime.fromisoformat(it["publicado"]) < limite:
                        continue
                except ValueError:
                    pass
            vistos.add(it["link"])
            itens.append(it)
    itens.sort(key=lambda i: i.get("publicado", ""), reverse=True)
    return itens


def _montar_mensagem(item: dict) -> str:
    pub = ""
    if item.get("publicado"):
        try:
            pub = " · " + _data_br(datetime.fromisoformat(item["publicado"]))
        except ValueError:
            pass
    return (
        f"🏅 <b>OFERTA EM MILHAS</b>\n"
        f"<b>{item['titulo']}</b>\n\n"
        f"{item['resumo']}\n\n"
        f"🔗 <a href=\"{item['link']}\">Ver detalhes</a>\n"
        f"📰 {item['fonte']}{pub}"
    )


def _enviar_telegram(texto: str) -> bool:
    token = _env("TELEGRAM_TOKEN")
    chat = _env("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("[aviso] TELEGRAM_TOKEN/CHAT_ID ausentes; nao envio.")
        return False
    import urllib.parse
    dados = urllib.parse.urlencode({
        "chat_id": chat, "text": texto, "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=dados)
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status == 200
    except Exception as e:  # noqa: BLE001
        print(f"[erro] envio falhou: {type(e).__name__}: {e}")
        return False


def main() -> int:
    _carregar_dotenv()
    novos = coletar()
    cache = _ler_cache()
    ja_vistos = {i["link"] for i in cache}

    ineditos = [i for i in novos if i["link"] not in ja_vistos]
    print(f"Curtidas no feed: {len(novos)} | novas: {len(ineditos)}")

    if "--listar" in sys.argv:
        for i in ineditos:
            print(f"- [{i['fonte']}] {i['titulo']}")
            print(f"    {i['link']}")
        return 0

    limite = int(_env("MAX_MILHAS", "5") or "5")
    enviados = 0
    for item in ineditos[:limite]:
        if _enviar_telegram(_montar_mensagem(item)):
            enviados += 1

    todas = ineditos + cache
    _salvar_cache(todas)
    print(f"Enviadas {enviados} ofertas de milhas (de {len(ineditos)} novas).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
