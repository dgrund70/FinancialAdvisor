# FinancialAdvisor — Beleggingsdashboard

Een Flask-webapp voor persoonlijk portefeuillebeheer: een transactie-grootboek met
realized/ongerealiseerd rendement, geld- én tijd-gewogen rendement, benchmark- en
risico-analyse, en een AI-adviesmodule op basis van de Claude API.

> ⚠️ Dit project geeft **geen** officieel financieel advies. Het is een persoonlijk
> hulpmiddel voor inzicht en educatie.

## Functies

- **Portefeuille-overzicht** — totale waarde, dagrendement, ongerealiseerd én
  gerealiseerd rendement, en cash-saldo, per positie en per broker-account.
- **Transactie-grootboek** — holdings worden afgeleid uit koop/verkoop/dividend/
  storting/opname-transacties (average-cost). Zo zie je je werkelijke kostprijs (GAK),
  gerealiseerde winst en cash. Posities snel toevoegen kan ook (legt een eerste koop vast).
- **Rendement: XIRR & TWR** — geld-gewogen rendement (XIRR, houdt rekening met de
  timing van je stortingen) en tijd-gewogen rendement (TWR, de eerlijke maatstaf om je
  met een index te vergelijken).
- **Benchmarkvergelijking** — "had ik beter de index kunnen kopen?": je werkelijke
  stortingen gesimuleerd in MSCI World / Nasdaq 100 (deposit-matched), met het verschil
  in procentpunten.
- **Analyse-pagina** — allocatie (sector, regio, type, valuta), concentratie (top-5,
  effectief aantal posities, waarschuwingen), en risico: volatiliteit, max drawdown,
  Sharpe-ratio, bèta vs. de wereldindex, en een correlatiematrix.
- **Live koersen** — aandelen/ETF's via [yfinance](https://pypi.org/project/yfinance/)
  en crypto via de [CoinGecko](https://www.coingecko.com/)-API, met automatische
  omrekening naar euro.
- **AI-adviesmodule** — genereert per gebruiker (of per tag) een gestructureerd advies
  via de Claude API, **onderbouwd met echte fundamentals** (waardering, groei, marges,
  analisten-koersdoel), macro-indicatoren en recent nieuws — met risicoscore, koop-tips
  en rendement-tracking van eerdere tips.
- **Volglijst** — kandidaat-tickers die het advies met echte fundamentals beoordeelt
  als ideeën voor nieuwe posities.
- **Uitleg voor beginners** — bij elke "moeilijke" waarde een ⓘ-icoon met een korte
  uitleg in gewone taal.
- **Meerdere gebruikers**, **tags**, en **ticker-autocomplete** (typ `VWCE` of `Vanguard`).

## Vereisten

- Python 3.10 of nieuwer
- Internettoegang (koersen, nieuws, fundamentals, ticker-zoeken)
- Optioneel: een **Anthropic API-key** voor de adviesmodule

## Installatie

```bash
git clone https://github.com/dgrund70/FinancialAdvisor.git
cd FinancialAdvisor
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Starten

```bash
# Ontwikkeling (Flask dev-server)
python3 app.py                         # http://localhost:5002

# Productie-draaimodel (zoals op de Pi) — let op: ÉÉN worker
gunicorn -w 1 --threads 4 --bind 0.0.0.0:5002 app:app
```

Bij de eerste start maakt de app `data/app.db` aan met een standaardgebruiker en
broker-account. Eén worker is bewust: de "Ververs"-achtergrondtaken houden hun status
in het geheugen bij.

## Omgevingsvariabelen

| Variabele | Doel |
|---|---|
| `ANTHROPIC_API_KEY` | Vereist voor "Genereer advies". Zonder key werkt de rest gewoon. |
| `SECRET_KEY` | Flask-sessiesleutel. Leeg laten = de app genereert en bewaart automatisch een sterke sleutel in `data/secret_key`. |
| `HOST` / `PORT` | Bind-adres/poort (default `127.0.0.1` / `5002`). Zet `HOST=0.0.0.0` om op het LAN bereikbaar te zijn. |
| `FLASK_DEBUG` | `1` zet de debugger aan (alleen voor ontwikkeling; standaard uit). |

Kopieer `.env.example` naar `.env` en vul je waarden in (`.env` staat in `.gitignore`).

## Data verversen

Koersen, nieuws, fundamentals en historie kun je in de app verversen met de knoppen,
of los via de scripts:

```bash
python3 fetch_prices.py        # koersen → data/prijzen.json   (--loop voor elke 15 min)
python3 fetch_news.py          # nieuws → database             (--loop voor elk uur)
python3 fetch_fundamentals.py  # fundamentals per holding + volglijst
python3 fetch_historie.py      # dagelijkse EUR-koershistorie (voor TWR/risico)
python3 fetch_benchmark.py     # benchmark-koersen (voor de vergelijking)
```

Op een server kun je deze automatisch op schema laten draaien.

## 24/7 op een Raspberry Pi

Voor altijd-aan draaien op een Pi (bereikbaar op je thuisnetwerk, met geplande
updates via systemd-timers): zie **[`deploy/README.md`](deploy/README.md)** — bevat de
systemd-units, het runbook en hoe het netjes naast andere projecten draait.

> **Beveiliging:** de app heeft geen login. Op het LAN vertrouw je het thuisnetwerk;
> stel poort 5002 **niet** open naar internet.

## Tickers gebruiken

Gebruik de **Yahoo Finance-ticker** inclusief beurssuffix, bv. `VWCE.AS` (Amsterdam)
of `XNAS.DE` (XETRA); de autocomplete helpt. Voor crypto: `BASE-QUOTE`, bv. `BTC-EUR`.

## Projectstructuur

```
app.py                 Flask-app: routes, dashboard, analyse, advies, achtergrondtaken
models.py              SQLAlchemy-datamodel + begrippenlijst voor de uitleg-icoontjes
projectie.py           Leidt Positie-cache af uit het Transactie-grootboek (average-cost)
helpers.py             Pure berekeningen: valuta, XIRR, TWR, volatiliteit, Sharpe, bèta, correlatie
fetch_prices.py        Koersen (yfinance + CoinGecko)
fetch_news.py          Nieuws (yfinance)
fetch_fundamentals.py  Fundamentals per ticker (yfinance)
fetch_historie.py      Dagelijkse EUR-koershistorie (yfinance + FX)
fetch_benchmark.py     Benchmark-koershistorie (yfinance)
advies_generator.py    Beleggingsadvies via de Claude API
templates/             Jinja2-templates (Bootstrap 5, Chart.js); macros.html = uitleg-icoon
deploy/                systemd-units + runbook voor de Raspberry Pi
tests/                 pytest voor de reken-logica
data/                  Database + caches (lokaal, niet in git)
```

## Datamodel

```
Gebruiker ──< BrokerAccount ──< Positie >──< Tag        (Positie = cache, afgeleid uit Transactie)
   │                        └──< Transactie               (grootboek = bron van waarheid)
   ├──< Advies ──< Aanbeveling
   └──< Volglijst
Caches: NieuwsArtikel · Fundamental · KoersHistorie · BenchmarkPunt
```

Een gebruiker heeft broker-accounts met transacties; daaruit worden de posities
(aantal + gemiddelde aankoopprijs + gerealiseerde winst) afgeleid. Adviezen,
aanbevelingen en de volglijst horen bij een gebruiker.

## Privacy & data

`data/` (database, koersen, caches, backups, sessiesleutel) staat in `.gitignore` en
wordt **niet** meegecommit — je portefeuille blijft lokaal. De `ANTHROPIC_API_KEY`
wordt alleen uit de omgeving gelezen en nooit opgeslagen.

## Tests

```bash
python3 -m pytest -q
```

Dekt de pure reken-logica (average-cost-projectie, XIRR, TWR, Sharpe, correlatie,
benchmark).

## Technische stack

Flask · Flask-SQLAlchemy · Flask-WTF (CSRF) · gunicorn · SQLite · yfinance · CoinGecko ·
Anthropic Claude API · Bootstrap 5 · Chart.js · systemd (Pi-deployment)
