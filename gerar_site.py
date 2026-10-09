"""Gera a pagina estatica do GitHub Pages com as ultimas promocoes.

Le cache/ofertas.json (gravado pelo main.py) e escreve site/index.html
+ site/ofertas.json. Roda no GitHub Actions apos cada busca de voos.

    python gerar_site.py
"""

import json
import shutil
import urllib.parse
from datetime import datetime
from html import escape
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OFERTAS_FILE = BASE_DIR / "cache" / "ofertas.json"
MILHAS_FILE = BASE_DIR / "cache" / "milhas.json"
SITE_DIR = BASE_DIR / "site"


def _preco(valor) -> str:
    try:
        return f"R$ {int(valor):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "—"


def _data_br(iso: str) -> str:
    try:
        d = datetime.fromisoformat(iso[:10])
    except (TypeError, ValueError):
        return iso or "—"
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def _link_gyg(cidade: str) -> str:
    return f"https://www.getyourguide.com/s/?q={urllib.parse.quote(cidade)}"


def _cartao(o: dict) -> str:
    comps = []
    for c in o.get("companhias") or []:
        rotulo = escape(c) + (" 🇧🇷" if o.get("brasileira") else "")
        comps.append(f'<span class="cia">{rotulo}</span>')
    comps_html = " ".join(comps) or "—"

    ida = o.get("data_out") or ""
    volta = o.get("data_ret") or ""
    trecho = "Ida e volta" if o.get("tipo") == 1 else "Só ida"
    datas = f"{_data_br(ida)} → {_data_br(volta)}" if ida and volta else trecho

    escala = f"{o.get('escalas')} escala(s)".replace("1 escala(s)", "direto") if o.get("escalas") == 0 else f"{o.get('escalas')} escala(s)"

    return f"""
      <a class="card" href="{escape(o.get('url') or '#')}" target="_blank" rel="noopener">
        <div class="card-top">
          <div class="cidade">{escape(o.get('cidade') or o.get('iata'))}
            <span class="iata">{escape(o.get('iata') or '')}</span>
          </div>
          <div class="preco">{_preco(o.get('preco'))}</div>
        </div>
        <div class="rota">{escape(o.get('origem') or '')} → {escape(o.get('cidade') or '')}</div>
        <div class="comps">{comps_html}</div>
        <div class="meta">
          <span>{trecho}</span>
          <span>{datas}</span>
          <span>{escape(o.get('duracao') or '—')} · {escala}</span>
        </div>
        <div class="ponte">
          <span class="link">Ver no Google Flights</span>
          <span class="link">Passeios: GetYourGuide</span>
        </div>
      </a>"""


def _cartao_milha(m: dict) -> str:
    when = ""
    if m.get("publicado"):
        try:
            when = " · " + datetime.fromisoformat(m["publicado"]).strftime("%d/%m/%Y")
        except ValueError:
            when = ""
    return f"""
      <a class="mcard" href="{escape(m.get('link') or '#')}" target="_blank" rel="noopener">
        <div class="mtitulo">{escape(m.get('titulo') or '')}</div>
        <div class="mresumo">{escape(m.get('resumo') or '')}</div>
        <div class="mfonte">📰 {escape(m.get('fonte') or '')}{when}</div>
      </a>"""


def _gerar_html(ofertas: list[dict], milhas: list[dict], atualizado: str = "") -> str:
    if ofertas:
        cards = "\n".join(_cartao(o) for o in sorted(ofertas, key=lambda x: x.get("preco") or 0))
        corpo = f'<h2>💵 Melhores preços em dinheiro</h2><div class="grid">{cards}</div>'
    else:
        corpo = (
            '<div class="vazio">Ainda não há promoções em dinheiro registradas.<br>'
            "O bot busca ofertas 2× por dia (21h e 09h, horário de Brasília).</div>"
        )

    if milhas:
        mcards = "\n".join(_cartao_milha(m) for m in milhas[:24])
        bloco_milhas = f'<h2>🏅 Ofertas em milhas</h2><div class="grid">{mcards}</div>'
    else:
        bloco_milhas = ""

    rodape = (
        f"<footer>Última atualização: {atualizado} · Dados atualizados automaticamente "
        "a cada rodada do bot no GitHub Actions.</footer>"
        if atualizado else "<footer>Sistema ativo — aguardando a próxima rodada.</footer>"
    )

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Passagens em promoção saindo do Brasil</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif;
    background: linear-gradient(160deg, #0f172a, #1e293b);
    color: #e2e8f0; min-height: 100vh; padding: 32px 16px 64px;
  }}
  header {{ max-width: 1000px; margin: 0 auto 28px; }}
  h1 {{ font-size: 26px; color: #fff; }}
  h1 span {{ color: #38bdf8; }}
  p.sub {{ color: #94a3b8; margin-top: 6px; }}
  h2 {{ max-width: 1000px; margin: 28px auto 14px; font-size: 18px; color: #fff; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; max-width: 1000px; margin: 0 auto; }}
  a.card, a.mcard {{ text-decoration: none; color: inherit; background: #1e293b; border: 1px solid #334155;
    border-radius: 14px; padding: 16px; display: flex; flex-direction: column; gap: 10px;
    transition: transform .12s ease, border-color .12s ease; }}
  a.card:hover, a.mcard:hover {{ transform: translateY(-3px); border-color: #38bdf8; }}
  .card-top {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }}
  .cidade {{ font-size: 18px; font-weight: 700; color: #fff; }}
  .iata {{ font-size: 11px; color: #cbd5e1; background: #0f172a; border: 1px solid #334155;
    border-radius: 6px; padding: 2px 6px; margin-left: 6px; vertical-align: middle; }}
  .preco {{ font-size: 20px; font-weight: 800; color: #4ade80; white-space: nowrap; }}
  .rota {{ color: #94a3b8; font-size: 13px; }}
  .comps {{ display: flex; flex-wrap: wrap; gap: 4px; }}
  .cia {{ font-size: 12px; background: #0f172a; border: 1px solid #334155; border-radius: 999px;
    padding: 2px 10px; color: #cbd5e1; }}
  .meta {{ display: flex; flex-wrap: wrap; gap: 6px 12px; font-size: 12px; color: #94a3b8; }}
  .ponte {{ display: flex; justify-content: space-between; gap: 8px; border-top: 1px solid #334155; padding-top: 10px; }}
  .link {{ color: #38bdf8; font-size: 12px; font-weight: 600; }}
  .mtitulo {{ font-size: 15px; font-weight: 700; color: #fff; }}
  .mresumo {{ font-size: 13px; color: #94a3b8; }}
  .mfonte {{ font-size: 12px; color: #64748b; border-top: 1px solid #334155; padding-top: 8px; }}
  footer {{ max-width: 1000px; margin: 32px auto 0; color: #64748b; font-size: 12px; text-align: center; }}
  .vazio {{ text-align: center; color: #94a3b8; padding: 60px 16px; background: #1e293b;
    border-radius: 14px; border: 1px dashed #334155; max-width: 1000px; margin: 0 auto; }}
</style>
</head>
<body>
  <header>
    <h1>✈️ Passagens em <span>promoção</span> saindo do Brasil</h1>
    <p class="sub">Ofertas reais monitoradas automaticamente de Guarulhos, Congonhas e parceiros —
    em <b>dinheiro</b> (preço ≤ média da rota, via Google Flights) e em <b>milhas</b>
    (curadoria pública de Smiles, LATAM Pass e Azul).</p>
  </header>
  {corpo}
  {bloco_milhas}
  {rodape}
</body>
</html>"""


def main() -> int:
    if not OFERTAS_FILE.exists():
        print("Sem cache/ofertas.json ainda; gerando página vazia.")
        ofertas: list[dict] = []
        atualizado = ""
    else:
        try:
            ofertas = json.loads(OFERTAS_FILE.read_text(encoding="utf-8"))
            atualizado = ofertas[0].get("enviado_em", "") if ofertas else ""
        except (ValueError, OSError) as e:
            print(f"Erro ao ler {OFERTAS_FILE}: {e}")
            ofertas = []
            atualizado = ""

    if isinstance(ofertas, dict):
        ofertas = [o for o in ofertas.values() if isinstance(o, dict)]
    if not isinstance(ofertas, list):
        ofertas = []

    milhas: list[dict] = []
    if MILHAS_FILE.exists():
        try:
            dados = json.loads(MILHAS_FILE.read_text(encoding="utf-8"))
            if isinstance(dados, list):
                milhas = dados
        except (ValueError, OSError):
            milhas = []

    SITE_DIR.mkdir(parents=True, exist_ok=True)
    (SITE_DIR / "index.html").write_text(
        _gerar_html(ofertas, milhas, atualizado), encoding="utf-8")
    if ofertas:
        shutil.copyfile(OFERTAS_FILE, SITE_DIR / "ofertas.json")
    if milhas:
        shutil.copyfile(MILHAS_FILE, SITE_DIR / "milhas.json")

    print(f"Pagina gerada: {len(ofertas)} ofertas + {len(milhas)} milhas em {SITE_DIR / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())