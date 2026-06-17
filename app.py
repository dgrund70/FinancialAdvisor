import bisect
import json
import os
import re
import secrets
import subprocess
import sys
import threading
from datetime import date, datetime, timedelta
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
from markupsafe import Markup, escape
from sqlalchemy import inspect as sa_inspect, text
from sqlalchemy.orm import selectinload
from helpers import naar_eur, xirr, benchmark_eindwaarde
from models import (Advies, Aanbeveling, BenchmarkPunt, BENCHMARKS,
                    BrokerAccount, Gebruiker, NieuwsArtikel, Positie, Tag,
                    Transactie, TRANSACTIE_TYPES, db)
from projectie import (cash_saldi, herbereken_alles, herbereken_positie,
                       na_transactie_wijziging)

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

# Laad omgevingsvariabelen uit .env (indien aanwezig) vóór ze gelezen worden.
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

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

db.init_app(app)
csrf = CSRFProtect(app)


@app.context_processor
def inject_gebruikers():
    """Maak de volledige gebruikerslijst beschikbaar voor de navbar-switcher."""
    return {"alle_gebruikers": Gebruiker.query.order_by(Gebruiker.naam).all()}


@app.template_filter("compact_float")
def compact_float(value):
    if value is None:
        return "—"
    if value == int(value):
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


@app.template_filter("markdown")
def render_markdown(text):
    return _render_md(text)


# ── Helpers ──────────────────────────────────────────────────────

def laad_prijzen():
    if not PRIJZEN.exists():
        return {}, None, {}
    try:
        data = json.loads(PRIJZEN.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}, None, {}
    bijgewerkt = data.get("bijgewerkt")
    if bijgewerkt:
        try:
            bijgewerkt = datetime.fromisoformat(bijgewerkt).strftime("%d-%m-%Y %H:%M")
        except ValueError:
            pass
    return data.get("koersen", {}), bijgewerkt, data.get("wisselkoersen", {})


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
            k      = {"koers": pos.handmatige_koers, "dag": 0.0, "dag_pct": 0.0, "valuta": "EUR"}
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


# ── Dashboard ─────────────────────────────────────────────────────

def _externe_flows(account, wisselkoersen):
    """Externe cashflows (storting/opname) van een account, in EUR, voor XIRR.
    Storting = negatief (geld de portefeuille in), opname = positief."""
    flows = []
    for tx in account.transacties:
        if tx.type not in ("storting", "opname"):
            continue
        bedrag_eur = naar_eur(tx.bedrag or 0.0, tx.valuta or "EUR", wisselkoersen)
        if bedrag_eur is None:
            continue
        flows.append((tx.datum, -bedrag_eur if tx.type == "storting" else bedrag_eur))
    return flows


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

    for account in accounts:
        rows, tw, tk, td, tg = bereken_posities(account.posities, koersen, wisselkoersen)
        hp = tw is not None

        cash      = cash_saldi(account.id)
        cash_eur  = sum((naar_eur(s, v, wisselkoersen) or 0.0) for v, s in cash.items())
        flows     = _externe_flows(account, wisselkoersen)
        eind_eur  = (tw or 0.0) + cash_eur
        acc_xirr  = xirr(flows + [(date.today(), eind_eur)]) if flows else None

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
        })
        grand_gerealiseerd += tg
        grand_cash_eur     += cash_eur
        grand_flows        += flows
        if hp:
            grand_has_prices = True
            grand_waarde += tw
            grand_kosten += tk
            grand_dag    += td

    grand_eind = (grand_waarde if grand_has_prices else 0.0) + grand_cash_eur
    grand_xirr = xirr(grand_flows + [(date.today(), grand_eind)]) if grand_flows else None
    benchmarks = _benchmark_vergelijkingen(grand_flows)

    return render_template(
        "dashboard.html",
        gebruiker    = gebruiker,
        account_data = account_data,
        totaal       = {
            "waarde":       grand_waarde if grand_has_prices else None,
            "winst":        (grand_waarde - grand_kosten) if grand_has_prices else None,
            "dag":          grand_dag if grand_has_prices else None,
            "gerealiseerd": grand_gerealiseerd,
            "cash_eur":     grand_cash_eur,
            "xirr":         grand_xirr,
            "eind":         grand_eind,
        },
        benchmarks   = benchmarks,
        heeft_flows  = bool(grand_flows),
        bijgewerkt   = bijgewerkt,
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
        if aantal_val is None or aantal_val <= 0:
            fouten.append("Aantal moet groter dan 0 zijn.")
    except ValueError:
        fouten.append("Aantal moet een getal zijn.")
        aantal_val = None
    try:
        prijs_val = float(prijs_str) if prijs_str else None
        if prijs_val is None or prijs_val <= 0:
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
    if koers_type == "handmatig":
        try:
            hand_koers = float(hand_koers_s) if hand_koers_s else None
            if hand_koers is None:
                fouten.append("Handmatige koers is verplicht bij type 'handmatig'.")
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

def _parse_transactie_form(form, account, enforce_verkoop=True):
    """Valideer het transactie-formulier. Geeft (waarden, fouten) terug."""
    def _getal(naam):
        s = form.get(naam, "").strip()
        return float(s) if s else None   # kan ValueError gooien

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

    # Niet meer verkopen dan in bezit (alleen bij toevoegen; bij bewerken is de
    # huidige projectie lastig te corrigeren voor de transactie zelf).
    if enforce_verkoop and type_val == "verkoop" and ticker_val and aantal:
        pos = Positie.query.filter_by(broker_account_id=account.id, ticker=ticker_val).first()
        beschikbaar = (pos.aantal if pos else 0.0) or 0.0
        if aantal > beschikbaar + 1e-9:
            fouten.append(f"Niet genoeg stuks om te verkopen (max {beschikbaar:g}).")

    waarden = {
        "type": type_val, "ticker": ticker_val or None,
        "aantal": aantal, "prijs": prijs, "bedrag": bedrag,
        "kosten": kosten, "valuta": valuta_val, "datum": datum,
        "notitie": notitie_val or None,
    }
    if type_val in ("storting", "opname", "kosten"):
        waarden["ticker"] = None   # cash-types dragen geen ticker
    return waarden, fouten


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

    return render_template("transactie_form.html", gebruiker=gebruiker,
                           account=account, tx=None, types=TRANSACTIE_TYPES,
                           form_data={}, vandaag=date.today().isoformat())


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
        waarden, fouten = _parse_transactie_form(request.form, account,
                                                 enforce_verkoop=False)
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


@app.route("/gebruiker/<int:gebruiker_id>/herbereken", methods=["POST"])
def herbereken(gebruiker_id):
    Gebruiker.query.get_or_404(gebruiker_id)
    herbereken_alles()
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
        subprocess.run(cmd, timeout=timeout, check=True)
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

    # Meest recente advies per tag
    tags = Tag.query.filter_by(gebruiker_id=gebruiker_id).order_by(Tag.naam).all()
    tag_adviezen = {}
    for tag in tags:
        a = (Advies.query
             .filter_by(gebruiker_id=gebruiker_id, tag_id=tag.id)
             .order_by(Advies.gegenereerd.desc())
             .first())
        if a:
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

    # Prestatie aanbevelingen
    eerste_tip = {}
    for a in Advies.query.filter_by(gebruiker_id=gebruiker_id).order_by(Advies.gegenereerd).all():
        for rec in a.aanbevelingen:
            if rec.ticker not in eerste_tip:
                eerste_tip[rec.ticker] = (rec, a.gegenereerd)
    tips_prestatie = []
    for ticker, (rec, dt) in eerste_tip.items():
        huidig = koersen_nu.get(ticker, {}).get("koers")
        rendement = (((huidig - rec.instapkoers) / rec.instapkoers * 100)
                     if huidig and rec.instapkoers else None)
        tips_prestatie.append({
            "ticker":    ticker,
            "sinds":     dt.strftime("%d-%m %H:%M"),
            "instap":    rec.instapkoers,
            "huidig":    huidig,
            "valuta":    rec.valuta or "",
            "rendement": round(rendement, 1) if rendement is not None else None,
        })
    tips_prestatie.sort(key=lambda t: (t["rendement"] is None, -(t["rendement"] or 0)))

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

    return render_template(
        "advies.html",
        gebruiker      = gebruiker,
        advies         = latest,
        tag_adviezen   = tag_adviezen,
        tags           = tags,
        historie       = historie,
        waarde_serie   = waarde_serie,
        tips_prestatie = tips_prestatie,
        ticker_nieuws  = ticker_nieuws,
        heeft_api_key  = bool(os.environ.get("ANTHROPIC_API_KEY")),
    )


@app.route("/gebruiker/<int:gebruiker_id>/advies/genereer", methods=["POST"])
def advies_genereer(gebruiker_id):
    Gebruiker.query.get_or_404(gebruiker_id)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"taak_id": None,
                "fout": "Stel eerst ANTHROPIC_API_KEY in als omgevingsvariabele."}, 400
    tag_id = request.form.get("tag_id", type=int)  # None = generiek
    cmd = [sys.executable, str(BASE_DIR / "advies_generator.py"),
           "--gebruiker", str(gebruiker_id)]
    if tag_id:
        cmd += ["--tag", str(tag_id)]
    taak_id, al_bezig = _start_taak(
        f"advies-{gebruiker_id}-{tag_id or 'generiek'}",
        cmd, timeout=120, klaar_bericht="Advies gegenereerd.")
    return {"taak_id": taak_id, "al_bezig": al_bezig}


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
    # ontwikkeling aan te zetten. Expliciet aan 127.0.0.1 binden houdt de app lokaal.
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes", "on")
    app.run(host="127.0.0.1", port=5002, debug=debug)
