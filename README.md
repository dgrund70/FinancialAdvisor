# FinancialAdvisor — Beleggingsdashboard

Een Flask-webapp voor persoonlijk portefeuillebeheer met live koersen, meerdere
gebruikers, en een AI-adviesmodule op basis van de Claude API.

> ⚠️ Dit project geeft **geen** officieel financieel advies. Het is een persoonlijk
> hulpmiddel voor inzicht en educatie.

## Functies

- **Portefeuille-overzicht** — totale waarde, dagrendement en totaalrendement per
  positie en per broker-account.
- **Live koersen** — aandelen/ETF's via [yfinance](https://pypi.org/project/yfinance/)
  (Yahoo Finance) en crypto via de [CoinGecko](https://www.coingecko.com/)-API,
  inclusief automatische omrekening naar euro.
- **Meerdere gebruikers** — wissel via de account-switcher in de navbar; elke
  gebruiker heeft eigen broker-accounts, posities, tags en adviezen.
- **Posities beheren** — toevoegen, **bewerken** en verwijderen, met handmatige of
  live koers en vrije tags.
- **Ticker-autocomplete** — typ een naam of ticker (bijv. `VWCE` of `Vanguard`) en
  kies uit live suggesties; liquide EUR-beurzen staan bovenaan.
- **AI-adviesmodule** — genereert per gebruiker (of per tag) een gestructureerd
  beleggingsadvies via de Claude API, met risicoscore, koop-tips en
  rendement-tracking van eerdere tips.
- **Nieuws per positie** — recent nieuws per ticker via yfinance, gebruikt als
  context voor het advies.

## Vereisten

- Python 3.10 of nieuwer
- Internettoegang (voor koersen, nieuws en ticker-zoeken)
- Optioneel: een **Anthropic API-key** voor de adviesmodule

## Installatie

```bash
git clone https://github.com/dgrund70/FinancialAdvisor.git
cd FinancialAdvisor

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Starten

```bash
source .venv/bin/activate
python3 app.py
```

Open daarna **http://localhost:5002** in je browser.

Bij de eerste start maakt de app automatisch de database (`data/app.db`) aan, met
een standaardgebruiker *"Mijn account"* en een broker-account *"Mijn portefeuille"*.
Voeg een broker-account en posities toe om te beginnen.

### AI-advies inschakelen

De adviesmodule vereist een Anthropic API-key. Zet die als omgevingsvariabele
**voordat** je de app start:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python3 app.py
```

Zonder key werkt het dashboard gewoon; alleen de knop *"Genereer advies"* is
uitgeschakeld.

> Tip: zet ook een eigen `SECRET_KEY` voor de Flask-sessies:
> `export SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")`

## Koersen & nieuws verversen

Koersen en nieuws kun je in de app verversen met de knoppen, of los via scripts:

```bash
# Koersen ophalen (schrijft naar data/prijzen.json)
python3 fetch_prices.py            # eenmalig
python3 fetch_prices.py --loop     # elke 15 minuten herhalen

# Nieuws ophalen (schrijft naar de database)
python3 fetch_news.py              # eenmalig
python3 fetch_news.py --loop       # elk uur herhalen
```

`start_prijzen.sh` is een hulpscript dat de venv aanmaakt, dependencies installeert
en `fetch_prices.py` start:

```bash
chmod +x start_prijzen.sh
./start_prijzen.sh           # eenmalig
./start_prijzen.sh --loop    # elke 15 min
```

### Tickers gebruiken

Gebruik de **Yahoo Finance-ticker** inclusief beurssuffix, bijvoorbeeld
`VWCE.AS` (Amsterdam) of `XNAS.DE` (XETRA). De ticker-autocomplete helpt je de
juiste te vinden. Voor crypto gebruik je `BASE-QUOTE`, bijvoorbeeld `BTC-EUR` of
`ETH-EUR`.

## Projectstructuur

```
app.py                 Flask-app: routes, dashboard, posities, advies, API
models.py              SQLAlchemy-datamodel (gebruiker → account → positie → tag)
helpers.py             Gedeelde hulpfuncties (o.a. valuta-omrekening)
fetch_prices.py        Koersen ophalen (yfinance + CoinGecko)
fetch_news.py          Nieuws ophalen (yfinance)
advies_generator.py    Beleggingsadvies genereren via de Claude API
templates/             Jinja2-templates (Bootstrap 5)
static/                CSS
data/                  Database en koersen (lokaal, niet in git)
requirements.txt       Python-dependencies
```

## Datamodel

```
Gebruiker ──< BrokerAccount ──< Positie >──< Tag
   └──< Advies ──< Aanbeveling
NieuwsArtikel  (cache, per ticker)
```

Een gebruiker heeft meerdere broker-accounts; elk account heeft posities; posities
kunnen meerdere tags hebben (many-to-many). Adviezen en aanbevelingen horen bij een
gebruiker (optioneel gefilterd op tag).

## Privacy & data

De map `data/` (database, koersen, backups) staat in `.gitignore` en wordt **niet**
meegecommit — je portefeuille blijft lokaal. De `ANTHROPIC_API_KEY` wordt alleen
uit de omgeving gelezen en nooit opgeslagen.

## Technische stack

Flask · Flask-SQLAlchemy · Flask-WTF (CSRF) · SQLite · yfinance · CoinGecko ·
Anthropic Claude API · Bootstrap 5 · Chart.js
