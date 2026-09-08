"""Stel het team een losse vraag over je portefeuille.

Anders dan advies_team.py: één API-call, geen volledige portefeuille-analyse en
geen aanbevelingen. De portefeuille gaat wél mee als context, maar compact —
alleen wat een antwoord nodig heeft. Nieuws en fundamentals komen alleen mee
voor tickers die in de vraag zelf genoemd worden; macro alleen als de vraag
erover gaat. Dat scheelt bij een portefeuille van twaalf posities ruim 3.000
tokens per vraag, zonder dat het antwoard context mist.

Draaien:
    .venv/bin/python advies_vraag.py --gebruiker 1 --vraag "Is goud nu zinvol?"
"""
import argparse
import os
import re
import sqlite3
import sys
from datetime import datetime

from advies_generator import (
    DB_PATH, MODEL, laad_portefeuille, laad_koersen, laad_macro,
    laad_nieuws, laad_fundamentals, laad_volglijst,
)
from advies_team import laad_risico_cijfers, format_risico_cijfers, laad_risicoprofiel
from helpers import naar_eur

MAX_ANTWOORD_TOKENS = 1200

# Woorden die aangeven dat de vraag over de markt gaat; alleen dán halen we
# macro-indicatoren op (die kosten een yfinance-call én tokens).
_MACRO_WOORDEN = (
    "macro", "markt", "rente", "inflatie", "vix", "goud", "olie", "dollar",
    "recessie", "correctie", "crash", "beurs", "fed", "ecb", "wisselkoers",
    "geopolit", "oorlog", "verkiezing", "economie", "conjunctuur",
)

SYSTEM = (
    "Je bent een nuchtere beleggingsadviseur die één concrete vraag beantwoordt. "
    "Je krijgt de portefeuille van de gebruiker als context mee.\n\n"
    "Regels:\n"
    "- Beantwoord de vraag. Geef geen volledige portefeuille-analyse en geen "
    "ongevraagde lijst met koop- of verkoopadviezen.\n"
    "- Betrek de portefeuille alleen waar die het antwoord verandert.\n"
    "- Wees eerlijk over onzekerheid. Zeg het als de context ontbreekt om de "
    "vraag goed te beantwoorden, en zeg wat je dan nodig hebt.\n"
    "- Schrijf Nederlands, kort en zonder inleidende plichtplegingen. "
    "Markdown mag; begin niet met een kop.\n"
    "- Sluit af met één regel: wat dit concreet voor deze portefeuille betekent."
)


def _cash_saldi(gebruiker_id):
    """{valuta: saldo} over alle rekeningen van de gebruiker.

    Zelfde tekenconventie als models.Transactie / projectie.cash_saldi; die is
    de bron van waarheid. Hier los uitgerekend omdat dit script buiten Flask
    draait en geen app-context heeft.
    """
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        rijen = conn.execute("""
            SELECT t.type, t.aantal, t.prijs, t.bedrag, t.kosten, t.valuta
            FROM transacties t
            JOIN broker_accounts b ON b.id = t.broker_account_id
            WHERE b.gebruiker_id = ?
        """, (gebruiker_id,)).fetchall()
    finally:
        conn.close()

    saldi = {}
    for soort, aantal, prijs, bedrag, kosten, valuta in rijen:
        aantal, prijs = aantal or 0.0, prijs or 0.0
        bedrag, kosten = bedrag or 0.0, kosten or 0.0
        if soort == "storting":
            delta = bedrag
        elif soort == "opname":
            delta = -bedrag
        elif soort == "koop":
            delta = -(aantal * prijs + kosten)
        elif soort == "verkoop":
            delta = aantal * prijs - kosten
        elif soort == "dividend":
            delta = bedrag - kosten
        elif soort == "kosten":
            delta = -bedrag
        else:                       # correctie: geen cash-effect
            delta = 0.0
        v = valuta or "EUR"
        saldi[v] = saldi.get(v, 0.0) + delta
    return {v: round(s, 2) for v, s in saldi.items() if abs(s) >= 0.005}


def genoemde_tickers(vraag, kandidaten):
    """Welke van deze tickers komen in de vraag voor (heel woord, hoofdletterloos)."""
    gevonden = []
    for t in kandidaten:
        basis = t.split(".")[0]
        if re.search(rf"\b{re.escape(basis)}\b", vraag, re.I):
            gevonden.append(t)
    return gevonden


def bouw_vraagcontext(gebruiker_id, vraag):
    """Compacte context: portefeuille, risico, volglijst, profiel.

    Alleen aangevuld met fundamentals/nieuws van tickers uit de vraag, en met
    macro als de vraag daarover gaat.
    """
    posities = laad_portefeuille(gebruiker_id)
    koersen, wisselkoersen = laad_koersen()

    regels = [f"## Portefeuille (peildatum {datetime.now():%d-%m-%Y})"]
    totaal_waarde = totaal_kosten = 0.0
    for (ticker, naam, aantal, gak, _datum, account, koers_type,
         hand_koers, pos_valuta) in posities:
        pos_valuta = pos_valuta or "EUR"
        if koers_type == "handmatig" and hand_koers:
            koers_eur = naar_eur(float(hand_koers), pos_valuta, wisselkoersen) or float(hand_koers)
        else:
            k = koersen.get(ticker, {})
            koers_eur = naar_eur(k.get("koers"), k.get("valuta", "EUR"), wisselkoersen)
        gak_eur = naar_eur(gak, pos_valuta, wisselkoersen)
        waarde  = (aantal * koers_eur) if koers_eur is not None else None
        kosten  = (aantal * gak_eur) if gak_eur is not None else None
        if waarde is not None and kosten:
            totaal_waarde += waarde
            totaal_kosten += kosten
            rendement = (waarde - kosten) / kosten * 100
            regels.append(f"- {ticker} ({naam or ticker}) [{account}]: "
                          f"{aantal:g} st, EUR {waarde:.0f} ({rendement:+.1f}%)")
        else:
            regels.append(f"- {ticker} ({naam or ticker}) [{account}]: {aantal:g} st, koers onbekend")

    cash = _cash_saldi(gebruiker_id)
    cash_tekst = " · ".join(f"{v} {s:.0f}" for v, s in cash.items()) or "geen"
    if totaal_kosten:
        resultaat = totaal_waarde - totaal_kosten
        regels.append(f"Totaal belegd EUR {totaal_waarde:.0f} (inleg EUR {totaal_kosten:.0f}, "
                      f"{resultaat:+.0f} = {resultaat / totaal_kosten * 100:+.1f}%) · cash: {cash_tekst}")

    risico = laad_risico_cijfers(posities)
    risico_tekst = format_risico_cijfers(risico)
    if risico_tekst:
        # Alleen de portefeuillebrede cijfers; de lijst per positie (soms 12+
        # regels) voegt voor een losse vraag zelden iets toe.
        kop = risico_tekst.split("Per positie")[0].splitlines()[1:]
        kern = [r for r in kop if r.strip()]
        if kern:
            regels.append("\n## Risico\n" + "\n".join(kern))

    volglijst = laad_volglijst(gebruiker_id)
    if volglijst:
        regels.append("\n## Volglijst (kandidaten, nog geen positie)\n" + ", ".join(volglijst))

    profiel = laad_risicoprofiel(gebruiker_id)
    if profiel:
        regels.append(f"\n## Risicoprofiel van de gebruiker\n{profiel}")

    # Alleen verdiepen op wat de vraag noemt.
    relevante = genoemde_tickers(vraag, [p[0] for p in posities] + list(volglijst))
    if relevante:
        funds = laad_fundamentals(tickers=set(relevante))
        for ticker in relevante:
            f = funds.get(ticker)
            if f:
                velden = ", ".join(f"{k} {v}" for k, v in list(f.items())[:8]
                                   if v not in (None, "") and k != "ticker")
                if velden:
                    regels.append(f"\n## Fundamentals {ticker}\n{velden}")
        nieuws = laad_nieuws(tickers=set(relevante), max_per_ticker=2)
        for ticker, items in (nieuws or {}).items():
            koppen = "\n".join(f"- {i[0] if isinstance(i, (list, tuple)) else i}" for i in items)
            if koppen:
                regels.append(f"\n## Recent nieuws {ticker}\n{koppen}")

    if any(w in vraag.lower() for w in _MACRO_WOORDEN):
        macro = laad_macro()
        if macro:
            regels.append("\n## Macro vandaag\n"
                          + " · ".join(f"{n}: {w}" for n, w in macro.items()))

    return "\n".join(regels)


def stel_vraag(gebruiker_id, vraag):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[ERROR] ANTHROPIC_API_KEY niet ingesteld.")
        sys.exit(1)
    vraag = (vraag or "").strip()
    if not vraag:
        print("[ERROR] Lege vraag.")
        sys.exit(1)

    import anthropic

    print(f"[{datetime.now():%H:%M:%S}] Vraag van gebruiker {gebruiker_id}: {vraag[:80]}")
    context = bouw_vraagcontext(gebruiker_id, vraag)
    print(f"  context: {len(context)} tekens (~{len(context)//4} tokens)")

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=MODEL, max_tokens=MAX_ANTWOORD_TOKENS, system=SYSTEM,
        messages=[{"role": "user",
                   "content": f"{context}\n\n## Vraag\n{vraag}"}],
    )
    antwoord = "".join(blok.text for blok in resp.content if blok.type == "text").strip()

    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        conn.execute("""
            INSERT INTO team_vragen
              (gebruiker_id, gesteld, vraag, antwoord, model, context_tekens)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (gebruiker_id, datetime.now(), vraag, antwoord, MODEL, len(context)))
        conn.commit()
    finally:
        conn.close()
    print(f"[{datetime.now():%H:%M:%S}] Antwoord opgeslagen ({len(antwoord)} tekens).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stel het team een vraag over je portefeuille")
    parser.add_argument("--gebruiker", type=int, required=True)
    parser.add_argument("--vraag", required=True)
    args = parser.parse_args()
    stel_vraag(args.gebruiker, args.vraag)
