#!/usr/bin/env python3
"""
advies_generator.py — Genereert beleggingsadvies via Claude API

Gebruik:
  python3 advies_generator.py --gebruiker 1
  python3 advies_generator.py --gebruiker 1 --tag 3

Vereiste omgevingsvariabele:
  ANTHROPIC_API_KEY=sk-ant-...
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from helpers import naar_eur

SCRIPT_DIR = Path(__file__).parent
DATA_DIR   = SCRIPT_DIR / "data"
DB_PATH    = DATA_DIR / "app.db"
PRIJZEN    = DATA_DIR / "prijzen.json"

# Laad omgevingsvariabelen uit .env (o.a. ANTHROPIC_API_KEY) indien aanwezig.
try:
    from dotenv import load_dotenv
    load_dotenv(SCRIPT_DIR / ".env")
except ImportError:
    pass

MODEL = "claude-sonnet-4-6"


# ── Data ophalen ─────────────────────────────────────────────────

def laad_portefeuille(gebruiker_id, tag_id=None):
    """
    Laad posities van een gebruiker.
    Bij tag_id: alleen posities die aan die tag gekoppeld zijn.
    """
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        if tag_id:
            rows = conn.execute("""
                SELECT po.ticker, po.naam, po.aantal, po.aankoopprijs,
                       po.aankoopdatum, ba.naam,
                       po.koers_type, po.handmatige_koers
                FROM posities po
                JOIN broker_accounts ba ON po.broker_account_id = ba.id
                JOIN positie_tags pt    ON pt.positie_id = po.id
                WHERE ba.gebruiker_id = ? AND pt.tag_id = ?
                ORDER BY ba.naam, po.ticker
            """, (gebruiker_id, tag_id)).fetchall()
        else:
            rows = conn.execute("""
                SELECT po.ticker, po.naam, po.aantal, po.aankoopprijs,
                       po.aankoopdatum, ba.naam,
                       po.koers_type, po.handmatige_koers
                FROM posities po
                JOIN broker_accounts ba ON po.broker_account_id = ba.id
                WHERE ba.gebruiker_id = ?
                ORDER BY ba.naam, po.ticker
            """, (gebruiker_id,)).fetchall()
        return rows
    finally:
        conn.close()


def laad_tag_naam(tag_id):
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        row = conn.execute("SELECT naam FROM tags WHERE id = ?", (tag_id,)).fetchone()
        return row[0] if row else str(tag_id)
    finally:
        conn.close()


def laad_koersen():
    if not PRIJZEN.exists():
        return {}, {}
    try:
        data = json.loads(PRIJZEN.read_text(encoding="utf-8"))
        return data.get("koersen", {}), data.get("wisselkoersen", {})
    except Exception:
        return {}, {}


def laad_macro():
    """Haal macro-indicatoren op via yfinance (optioneel — mislukt graceful)."""
    try:
        import yfinance as yf
    except ImportError:
        return {}

    indicatoren = {
        "VIX":        "^VIX",
        "S&P 500":    "^GSPC",
        "AEX":        "^AEX",
        "Goud":       "GC=F",
        "Brent olie": "BZ=F",
        "EUR/USD":    "EURUSD=X",
    }

    resultaten = {}
    for naam, sym in indicatoren.items():
        try:
            fi    = yf.Ticker(sym).fast_info
            prijs = round(float(fi.last_price), 2)
            prev  = round(float(fi.previous_close), 2)
            pct   = round((prijs - prev) / prev * 100, 2) if prev else 0
            resultaten[naam] = f"{prijs}  ({'+' if pct >= 0 else ''}{pct}%)"
        except Exception:
            pass

    return resultaten


def _format_artikel(titel, bron, samenvatting, max_samenvatting=300):
    regel = f"• {titel} ({bron})"
    sam   = (samenvatting or "").strip()
    if sam:
        if len(sam) > max_samenvatting:
            sam = sam[:max_samenvatting].rstrip() + "…"
        regel += f"\n  {sam}"
    return regel


def laad_nieuws(tickers=None, max_per_ticker=6):
    """
    Laad nieuws uit de cache.
    Bij tickers (set/list): alleen artikelen voor die tickers.
    """
    conn  = sqlite3.connect(DB_PATH, timeout=10)
    grens = datetime.now() - timedelta(days=3)
    ticker_nieuws = {}
    try:
        for ticker, titel, bron, samenvatting in conn.execute("""
            SELECT ticker, titel, bron, samenvatting FROM nieuws_cache
            WHERE ticker IS NOT NULL AND opgeslagen >= ?
            ORDER BY gepubliceerd DESC
        """, (grens,)).fetchall():
            if tickers and ticker not in tickers:
                continue
            bucket = ticker_nieuws.setdefault(ticker, [])
            if len(bucket) < max_per_ticker:
                bucket.append(_format_artikel(titel, bron, samenvatting))
    finally:
        conn.close()
    return ticker_nieuws


# ── Prompt bouwen ─────────────────────────────────────────────────

def bouw_context(posities, koersen, wisselkoersen, macro, ticker_nieuws, tag_naam=None):
    """
    Bouw de context-string voor de prompt.
    posities = lijst van (ticker, naam, aantal, aankoopprijs, aankoopdatum,
                          account_naam, koers_type, handmatige_koers)
    """
    regels = []
    totaal_waarde = totaal_kosten = 0.0
    positie_regels = []

    for ticker, naam, aantal, aankoopprijs, aankoopdatum, account_naam, koers_type, handmatige_koers in posities:
        # Handmatige koers heeft voorrang
        if koers_type == "handmatig" and handmatige_koers:
            koers_eur = float(handmatige_koers)
            valuta    = "EUR"
        else:
            k         = koersen.get(ticker, {})
            koers_eur = naar_eur(k.get("koers"), k.get("valuta", "EUR"), wisselkoersen)
            valuta    = k.get("valuta", "EUR")

        waarde = aantal * koers_eur if koers_eur is not None else None
        kosten = aantal * aankoopprijs

        if waarde is not None:
            winst     = waarde - kosten
            winst_pct = winst / kosten * 100 if kosten else 0
            totaal_waarde += waarde
            totaal_kosten += kosten
            label = "handmatig" if koers_type == "handmatig" else valuta
            positie_regels.append(
                f"- **{ticker}** ({naam or ticker}) [{account_naam}]: "
                f"{aantal} × €{aankoopprijs:.2f} → huidig €{waarde:.2f} ({winst_pct:+.1f}%) [{label}]"
            )
        else:
            positie_regels.append(
                f"- **{ticker}** ({naam or ticker}) [{account_naam}]: "
                f"{aantal} × €{aankoopprijs:.2f}  (koers onbekend)"
            )

    titel = f"## Portefeuille{f' — tag: {tag_naam}' if tag_naam else ''}"
    regels.append(titel)
    if totaal_waarde > 0:
        totaal_winst = totaal_waarde - totaal_kosten
        regels.append(
            f"Totale waarde: €{totaal_waarde:.2f}  |  "
            f"Rendement: €{totaal_winst:+.2f} ({totaal_winst/totaal_kosten*100:+.1f}%)"
        )
    regels.extend(positie_regels)

    if macro:
        regels.append("\n## Macro-omgeving")
        for naam, waarde in macro.items():
            regels.append(f"- {naam}: {waarde}")

    if ticker_nieuws:
        regels.append("\n## Nieuws per positie (laatste 3 dagen)")
        for ticker, items in ticker_nieuws.items():
            regels.append(f"\n**{ticker}**")
            regels.extend(items)
    else:
        regels.append(
            "\n*Geen recent nieuws beschikbaar — "
            "baseer advies op portefeuille en macro.*"
        )

    return "\n".join(regels), totaal_waarde, totaal_kosten


def bouw_prompt(context, tag_naam=None):
    """Bouw de user-message voor de API-call."""
    if tag_naam:
        focus = (
            f"Dit is een detailadvies voor de beleggingsdoelstelling **'{tag_naam}'**. "
            f"Analyseer alleen de posities met deze tag en geef advies vanuit dat specifieke doel. "
            f"Benoem ook of de huidige samenstelling van deze posities past bij een '{tag_naam}'-strategie.\n\n"
        )
    else:
        focus = ""

    return (
        f"{context}\n\n"
        f"{focus}"
        "Begin je antwoord met exact deze twee regels (en niets ervóór):\n"
        "RISICO: N — <max 12 woorden motivatie>\n"
        "TIPS: <komma-gescheiden Yahoo Finance-tickers van je concrete koop-suggesties>\n"
        "waarbij N het risiconiveau weergeeft: "
        "1=zeer defensief, 2=defensief, 3=neutraal, 4=offensief, 5=zeer offensief. "
        "Gebruik bij TIPS uitsluitend geldige Yahoo Finance-tickers. "
        "Zet daarna een lege regel en begin met '## Samenvatting'.\n\n"
        "Geef een gestructureerd beleggingsadvies met precies deze secties:\n\n"
        "## Samenvatting\n"
        "2–3 zinnen met de kern van de situatie én de belangrijkste actie.\n\n"
        "## Huidige posities\n"
        "Per positie: houden / bijkopen / verkopen / reduceren, met concrete motivatie "
        "op basis van nieuws en macro.\n\n"
        "## Nieuwe kansen\n"
        "3–5 concrete nieuwe posities of sectoren die nu interessant zijn. "
        "Geef per suggestie: ticker, waarom nu, en hoe het de portefeuille aanvult.\n\n"
        "## Risico's\n"
        "Concrete risico's voor deze specifieke portefeuille.\n\n"
        "## Macro & Geopolitiek\n"
        "Welke externe ontwikkelingen zijn nu het meest impactvol voor deze posities?"
    )


# ── Tips parsen & valideren ───────────────────────────────────────

def parse_kop(advies_tekst):
    """Haal RISICO en TIPS uit de kopregels."""
    risico_score = risico_reden = None
    tickers = []
    regels  = advies_tekst.splitlines()
    idx     = 0
    while idx < len(regels):
        regel = regels[idx]
        mr = re.match(r"\s*RISICO:\s*([1-5])\s*(?:/\s*5)?\s*[—\-:]*\s*(.*)", regel)
        mt = re.match(r"\s*TIPS:\s*(.*)", regel, re.I)
        if mr:
            risico_score = int(mr.group(1))
            risico_reden = mr.group(2).strip()[:300] or None
        elif mt:
            for tok in mt.group(1).split(","):
                tok = tok.strip().strip("`*").upper()
                if re.fullmatch(r"[A-Z0-9.\-]{1,20}", tok):
                    tickers.append(tok)
        elif regel.strip() == "":
            pass
        else:
            break
        idx += 1
    rest    = "\n".join(regels[idx:]).lstrip("\n")
    tickers = list(dict.fromkeys(tickers))
    return risico_score, risico_reden, tickers, rest


def _haal_koers(ticker):
    try:
        from fetch_prices import fetch_crypto, fetch_equity, is_crypto
        data = fetch_crypto(ticker) if is_crypto(ticker) else fetch_equity(ticker)
        if "fout" in data:
            return None, None
        koers = data.get("koers")
        if koers and koers > 0:
            return round(float(koers), 4), data.get("valuta")
    except Exception:
        pass
    return None, None


# ── Advies genereren ──────────────────────────────────────────────

def genereer(gebruiker_id, tag_id=None):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[ERROR] ANTHROPIC_API_KEY niet ingesteld.")
        sys.exit(1)

    import anthropic

    tag_naam = laad_tag_naam(tag_id) if tag_id else None
    label    = f"tag '{tag_naam}'" if tag_naam else "generiek"
    print(f"[{datetime.now():%H:%M:%S}] Advies genereren voor gebruiker {gebruiker_id} — {label}")

    print(f"[{datetime.now():%H:%M:%S}] Portefeuille laden…")
    posities = laad_portefeuille(gebruiker_id, tag_id)
    if not posities:
        print(f"[ERROR] Geen posities gevonden voor gebruiker {gebruiker_id}"
              + (f", tag {tag_naam}" if tag_naam else "") + ".")
        sys.exit(1)

    koersen, wisselkoersen = laad_koersen()

    print(f"[{datetime.now():%H:%M:%S}] Macro-context ophalen…")
    macro = laad_macro()
    if not macro:
        print("  (yfinance niet beschikbaar — zonder macro-context)")

    print(f"[{datetime.now():%H:%M:%S}] Nieuws laden…")
    relevante_tickers = {p[0] for p in posities}
    ticker_nieuws     = laad_nieuws(tickers=relevante_tickers)
    n_ticker = sum(len(v) for v in ticker_nieuws.values())
    print(f"  {n_ticker} artikelen voor {len(relevante_tickers)} tickers")

    context, totaal_waarde, totaal_kosten = bouw_context(
        posities, koersen, wisselkoersen, macro, ticker_nieuws, tag_naam
    )

    snapshot = json.dumps({
        "posities": [
            {"ticker": p[0], "naam": p[1], "aantal": p[2], "aankoopprijs": p[3]}
            for p in posities
        ],
        "totaal_waarde": round(totaal_waarde, 2) if totaal_waarde else None,
        "totaal_kosten": round(totaal_kosten, 2),
        "tag_id":        tag_id,
        "tag_naam":      tag_naam,
        "timestamp":     datetime.now().isoformat(),
    }, ensure_ascii=False)

    print(f"[{datetime.now():%H:%M:%S}] Claude API aanroepen ({MODEL})…")

    client  = anthropic.Anthropic(api_key=api_key)
    bericht = client.messages.create(
        model      = MODEL,
        max_tokens = 4096,
        system     = (
            "Je bent een ervaren, onafhankelijke beleggingsadviseur. "
            "Je analyseert portefeuilles op basis van actuele markt- en nieuwsdata "
            "en geeft concreet, afgewogen advies in het Nederlands. "
            "Gebruik Markdown voor opmaak. Wees direct en praktisch. "
            "Sluit altijd af met: *Dit is geen officieel financieel advies.*"
        ),
        messages = [{"role": "user", "content": bouw_prompt(context, tag_naam)}],
    )

    advies_tekst = bericht.content[0].text

    risico_score, risico_reden, tip_tickers, advies_tekst = parse_kop(advies_tekst)
    if risico_score:
        print(f"  Risiconiveau: {risico_score}/5 — {risico_reden}")
    else:
        print("  [WARN] Geen risiconiveau herkend in het advies.")

    geldige_tips = []
    if tip_tickers:
        print(f"  Tips controleren: {', '.join(tip_tickers)}")
    for tk in tip_tickers:
        koers, valuta = _haal_koers(tk)
        if koers:
            geldige_tips.append((tk, koers, valuta))
        else:
            print(f"    [WARN] overgeslagen (geen geldige koers): {tk}")

    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        cur = conn.execute("""
            INSERT INTO adviezen
              (gebruiker_id, tag_id, gegenereerd, portfolio_snapshot,
               advies_tekst, model, risico_score, risico_reden)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (gebruiker_id, tag_id, datetime.now(), snapshot,
              advies_tekst, MODEL, risico_score, risico_reden))
        advies_id = cur.lastrowid
        for tk, koers, valuta in geldige_tips:
            conn.execute("""
                INSERT INTO aanbevelingen (advies_id, ticker, instapkoers, valuta, opgeslagen)
                VALUES (?, ?, ?, ?, ?)
            """, (advies_id, tk, koers, valuta, datetime.now()))
        conn.commit()
    finally:
        conn.close()

    if geldige_tips:
        print(f"  {len(geldige_tips)} tips opgeslagen: "
              f"{', '.join(t[0] for t in geldige_tips)}")
    print(f"[{datetime.now():%H:%M:%S}] Advies opgeslagen (id={advies_id}).\n")


# ── Entry point ───────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Genereer beleggingsadvies via Claude API")
    parser.add_argument("--gebruiker", type=int, required=True,
                        help="Gebruiker-ID waarvoor het advies gegenereerd wordt")
    parser.add_argument("--tag", type=int, default=None,
                        help="Tag-ID voor detailadvies (optioneel; zonder = generiek)")
    args = parser.parse_args()

    genereer(args.gebruiker, args.tag)
