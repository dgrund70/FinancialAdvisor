import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, url_for
from flask_wtf.csrf import CSRFProtect
from markupsafe import Markup, escape
from sqlalchemy import text
from helpers import naar_eur
from models import (Advies, Aanbeveling, BrokerAccount, Gebruiker,
                    NieuwsArtikel, Positie, Tag, db)

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

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
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
    """Bereken marktwaarde, winst en dagverandering per positie."""
    wisselkoersen = wisselkoersen or {}
    rows = []
    totaal_waarde = totaal_kosten = totaal_dag = 0.0
    has_prices = False

    for pos in posities:
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

        kosten    = pos.aantal * pos.aankoopprijs
        winst     = (waarde - kosten) if waarde is not None else None
        winst_pct = (winst / kosten * 100) if winst is not None and kosten else None

        rows.append({
            "pos":       pos,
            "koers":     koers,
            "waarde":    waarde,
            "kosten":    kosten,
            "winst":     winst,
            "winst_pct": winst_pct,
            "dag_winst": dag_winst,
            "dag_pct":   k.get("dag_pct", 0.0),
            "valuta":    valuta,
        })

        if waarde is not None:
            has_prices = True
            totaal_waarde += waarde
            totaal_kosten += kosten
            totaal_dag    += dag_winst

    if not has_prices:
        return rows, None, None, None
    return rows, totaal_waarde, totaal_kosten, totaal_dag


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

@app.route("/gebruiker/<int:gebruiker_id>")
def dashboard(gebruiker_id):
    gebruiker = Gebruiker.query.get_or_404(gebruiker_id)
    koersen, bijgewerkt, wisselkoersen = laad_prijzen()

    account_data = []
    grand_waarde = grand_kosten = grand_dag = 0.0
    grand_has_prices = False

    for account in gebruiker.broker_accounts:
        rows, tw, tk, td = bereken_posities(account.posities, koersen, wisselkoersen)
        hp = tw is not None
        account_data.append({
            "account": account,
            "rows":    rows,
            "waarde":  tw,
            "dag":     td,
            "winst":   (tw - tk) if hp else None,
        })
        if hp:
            grand_has_prices = True
            grand_waarde += tw
            grand_kosten += tk
            grand_dag    += td

    return render_template(
        "dashboard.html",
        gebruiker    = gebruiker,
        account_data = account_data,
        totaal       = {
            "waarde": grand_waarde if grand_has_prices else None,
            "winst":  (grand_waarde - grand_kosten) if grand_has_prices else None,
            "dag":    grand_dag if grand_has_prices else None,
        },
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
        pos = Positie(broker_account_id=account.id, tags=geselecteerde_tags, **waarden)
        db.session.add(pos)
        db.session.commit()
        flash(f"{pos.ticker} toegevoegd aan {account.naam}.", "success")
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

        for veld, waarde in waarden.items():
            setattr(pos, veld, waarde)
        pos.tags = Tag.query.filter(Tag.id.in_(tag_ids),
                                    Tag.gebruiker_id == gebruiker_id).all()
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


# ── Koersen ───────────────────────────────────────────────────────

@app.route("/prijzen/verversen", methods=["POST"])
def prijzen_verversen():
    gebruiker_id = request.form.get("gebruiker_id", type=int)
    try:
        subprocess.run(
            [sys.executable, str(BASE_DIR / "fetch_prices.py")],
            timeout=60, check=True,
        )
        flash("Koersen bijgewerkt.", "success")
    except subprocess.TimeoutExpired:
        flash("Koersen ophalen duurde te lang.", "warning")
    except subprocess.CalledProcessError as e:
        flash(f"Fout bij ophalen koersen: {e}", "danger")
    if gebruiker_id:
        return redirect(url_for("dashboard", gebruiker_id=gebruiker_id))
    return redirect(url_for("index"))


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

    # Ticker-nieuws voor posities van deze gebruiker
    grens_nieuws = datetime.now() - timedelta(days=3)
    ticker_nieuws = {}
    for account in gebruiker.broker_accounts:
        for pos in account.posities:
            artikelen = (NieuwsArtikel.query
                         .filter(NieuwsArtikel.ticker == pos.ticker,
                                 NieuwsArtikel.opgeslagen >= grens_nieuws)
                         .order_by(NieuwsArtikel.gepubliceerd.desc())
                         .limit(5).all())
            if artikelen:
                ticker_nieuws[pos.ticker] = artikelen

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
    gebruiker = Gebruiker.query.get_or_404(gebruiker_id)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        flash("Stel eerst ANTHROPIC_API_KEY in als omgevingsvariabele.", "danger")
        return redirect(url_for("advies", gebruiker_id=gebruiker_id))
    tag_id = request.form.get("tag_id", type=int)  # None = generiek
    try:
        cmd = [sys.executable, str(BASE_DIR / "advies_generator.py"),
               "--gebruiker", str(gebruiker_id)]
        if tag_id:
            cmd += ["--tag", str(tag_id)]
        subprocess.run(cmd, timeout=120, check=True)
        flash("Advies gegenereerd.", "success")
    except subprocess.TimeoutExpired:
        flash("Advies genereren duurde te lang (>120s).", "warning")
    except subprocess.CalledProcessError as e:
        flash(f"Fout bij genereren advies: {e}", "danger")
    return redirect(url_for("advies", gebruiker_id=gebruiker_id))


@app.route("/gebruiker/<int:gebruiker_id>/nieuws/verversen", methods=["POST"])
def nieuws_verversen(gebruiker_id):
    try:
        subprocess.run(
            [sys.executable, str(BASE_DIR / "fetch_news.py")],
            timeout=120, check=True,
        )
        flash("Nieuws bijgewerkt.", "success")
    except subprocess.TimeoutExpired:
        flash("Nieuws ophalen duurde te lang.", "warning")
    except subprocess.CalledProcessError as e:
        flash(f"Fout bij ophalen nieuws: {e}", "danger")
    return redirect(url_for("advies", gebruiker_id=gebruiker_id))


# ── Database initialisatie ────────────────────────────────────────

def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    db.create_all()

    if not Gebruiker.query.first():
        g = Gebruiker(naam="Mijn account")
        db.session.add(g)
        db.session.flush()
        db.session.add(BrokerAccount(naam="Mijn portefeuille", gebruiker_id=g.id))
        db.session.commit()


with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5002)
