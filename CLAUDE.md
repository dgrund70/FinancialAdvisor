# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Een Flask + SQLite beleggingsdashboard (portefeuillebeheer, rendement/risico-analyse, AI-advies). **UI en code-commentaar zijn in het Nederlands** — houd dat aan.

## Commands

```bash
# Dependencies (in een venv)
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Lokaal draaien (dev-server). init_db() draait bij import → migratie gebeurt vanzelf.
.venv/bin/python app.py                       # bindt op 127.0.0.1:5002
HOST=0.0.0.0 PORT=5002 .venv/bin/python app.py # bereikbaar op het LAN

# Productie-draaimodel (ook op de Pi): ÉÉN worker verplicht (zie architectuur).
.venv/bin/gunicorn -w 1 --threads 4 --bind 0.0.0.0:5002 app:app

# Tests
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest tests/test_projectie.py::test_average_cost_and_realized   # één test

# Data ophalen (handmatig; normaal via knoppen of systemd-timers)
.venv/bin/python fetch_prices.py        # koersen (yfinance + CoinGecko) → data/prijzen.json
.venv/bin/python fetch_news.py          # nieuws → tabel nieuws_cache
.venv/bin/python fetch_fundamentals.py  # waardering/groei per holding → tabel fundamentals
.venv/bin/python fetch_historie.py      # dagelijkse EUR-koershistorie → tabel koers_historie
.venv/bin/python fetch_benchmark.py     # benchmark-koersen → tabel benchmark_punten
```

24/7 op een Raspberry Pi: zie **`deploy/README.md`** (systemd-service + timers).

## Architectuur (het grote plaatje)

**Eén-bestand Flask-app** (`app.py`: routes + view-logica) op SQLAlchemy-modellen (`models.py`), Jinja-templates (`templates/`) en SQLite (`data/app.db`). `init_db()` draait op module-niveau bij import, dus `gunicorn app:app` triggert de migratie automatisch.

**Migraties zijn additief, geen Alembic.** `_sync_schema()` voegt bij startup ontbrekende kolommen toe (`ALTER ADD`), `db.create_all()` maakt nieuwe tabellen. Kolommen hernoemen/verwijderen kan SQLite niet → handmatig. Nieuwe modellen/kolommen werken dus vanzelf bij de volgende start.

**Het grootboek is de bron van waarheid; `Positie` is een afgeleide cache.** `Transactie` (koop/verkoop/dividend/storting/opname/kosten/correctie) → `projectie.py` herberekent per `(account, ticker)` de `Positie` (aantal, gemiddelde GAK, `gerealiseerde_winst`) met **average-cost**. **Muteer `Positie.aantal/aankoopprijs/gerealiseerde_winst` nooit direct** — schrijf een transactie en roep `na_transactie_wijziging()` / `herbereken_positie()` aan vóór commit (zie de positie- en transactie-routes). Reden dat `Positie` als cache blijft bestaan: `fetch_*.py` en `advies_generator.py` lezen de `posities`-tabel via ruwe SQL.

**Data-fetch-patroon.** Elke externe databron heeft een eigen cachetabel, gevuld door een standalone `fetch_*.py` (ruwe `sqlite3`, `import yfinance` lui binnen functies — draait als los proces, niet via Flask). De app leest die tabellen bij het renderen via SQLAlchemy. Twee triggers: in-app achtergrondtaken (de `data-taak`-knoppen) of systemd-timers op de Pi.

**Achtergrondtaken zijn in-process threads.** "Ververs"-acties draaien een subprocess in een daemon-thread (`_start_taak`, status in het in-memory dict `_taken`), gepolld via `/taken/<id>/status` + gedeelde JS in `base.html` (formulieren met `data-taak`). Omdat de status in het geheugen zit: **draai precies één web-worker** (`gunicorn -w 1`); meerdere workers breken de voortgangs-poll.

**Analytics: pure math in `helpers.py`, DB-assemblage in `app.py`.** `helpers.py` bevat `xirr`, `twr`, `volatiliteit`, `max_drawdown`, `beta`, `sharpe`, `correlatie`, `benchmark_eindwaarde`, `naar_eur` (allemaal puur en getest). `app.py` zet de data klaar: `_portefeuille_risico` (volatiliteit/drawdown/beta/Sharpe/correlatie van het *huidige* mandje), `_portefeuille_twr` (tijd-gewogen rendement uit de *historisch gereconstrueerde* NAV: alleen holdings gewaardeerd, aan-/verkopen als externe flows), `_benchmark_vergelijkingen` (deposit-matched). TWR/risico gebruiken `koers_historie`; XIRR/benchmark gebruiken externe cashflows (storting/opname).

**Advies-pijplijn.** De `advies_genereer`-route start `advies_generator.py` als subprocess (Claude API, model `claude-sonnet-4-6`). Dat bouwt context uit portefeuille + macro + gecacht nieuws + fundamentals + volglijst, vraagt een gestructureerd advies, parst de `RISICO:`/`TIPS:`-kopregels, valideert dat tip-tickers een echte koers hebben, en slaat `Advies` + `Aanbeveling` op. **Dit is het enige pad dat tokens/geld kost**; alle geplande fetches zijn gratis (Yahoo/CoinGecko).

**Blootstelling.** Single-user, **bewust géén authenticatie**. Bind-host via env `HOST` (default `127.0.0.1`; `0.0.0.0` op het vertrouwde thuis-LAN op de Pi). `debug` staat uit tenzij `FLASK_DEBUG`. `SECRET_KEY`: env > `data/secret_key` > automatisch gegenereerd. `data/` (DB, caches, secret) staat in `.gitignore`.

## Conventies & valkuilen

- **Geld naar EUR** loopt altijd via `helpers.naar_eur(bedrag, valuta, wisselkoersen)`; `wisselkoersen` komt uit `prijzen.json`. USD-koershistorie wordt in `fetch_historie.py` met historische FX naar EUR gezet.
- **Begrippen-uitleg (ⓘ):** centrale `BEGRIPPEN`-dict in `app.py` (context-processor) + macro `uitleg('sleutel')` in `templates/macros.html` (`{% from "macros.html" import uitleg with context %}`); popovers worden in `base.html` geactiveerd.
- **Tests** dekken de pure reken-logica (projectie/average-cost, XIRR, TWR, Sharpe, correlatie, benchmark) — draai ze na wijzigingen aan `helpers.py`/`projectie.py`.
- Bij een nieuwe externe-data-feature: volg het data-fetch-patroon (cachetabel in `models.py` → `fetch_x.py` → achtergrond-route met `data-taak` of een systemd-timer in `deploy/`).
