import bisect
import json
import math
import os
import re
import secrets
import subprocess
import sys
import threading
from collections import Counter
from datetime import date, datetime, time as dt_time, timedelta
from itertools import count
from pathlib import Path

# Laad .env als die bestaat (optioneel — werkt ook zonder)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

from flask import Flask, flash, redirect, render_template, request, url_for
from flask_wtf.csrf import CSRFProtect
from itsdangerous import BadData, URLSafeTimedSerializer
from markupsafe import Markup, escape
from sqlalchemy import inspect as sa_inspect, text
from sqlalchemy.orm import selectinload
from helpers import (naar_eur, xirr, benchmark_eindwaarde,
                     dagrendementen, volatiliteit, max_drawdown, beta,
                     twr, sharpe, correlatie, annualiseer)
from models import (Advies, Aanbeveling, BenchmarkPunt, BENCHMARKS,
                    BrokerAccount, Fundamental, Gebruiker, KoersHistorie,
                    NieuwsArtikel, Positie, Tag, Transactie, TRANSACTIE_TYPES,
                    Volglijst, db)
import advies_parser
from projectie import (cash_saldi, herbereken_alles, herbereken_positie,
                       na_transactie_wijziging)
from fetch_prices import is_crypto
import rabo_import

try:
    import markdown as _md
    def _render_md(text):
        # Escape '<' zodat ruwe HTML uit (mogelijk prompt-geïnjecteerde)
        # adviestekst niet als HTML draait. Markdown genereert zélf nog veilige
        # tags uit #/*/lijst-syntax; alleen door gebruiker/LLM aangeleverde
        # angle brackets worden onschadelijk gemaakt.
        veilig = (text or "").replace("<", "&lt;")
        return Markup(_md.markdown(veilig, extensions=["nl2br", "tables"]))
except ImportError:
    def _render_md(text):
        return Markup(f"<pre>{escape(text or '')}</pre>")

BASE_DIR = Path(__file__).parent
PRIJZEN  = BASE_DIR / "data" / "prijzen.json"
DB_PATH  = BASE_DIR / "data" / "app.db"

RISICOVRIJE_RENTE = 0.025   # voor de Sharpe-ratio (EUR-cash/korte rente, jaarbasis)

def _laad_secret_key():
    """Bepaal de Flask SECRET_KEY zonder ooit terug te vallen op een publiek
    bekende default (die zou sessie- en CSRF-tokens vervalsbaar maken).

    Volgorde: env-var SECRET_KEY > eerder gegenereerde sleutel in data/ >
    nieuw gegenereerde willekeurige sleutel die lokaal wordt bewaard. Zo werkt
    de app out-of-the-box, maar altijd met een unieke, geheime sleutel.
    """
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key

    key_file = BASE_DIR / "data" / "secret_key"
    if key_file.exists():
        bestaande = key_file.read_text(encoding="utf-8").strip()
        if bestaande:
            return bestaande

    key_file.parent.mkdir(exist_ok=True)
    nieuwe_key = secrets.token_hex(32)
    key_file.write_text(nieuwe_key, encoding="utf-8")
    try:
        os.chmod(key_file, 0o600)   # alleen leesbaar voor de eigenaar
    except OSError:
        pass
    return nieuwe_key


app = Flask(__name__)
app.config["SECRET_KEY"] = _laad_secret_key()
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# Ruim voor een mutatieoverzicht (tientallen KB), krap genoeg om een
# per ongeluk geuploade portefeuille-dump meteen te weigeren.
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

db.init_app(app)
csrf = CSRFProtect(app)


@app.context_processor
def inject_gebruikers():
    """Maak de volledige gebruikerslijst beschikbaar voor de navbar-switcher."""
    return {"alle_gebruikers": Gebruiker.query.order_by(Gebruiker.naam).all()}


# Plain-Nederlandse uitleg per begrip, getoond via een ⓘ-icoon (uitleg-macro).
# Eén plek om de teksten te onderhouden; elke uitleg: (1) wat het is, (2) wat je eraan hebt.
BEGRIPPEN = {
    "waarde":        {"titel": "Totale waarde",
                      "uitleg": "Wat er in totaal op je beleggingsrekening staat: de actuele marktwaarde van al je posities plus het cash-saldo. De regel eronder splitst die twee. De grafiek toont alleen het belegde deel — cash staat stil en zou de rendementslijn vertekenen."},
    "dag":           {"titel": "Dag rendement",
                      "uitleg": "De winst of het verlies van vandaag, in euro. Korte-termijn beweging — leuk om te zien, maar het zegt weinig over je echte rendement."},
    "ongerealiseerd":{"titel": "Ongerealiseerd rendement",
                      "uitleg": "De papieren winst/verlies op posities die je nóg hebt (huidige waarde min inleg). 'Ongerealiseerd' = nog niet verkocht, dus het kan nog veranderen."},
    "gerealiseerd":  {"titel": "Gerealiseerd rendement",
                      "uitleg": "De winst/verlies die je écht hebt vastgeklikt door te verkopen. Dit staat vast en beweegt niet meer mee met de koers."},
    "cash":          {"titel": "Cash (totaal)",
                      "uitleg": "Het geld op je rekening dat nog niet belegd is, omgerekend naar euro. Zo zie je hoeveel je nog kunt inleggen; staat het negatief, dan zijn er aankopen geboekt zonder dat je stortingen hebt vastgelegd."},
    "xirr":          {"titel": "Rendement (XIRR)",
                      "uitleg": "Je geld-gewogen rendement: het houdt rekening met hoeveel je inlegde én wanneer. Verschijnt zodra je stortingen hebt geboekt; '—' betekent dat die nog ontbreken."},
    "twr":           {"titel": "Rendement (TWR)",
                      "uitleg": "Tijd-gewogen rendement: hoe goed je beleggingen presteerden, los van de timing van je stortingen — de eerlijke maatstaf om je met een index te vergelijken. Op jaarbasis, dus over een korte periode kan het fors oogen."},
    "benchmark":     {"titel": "Benchmarkvergelijking",
                      "uitleg": "Wat je had gehad als je dezelfde stortingen in een index (bv. MSCI World) had gedaan. Δ positief = jij deed het beter dan de index."},
    "volatiliteit":  {"titel": "Volatiliteit",
                      "uitleg": "Hoe sterk de waarde schommelt, op jaarbasis. Hoger = grilliger; het zegt iets over risico, niet over rendement."},
    "max_drawdown":  {"titel": "Max drawdown",
                      "uitleg": "De grootste daling van top naar dal in de gemeten periode. Geeft een gevoel van hoe diep je portefeuille tijdelijk kan wegzakken."},
    "sharpe":        {"titel": "Sharpe-ratio",
                      "uitleg": "Rendement per eenheid risico (na aftrek van een veilige rente). Hoger is beter; als vuistregel is onder 1 mager en boven 1 goed."},
    "beta":          {"titel": "Bèta",
                      "uitleg": "Hoe hard je portefeuille meebeweegt met de wereldindex. 1 = beweegt gelijk op, boven 1 = beweeglijker, onder 1 = rustiger."},
    "concentratie":  {"titel": "Concentratie",
                      "uitleg": "Hoe sterk je vermogen in een paar posities zit. 'Top 5' = aandeel van je vijf grootste; 'effectief aantal' = hoe gespreid je in de praktijk bent (lager = geconcentreerder)."},
    "correlatie":    {"titel": "Correlatie",
                      "uitleg": "In hoeverre twee posities samen bewegen (1 = identiek, 0 = los van elkaar, negatief = tegengesteld). Veel hoge correlaties betekent minder spreiding dan het lijkt."},
    "gak":           {"titel": "GAK (aankoopprijs)",
                      "uitleg": "Gemiddelde aankoopkoers per stuk: wat je gemiddeld betaalde. Samen met de huidige koers bepaalt dit je winst of verlies."},
    "volglijst":     {"titel": "Volglijst",
                      "uitleg": "Tickers die je overweegt te kopen. De adviesmodule beoordeelt ze met echte fundamentals als kandidaat voor nieuwe posities."},
}


@app.context_processor
def inject_begrippen():
    """Stel de begrippenlijst beschikbaar in alle templates (voor de uitleg-macro)."""
    return {"begrippen": BEGRIPPEN}


@app.template_filter("compact_float")
def compact_float(value):
    if value is None:
        return "—"
    if not math.isfinite(value):
        return "—"
    if value == int(value):
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".").replace(".", ",")


@app.template_filter("bedrag_nl")
def bedrag_nl(value, valuta="EUR"):
    """Formatteer een bedrag met Nederlandse cijfer- en valutanotatie."""
    if value is None:
        return "—"
    # -0.0 komt uit floatafronding (een saldo dat precies nul is). "-0,00" leest
    # als een tekort dat er niet is.
    if abs(value) < 0.005:
        value = 0.0
    getal = f"{value:,.2f}".translate(str.maketrans({",": ".", ".": ","}))
    if not valuta or valuta.upper() == "EUR":
        return f"€\u00a0{getal}"
    return f"{getal}\u00a0{valuta.upper()}"


@app.template_filter("pct_nl")
def pct_nl(value, decimalen=1):
    """Formatteer een percentage met decimale komma en expliciet plusteken."""
    if value is None:
        return "—"
    teken = "+" if value > 0 else ""
    return f"{teken}{value:.{decimalen}f}".replace(".", ",") + "%"


@app.template_filter("getal_nl")
def getal_nl(value, decimalen=2):
    """Kaal getal in Nederlandse notatie, zonder valuta of procentteken."""
    if value is None:
        return "—"
    return f"{value:,.{decimalen}f}".translate(str.maketrans({",": ".", ".": ","}))


@app.template_filter("pp_nl")
def pp_nl(value, decimalen=1):
    """Formatteer een verschil in procentpunten, Nederlandse notatie."""
    if value is None:
        return "—"
    teken = "+" if value > 0 else ""
    return f"{teken}{value:.{decimalen}f}".replace(".", ",") + "\u00a0pp"


@app.template_filter("markdown")
def render_markdown(text):
    return _render_md(text)


@app.template_filter("fromjson")
def parse_json(text):
    """Voor Advies.team_details: JSON-string -> dict, of None bij leeg/ongeldig
    (enkelvoudige adviezen hebben geen team_details)."""
    if not text:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


# ── Helpers ──────────────────────────────────────────────────────

# laad_prijzen() geeft de tijd al geformatteerd terug; de ruwe ISO-string is
# nodig voor de relatieve tijd op het dashboard. Hier bewaard zodat de
# signatuur van laad_prijzen() (vijf aanroepplekken) ongemoeid blijft.
_RUWE_BIJGEWERKT = {"waarde": None}


def laad_prijzen():
    if not PRIJZEN.exists():
        return {}, None, {}
    try:
        data = json.loads(PRIJZEN.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}, None, {}
    bijgewerkt = data.get("bijgewerkt")
    _RUWE_BIJGEWERKT["waarde"] = bijgewerkt
    if bijgewerkt:
        try:
            bijgewerkt = datetime.fromisoformat(bijgewerkt).strftime("%d-%m-%Y %H:%M")
        except ValueError:
            pass
    return data.get("koersen", {}), bijgewerkt, data.get("wisselkoersen", {})


# Euronext Amsterdam: ma-vr 09:00-17:30 (lokale tijd). Fondsen met een NAV
# kennen geen beurstijd; voor de statusregel volgen we de beurs, want daar komen
# de meeste koersen vandaan.
BEURS_OPEN  = dt_time(9, 0)
BEURS_DICHT = dt_time(17, 30)
_WEEKDAGEN = ["maandag", "dinsdag", "woensdag", "donderdag",
              "vrijdag", "zaterdag", "zondag"]


def _relatieve_tijd(moment, nu):
    """'45 min geleden', '2 uur geleden', 'gisteren', '3 dagen geleden'."""
    seconden = (nu - moment).total_seconds()
    if seconden < 0:
        return "zojuist"
    minuten = int(seconden // 60)
    if minuten < 1:
        return "zojuist"
    if minuten < 60:
        return f"{minuten} min geleden"
    uren = minuten // 60
    if uren < 24:
        return f"{uren} uur geleden"
    dagen = (nu.date() - moment.date()).days
    if dagen <= 1:
        return "gisteren"
    return f"{dagen} dagen geleden"


def _markt_status(nu):
    """(open?, omschrijving) voor de koersregel op het dashboard."""
    is_werkdag = nu.weekday() < 5
    if is_werkdag and BEURS_OPEN <= nu.time() < BEURS_DICHT:
        return True, "markt open"
    # Laatste handelsdag: vandaag als de beurs al dicht is, anders terug in de tijd.
    dag = nu.date()
    if not (is_werkdag and nu.time() >= BEURS_DICHT):
        dag -= timedelta(days=1)
        while dag.weekday() >= 5:
            dag -= timedelta(days=1)
    return False, f"markt gesloten (slotkoers {_WEEKDAGEN[dag.weekday()]})"


def koersen_status(ruwe_tijd, nu=None):
    """Bouw de statusregel: absolute tijd, hoe lang geleden, en marktstatus."""
    nu = nu or datetime.now()
    open_, markt = _markt_status(nu)
    status = {"markt_open": open_, "markt": markt, "absoluut": None, "relatief": None}
    if not ruwe_tijd:
        return status
    try:
        moment = datetime.fromisoformat(ruwe_tijd)
    except (TypeError, ValueError):
        return status
    if moment.tzinfo is not None:
        moment = moment.replace(tzinfo=None)
    status["absoluut"] = moment.strftime("%d-%m-%Y %H:%M")
    status["relatief"] = _relatieve_tijd(moment, nu)
    return status


def bereken_posities(posities, koersen, wisselkoersen=None):
    """Bereken marktwaarde, winst en dagverandering per positie.

    Geeft (rows, totaal_waarde, totaal_kosten, totaal_dag, totaal_gerealiseerd)
    terug. Gesloten posities (aantal 0) verschijnen niet in `rows`, maar hun
    gerealiseerde winst telt wél mee in `totaal_gerealiseerd` (in EUR).
    """
    wisselkoersen = wisselkoersen or {}
    rows = []
    totaal_waarde = totaal_kosten = totaal_dag = 0.0
    totaal_gerealiseerd = 0.0
    has_prices = False

    for pos in posities:
        pos_valuta_real = getattr(pos, "valuta", "EUR") or "EUR"
        gereal = getattr(pos, "gerealiseerde_winst", 0.0) or 0.0
        totaal_gerealiseerd += naar_eur(gereal, pos_valuta_real, wisselkoersen) or 0.0

        # Gesloten positie (volledig verkocht): geen holding-rij tonen.
        if abs(pos.aantal or 0.0) < 1e-9:
            continue

        # Handmatige koers heeft voorrang boven live koers
        if pos.koers_type == "handmatig" and pos.handmatige_koers is not None:
            k      = {"koers": pos.handmatige_koers, "dag": 0.0, "dag_pct": 0.0,
                      "valuta": getattr(pos, "valuta", "EUR") or "EUR"}
        else:
            k = koersen.get(pos.ticker, {})

        koers  = k.get("koers")
        dag    = k.get("dag", 0.0)
        valuta = k.get("valuta", "EUR")

        koers_eur = naar_eur(koers, valuta, wisselkoersen)
        if koers_eur is not None:
            waarde    = pos.aantal * koers_eur
            dag_winst = pos.aantal * (naar_eur(dag, valuta, wisselkoersen) or 0.0)
        else:
            waarde    = None
            dag_winst = 0.0

        # Handelsvaluta van de positie (EUR voor ETFs, USD voor US-aandelen)
        pos_valuta = getattr(pos, "valuta", "EUR") or "EUR"

        # Rendement % in native currency (USD vs USD, EUR vs EUR)
        kosten_native  = pos.aantal * pos.aankoopprijs
        waarde_native  = pos.aantal * koers if koers is not None else None
        winst_native   = (waarde_native - kosten_native) if waarde_native is not None else None
        winst_pct      = (winst_native / kosten_native * 100) if winst_native is not None and kosten_native else None

        # Kosten en winst in EUR (voor portfolio-totalen en EUR W/V)
        gak_eur   = naar_eur(pos.aankoopprijs, pos_valuta, wisselkoersen) or pos.aankoopprijs
        kosten    = pos.aantal * gak_eur
        winst     = (waarde - kosten) if waarde is not None else None

        rows.append({
            "pos":         pos,
            "koers":       koers,
            "waarde":      waarde,
            "kosten":      kosten,
            "winst":       winst,
            "winst_pct":   winst_pct,
            "dag_winst":   dag_winst,
            "dag_pct":     k.get("dag_pct", 0.0),
            "valuta":      valuta,
            "gerealiseerd": gereal,
        })

        if waarde is not None:
            has_prices = True
            totaal_waarde += waarde
            totaal_kosten += kosten
            totaal_dag    += dag_winst

    if not has_prices:
        return rows, None, None, None, totaal_gerealiseerd
    return rows, totaal_waarde, totaal_kosten, totaal_dag, totaal_gerealiseerd


# ── Gebruikersselectie ────────────────────────────────────────────

@app.route("/")
def index():
    gebruikers = Gebruiker.query.order_by(Gebruiker.naam).all()
    # Eén gebruiker: direct doorsturen naar dashboard — tenzij expliciet
    # om de keuze-/beheerpagina gevraagd wordt (?kies=1, vanuit de switcher).
    if len(gebruikers) == 1 and not request.args.get("kies"):
        return redirect(url_for("dashboard", gebruiker_id=gebruikers[0].id))
    return render_template("index.html", gebruikers=gebruikers)


@app.route("/gebruiker/registreren", methods=["POST"])
def gebruiker_registreren():
    naam = request.form.get("naam", "").strip()
    if not naam:
        flash("Naam is verplicht.", "danger")
        return redirect(url_for("index"))
    if Gebruiker.query.filter_by(naam=naam).first():
        flash(f"Gebruiker '{naam}' bestaat al.", "warning")
        return redirect(url_for("index"))
    g = Gebruiker(naam=naam)
    db.session.add(g)
    db.session.commit()
    flash(f"Welkom, {naam}!", "success")
    return redirect(url_for("dashboard", gebruiker_id=g.id))


@app.route("/gebruiker/<int:gebruiker_id>/verwijderen", methods=["POST"])
def gebruiker_verwijderen(gebruiker_id):
    gebruiker = Gebruiker.query.get_or_404(gebruiker_id)
    naam = gebruiker.naam
    # ORM-delete cascadet naar broker accounts, posities, tags en adviezen.
    db.session.delete(gebruiker)
    db.session.commit()
    flash(f"Gebruiker '{naam}' en alle bijbehorende gegevens zijn verwijderd.", "info")
    return redirect(url_for("index"))


@app.route("/gebruiker/<int:gebruiker_id>/hernoemen", methods=["POST"])
def gebruiker_hernoemen(gebruiker_id):
    gebruiker = Gebruiker.query.get_or_404(gebruiker_id)
    nieuwe_naam = request.form.get("naam", "").strip()
    if not nieuwe_naam:
        flash("Naam mag niet leeg zijn.", "danger")
        return redirect(url_for("beheer", gebruiker_id=gebruiker_id))
    bezet = Gebruiker.query.filter(
        Gebruiker.naam == nieuwe_naam, Gebruiker.id != gebruiker_id
    ).first()
    if bezet:
        flash(f"Naam '{nieuwe_naam}' is al in gebruik.", "warning")
        return redirect(url_for("beheer", gebruiker_id=gebruiker_id))
    gebruiker.naam = nieuwe_naam
    db.session.commit()
    flash(f"Naam gewijzigd naar '{nieuwe_naam}'.", "success")
    return redirect(url_for("beheer", gebruiker_id=gebruiker_id))


@app.route("/gebruiker/<int:gebruiker_id>/beheer")
def beheer(gebruiker_id):
    """Profielbeheer: naam wijzigen en het profiel verwijderen.

    Bewust een eigen pagina en niet het switch-menu: verwijderen hoort niet
    één muisklik naast 'wissel van gebruiker' te liggen.
    """
    gebruiker = Gebruiker.query.get_or_404(gebruiker_id)
    aantal_accounts = BrokerAccount.query.filter_by(gebruiker_id=gebruiker.id).count()
    aantal_posities = (Positie.query
                       .join(BrokerAccount)
                       .filter(BrokerAccount.gebruiker_id == gebruiker.id)
                       .count())
    return render_template("beheer.html", gebruiker=gebruiker,
                           aantal_accounts=aantal_accounts,
                           aantal_posities=aantal_posities)


# ── Dashboard ─────────────────────────────────────────────────────

def _externe_flows(account, wisselkoersen):
    """Externe cashflows (storting/opname) van een account, in EUR, voor XIRR.
    Storting = negatief (geld de portefeuille in), opname = positief.

    Retourneert (flows, had_fx_issues). had_fx_issues=True als één of meer
    flows wegens ontbrekende FX-koers overgeslagen werden — de XIRR is dan
    gebaseerd op onvolledige data."""
    flows = []
    had_fx_issues = False
    for tx in account.transacties:
        if tx.type not in ("storting", "opname"):
            continue
        bedrag_eur = naar_eur(tx.bedrag or 0.0, tx.valuta or "EUR", wisselkoersen)
        if bedrag_eur is None:
            had_fx_issues = True
            continue
        flows.append((tx.datum, -bedrag_eur if tx.type == "storting" else bedrag_eur))
    return flows, had_fx_issues


def _maak_prijs_op(ticker):
    """Bouw een opzoekfunctie prijs_op(datum)→EUR-koers uit de benchmark-cache.
    Geeft de meest recente koers op of vóór `datum`. Tweede returnwaarde geeft
    aan of er überhaupt cache-data is."""
    punten = (BenchmarkPunt.query.filter_by(ticker=ticker)
              .order_by(BenchmarkPunt.datum).all())
    datums  = [p.datum for p in punten]
    koersen = [p.koers for p in punten]

    def prijs_op(d):
        i = bisect.bisect_right(datums, d) - 1
        return koersen[i] if i >= 0 else None

    return prijs_op, bool(punten)


def _benchmark_vergelijkingen(grand_flows):
    """Deposit-matched vergelijking: voor elke benchmark de eindwaarde en XIRR
    als je dezelfde stortingen in die index had gedaan. Lege lijst als er geen
    stortingen of geen cache-data zijn."""
    if not grand_flows:
        return []
    vandaag = date.today()
    resultaten = []
    for bm in BENCHMARKS:
        prijs_op, heeft_data = _maak_prijs_op(bm["ticker"])
        if not heeft_data:
            continue
        eind, _ = benchmark_eindwaarde(grand_flows, prijs_op, vandaag)
        if eind is None:
            continue   # cache dekt niet alle flow-datums
        resultaten.append({
            "label": bm["label"],
            "ticker": bm["ticker"],
            "eind":  eind,
            "xirr":  xirr(grand_flows + [(vandaag, eind)]),
        })
    return resultaten


@app.route("/gebruiker/<int:gebruiker_id>")
def dashboard(gebruiker_id):
    gebruiker = Gebruiker.query.get_or_404(gebruiker_id)
    koersen, bijgewerkt, wisselkoersen = laad_prijzen()

    # Eager-load accounts → posities → tags (+ transacties) in enkele queries
    # i.p.v. lazy loading per account/positie (voorkomt N+1 op het dashboard).
    accounts = (BrokerAccount.query
                .filter_by(gebruiker_id=gebruiker_id)
                .options(selectinload(BrokerAccount.posities)
                         .selectinload(Positie.tags),
                         selectinload(BrokerAccount.transacties))
                .order_by(BrokerAccount.id)
                .all())

    account_data = []
    grand_waarde = grand_kosten = grand_dag = 0.0
    grand_gerealiseerd = grand_cash_eur = 0.0
    grand_has_prices = False
    grand_flows = []
    grand_fx_issues = False

    for account in accounts:
        rows, tw, tk, td, tg = bereken_posities(account.posities, koersen, wisselkoersen)
        hp = tw is not None

        cash      = cash_saldi(account.id)
        cash_eur  = sum((naar_eur(s, v, wisselkoersen) or 0.0) for v, s in cash.items())
        flows, had_fx = _externe_flows(account, wisselkoersen)
        eind_eur  = (tw or 0.0) + cash_eur
        # Bereken XIRR alleen als de eindwaarde betrouwbaar is: prijzen beschikbaar
        # (hp) of het account heeft geen open posities (puur cash-account).
        heeft_open = bool(rows)
        acc_xirr  = (xirr(flows + [(date.today(), eind_eur)])
                     if flows and (hp or not heeft_open) else None)

        account_data.append({
            "account":      account,
            "rows":         rows,
            "waarde":       tw,
            "dag":          td,
            "winst":        (tw - tk) if hp else None,
            "gerealiseerd": tg,
            "cash":         cash,
            "cash_eur":     cash_eur,
            "xirr":         acc_xirr,
            "fx_issues":    had_fx,
            # Dagrendement in procent, zodat de kleurdrempel (±0,5%) ook op de
            # totaalregel van een account kan werken.
            "dag_pct":      (td / (tw - td) * 100
                             if hp and tw is not None and abs(tw - td) > 0.01 else None),
        })
        grand_gerealiseerd += tg
        grand_cash_eur     += cash_eur
        grand_flows        += flows
        grand_fx_issues     = grand_fx_issues or had_fx
        if hp:
            grand_has_prices = True
            grand_waarde += tw
            grand_kosten += tk
            grand_dag    += td

    grand_eind = (grand_waarde if grand_has_prices else 0.0) + grand_cash_eur
    # Netto inleg = stortingen minus opnames. In grand_flows staat een storting
    # negatief (geld de portefeuille in), dus omdraaien.
    grand_inleg = -sum(bedrag for _, bedrag in grand_flows) if grand_flows else None
    grand_resultaat = (grand_eind - grand_inleg) if grand_inleg is not None else None
    grand_resultaat_pct = (grand_resultaat / grand_inleg * 100
                           if grand_inleg and abs(grand_inleg) > 0.01 else None)
    heeft_open_globaal = any(bool(d["rows"]) for d in account_data)
    grand_xirr = (xirr(grand_flows + [(date.today(), grand_eind)])
                  if grand_flows and (grand_has_prices or not heeft_open_globaal) else None)
    benchmarks = _benchmark_vergelijkingen(grand_flows)

    twr_res = _portefeuille_twr(accounts)
    grand_twr = (twr_res.get("geannualiseerd")
                 if twr_res and not twr_res.get("te_weinig_data") else None)

    # Dagverandering %: dag_winst / (waarde_gisteren) = dag / (waarde - dag)
    grand_vorig = (grand_waarde - grand_dag) if grand_has_prices else None
    grand_dag_pct = (grand_dag / grand_vorig * 100
                     if grand_has_prices and grand_vorig and abs(grand_vorig) > 0.01
                     else None)

    # Waardeontwikkelingsreeks voor grafiek, op basis van echte koershistorie
    # (niet meer van advies-snapshots — front-end filtert zelf op periode).
    waarde_serie = _portefeuille_waarde_serie(accounts)

    return render_template(
        "dashboard.html",
        gebruiker    = gebruiker,
        account_data = account_data,
        totaal       = {
            "waarde":       grand_waarde if grand_has_prices else None,
            "winst":        (grand_waarde - grand_kosten) if grand_has_prices else None,
            "dag":          grand_dag if grand_has_prices else None,
            "dag_pct":      grand_dag_pct,
            "gerealiseerd": grand_gerealiseerd,
            "cash_eur":     grand_cash_eur,
            "xirr":         grand_xirr,
            "eind":         grand_eind,
            "twr":          grand_twr,
            "inleg":          grand_inleg,
            "resultaat":      grand_resultaat,
            "resultaat_pct":  grand_resultaat_pct,
        },
        benchmarks   = benchmarks,
        heeft_flows  = bool(grand_flows),
        bijgewerkt   = bijgewerkt,
        koersen_status = koersen_status(_RUWE_BIJGEWERKT["waarde"]),
        waarde_serie = waarde_serie,
    )


# ── Analyse: allocatie & risico ───────────────────────────────────

def _classificeer_type(ticker, heeft_sector):
    if is_crypto(ticker):
        return "Crypto"
    return "Aandeel" if heeft_sector else "ETF / overig"


def _laad_historie(tickers):
    """{ticker: ([datums], [koersen])} uit koers_historie, chronologisch."""
    res = {}
    rows = (KoersHistorie.query
            .filter(KoersHistorie.ticker.in_(list(tickers)))
            .order_by(KoersHistorie.ticker, KoersHistorie.datum).all())
    for r in rows:
        datums, koersen = res.setdefault(r.ticker, ([], []))
        datums.append(r.datum)
        koersen.append(r.koers)
    return res


def _portefeuille_risico(holdings, benchmark_ticker="IWDA.AS", venster=252):
    """holdings: lijst van (ticker, aantal). Reconstrueert de dagelijkse EUR-
    waarde van het huidige mandje en geeft volatiliteit, max drawdown en beta.
    Werkt op de holdings waarvoor historie beschikbaar is (dekking)."""
    if not holdings:
        return None
    hist   = _laad_historie({t for t, _ in holdings} | {benchmark_ticker})
    gedekt = [(t, a) for t, a in holdings if t in hist and len(hist[t][0]) > 2]
    if not gedekt:
        return {"dekking": 0, "totaal_holdings": len(holdings), "te_weinig_data": True,
                "volatiliteit": None, "max_drawdown": None, "sharpe": None, "beta": None,
                "per_holding": None, "correlatie": None, "dagen": 0}

    serie  = {t: dict(zip(hist[t][0], hist[t][1])) for t, _ in gedekt}
    gemeen = set.intersection(*[set(serie[t]) for t, _ in gedekt])
    bench  = dict(zip(hist[benchmark_ticker][0], hist[benchmark_ticker][1])) \
             if benchmark_ticker in hist else None
    if bench:
        gemeen &= set(bench)
    gemeen = sorted(gemeen)[-venster:]
    if len(gemeen) < 20:
        return {"dekking": len(gedekt), "totaal_holdings": len(holdings),
                "te_weinig_data": True,
                "volatiliteit": None, "max_drawdown": None, "sharpe": None, "beta": None,
                "per_holding": None, "correlatie": None, "dagen": 0}

    waarden   = [sum(a * serie[t][d] for t, a in gedekt) for d in gemeen]
    port_rend = dagrendementen(waarden)

    # Volatiliteit per holding + correlatiematrix (op de gemene datums).
    rend_per_ticker = {t: dagrendementen([serie[t][d] for d in gemeen]) for t, _ in gedekt}
    per_holding = sorted(
        [{"ticker": t, "vol": volatiliteit(rend_per_ticker[t])} for t, _ in gedekt],
        key=lambda h: (h["vol"] is None, -(h["vol"] or 0)))
    matrix_tickers = [t for t, _ in gedekt]
    matrix = [[(1.0 if ti == tj else correlatie(rend_per_ticker[ti], rend_per_ticker[tj]))
               for tj in matrix_tickers] for ti in matrix_tickers]

    res = {
        "volatiliteit":    volatiliteit(port_rend),
        "max_drawdown":    max_drawdown(waarden),
        "sharpe":          sharpe(port_rend, rf=RISICOVRIJE_RENTE),
        "beta":            None,
        "per_holding":     per_holding,
        "correlatie":      {"tickers": matrix_tickers, "matrix": matrix},
        "dekking":         len(gedekt),
        "totaal_holdings": len(holdings),
        "dagen":           len(gemeen),
        "te_weinig_data":  False,
    }
    if bench:
        res["beta"] = beta(port_rend, dagrendementen([bench[d] for d in gemeen]))
    return res


def _portefeuille_holdings_reeks(accounts, venster=430):
    """Reconstrueert de dagelijkse EUR-waarde van de **holdings-pot** (géén cash)
    uit het grootboek + koers_historie, en de netto-flow (aan-/verkoop/correctie)
    die dag in/uit die pot. Gedeelde basis voor TWR (`_portefeuille_twr`) en de
    waardeontwikkelingsgrafiek (`_portefeuille_waarde_serie`).

    Geeft {grid, navs, flows, dekking, totaal_holdings, gedekt, qty_nu, flow_na};
    grid/navs/flows leeg ([]) als er te weinig koershistorie is om iets te
    reconstrueren. `qty_nu` en `flow_na` dekken óók de transacties ná de laatste
    griddag: koershistorie van fondsen loopt een dag of twee achter, dus wie de
    grafiek tot vandaag wil doortrekken (`_portefeuille_waarde_serie`) heeft de
    stukken van nú nodig, niet die van de laatste dag met een koers.
    """
    _, _, wisselkoersen = laad_prijzen()
    txs = [tx for acc in accounts for tx in acc.transacties]
    aandeel_tickers = {tx.ticker for tx in txs
                       if tx.type in ("koop", "verkoop", "correctie") and tx.ticker}
    leeg = {"grid": [], "navs": [], "flows": [],
            "dekking": 0, "totaal_holdings": len(aandeel_tickers),
            "gedekt": set(), "qty_nu": {}, "flow_na": 0.0}
    if not txs or not aandeel_tickers:
        return leeg

    hist   = _laad_historie(aandeel_tickers)
    gedekt = {t for t in aandeel_tickers if t in hist and len(hist[t][0]) > 2}
    if not gedekt:
        return leeg

    serie = {t: dict(zip(hist[t][0], hist[t][1])) for t in gedekt}
    grid  = sorted({d for t in gedekt for d in serie[t]})[-venster:]
    if len(grid) < 2:
        return {**leeg, "dekking": len(gedekt)}

    txs_sorted = sorted(txs, key=lambda t: (t.datum, t.id))
    qty, laatste_koers = {}, {}
    idx = 0
    navs, flows = [], []

    def verwerk(tx):
        """Boek één trade in `qty` en geef de EUR-flow in/uit de holdings-pot."""
        if tx.ticker not in gedekt:
            return 0.0
        v      = tx.valuta or "EUR"
        aantal = tx.aantal or 0.0
        prijs  = tx.prijs or 0.0
        kosten = tx.kosten or 0.0
        if tx.type == "koop":
            flow_eur = naar_eur(aantal * prijs + kosten, v, wisselkoersen)
            if flow_eur is None:
                # FX ontbreekt: schat op basis van EUR-koershistorie (al FX-gecorrigeerd)
                flow_eur = aantal * (laatste_koers.get(tx.ticker) or 0.0)
            qty[tx.ticker] = qty.get(tx.ticker, 0.0) + aantal
            return flow_eur
        if tx.type == "verkoop":
            flow_eur = naar_eur(aantal * prijs - kosten, v, wisselkoersen)
            if flow_eur is None:
                flow_eur = aantal * (laatste_koers.get(tx.ticker) or 0.0)
            qty[tx.ticker] = qty.get(tx.ticker, 0.0) - aantal
            return -flow_eur
        if tx.type == "correctie":
            oud   = qty.get(tx.ticker, 0.0)
            koers = laatste_koers.get(tx.ticker) or (naar_eur(prijs, v, wisselkoersen) or 0.0)
            qty[tx.ticker] = aantal
            return (aantal - oud) * koers
        return 0.0

    for d in grid:
        for t in gedekt:                       # carry-forward laatst bekende koers ≤ d
            if d in serie[t]:
                laatste_koers[t] = serie[t][d]
        flow_d = 0.0
        # Trades t/m deze datum: het bedrag dat de holdings-pot in/uit gaat is een
        # externe flow (geen rendement).
        while idx < len(txs_sorted) and txs_sorted[idx].datum <= d:
            flow_d += verwerk(txs_sorted[idx]); idx += 1

        holdings = sum(qty.get(t, 0.0) * laatste_koers[t]
                       for t in gedekt if t in laatste_koers)
        navs.append(holdings)
        flows.append(flow_d)

    # Start bij de eerste dag met daadwerkelijk holdings (geen vlakke nul-inleiding
    # vóór de eerste aankoop, bv. door de venster-marge of stille periodes).
    eerste = next((i for i, v in enumerate(navs) if v and v > 0), None)
    if eerste is None:
        return {**leeg, "dekking": len(gedekt)}
    grid, navs, flows = grid[eerste:], navs[eerste:], flows[eerste:]
    if len(grid) < 2:
        return {**leeg, "dekking": len(gedekt)}

    # Restant: trades ná de laatste dag met koershistorie. Die tellen niet mee in
    # navs/flows (daar hoort geen koers bij), maar wél in de stand van vandaag.
    flow_na = 0.0
    while idx < len(txs_sorted):
        flow_na += verwerk(txs_sorted[idx]); idx += 1

    return {"grid": grid, "navs": navs, "flows": flows,
            "dekking": len(gedekt), "totaal_holdings": len(aandeel_tickers),
            "gedekt": gedekt, "qty_nu": dict(qty), "flow_na": flow_na}


def _portefeuille_twr(accounts, venster=400):
    """Tijd-gewogen rendement van de belegde holdings.

    Waardeert alleen de **holdings** (Σ qty·koers, EUR) per dag uit het grootboek
    + koers_historie, en behandelt aan-/verkopen (en correcties) als externe
    flows naar die pot. Zo meet TWR puur het rendement van je posities, los van
    de timing van stortingen — en hangt het niet af van een (hier ontbrekend)
    cash-saldo. Geeft {cumulatief, geannualiseerd, dagen, dekking, ...}.
    """
    reeks = _portefeuille_holdings_reeks(accounts, venster=venster)
    basis = {"dekking": reeks["dekking"], "totaal_holdings": reeks["totaal_holdings"],
             "te_weinig_data": True, "cumulatief": None, "geannualiseerd": None, "dagen": 0}
    grid, navs, flows = reeks["grid"], reeks["navs"], reeks["flows"]
    if len(grid) < 20:
        return basis

    cum = twr(navs, flows)
    if cum is None:
        return basis
    dagen = max((grid[-1] - grid[0]).days, 1)
    return {
        "cumulatief":      cum,
        "geannualiseerd":  annualiseer(cum, dagen),
        "dagen":           len(navs),
        "dekking":         reeks["dekking"],
        "totaal_holdings": reeks["totaal_holdings"],
        "te_weinig_data":  False,
    }


# Volgorde bepaalt hoe de mutaties in de grafiek-tooltip worden opgesomd.
_MUTATIE_LABELS = {
    "storting":  ("storting",  "stortingen"),
    "koop":      ("aankoop",   "aankopen"),
    "verkoop":   ("verkoop",   "verkopen"),
    "dividend":  ("dividend",  "dividenden"),
    "kosten":    ("kostenpost", "kostenposten"),
    "opname":    ("opname",    "opnames"),
    "correctie": ("correctie", "correcties"),
}


def _mutatie_markers(accounts, grid):
    """{iso-datum: 'omschrijving'} van de grootboekmutaties per griddag.

    Het grid bestaat uit handelsdagen uit `koers_historie`; een transactie op
    een weekend- of feestdag (of vóór de eerste dag van de grafiek) schuift naar
    de eerstvolgende griddag, zodat de marker altijd op een punt in de lijn valt.
    Mutaties ná de laatste griddag vallen weg.
    """
    if not grid:
        return {}
    per_dag = {}
    for account in accounts:
        for tx in account.transacties:
            if tx.datum is None:
                continue
            i = bisect.bisect_left(grid, tx.datum)
            if i >= len(grid):
                continue
            per_dag.setdefault(grid[i].isoformat(), []).append(tx.type)

    markers = {}
    for iso, types in per_dag.items():
        delen = []
        for soort, (enkel, meervoud) in _MUTATIE_LABELS.items():
            n = types.count(soort)
            if n:
                delen.append(f"{n} {enkel if n == 1 else meervoud}")
        if delen:
            markers[iso] = ", ".join(delen)
    return markers


def _live_holdings_waarde(reeks):
    """EUR-waarde van de gedekte holdings tegen de *actuele* koers uit
    prijzen.json, op basis van de stukken van nú (`qty_nu`).

    None zodra één van die holdings geen bruikbare live koers heeft: een
    ontbrekende koers zou het laatste punt van de grafiek laten inzakken en dat
    leest als koersverlies dat er niet is.
    """
    koersen, _, wisselkoersen = laad_prijzen()
    totaal = 0.0
    for ticker in reeks["gedekt"]:
        aantal = reeks["qty_nu"].get(ticker, 0.0)
        if abs(aantal) < 1e-9:
            continue
        info = koersen.get(ticker) or {}
        koers = info.get("koers")
        if koers is None:
            return None
        eur = naar_eur(aantal * koers, info.get("valuta") or "EUR", wisselkoersen)
        if eur is None:
            return None
        totaal += eur
    return totaal if totaal > 0 else None


def _portefeuille_waarde_serie(accounts):
    """Waardeontwikkeling voor de dashboardgrafiek, op basis van échte
    koershistorie (géén advies-snapshots): per dag de holdings-waarde (EUR),
    de cumulatieve netto-inleg in die holdings-pot en — als er die dag iets in
    het grootboek is geboekt — een korte omschrijving daarvan voor de marker.
    Front-end filtert dit zelf op periode (week/maand/3 maanden/alles) — dus
    hier altijd de volle reeks.

    Cash zit hier bewust *niet* in: een stilstaand saldo is geen prestatie van
    de portefeuille en zou de rendementslijn vertekenen. Het cash-saldo staat
    als eigen kengetal op het dashboard.
    """
    reeks = _portefeuille_holdings_reeks(accounts, venster=430)
    grid, navs, flows = reeks["grid"], reeks["navs"], reeks["flows"]
    if len(grid) < 2:
        return []

    # Koershistorie van beleggingsfondsen loopt een dag of twee achter (de NAV
    # van een handelsdag wordt pas daarna gepubliceerd), en in het weekend komt
    # er niets bij. Plak daarom de actuele koers uit prijzen.json als laatste
    # punt aan de lijn: dan eindigt de grafiek op hetzelfde bedrag als de tegels
    # bovenaan, en vallen mutaties van ná de laatste koersdag alsnog binnen het
    # bereik van de markers.
    vandaag  = date.today()
    live_iso = None
    live     = _live_holdings_waarde(reeks)
    if live is not None and vandaag > grid[-1]:
        grid, navs, flows = grid + [vandaag], navs + [live], flows + [reeks["flow_na"]]
        live_iso = vandaag.isoformat()

    markers = _mutatie_markers(accounts, grid)
    serie, inleg_cum = [], 0.0
    for d, w, f in zip(grid, navs, flows):
        inleg_cum += f
        iso = d.isoformat()
        serie.append({"datum": iso, "waarde": round(w, 2),
                       "inleg": round(inleg_cum, 2),
                       "mutatie": markers.get(iso),
                       "live": iso == live_iso})
    return serie


@app.route("/gebruiker/<int:gebruiker_id>/analyse")
def analyse(gebruiker_id):
    gebruiker = Gebruiker.query.get_or_404(gebruiker_id)
    koersen, bijgewerkt, wisselkoersen = laad_prijzen()
    accounts = (BrokerAccount.query.filter_by(gebruiker_id=gebruiker_id)
                .options(selectinload(BrokerAccount.posities),
                         selectinload(BrokerAccount.transacties)).all())

    # Huidige EUR-waarde + aantal per ticker, geaggregeerd over accounts.
    waarde_per_ticker = {}
    aantal_per_ticker = {}
    valuta_per_ticker = {}
    totaal = 0.0
    for account in accounts:
        rows, *_ = bereken_posities(account.posities, koersen, wisselkoersen)
        for r in rows:
            if r["waarde"] is None:
                continue
            t = r["pos"].ticker
            waarde_per_ticker[t] = waarde_per_ticker.get(t, 0.0) + r["waarde"]
            aantal_per_ticker[t] = aantal_per_ticker.get(t, 0.0) + (r["pos"].aantal or 0.0)
            valuta_per_ticker[t] = r["pos"].valuta or "EUR"
            totaal += r["waarde"]

    funds = {f.ticker: f for f in Fundamental.query
             .filter(Fundamental.ticker.in_(list(waarde_per_ticker) or [""])).all()}

    def _bucket(sleutelfn):
        agg = {}
        for t, w in waarde_per_ticker.items():
            k = sleutelfn(t)
            agg[k] = agg.get(k, 0.0) + w
        items = sorted(agg.items(), key=lambda kv: kv[1], reverse=True)
        return [{"label": k, "waarde": v, "pct": (v / totaal * 100 if totaal else 0)}
                for k, v in items]

    sector_alloc = _bucket(lambda t: funds[t].sector if t in funds and funds[t].sector
                           else "ETF / onbekend")
    valuta_alloc = _bucket(lambda t: valuta_per_ticker.get(t, "EUR"))
    type_alloc   = _bucket(lambda t: _classificeer_type(t, t in funds and bool(funds[t].sector)))
    regio_alloc  = _bucket(lambda t: funds[t].land if t in funds and funds[t].land else "Onbekend")

    holdings_sorted = sorted(
        [{"ticker": t, "waarde": w, "pct": (w / totaal * 100 if totaal else 0),
          "naam": (funds[t].naam if t in funds and funds[t].naam else t)}
         for t, w in waarde_per_ticker.items()],
        key=lambda h: h["waarde"], reverse=True)
    top5      = sum(h["pct"] for h in holdings_sorted[:5])
    hhi       = sum((h["pct"] / 100) ** 2 for h in holdings_sorted)
    effectief = (1 / hhi) if hhi else 0

    flags = []
    for h in holdings_sorted:
        if h["pct"] > 20:
            flags.append(f"{h['ticker']} is {h['pct']:.0f}% van de portefeuille (>20%).")
    for s in sector_alloc:
        if s["pct"] > 40 and s["label"] != "ETF / onbekend":
            flags.append(f"Sector '{s['label']}' is {s['pct']:.0f}% (>40%).")

    risico = _portefeuille_risico([(t, a) for t, a in aantal_per_ticker.items()])
    twr_res = _portefeuille_twr(accounts)

    return render_template(
        "analyse.html",
        gebruiker        = gebruiker,
        totaal           = totaal if totaal else None,
        sector_alloc     = sector_alloc,
        valuta_alloc     = valuta_alloc,
        type_alloc       = type_alloc,
        regio_alloc      = regio_alloc,
        holdings         = holdings_sorted,
        top5             = top5,
        effectief        = effectief,
        flags            = flags,
        risico           = risico,
        twr              = twr_res,
        rf               = RISICOVRIJE_RENTE,
        heeft_fundamentals = bool(funds),
    )


# ── Broker accounts ───────────────────────────────────────────────

@app.route("/gebruiker/<int:gebruiker_id>/account/toevoegen", methods=["POST"])
def account_toevoegen(gebruiker_id):
    gebruiker = Gebruiker.query.get_or_404(gebruiker_id)
    naam = request.form.get("naam", "").strip()
    if not naam:
        flash("Naam is verplicht.", "danger")
    else:
        db.session.add(BrokerAccount(naam=naam, gebruiker_id=gebruiker.id))
        db.session.commit()
        flash(f"Broker account '{naam}' toegevoegd.", "success")
    return redirect(url_for("dashboard", gebruiker_id=gebruiker_id))


@app.route("/gebruiker/<int:gebruiker_id>/account/<int:account_id>/verwijderen", methods=["POST"])
def account_verwijderen(gebruiker_id, account_id):
    account = BrokerAccount.query.get_or_404(account_id)
    if account.gebruiker_id != gebruiker_id:
        flash("Niet geautoriseerd.", "danger")
        return redirect(url_for("dashboard", gebruiker_id=gebruiker_id))
    naam = account.naam
    db.session.delete(account)
    db.session.commit()
    flash(f"Account '{naam}' verwijderd.", "info")
    return redirect(url_for("dashboard", gebruiker_id=gebruiker_id))


# ── Posities ──────────────────────────────────────────────────────

def _parse_positie_form(form):
    """Valideer het positie-formulier. Geeft (waarden, fouten) terug."""
    ticker_val   = form.get("ticker", "").strip().upper()
    naam_val     = form.get("naam", "").strip()
    aantal_str   = form.get("aantal", "").strip()
    prijs_str    = form.get("aankoopprijs", "").strip()
    datum_str    = form.get("aankoopdatum", "").strip()
    koers_type   = form.get("koers_type", "live")
    hand_koers_s = form.get("handmatige_koers", "").strip()
    valuta_val   = form.get("valuta", "EUR").strip().upper() or "EUR"

    fouten = []
    if not ticker_val:
        fouten.append("Ticker is verplicht.")
    try:
        aantal_val = float(aantal_str) if aantal_str else None
        if aantal_val is None or not math.isfinite(aantal_val) or aantal_val <= 0:
            fouten.append("Aantal moet groter dan 0 zijn.")
    except ValueError:
        fouten.append("Aantal moet een getal zijn.")
        aantal_val = None
    try:
        prijs_val = float(prijs_str) if prijs_str else None
        if prijs_val is None or not math.isfinite(prijs_val) or prijs_val <= 0:
            fouten.append("Aankoopprijs moet groter dan 0 zijn.")
    except ValueError:
        fouten.append("Aankoopprijs moet een getal zijn.")
        prijs_val = None
    try:
        datum = datetime.strptime(datum_str, "%Y-%m-%d").date() if datum_str else None
    except ValueError:
        fouten.append("Ongeldige datum.")
        datum = None
    hand_koers = None
    if koers_type not in ("live", "handmatig"):
        fouten.append("Ongeldig koerstype.")
    if koers_type == "handmatig":
        try:
            hand_koers = float(hand_koers_s) if hand_koers_s else None
            if hand_koers is None or not math.isfinite(hand_koers) or hand_koers <= 0:
                fouten.append("Handmatige koers moet groter dan 0 zijn.")
        except ValueError:
            fouten.append("Handmatige koers moet een getal zijn.")

    waarden = {
        "ticker": ticker_val, "naam": naam_val, "aantal": aantal_val,
        "aankoopprijs": prijs_val, "aankoopdatum": datum,
        "koers_type": koers_type, "handmatige_koers": hand_koers,
        "valuta": valuta_val,
    }
    return waarden, fouten


@app.route("/gebruiker/<int:gebruiker_id>/account/<int:account_id>/positie/toevoegen",
           methods=["GET", "POST"])
def positie_toevoegen(gebruiker_id, account_id):
    gebruiker = Gebruiker.query.get_or_404(gebruiker_id)
    account   = BrokerAccount.query.get_or_404(account_id)
    if account.gebruiker_id != gebruiker_id:
        flash("Niet geautoriseerd.", "danger")
        return redirect(url_for("dashboard", gebruiker_id=gebruiker_id))

    alle_tags = Tag.query.filter_by(gebruiker_id=gebruiker_id).order_by(Tag.naam).all()

    if request.method == "POST":
        waarden, fouten = _parse_positie_form(request.form)
        tag_ids = request.form.getlist("tag_ids", type=int)
        if fouten:
            for f in fouten:
                flash(f, "danger")
            return render_template("positie_form.html",
                                   gebruiker=gebruiker, account=account, pos=None,
                                   alle_tags=alle_tags, form_data=request.form,
                                   geselecteerde_tag_ids=tag_ids)

        geselecteerde_tags = Tag.query.filter(Tag.id.in_(tag_ids),
                                              Tag.gebruiker_id == gebruiker_id).all()
        # Quick-add: leg de holding vast als eerste koop in het grootboek,
        # projecteer naar een Positie en zet daarna de metadata erop.
        db.session.add(Transactie(
            broker_account_id=account.id, type="koop",
            ticker=waarden["ticker"], aantal=waarden["aantal"],
            prijs=waarden["aankoopprijs"], kosten=0.0,
            valuta=waarden["valuta"],
            datum=waarden["aankoopdatum"] or date.today(),
            notitie="Eerste aankoop",
        ))
        db.session.flush()
        pos = herbereken_positie(account.id, waarden["ticker"])
        if pos is not None:
            pos.naam             = waarden["naam"]
            pos.koers_type       = waarden["koers_type"]
            pos.handmatige_koers = waarden["handmatige_koers"]
            pos.valuta           = waarden["valuta"]
            pos.aankoopdatum     = waarden["aankoopdatum"] or pos.aankoopdatum
            pos.tags             = geselecteerde_tags
        db.session.commit()
        flash(f"{waarden['ticker']} toegevoegd aan {account.naam}.", "success")
        return redirect(url_for("dashboard", gebruiker_id=gebruiker_id))

    return render_template("positie_form.html",
                           gebruiker=gebruiker, account=account, pos=None,
                           alle_tags=alle_tags, form_data={}, geselecteerde_tag_ids=[])


@app.route("/gebruiker/<int:gebruiker_id>/positie/<int:pos_id>/bewerken",
           methods=["GET", "POST"])
def positie_bewerken(gebruiker_id, pos_id):
    gebruiker = Gebruiker.query.get_or_404(gebruiker_id)
    pos       = Positie.query.get_or_404(pos_id)
    if pos.broker_account.gebruiker_id != gebruiker_id:
        flash("Niet geautoriseerd.", "danger")
        return redirect(url_for("dashboard", gebruiker_id=gebruiker_id))
    account   = pos.broker_account
    alle_tags = Tag.query.filter_by(gebruiker_id=gebruiker_id).order_by(Tag.naam).all()

    if request.method == "POST":
        waarden, fouten = _parse_positie_form(request.form)
        tag_ids = request.form.getlist("tag_ids", type=int)
        if fouten:
            for f in fouten:
                flash(f, "danger")
            return render_template("positie_form.html",
                                   gebruiker=gebruiker, account=account, pos=pos,
                                   alle_tags=alle_tags, form_data=request.form,
                                   geselecteerde_tag_ids=tag_ids)

        # Ticker is de identiteit van de holding en verandert niet via dit
        # scherm; metadata is altijd bewerkbaar.
        pos.naam             = waarden["naam"]
        pos.koers_type       = waarden["koers_type"]
        pos.handmatige_koers = waarden["handmatige_koers"]
        pos.valuta           = waarden["valuta"]
        if waarden["aankoopdatum"]:
            pos.aankoopdatum = waarden["aankoopdatum"]
        pos.tags = Tag.query.filter(Tag.id.in_(tag_ids),
                                    Tag.gebruiker_id == gebruiker_id).all()

        # Direct gewijzigd(e) aantal/GAK → vastleggen als correctie-transactie en
        # opnieuw projecteren, zodat de cache nooit buiten het grootboek om wijzigt.
        aantal_gewijzigd = abs((pos.aantal or 0.0) - (waarden["aantal"] or 0.0)) > 1e-9
        gak_gewijzigd    = abs((pos.aankoopprijs or 0.0) - (waarden["aankoopprijs"] or 0.0)) > 1e-9
        if aantal_gewijzigd or gak_gewijzigd:
            db.session.add(Transactie(
                broker_account_id=account.id, type="correctie",
                ticker=pos.ticker, aantal=waarden["aantal"],
                prijs=waarden["aankoopprijs"], kosten=0.0,
                valuta=waarden["valuta"], datum=date.today(),
                notitie="Handmatige correctie",
            ))
            db.session.flush()
            herbereken_positie(account.id, pos.ticker)
        db.session.commit()
        flash(f"{pos.ticker} bijgewerkt.", "success")
        return redirect(url_for("dashboard", gebruiker_id=gebruiker_id))

    form_data = {
        "ticker":           pos.ticker,
        "naam":             pos.naam,
        "aantal":           pos.aantal,
        "aankoopprijs":     pos.aankoopprijs,
        "aankoopdatum":     pos.aankoopdatum.isoformat() if pos.aankoopdatum else "",
        "koers_type":       pos.koers_type,
        "handmatige_koers": pos.handmatige_koers if pos.handmatige_koers is not None else "",
        "valuta":           getattr(pos, "valuta", "EUR") or "EUR",
    }
    return render_template("positie_form.html",
                           gebruiker=gebruiker, account=account, pos=pos,
                           alle_tags=alle_tags, form_data=form_data,
                           geselecteerde_tag_ids=[t.id for t in pos.tags])


@app.route("/gebruiker/<int:gebruiker_id>/positie/<int:pos_id>/verwijderen", methods=["POST"])
def positie_verwijderen(gebruiker_id, pos_id):
    pos = Positie.query.get_or_404(pos_id)
    if pos.broker_account.gebruiker_id != gebruiker_id:
        flash("Niet geautoriseerd.", "danger")
        return redirect(url_for("dashboard", gebruiker_id=gebruiker_id))
    ticker = pos.ticker
    account_id = pos.broker_account_id
    # Verwijder ook de grootboektransacties voor deze ticker, anders her-creëert
    # de projectie de holding. Cash-transacties (zonder ticker) blijven ongemoeid.
    Transactie.query.filter_by(broker_account_id=account_id, ticker=ticker).delete()
    db.session.delete(pos)
    db.session.commit()
    flash(f"{ticker} verwijderd.", "info")
    return redirect(url_for("dashboard", gebruiker_id=gebruiker_id))


@app.route("/gebruiker/<int:gebruiker_id>/positie/<int:pos_id>/tags/bewerken",
           methods=["GET", "POST"])
def positie_tags_bewerken(gebruiker_id, pos_id):
    gebruiker = Gebruiker.query.get_or_404(gebruiker_id)
    pos       = Positie.query.get_or_404(pos_id)
    if pos.broker_account.gebruiker_id != gebruiker_id:
        flash("Niet geautoriseerd.", "danger")
        return redirect(url_for("dashboard", gebruiker_id=gebruiker_id))

    alle_tags = Tag.query.filter_by(gebruiker_id=gebruiker_id).order_by(Tag.naam).all()

    if request.method == "POST":
        tag_ids = request.form.getlist("tag_ids", type=int)
        pos.tags = Tag.query.filter(Tag.id.in_(tag_ids),
                                    Tag.gebruiker_id == gebruiker_id).all()
        db.session.commit()
        flash("Tags bijgewerkt.", "success")
        return redirect(url_for("dashboard", gebruiker_id=gebruiker_id))

    return render_template("positie_tags.html",
                           gebruiker=gebruiker, pos=pos, alle_tags=alle_tags)


# ── Transacties (grootboek) ───────────────────────────────────────

def _parse_transactie_form(form, account):
    """Valideer het transactie-formulier. Geeft (waarden, fouten) terug."""
    def _getal(naam):
        s = form.get(naam, "").strip()
        waarde = float(s) if s else None   # kan ValueError gooien
        if waarde is not None and not math.isfinite(waarde):
            raise ValueError
        return waarde

    fouten      = []
    type_val    = form.get("type", "").strip()
    ticker_val  = form.get("ticker", "").strip().upper()
    valuta_val  = form.get("valuta", "EUR").strip().upper() or "EUR"
    datum_str   = form.get("datum", "").strip()
    notitie_val = form.get("notitie", "").strip()[:200]

    if type_val not in TRANSACTIE_TYPES:
        fouten.append("Ongeldig transactietype.")

    try:
        datum = datetime.strptime(datum_str, "%Y-%m-%d").date() if datum_str else None
    except ValueError:
        datum = None
    if datum is None:
        fouten.append("Geldige datum is verplicht.")

    kosten = 0.0
    try:
        kosten = _getal("kosten") or 0.0
        if kosten < 0:
            fouten.append("Kosten mogen niet negatief zijn.")
    except ValueError:
        fouten.append("Kosten moeten een getal zijn.")

    aantal = prijs = bedrag = None
    is_aandeel = type_val in ("koop", "verkoop")
    if type_val in ("koop", "verkoop", "dividend") and not ticker_val:
        fouten.append("Ticker is verplicht voor dit type.")
    if is_aandeel:
        try:
            aantal = _getal("aantal")
            if not aantal or aantal <= 0:
                fouten.append("Aantal moet groter dan 0 zijn.")
        except ValueError:
            fouten.append("Aantal moet een getal zijn.")
        try:
            prijs = _getal("prijs")
            if not prijs or prijs <= 0:
                fouten.append("Prijs moet groter dan 0 zijn.")
        except ValueError:
            fouten.append("Prijs moet een getal zijn.")
    if type_val in ("storting", "opname", "dividend", "kosten"):
        try:
            bedrag = _getal("bedrag")
            if not bedrag or bedrag <= 0:
                fouten.append("Bedrag moet groter dan 0 zijn.")
        except ValueError:
            fouten.append("Bedrag moet een getal zijn.")

    waarden = {
        "type": type_val, "ticker": ticker_val or None,
        "aantal": aantal, "prijs": prijs, "bedrag": bedrag,
        "kosten": kosten, "valuta": valuta_val, "datum": datum,
        "notitie": notitie_val or None,
    }
    if type_val in ("storting", "opname", "kosten"):
        waarden["ticker"] = None   # cash-types dragen geen ticker
    return waarden, fouten


def _valideer_aandelenverloop(account_id, waarden, vervang_id=None):
    """Controleer het volledige grootboek met een nieuwe/gewijzigde transactie.

    Dit vangt ook teruggedateerde mutaties en verkopen ná een gewijzigde koop op.
    """
    records = []
    for tx in Transactie.query.filter_by(broker_account_id=account_id).all():
        if tx.id == vervang_id:
            continue
        if tx.type in ("koop", "verkoop", "correctie") and tx.ticker:
            records.append((tx.datum, tx.id, tx.type, tx.ticker, tx.aantal or 0.0))
    if waarden.get("type") in ("koop", "verkoop", "correctie") and waarden.get("ticker"):
        volgorde = vervang_id if vervang_id is not None else float("inf")
        records.append((waarden["datum"], volgorde, waarden["type"],
                        waarden["ticker"], waarden.get("aantal") or 0.0))

    bezit = {}
    for datum, _, soort, ticker, aantal in sorted(records, key=lambda r: (r[0], r[1])):
        huidig = bezit.get(ticker, 0.0)
        if soort == "koop":
            bezit[ticker] = huidig + aantal
        elif soort == "correctie":
            bezit[ticker] = aantal
        elif aantal > huidig + 1e-9:
            return (f"Niet genoeg {ticker} op {datum.strftime('%d-%m-%Y')} om "
                    f"{aantal:g} stuks te verkopen (max {huidig:g}).")
        else:
            bezit[ticker] = huidig - aantal
    return None


def _autoriseer_account(account, gebruiker_id):
    """True als het account bij de gebruiker hoort; anders flash + False."""
    if account.gebruiker_id != gebruiker_id:
        flash("Niet geautoriseerd.", "danger")
        return False
    return True


@app.route("/gebruiker/<int:gebruiker_id>/account/<int:account_id>/transacties")
def transacties_overzicht(gebruiker_id, account_id):
    gebruiker = Gebruiker.query.get_or_404(gebruiker_id)
    account   = BrokerAccount.query.get_or_404(account_id)
    if not _autoriseer_account(account, gebruiker_id):
        return redirect(url_for("dashboard", gebruiker_id=gebruiker_id))

    q = Transactie.query.filter_by(broker_account_id=account.id)
    ticker_filter = request.args.get("ticker", "").strip().upper()
    if ticker_filter:
        q = q.filter_by(ticker=ticker_filter)
    transacties = q.order_by(Transactie.datum.desc(), Transactie.id.desc()).all()

    return render_template("transacties.html",
                           gebruiker=gebruiker, account=account,
                           transacties=transacties, cash=cash_saldi(account.id),
                           ticker_filter=ticker_filter, types=TRANSACTIE_TYPES)


@app.route("/gebruiker/<int:gebruiker_id>/account/<int:account_id>/transactie/toevoegen",
           methods=["GET", "POST"])
def transactie_toevoegen(gebruiker_id, account_id):
    gebruiker = Gebruiker.query.get_or_404(gebruiker_id)
    account   = BrokerAccount.query.get_or_404(account_id)
    if not _autoriseer_account(account, gebruiker_id):
        return redirect(url_for("dashboard", gebruiker_id=gebruiker_id))

    if request.method == "POST":
        waarden, fouten = _parse_transactie_form(request.form, account)
        if not fouten:
            verloopfout = _valideer_aandelenverloop(account.id, waarden)
            if verloopfout:
                fouten.append(verloopfout)
        if fouten:
            for f in fouten:
                flash(f, "danger")
            return render_template("transactie_form.html", gebruiker=gebruiker,
                                   account=account, tx=None, types=TRANSACTIE_TYPES,
                                   form_data=request.form, vandaag=date.today().isoformat())
        db.session.add(Transactie(broker_account_id=account.id, **waarden))
        db.session.flush()
        na_transactie_wijziging(account.id, waarden["ticker"])
        db.session.commit()
        flash("Transactie toegevoegd.", "success")
        return redirect(url_for("transacties_overzicht",
                                gebruiker_id=gebruiker_id, account_id=account.id))

    # Voorvullen via de querystring (?ticker=…&type=koop), zodat het advies
    # rechtstreeks naar een ingevuld boekingsformulier kan linken.
    return render_template("transactie_form.html", gebruiker=gebruiker,
                           account=account, tx=None, types=TRANSACTIE_TYPES,
                           form_data=request.args, vandaag=date.today().isoformat())


@app.route("/gebruiker/<int:gebruiker_id>/transactie/<int:transactie_id>/bewerken",
           methods=["GET", "POST"])
def transactie_bewerken(gebruiker_id, transactie_id):
    gebruiker = Gebruiker.query.get_or_404(gebruiker_id)
    tx        = Transactie.query.get_or_404(transactie_id)
    account   = tx.broker_account
    if not _autoriseer_account(account, gebruiker_id):
        return redirect(url_for("dashboard", gebruiker_id=gebruiker_id))

    if request.method == "POST":
        oude_ticker = tx.ticker
        waarden, fouten = _parse_transactie_form(request.form, account)
        if not fouten:
            verloopfout = _valideer_aandelenverloop(account.id, waarden, tx.id)
            if verloopfout:
                fouten.append(verloopfout)
        if fouten:
            for f in fouten:
                flash(f, "danger")
            return render_template("transactie_form.html", gebruiker=gebruiker,
                                   account=account, tx=tx, types=TRANSACTIE_TYPES,
                                   form_data=request.form, vandaag=date.today().isoformat())
        for veld, waarde in waarden.items():
            setattr(tx, veld, waarde)
        db.session.flush()
        na_transactie_wijziging(account.id, oude_ticker, waarden["ticker"])
        db.session.commit()
        flash("Transactie bijgewerkt.", "success")
        return redirect(url_for("transacties_overzicht",
                                gebruiker_id=gebruiker_id, account_id=account.id))

    form_data = {
        "type": tx.type, "ticker": tx.ticker or "", "aantal": tx.aantal,
        "prijs": tx.prijs, "bedrag": tx.bedrag, "kosten": tx.kosten,
        "valuta": tx.valuta, "datum": tx.datum.isoformat() if tx.datum else "",
        "notitie": tx.notitie or "",
    }
    return render_template("transactie_form.html", gebruiker=gebruiker,
                           account=account, tx=tx, types=TRANSACTIE_TYPES,
                           form_data=form_data, vandaag=date.today().isoformat())


@app.route("/gebruiker/<int:gebruiker_id>/transactie/<int:transactie_id>/verwijderen",
           methods=["POST"])
def transactie_verwijderen(gebruiker_id, transactie_id):
    tx      = Transactie.query.get_or_404(transactie_id)
    account = tx.broker_account
    if not _autoriseer_account(account, gebruiker_id):
        return redirect(url_for("dashboard", gebruiker_id=gebruiker_id))
    account_id, ticker = account.id, tx.ticker
    db.session.delete(tx)
    db.session.flush()
    na_transactie_wijziging(account_id, ticker)
    db.session.commit()
    flash("Transactie verwijderd.", "info")
    return redirect(url_for("transacties_overzicht",
                            gebruiker_id=gebruiker_id, account_id=account_id))


# ---------------------------------------------------------------------------
# Rabo Zakelijk Import — mutatieoverzicht (CSV) inlezen in het grootboek
# ---------------------------------------------------------------------------
# Alleen zichtbaar op het grootboek van de zakelijke Rabo-rekening; andere
# accounts hebben een ander exportformaat en zouden er niets aan hebben.

RABO_IMPORT_ACCOUNT = "Rabobank Zakelijk"


def is_rabo_import_account(account):
    return bool(account) and account.naam.strip().lower() == RABO_IMPORT_ACCOUNT.lower()


app.jinja_env.globals["is_rabo_import_account"] = is_rabo_import_account


def _rabo_splits(account, regels):
    """Verdeel de gelezen regels in 'nieuw' en 'staat er al'.

    Vergelijkt op aantal per signatuur, niet op bestaan: de inleg van 28-08
    stond als drie identieke boekingen van 50.000 op het afschrift, en die
    moeten alle drie geboekt worden.
    """
    bestaand = Counter(
        rabo_import.transactie_signatuur(tx)
        for tx in Transactie.query.filter_by(broker_account_id=account.id).all()
    )
    nieuw, dubbel, gezien = [], [], Counter()
    for regel in regels:
        sig = rabo_import.regel_signatuur(regel)
        gezien[sig] += 1
        if gezien[sig] <= bestaand.get(sig, 0):
            dubbel.append(regel)
        else:
            nieuw.append(regel)
    return nieuw, dubbel


def _rabo_serialiseer(regels):
    """Regels naar een ondertekend token voor het bevestigingsformulier."""
    uit = []
    for r in regels:
        kopie = dict(r)
        kopie["boek_datum"] = r["boek_datum"].isoformat()
        kopie["bron_datum"] = r["bron_datum"].isoformat()
        uit.append(kopie)
    return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="rabo-import").dumps(uit)


def _rabo_deserialiseer(payload):
    regels = URLSafeTimedSerializer(
        app.config["SECRET_KEY"], salt="rabo-import"
    ).loads(payload, max_age=30 * 60)
    if not isinstance(regels, list):
        raise BadData("Ongeldige importgegevens")
    for r in regels:
        if not isinstance(r, dict):
            raise BadData("Ongeldige importregel")
        r["boek_datum"] = date.fromisoformat(r["boek_datum"])
        r["bron_datum"] = date.fromisoformat(r["bron_datum"])
    return regels


@app.route("/gebruiker/<int:gebruiker_id>/account/<int:account_id>/rabo-import",
           methods=["POST"])
def rabo_import_voorbeeld(gebruiker_id, account_id):
    """Stap 1: bestand inlezen en tonen wat er geboekt zou worden."""
    gebruiker = Gebruiker.query.get_or_404(gebruiker_id)
    account   = BrokerAccount.query.get_or_404(account_id)
    terug = redirect(url_for("transacties_overzicht",
                             gebruiker_id=gebruiker_id, account_id=account_id))
    if not _autoriseer_account(account, gebruiker_id):
        return redirect(url_for("dashboard", gebruiker_id=gebruiker_id))
    if not is_rabo_import_account(account):
        flash("Rabo Zakelijk Import is alleen beschikbaar op de zakelijke "
              "Rabo-rekening.", "danger")
        return terug

    bestand = request.files.get("bestand")
    if bestand is None or not bestand.filename:
        flash("Geen bestand gekozen.", "danger")
        return terug
    if not bestand.filename.lower().endswith(".csv"):
        flash("Kies het CSV-mutatieoverzicht zoals Rabo het levert "
              "(Mutatieoverzicht_…csv).", "danger")
        return terug

    try:
        regels, overgeslagen = rabo_import.parse(bestand.read())
    except rabo_import.ImportFout as fout:
        flash(str(fout), "danger")
        return terug

    nieuw, dubbel = _rabo_splits(account, regels)
    return render_template("rabo_import.html",
                           gebruiker=gebruiker, account=account,
                           bestandsnaam=bestand.filename,
                           nieuw=nieuw, dubbel=dubbel,
                           overgeslagen=overgeslagen,
                           payload=_rabo_serialiseer(nieuw))


@app.route("/gebruiker/<int:gebruiker_id>/account/<int:account_id>/rabo-import/bevestigen",
           methods=["POST"])
def rabo_import_bevestigen(gebruiker_id, account_id):
    """Stap 2: pas ná bevestiging schrijven we naar het grootboek."""
    account = BrokerAccount.query.get_or_404(account_id)
    terug = redirect(url_for("transacties_overzicht",
                             gebruiker_id=gebruiker_id, account_id=account_id))
    if not _autoriseer_account(account, gebruiker_id):
        return redirect(url_for("dashboard", gebruiker_id=gebruiker_id))
    if not is_rabo_import_account(account):
        flash("Rabo Zakelijk Import is alleen beschikbaar op de zakelijke "
              "Rabo-rekening.", "danger")
        return terug

    try:
        regels = _rabo_deserialiseer(request.form.get("payload", "[]"))
    except (BadData, KeyError, ValueError, TypeError):
        flash("De import is verlopen of onleesbaar. Kies het bestand opnieuw.",
              "danger")
        return terug

    # Nog een keer tegen het grootboek houden: tussen voorbeeld en bevestiging
    # kan er iets geboekt zijn (of is er twee keer op de knop gedrukt).
    nieuw, dubbel = _rabo_splits(account, regels)
    if not nieuw:
        flash("Alle mutaties uit dit bestand stonden al in het grootboek. "
              "Er is niets toegevoegd.", "info")
        return terug

    tickers = set()
    for regel in nieuw:
        db.session.add(Transactie(
            broker_account_id=account.id,
            type=regel["type"],
            ticker=regel["ticker"],
            aantal=regel["aantal"],
            prijs=regel["prijs"],
            bedrag=regel["bedrag"] if regel["type"] in
                   ("storting", "opname", "dividend", "kosten") else None,
            kosten=regel["kosten"] or 0.0,
            valuta=regel["valuta"] or "EUR",
            datum=regel["boek_datum"],
            notitie=regel["notitie"][:200],
        ))
        if regel["ticker"]:
            tickers.add(regel["ticker"])

    db.session.flush()
    if tickers:
        na_transactie_wijziging(account.id, *tickers)
    db.session.commit()

    melding = f"{len(nieuw)} mutatie(s) geboekt."
    if dubbel:
        melding += f" {len(dubbel)} stond(en) al in het grootboek en zijn overgeslagen."
    flash(melding, "success")
    return terug


@app.route("/gebruiker/<int:gebruiker_id>/herbereken", methods=["POST"])
def herbereken(gebruiker_id):
    Gebruiker.query.get_or_404(gebruiker_id)
    herbereken_alles(gebruiker_id)
    flash("Holdings opnieuw berekend uit het grootboek.", "success")
    return redirect(url_for("dashboard", gebruiker_id=gebruiker_id))


# ── Tags ──────────────────────────────────────────────────────────

@app.route("/gebruiker/<int:gebruiker_id>/tags")
def tags_overzicht(gebruiker_id):
    gebruiker = Gebruiker.query.get_or_404(gebruiker_id)
    tags = Tag.query.filter_by(gebruiker_id=gebruiker_id).order_by(Tag.naam).all()
    return render_template("tags.html", gebruiker=gebruiker, tags=tags)


@app.route("/gebruiker/<int:gebruiker_id>/tags/toevoegen", methods=["POST"])
def tag_toevoegen(gebruiker_id):
    gebruiker = Gebruiker.query.get_or_404(gebruiker_id)
    naam = request.form.get("naam", "").strip()
    if not naam:
        flash("Naam is verplicht.", "danger")
    elif Tag.query.filter_by(gebruiker_id=gebruiker_id, naam=naam).first():
        flash(f"Tag '{naam}' bestaat al.", "warning")
    else:
        db.session.add(Tag(gebruiker_id=gebruiker_id, naam=naam))
        db.session.commit()
        flash(f"Tag '{naam}' toegevoegd.", "success")
    return redirect(url_for("tags_overzicht", gebruiker_id=gebruiker_id))


@app.route("/gebruiker/<int:gebruiker_id>/tags/<int:tag_id>/verwijderen", methods=["POST"])
def tag_verwijderen(gebruiker_id, tag_id):
    tag = Tag.query.get_or_404(tag_id)
    if tag.gebruiker_id != gebruiker_id:
        flash("Niet geautoriseerd.", "danger")
        return redirect(url_for("tags_overzicht", gebruiker_id=gebruiker_id))
    naam = tag.naam
    db.session.delete(tag)
    db.session.commit()
    flash(f"Tag '{naam}' verwijderd.", "info")
    return redirect(url_for("tags_overzicht", gebruiker_id=gebruiker_id))


# ── Volglijst (kandidaten voor advies) ────────────────────────────

@app.route("/gebruiker/<int:gebruiker_id>/volglijst/toevoegen", methods=["POST"])
def volglijst_toevoegen(gebruiker_id):
    Gebruiker.query.get_or_404(gebruiker_id)
    ticker  = request.form.get("ticker", "").strip().upper()
    notitie = request.form.get("notitie", "").strip()[:200] or None
    if not ticker:
        flash("Ticker is verplicht.", "danger")
    elif Volglijst.query.filter_by(gebruiker_id=gebruiker_id, ticker=ticker).first():
        flash(f"{ticker} staat al op je volglijst.", "warning")
    else:
        db.session.add(Volglijst(gebruiker_id=gebruiker_id, ticker=ticker, notitie=notitie))
        db.session.commit()
        flash(f"{ticker} toegevoegd aan de volglijst.", "success")
    return redirect(url_for("advies", gebruiker_id=gebruiker_id))


@app.route("/gebruiker/<int:gebruiker_id>/volglijst/<int:item_id>/verwijderen", methods=["POST"])
def volglijst_verwijderen(gebruiker_id, item_id):
    item = Volglijst.query.get_or_404(item_id)
    if item.gebruiker_id != gebruiker_id:
        flash("Niet geautoriseerd.", "danger")
        return redirect(url_for("advies", gebruiker_id=gebruiker_id))
    ticker = item.ticker
    db.session.delete(item)
    db.session.commit()
    flash(f"{ticker} van de volglijst verwijderd.", "info")
    return redirect(url_for("advies", gebruiker_id=gebruiker_id))


# ── Achtergrondtaken (verversen koersen/nieuws/advies) ────────────
# Verversingen draaien in een achtergrond-thread zodat het HTTP-verzoek niet
# tot 120s blijft hangen. De status wordt in-memory bijgehouden; de frontend
# pollt /taken/<id>/status en herlaadt de pagina zodra een taak klaar is.

_taken       = {}
_taken_lock  = threading.Lock()
_taak_teller = count(1)


def _draai_taak(taak_id, cmd, timeout, klaar_bericht):
    cat, msg = "success", klaar_bericht
    try:
        commands = cmd if cmd and isinstance(cmd[0], (list, tuple)) else [cmd]
        for command in commands:
            subprocess.run(command, timeout=timeout, check=True)
    except subprocess.TimeoutExpired:
        cat, msg = "warning", "Bewerking duurde te lang en is afgebroken."
    except subprocess.CalledProcessError as e:
        cat, msg = "danger", f"Fout bij uitvoeren: {e}"
    except Exception as e:
        cat, msg = "danger", f"Onverwachte fout: {e}"
    with _taken_lock:
        _taken[taak_id].update(status="klaar", categorie=cat, bericht=msg)


def _start_taak(naam, cmd, timeout, klaar_bericht):
    """Start cmd in een achtergrond-thread. Voorkomt dubbele gelijktijdige runs
    van dezelfde taaknaam. Geeft (taak_id, al_bezig) terug."""
    with _taken_lock:
        for tid, t in _taken.items():
            if t["naam"] == naam and t["status"] == "bezig":
                return tid, True
        taak_id = f"{naam}-{next(_taak_teller)}"
        _taken[taak_id] = {"naam": naam, "status": "bezig",
                           "categorie": None, "bericht": None}
    threading.Thread(target=_draai_taak,
                     args=(taak_id, cmd, timeout, klaar_bericht),
                     daemon=True).start()
    return taak_id, False


@app.route("/taken/<taak_id>/status")
def taak_status(taak_id):
    with _taken_lock:
        t = _taken.get(taak_id)
        if not t:
            return {"status": "onbekend"}, 404
        return {"status": t["status"], "categorie": t["categorie"],
                "bericht": t["bericht"]}


# ── Koersen ───────────────────────────────────────────────────────

@app.route("/prijzen/verversen", methods=["POST"])
def prijzen_verversen():
    taak_id, al_bezig = _start_taak(
        "koersen",
        [sys.executable, str(BASE_DIR / "fetch_prices.py")],
        timeout=60, klaar_bericht="Koersen bijgewerkt.")
    return {"taak_id": taak_id, "al_bezig": al_bezig}


@app.route("/benchmark/verversen", methods=["POST"])
def benchmark_verversen():
    taak_id, al_bezig = _start_taak(
        "benchmark",
        [sys.executable, str(BASE_DIR / "fetch_benchmark.py")],
        timeout=120, klaar_bericht="Benchmark-historie bijgewerkt.")
    return {"taak_id": taak_id, "al_bezig": al_bezig}


@app.route("/gebruiker/<int:gebruiker_id>/gegevens/verversen", methods=["POST"])
def gegevens_verversen(gebruiker_id):
    """Werk alle databronnen vanuit één centrale gebruikersactie bij."""
    Gebruiker.query.get_or_404(gebruiker_id)
    scripts = [
        "fetch_prices.py",
        "fetch_benchmark.py",
        "fetch_historie.py",
        "fetch_fundamentals.py",
        "fetch_news.py",
    ]
    commands = [[sys.executable, str(BASE_DIR / script)] for script in scripts]
    taak_id, al_bezig = _start_taak(
        "alle-gegevens",
        commands,
        timeout=180,
        klaar_bericht="Koersen, benchmark, risico-data, fundamentals en nieuws bijgewerkt.",
    )
    return {"taak_id": taak_id, "al_bezig": al_bezig}


# ── Ticker zoeken (autocomplete) ──────────────────────────────────

# Lagere waarde = hoger in de suggestielijst. Liquide EUR-beurzen eerst,
# dun verhandelde beurzen achteraan (geven vaak geen live koers terug).
_BEURS_PRIORITEIT = {
    "Amsterdam": 0, "XETRA": 1, "Milan": 2, "Paris": 3,
    "Madrid": 4, "Brussels": 4, "London": 6, "Frankfurt": 6,
    "Dublin": 7, "Vienna": 7,
    "Stuttgart": 9, "Hamburg": 9, "Munich": 9, "Berlin": 9,
}


def _rangschik_tickers(resultaten):
    """Zet liquide EUR-beurzen bovenaan en kale ISIN-listings onderaan."""
    def sleutel(idx, item):
        base    = item["symbol"].split(".")[0]
        is_isin = bool(re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9,10}", base))
        prio    = _BEURS_PRIORITEIT.get(item["beurs"], 5)
        return (is_isin, prio, idx)   # idx houdt de oorspronkelijke volgorde stabiel
    return [it for _, it in sorted(enumerate(resultaten),
                                   key=lambda p: sleutel(p[0], p[1]))]


@app.route("/api/ticker-zoeken")
def ticker_zoeken():
    """Zoek tickers via de Yahoo Finance-zoek-API voor de autocomplete."""
    import requests
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return {"resultaten": []}
    try:
        r = requests.get(
            "https://query2.finance.yahoo.com/v1/finance/search",
            params={"q": q, "quotesCount": 8, "newsCount": 0},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=6,
        )
        r.raise_for_status()
        quotes = r.json().get("quotes", [])
    except Exception:
        return {"resultaten": []}

    resultaten = []
    for it in quotes:
        sym = it.get("symbol")
        if not sym:
            continue
        resultaten.append({
            "symbol": sym,
            "naam":   it.get("shortname") or it.get("longname") or "",
            "beurs":  it.get("exchDisp") or "",
            "type":   it.get("quoteType") or "",
        })
    return {"resultaten": _rangschik_tickers(resultaten)}


# ── Adviesmodule ──────────────────────────────────────────────────

@app.route("/gebruiker/<int:gebruiker_id>/advies")
def advies(gebruiker_id):
    gebruiker = Gebruiker.query.get_or_404(gebruiker_id)

    # Meest recente generiek advies (tag_id is None)
    latest = (Advies.query
              .filter_by(gebruiker_id=gebruiker_id, tag_id=None)
              .order_by(Advies.gegenereerd.desc())
              .first())

    # Meest recente advies per tag — één query i.p.v. een query per tag; daarna
    # in Python het nieuwste advies per tag pakken (desc-volgorde → eerste = nieuwste).
    tags = Tag.query.filter_by(gebruiker_id=gebruiker_id).order_by(Tag.naam).all()
    tag_op_id = {t.id: t for t in tags}
    tag_adviezen = {}
    for a in (Advies.query
              .filter(Advies.gebruiker_id == gebruiker_id, Advies.tag_id.isnot(None))
              .order_by(Advies.gegenereerd.desc())
              .all()):
        tag = tag_op_id.get(a.tag_id)
        if tag is not None and tag not in tag_adviezen:
            tag_adviezen[tag] = a

    # Advieshistorie (14 dagen)
    grens = datetime.now() - timedelta(days=14)
    historie = (Advies.query
                .filter(Advies.gebruiker_id == gebruiker_id,
                        Advies.gegenereerd >= grens)
                .order_by(Advies.gegenereerd.desc())
                .all())
    if latest:
        historie = [a for a in historie if a.id != latest.id]

    # Waardeontwikkeling voor grafiek
    koersen_nu, _, _ = laad_prijzen()
    waarde_serie = []
    for a in (Advies.query
              .filter(Advies.gebruiker_id == gebruiker_id,
                      Advies.tag_id == None,
                      Advies.gegenereerd >= grens)
              .order_by(Advies.gegenereerd).all()):
        try:
            snap = json.loads(a.portfolio_snapshot)
        except (ValueError, TypeError):
            continue
        waarde = snap.get("totaal_waarde")
        kosten = snap.get("totaal_kosten")
        if waarde is None or kosten is None:
            w = k = 0.0
            for p in snap.get("posities", []):
                koers = koersen_nu.get(p["ticker"], {}).get("koers")
                k += p["aantal"] * p["aankoopprijs"]
                if koers:
                    w += p["aantal"] * koers
            waarde = round(w, 2) if w else None
            kosten = round(k, 2)
        waarde_serie.append({
            "datum":  a.gegenereerd.strftime("%d-%m %H:%M"),
            "waarde": waarde,
            "kosten": kosten,
            "risico": a.risico_score,
        })

    # Prestatie aanbevelingen — eager-load aanbevelingen (voorkomt een query per advies)
    eerste_tip = {}
    for a in (Advies.query.filter_by(gebruiker_id=gebruiker_id)
              .options(selectinload(Advies.aanbevelingen))
              .order_by(Advies.gegenereerd).all()):
        for rec in a.aanbevelingen:
            if rec.ticker not in eerste_tip:
                eerste_tip[rec.ticker] = (rec, a.gegenereerd)
    tips_prestatie = []
    nu = datetime.now()
    for ticker, (rec, dt) in eerste_tip.items():
        huidig = koersen_nu.get(ticker, {}).get("koers")
        rendement = (((huidig - rec.instapkoers) / rec.instapkoers * 100)
                     if huidig and rec.instapkoers else None)
        tips_prestatie.append({
            "ticker":    ticker,
            # Mét jaar: een tip van 17-06 kan uit elk jaar komen.
            "sinds":     dt.strftime("%d-%m-%Y"),
            "sinds_rel": _relatieve_tijd(dt, nu),
            "instap":    rec.instapkoers,
            "huidig":    huidig,
            "valuta":    rec.valuta or "",
            "rendement": round(rendement, 1) if rendement is not None else None,
        })
    tips_prestatie.sort(key=lambda t: (t["rendement"] is None, -(t["rendement"] or 0)))

    # ── punt 10: samenvatting boven de tabel — het vertrouwenscijfer ──
    met_rendement = [t["rendement"] for t in tips_prestatie if t["rendement"] is not None]
    tips_samenvatting = {
        "aantal":    len(tips_prestatie),
        "gemeten":   len(met_rendement),
        "positief":  sum(1 for r in met_rendement if r > 0),
        "gemiddeld": (sum(met_rendement) / len(met_rendement)) if met_rendement else None,
    }

    # Ticker-nieuws voor posities van deze gebruiker.
    # Eén query voor alle tickers (i.p.v. een query per positie), daarna in
    # Python groeperen en per ticker tot 5 meest recente artikelen bewaren.
    grens_nieuws = datetime.now() - timedelta(days=3)
    tickers = {t for (t,) in
               db.session.query(Positie.ticker)
               .join(BrokerAccount, Positie.broker_account_id == BrokerAccount.id)
               .filter(BrokerAccount.gebruiker_id == gebruiker_id).all()}
    ticker_nieuws = {}
    if tickers:
        artikelen = (NieuwsArtikel.query
                     .filter(NieuwsArtikel.ticker.in_(tickers),
                             NieuwsArtikel.opgeslagen >= grens_nieuws)
                     .order_by(NieuwsArtikel.gepubliceerd.desc())
                     .all())
        for art in artikelen:
            bucket = ticker_nieuws.setdefault(art.ticker, [])
            if len(bucket) < 5:
                bucket.append(art)

    # Volglijst + (gecachte) fundamentals van de kandidaten
    volg = (Volglijst.query.filter_by(gebruiker_id=gebruiker_id)
            .order_by(Volglijst.ticker).all())
    volg_funds = {f.ticker: f for f in Fundamental.query
                  .filter(Fundamental.ticker.in_([v.ticker for v in volg] or [""])).all()}
    volglijst = [{"item": v, "fund": volg_funds.get(v.ticker)} for v in volg]
    volglijst_tickers = {v.ticker.upper() for v in volg}

    # Doelrekening voor "Boek deze koop": die met de meeste posities, anders de
    # eerste. Bij meerdere rekeningen is dat een gok, maar het formulier laat de
    # rekening zien en is één klik van het dashboard te corrigeren.
    accounts = (BrokerAccount.query.filter_by(gebruiker_id=gebruiker_id)
                .options(selectinload(BrokerAccount.posities))
                .order_by(BrokerAccount.id).all())
    koop_account = max(accounts, key=lambda a: len(a.posities), default=None)

    # Punt 11: het model markeert posities met gekleurde bollen. Toon alleen de
    # bollen die in dít advies voorkomen, met de betekenis erbij.
    _BOLLEN = [("\U0001F7E2", "positief — houden of bijkopen"),
               ("\U0001F7E1", "gemengd — let op, deels afbouwen of optioneel"),
               ("\U0001F534", "negatief — verkopen of sterk reduceren"),
               ("\U0001F535", "kans — nieuw beoordeeld, nog geen positie")]
    advies_legenda = ([{"bol": b, "betekenis": u} for b, u in _BOLLEN
                       if latest and b in latest.advies_tekst])

    return render_template(
        "advies.html",
        advies_legenda = advies_legenda,
        gebruiker      = gebruiker,
        advies         = latest,
        # Het advies is markdown van een taalmodel; advies_parser knipt het in
        # ##-secties en haalt de kernboodschap en de concrete acties eruit.
        advies_secties = advies_parser.secties(latest.advies_tekst) if latest else [],
        advies_kern    = advies_parser.kernboodschap(latest.advies_tekst) if latest else "",
        advies_acties  = advies_parser.hoofdacties(latest.advies_tekst) if latest else [],
        # Punt 7: een advies praat over "vandaag", maar is een momentopname.
        advies_leeftijd = ((datetime.now() - latest.gegenereerd).days) if latest else None,
        tag_adviezen   = tag_adviezen,
        tags           = tags,
        historie       = historie,
        waarde_serie   = waarde_serie,
        tips_prestatie = tips_prestatie,
        tips_samenvatting = tips_samenvatting,
        ticker_nieuws  = ticker_nieuws,
        volglijst      = volglijst,
        volglijst_tickers = volglijst_tickers,
        koop_account   = koop_account,
        heeft_api_key  = bool(os.environ.get("ANTHROPIC_API_KEY")),
    )


@app.route("/gebruiker/<int:gebruiker_id>/advies/genereer", methods=["POST"])
def advies_genereer(gebruiker_id):
    Gebruiker.query.get_or_404(gebruiker_id)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"taak_id": None,
                "fout": "Stel eerst ANTHROPIC_API_KEY in als omgevingsvariabele."}, 400
    tag_id = request.form.get("tag_id", type=int)  # None = generiek
    if tag_id and not Tag.query.filter_by(id=tag_id, gebruiker_id=gebruiker_id).first():
        return {"taak_id": None, "fout": "Ongeldige tag."}, 400
    cmd = [sys.executable, str(BASE_DIR / "advies_generator.py"),
           "--gebruiker", str(gebruiker_id)]
    if tag_id:
        cmd += ["--tag", str(tag_id)]
    taak_id, al_bezig = _start_taak(
        f"advies-{gebruiker_id}-{tag_id or 'generiek'}",
        cmd, timeout=120, klaar_bericht="Advies gegenereerd.")
    return {"taak_id": taak_id, "al_bezig": al_bezig}


@app.route("/gebruiker/<int:gebruiker_id>/advies/team-genereer", methods=["POST"])
def advies_team_genereer(gebruiker_id):
    """Team-advies: 4 Claude-agents (Gewaagd/Gemiddeld/Macro-briefing/Persoonlijk
    perspectief) + eindsynthese, i.p.v. de enkelvoudige advies_generator.py-call.
    Duurt langer (meerdere sequentiële API-calls) — vandaar de ruimere timeout."""
    Gebruiker.query.get_or_404(gebruiker_id)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"taak_id": None,
                "fout": "Stel eerst ANTHROPIC_API_KEY in als omgevingsvariabele."}, 400
    tag_id = request.form.get("tag_id", type=int)  # None = generiek
    if tag_id and not Tag.query.filter_by(id=tag_id, gebruiker_id=gebruiker_id).first():
        return {"taak_id": None, "fout": "Ongeldige tag."}, 400
    cmd = [sys.executable, str(BASE_DIR / "advies_team.py"),
           "--gebruiker", str(gebruiker_id)]
    if tag_id:
        cmd += ["--tag", str(tag_id)]
    taak_id, al_bezig = _start_taak(
        f"advies-team-{gebruiker_id}-{tag_id or 'generiek'}",
        cmd, timeout=240, klaar_bericht="Team-advies gegenereerd.")
    return {"taak_id": taak_id, "al_bezig": al_bezig}


@app.route("/gebruiker/<int:gebruiker_id>/risicoprofiel/bijwerken", methods=["POST"])
def risicoprofiel_bijwerken(gebruiker_id):
    """Persoonlijke context voor de 'Persoonlijk perspectief'-rol in het
    team-advies (bijv. privé vs. zakelijk vermogen, risicohouding, horizon)."""
    gebruiker = Gebruiker.query.get_or_404(gebruiker_id)
    gebruiker.risicoprofiel = request.form.get("risicoprofiel", "").strip() or None
    db.session.commit()
    flash("Risicoprofiel opgeslagen.", "success")
    return redirect(url_for("advies", gebruiker_id=gebruiker_id))


@app.route("/gebruiker/<int:gebruiker_id>/nieuws/verversen", methods=["POST"])
def nieuws_verversen(gebruiker_id):
    taak_id, al_bezig = _start_taak(
        "nieuws",
        [sys.executable, str(BASE_DIR / "fetch_news.py")],
        timeout=120, klaar_bericht="Nieuws bijgewerkt.")
    return {"taak_id": taak_id, "al_bezig": al_bezig}


@app.route("/gebruiker/<int:gebruiker_id>/fundamentals/verversen", methods=["POST"])
def fundamentals_verversen(gebruiker_id):
    taak_id, al_bezig = _start_taak(
        "fundamentals",
        [sys.executable, str(BASE_DIR / "fetch_fundamentals.py")],
        timeout=120, klaar_bericht="Fundamentals bijgewerkt.")
    return {"taak_id": taak_id, "al_bezig": al_bezig}


@app.route("/historie/verversen", methods=["POST"])
def historie_verversen():
    taak_id, al_bezig = _start_taak(
        "historie",
        [sys.executable, str(BASE_DIR / "fetch_historie.py")],
        timeout=180, klaar_bericht="Risico-data bijgewerkt.")
    return {"taak_id": taak_id, "al_bezig": al_bezig}


# ── Database initialisatie ────────────────────────────────────────

def _kolom_default_sql(kolom):
    """Geef een SQL-literal voor de scalar default van een kolom, of None.

    SQLite vereist een constante DEFAULT bij het toevoegen van een NOT NULL-kolom.
    """
    default = kolom.default
    if default is None or not getattr(default, "is_scalar", False):
        return None
    waarde = default.arg
    if isinstance(waarde, bool):
        return "1" if waarde else "0"
    if isinstance(waarde, (int, float)):
        return str(waarde)
    if isinstance(waarde, str):
        return "'" + waarde.replace("'", "''") + "'"
    return None


def _sync_schema():
    """Lichtgewicht additieve migratie: voeg kolommen toe die in de modellen
    staan maar nog niet in een bestaande tabel.

    Dit dekt de gangbare casus voor deze app — een nieuw veld op een bestaand
    model (bijv. Positie.valuta) — zonder dat bestaande databases handmatig
    gemigreerd hoeven te worden. SQLite kan kolommen niet wijzigen of
    verwijderen; voor zulke veranderingen is nog steeds een handmatige migratie
    nodig.
    """
    inspector = sa_inspect(db.engine)
    bestaande_tabellen = set(inspector.get_table_names())

    for tabel_naam, tabel in db.metadata.tables.items():
        if tabel_naam not in bestaande_tabellen:
            continue   # nieuwe tabel — create_all() heeft 'm net aangemaakt
        bestaande_kolommen = {c["name"] for c in inspector.get_columns(tabel_naam)}
        for kolom in tabel.columns:
            if kolom.name in bestaande_kolommen:
                continue
            type_sql = kolom.type.compile(dialect=db.engine.dialect)
            ddl = f'ALTER TABLE "{tabel_naam}" ADD COLUMN "{kolom.name}" {type_sql}'
            default_sql = _kolom_default_sql(kolom)
            if default_sql is not None:
                ddl += f" DEFAULT {default_sql}"
                if not kolom.nullable:
                    ddl += " NOT NULL"
            elif not kolom.nullable:
                # Geen constante default beschikbaar: voeg toe als nullable zodat
                # de migratie niet faalt op bestaande rijen. Waarschuw expliciet.
                print(f"[migratie] WAARSCHUWING: {tabel_naam}.{kolom.name} is "
                      f"NOT NULL zonder default — toegevoegd als nullable.")
            db.session.execute(text(ddl))
            print(f"[migratie] kolom toegevoegd: {tabel_naam}.{kolom.name}")
    db.session.commit()


def _backfill_transacties():
    """Eenmalige migratie: zet bestaande (handmatig ingevoerde) posities om naar
    een opening-koop in het grootboek, zodat holdings voortaan afgeleid worden.

    Idempotent: draait alleen als er nog géén transacties zijn. (Wie later álle
    transacties handmatig wist, zou bij herstart opnieuw geseed worden — voor een
    lokale single-user app acceptabel.)
    """
    if Transactie.query.first() is not None:
        return
    posities = Positie.query.all()
    if not posities:
        return

    for pos in posities:
        if (pos.aantal or 0.0) <= 0:
            continue
        db.session.add(Transactie(
            broker_account_id=pos.broker_account_id, type="koop",
            ticker=pos.ticker, aantal=pos.aantal, prijs=pos.aankoopprijs,
            kosten=0.0, valuta=pos.valuta or "EUR",
            datum=pos.aankoopdatum or date(2020, 1, 1),
            notitie="Openingssaldo (automatisch)",
        ))
    db.session.flush()

    # Dubbele posities per (account, ticker) samenvoegen: laagste id blijft,
    # tags worden geünioneerd, de rest verwijderd; daarna herprojecteren tot één
    # holding met gewogen-gemiddelde GAK over de opening-koops.
    groepen = {}
    for pos in Positie.query.order_by(Positie.id).all():
        groepen.setdefault((pos.broker_account_id, pos.ticker), []).append(pos)
    for (account_id, ticker), groep in groepen.items():
        survivor = groep[0]
        for dubbel in groep[1:]:
            for tag in dubbel.tags:
                if tag not in survivor.tags:
                    survivor.tags.append(tag)
            db.session.delete(dubbel)
        db.session.flush()
        herbereken_positie(account_id, ticker)
    db.session.commit()
    print(f"[migratie] grootboek-backfill voltooid ({len(posities)} posities verwerkt).")


def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    db.create_all()
    _sync_schema()

    if not Gebruiker.query.first():
        g = Gebruiker(naam="Mijn account")
        db.session.add(g)
        db.session.flush()
        db.session.add(BrokerAccount(naam="Mijn portefeuille", gebruiker_id=g.id))
        db.session.commit()

    _backfill_transacties()


with app.app_context():
    init_db()

if __name__ == "__main__":
    # Debug staat standaard UIT: de Werkzeug-debugger voert willekeurige code uit
    # bij een fout en mag nooit zomaar aanstaan. Zet FLASK_DEBUG=1 om 'm tijdens
    # ontwikkeling aan te zetten.
    #
    # Bind standaard op 127.0.0.1 (lokaal/veilig op de Mac). Op de Raspberry Pi
    # zet je HOST=0.0.0.0 (via de systemd-service) om de app op het thuisnetwerk
    # bereikbaar te maken. In productie draaien we via gunicorn (zie deploy/),
    # dat de bind zelf bepaalt; deze fallback is voor `python app.py`.
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes", "on")
    host  = os.environ.get("HOST", "127.0.0.1")
    port  = int(os.environ.get("PORT", "5002"))
    app.run(host=host, port=port, debug=debug)
