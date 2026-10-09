# ✈️ Voos BR + GetYourGuide — Bot de Telegram (100% grátis)

Bot que monitora **promoções de passagens aéreas saindo do Brasil**
(GOL, Azul, LATAM em destaque) e envia no Telegram junto com
**ideias de passeio e links do GetYourGuide** para cada destino.

Roda de graça no **GitHub Actions** por agendamento (cron). Nenhum servidor pago.

---

## Como funciona

```
GitHub Actions (a cada 6h)
        │
        ▼
  main.py consulta SerpAPI (engine google_flights — Google Flights real)
        │   1 origem × janela de destinos (ida e volta, datas do próximo mês)
        ▼
  Pega o voo mais barato + price_insights (preço médio da rota)
        │
        ▼
  Só envia se: preço ≤ média da rota E dentro do MAX_PRECO
               (e dedupe no cache: não repete a mesma oferta)
        │
        ▼
  Monta mensagem: oferta + links (Google Flights / site da cia) +
                  3 ideias de passeio + link GetYourGuide
        │
        ▼
  Envia para os seus chats no Telegram
```

O segredo da detecção de promoção é o campo `price_insights` do Google
Flights: ele mostra a **faixa típica de preço** da rota. Só consideramos
promoção quando o preço encontrado está **igual ou abaixo** do mínimo dessa
faixa.

**Arquivos**

| Arquivo | Papel |
|---|---|
| `main.py` | Toda a lógica (busca, filtro, formatação, envio) |
| `requirements.txt` | Dependências (`requests` e `python-telegram-bot`) |
| `.github/workflows/voos.yml` | Agendamento + segredos + cache de dedupe |
| `cache/deals.json` | Histórico das ofertas já enviadas (não repete spam) |

---

## Passo 1 — Criar o bot no Telegram (2 minutos)

1. Abra o Telegram e procure por **@BotFather**.
2. Envie `/newbot`, escolha um nome (ex.: `Promo Voos BR`) e um username
   (ex.: `promovoosbr_bot`).
3. O BotFather devolve um **token** do tipo:
   `123456789:AAHT26...`.
   Salve esse token — é a sua `TELEGRAM_TOKEN`.

### Obter o seu chat ID

- Envie uma mensagem para o seu bot (o mesmo que acabou de criar).
- Abra o **@userinfobot** no Telegram e envie `/start`.
- Ele mostra seu `id` (número puro, ex.: `123456789`).
  Guarde como `TELEGRAM_CHAT_ID`.

> Dica: para enviar para várias pessoas/grupos, separe os IDs por vírgula.

---

## Passo 2 — Obter a chave grátis da API de voos (SerpAPI)

1. Crie conta grátis em **https://serpapi.com/users/sign_up**
   (o e-mail de verificação é obrigatório).
2. No dashboard, copie o **API Key**
   (**Account → API Key** — https://serpapi.com/manage-api-key).
   É a sua `SERPAPI_KEY`.
3. Plano free: **250 buscas/mês**. As configurações padrão do bot ficam
   confortavelmente abaixo disso (conta no Passo 4).

> O que o bot usa: o widget **Google Flights** (`engine=google_flights`)
> com ida e volta. Para cada origem×destino ele retorna os melhores voos,
> o preço e o `price_insights` (média da rota) — é com isso que decidimos
> se a oferta é promoção de verdade.

> Nota: o plano grátis *Self-Service* da Amadeus foi **descontinuado**
> (17/07/2026). O código de fallback Amadeus permanece no repositório, mas
> o padrão atual é usar só o SerpAPI — que sozinho já cobre as 250 buscas/mês.

---

## Passo 3 — (Opcional) código de parceiro do GetYourGuide

Links de passeio funcionam sem nada disso. Mas, se você quiser **ganhar
comissão** (2–4%) quando alguém reservar pelo seu link:

1. Cadastre-se em **https://partner.getyourguide.com** (grátis).
2. Pegue seu **partner id** e coloque como `GETYOURGUIDE_PARTNER_ID`.
   Os links passam a levar o seu `&partner_id=...` automaticamente.

---

## Passo 4 — Criar o repositório e configurar no GitHub

1. Crie um repositório novo no GitHub (ex.: `voos-bot`).
   Pode ser **privado**.
2. Suba os arquivos desta pasta:

   ```bash
   git init
   git add .
   git commit -m "Voos BR + GetYourGuide bot"
   git branch -M main
   git remote add origin https://github.com/SEU_USUARIO/voos-bot.git
   git push -u origin main
   ```

   (Ou arraste os arquivos pela interface web — conta também.)

### Adicionar os Secrets

No GitHub, entre em **Settings → Secrets and variables → Actions** e adicione:

| Secret | Valor |
|---|---|
| `TELEGRAM_TOKEN` | Token do @BotFather |
| `TELEGRAM_CHAT_ID` | Seu chat id (ou vários separados por `,`) |
| `SERPAPI_KEY` | Sua API Key do SerpAPI |
| `GETYOURGUIDE_PARTNER_ID` | (opcional) id de parceiro GetYourGuide |
| `AMADEUS_CLIENT_ID` | (opcional) API Key do Amadeus |
| `AMADEUS_CLIENT_SECRET` | (opcional) API Secret do Amadeus |

### Ajustes (opcionais) — aba *Variables*

| Variable | Padrão | O que faz |
|---|---|---|
| `ORIGENS_ROTACAO` | `GRU,GRU,CGH` | Aeroportos de origem, sequência diária |
| `DESTINOS_POOL` | 27 cidades (EUA, Europa, Japão, Caribe, Brasil) | Lista de destinos monitorados (IATA) |
| `DESTINOS_POR_EXECUCAO` | `4` | Quantos destinos buscar a cada rodada |
| `MAX_PRECO` | `8000` | Teto de preço (ida e volta, por pessoa) |
| `MAX_MENSAGENS` | `5` | Máximo de ofertas enviadas por rodada |
| `MAX_ESCALAS` | `2` | `0`=qualquer, `1`=só direto, `2`=até 1 escala, `3`=até 2 |
| `TIPO_VOO` | `1` | `1` = ida e volta, `2` = só ida |
| `OUTBOUND_DATE` / `RETURN_DATE` | vazio | Datas fixas `AAAA-MM-DD` (sobrepõe o mês automático) |
| `MES_ALVO` | próximo mês | Mês a buscar (`AAAA-MM`) |
| `SO_PROMOCAO` | `true` | `false` = envia tudo (para teste) |
| `SO_COMPANHIAS_BR` | `true` | Enfatiza GOL/Azul/LATAM |
| `PERMITIR_OUTRAS` | `true` | Envia ofertas de cias não-brasileiras também |

### Rotação de origens e a cota grátis (importante!)

Padrão `ORIGENS_ROTACAO = GRU,GRU,CGH`:
- Rodada das 00h → busca **GRU** (Guarulhos)
- Rodada das 12h → busca **CGH** (Congonhas)
- Rodadas das 06h/18h → repouso (poupa a cota)

Com `DESTINOS_POR_EXECUCAO = 4`, temos:
**4 destinos × 2 rodadas/dia = 8 buscas/dia ≈ 240 buscas/mês** — dentro das
**250 gratuitas** do SerpAPI, com folga para rodadas manuais.

A janela de destinos é **deslizante**: cada rodada pega 4 do `DESTINOS_POOL`,
e em ~4 dias todas as 27 cidades já foram verificadas.

> Fuso: o cron do GitHub roda em **UTC**. As rodadas às 00h e 12h UTC
> correspondem a **21h e 09h no horário de Brasília**.

---

## Focar em um destino específico (Europa, Japão, etc.)

Duas formas:

**1. Disparo manual com foco (mais rápido)** — na aba **Actions → Voos Bot →
Run workflow** você vê campos opcionais antes de disparar:
- `focar_destinos`: escreva os IATA desejados. Ex.:
  - Europa: `MAD,BCN,LIS,CDG,LHR,FCO,MXP`
  - Japão: `NRT,TIY,HND,CTS`
  - Deixa em branco = segue o pool padrão.
- `origem_fixa`: ex. `GRU`, `CGH` ou `VCP`.
- `mes_alvo`: `2026-12` para planejar dezembro, etc.
- `outbound_date` / `return_date`: datas exatas de ida e volta.

**2. Foco permanente (sem digitar toda vez)** — em
**Settings → Secrets and variables → Actions → Variables**, edite
`DESTINOS_POOL` para a lista fixa que quiser (ex.: só Europa). A rotão
deslizante passa a percorrer só essa lista.

> Dupla: a mudança de `DESTINOS_POOL` vale para as próximas rodadas
> automáticas; o `focar_destinos` vale só para aquele disparo manual.

---

## Passo 5 — Testar

### Primeira execução manual

Na aba **Actions → Voos Bot → Run workflow → Run workflow**, clique para
rodar na hora. Veja os logs verdes e confira se chegaram as mensagens.

### Testar localmente (opcional)

```bash
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Crie um arquivo `.env` na pasta (não versionado):

```ini
TELEGRAM_TOKEN=123456789:AAHT26...
TELEGRAM_CHAT_ID=123456789
SERPAPI_KEY=seu_api_key
```

Envie uma mensagem de teste:

```bash
.venv\Scripts\python main.py --teste
```

Ou fique ouvindo comandos do bot:

```bash
.venv\Scripts\python main.py --polling
```

No Telegram, `/status` mostra a configuração e `/ultimas` as ofertas já vistas.

Para testar uma busca de verdade na hora, use (gasta 1–2 buscas da cota):

```bash
.venv\Scripts\python main.py   # cuidado: com ORIGEM_FIXA=GRU força a origem
```

---

## Exemplo de mensagem enviada

```
✈️ PROMOÇÃO DE PASSAGEM

🛫 Guarulhos (GRU) → Miami (MIA)
📅 5 nov → 15 nov 2026 · ida e volta
🏢 COPA · LATAM
⏱️ 10h54min · 1 escala(s)
💵 R$ 2.849 · média: a partir de R$ 2.350

🔗 Ver oferta no Google Flights
🛒 Comprar direto no site

━━━━━━━━━━━━━━━
🗺️ Roteiro & Passeios — Miami
• Little Havana e Wynwood Walls (arte de rua e cultura cubana)
• Passeio de lancha pelas mansões e pelo bairro Art Deco
• Tour pelos Everglades com barco a ar e jacarés
🎟️ Buscar passeios no GetYourGuide

— sent by Voos BR + GetYourGuide
```

---

## Página de ofertas (GitHub Pages)

A cada rodada o bot grava `cache/ofertas.json` e o workflow gera um site
estático com as últimas promoções (o link vai junto no resumo do Telegram).

1. Ative o Pages: **Settings → Pages → Source: GitHub Actions**.
2. Pronto — o site fica em
   `https://gsantos622-stack.github.io/voos-bot/`.
3. Localmente, `python gerar_site.py` gera a pasta `site/` para pré-visualizar.

---

## Solução de problemas

| Sintoma | Causa provável |
|---|---|
| `SERPAPI_KEY nao configurado` | Secret não adicionado ou nome com espaço |
| `error`: "This key is not valid" | Key digitada errada / e-mail não verificado |
| Nada chega no Telegram | `TELEGRAM_CHAT_ID` errado; envie `/start` para o bot primeiro |
| Rodada "em repouso" nos logs | Normal fora dos horários 21h/09h (Brasília) |
| "acima da media ...; ignorado" | Não é promoção — o bot trabalha como esperado |
| API retorna erro de cota | Free do SerpAPI estourou (250/mês) |

O bot também tenta te avisar **por Telegram** caso alguma rodada falhe.

---

## Roadmap

- ✅ Página web estática (GitHub Pages) com as últimas promoções.
- Hospedagem em camada grátis (Render/PythonAnywhere) para o modo polling 24/7.