#!/usr/bin/env python3
"""
advies_team.py — Team-advies: meerdere Claude-agents met verschillende
risico-perspectieven, gevolgd door een persoonlijke afweging en een
eindsynthese, i.p.v. één enkele adviesoproep (advies_generator.py).

Pijplijn:
  1. Portefeuille + kwantitatief risico + macro/nieuws/fundamentals laden
     (hergebruikt advies_generator.py, geen duplicatie).
  2. Parallel (Haiku-klasse, goedkoop): Gewaagd · Gemiddeld risico · Macro/geo-briefing.
  3. Persoonlijk perspectief (sterker model): weegt de drie concepten tegen
     Gebruiker.risicoprofiel.
  4. Eindsynthese (sterker model): produceert het advies in hetzelfde
     RISICO:/TIPS:-formaat als advies_generator.py, zodat parse_kop() en de
     rest van de app ongewijzigd blijven werken.

Gebruik:
  python3 advies_team.py --gebruiker 1
  python3 advies_team.py --gebruiker 1 --tag 3

Vereiste omgevingsvariabele:
  ANTHROPIC_API_KEY=sk-ant-...
"""

import argparse
import json
import os
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from helpers import volatiliteit, max_drawdown, beta, sharpe, correlatie, dagrendementen
from advies_generator import (
    DB_PATH, MODEL as MODEL_STERK, FORMAAT_INSTRUCTIE,
    laad_portefeuille, laad_tag_naam, laad_koersen, laad_macro, laad_nieuws,
    laad_fundamentals, laad_volglijst, bouw_context, parse_kop, _haal_koers,
)

MODEL_GOEDKOOP    = "claude-haiku-4-5-20251001"
BENCHMARK_TICKER  = "IWDA.AS"   # wereldindex, zelfde als in app.py's _portefeuille_risico
RISICOVRIJE_RENTE = 0.025       # zelfde als app.py/CLAUDE.md


# ── Kwantitatief risico (puur cijferwerk, geen LLM-call) ───────────

def laad_risico_cijfers(posities, benchmark_ticker=BENCHMARK_TICKER, venster=252):
    """Volatiliteit/max drawdown/Sharpe/bèta/correlatie uit koers_historie +
    benchmark_punten, met helpers.py (dezelfde pure functies als de
    Analyse-pagina in app.py). Respecteert de tag-scope van `posities` (die
    komt al gefilterd binnen als er een tag_id is meegegeven). Geeft None bij
    onvoldoende historie — degradeert gracieus, net als laad_macro()."""
    aantal_per_ticker = {}
    for p in posities:
        ticker, aantal = p[0], p[2]
        aantal_per_ticker[ticker] = aantal_per_ticker.get(ticker, 0.0) + aantal
    if not aantal_per_ticker:
        return None

    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        serie = {}
        for ticker in aantal_per_ticker:
            rows = conn.execute(
                "SELECT datum, koers FROM koers_historie WHERE ticker = ? ORDER BY datum",
                (ticker,)).fetchall()
            if rows:
                serie[ticker] = dict(rows)
        gedekt = {t: a for t, a in aantal_per_ticker.items() if t in serie}
        if not gedekt:
            return None

        gemeen = sorted(set.intersection(*[set(serie[t]) for t in gedekt]))[-venster:]
        if len(gemeen) < 20:
            return None

        nav       = [sum(gedekt[t] * serie[t][d] for t in gedekt) for d in gemeen]
        port_rend = dagrendementen(nav)

        bench_rows = conn.execute(
            "SELECT datum, koers FROM benchmark_punten WHERE ticker = ? ORDER BY datum",
            (benchmark_ticker,)).fetchall()
        bench = dict(bench_rows)
        gemeen_b = sorted(set(gemeen) & set(bench))
        port_beta = port_corr = None
        if len(gemeen_b) >= 20:
            nav_b       = [sum(gedekt[t] * serie[t][d] for t in gedekt) for d in gemeen_b]
            rend_port_b = dagrendementen(nav_b)
            rend_bench  = dagrendementen([bench[d] for d in gemeen_b])
            port_beta = beta(rend_port_b, rend_bench)
            port_corr = correlatie(rend_port_b, rend_bench)

        per_positie = []
        for t in sorted(gedekt):
            rend_t = dagrendementen([serie[t][d] for d in gemeen])
            per_positie.append({
                "ticker": t,
                "volatiliteit": volatiliteit(rend_t),
                "correlatie_portefeuille": correlatie(rend_t, port_rend),
            })

        return {
            "volatiliteit":           volatiliteit(port_rend),
            "max_drawdown":           max_drawdown(nav),
            "sharpe":                 sharpe(port_rend, rf=RISICOVRIJE_RENTE),
            "beta":                   port_beta,
            "correlatie_wereldindex": port_corr,
            "dekking":                len(gedekt),
            "totaal_posities":        len(aantal_per_ticker),
            "dagen":                  len(gemeen),
            "per_positie":            per_positie,
        }
    finally:
        conn.close()


def _pct(v):
    return f"{v * 100:.1f}%" if v is not None else "onbekend"


def format_risico_cijfers(risico):
    """Compacte, LLM-vriendelijke weergave van laad_risico_cijfers()."""
    if not risico:
        return None
    sharpe_str = f"{risico['sharpe']:.2f}" if risico["sharpe"] is not None else "onbekend"
    beta_str   = f"{risico['beta']:.2f}" if risico["beta"] is not None else "onbekend"
    corr_str   = (f"{risico['correlatie_wereldindex']:.2f}"
                  if risico["correlatie_wereldindex"] is not None else "onbekend")
    regels = [
        "## Kwantitatief risico (berekend uit historische koersen, geen AI-inschatting)",
        f"Volatiliteit (jaarbasis): {_pct(risico['volatiliteit'])}  |  "
        f"Max drawdown: {_pct(risico['max_drawdown'])}  |  "
        f"Sharpe-ratio (rf=2,5%): {sharpe_str}",
        f"Bèta vs wereldindex: {beta_str}  |  Correlatie met wereldindex: {corr_str}",
        f"(dekking: {risico['dekking']}/{risico['totaal_posities']} posities, "
        f"{risico['dagen']} handelsdagen historie)",
        "\nPer positie (volatiliteit / correlatie met de totale portefeuille):",
    ]
    for p in risico["per_positie"]:
        vol_str = _pct(p["volatiliteit"])
        corr = p["correlatie_portefeuille"]
        if corr is not None:
            regels.append(f"- **{p['ticker']}**: volatiliteit {vol_str}, correlatie {corr:.2f}")
        else:
            regels.append(f"- **{p['ticker']}**: volatiliteit {vol_str}")
    return "\n".join(regels)


# ── Risicoprofiel van de gebruiker ──────────────────────────────────

_STANDAARD_RISICOPROFIEL = (
    "Geen persoonlijk risicoprofiel ingesteld. Redeneer vanuit een algemeen "
    "voorzichtige particuliere belegger die op lange termijn vermogen wil "
    "opbouwen met acceptabel risico."
)


def laad_risicoprofiel(gebruiker_id):
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        row = conn.execute(
            "SELECT risicoprofiel FROM gebruikers WHERE id = ?", (gebruiker_id,)
        ).fetchone()
        profiel = (row[0] if row else None) or ""
        return profiel.strip() or _STANDAARD_RISICOPROFIEL
    finally:
        conn.close()


# ── Agent-rollen ─────────────────────────────────────────────────

GEWAAGD_SYSTEM = (
    "Je bent een agressieve, kansgedreven beleggingsadviseur binnen een team van "
    "vier onafhankelijke adviseurs die elk apart naar dezelfde portefeuille kijken "
    "(jullie zien elkaars antwoord niet). Je zoekt expliciet naar hoog-risico/"
    "hoog-rendement kansen en durft tegen de markt in te gaan. Onderbouw met de "
    "meegegeven fundamentals en cijfers — verzin geen getallen die niet in de "
    "context staan. Antwoord in het Nederlands, beknopt (max 250 woorden) maar "
    "met een duidelijk standpunt per opvallende positie."
)

GEMIDDELD_SYSTEM = (
    "Je bent een gebalanceerde, risicobewuste beleggingsadviseur binnen een team "
    "van vier onafhankelijke adviseurs die elk apart naar dezelfde portefeuille "
    "kijken (jullie zien elkaars antwoord niet). Je let expliciet op "
    "concentratierisico, spreiding en positiegrootte t.o.v. het geheel. Onderbouw "
    "met de meegegeven fundamentals en cijfers — verzin geen getallen die niet in "
    "de context staan. Antwoord in het Nederlands, beknopt (max 250 woorden) maar "
    "met een duidelijk standpunt per opvallende positie."
)

MACRO_SYSTEM = (
    "Je bent een macro-econoom binnen een beleggingsadviesteam. Je duidt of de "
    "koersbewegingen van vandaag marktbreed zijn of bedrijfsspecifiek, en welke "
    "macro-/geopolitieke factoren relevant zijn voor déze portefeuille. Gebruik "
    "uitsluitend de cijfers uit de meegegeven context. Let goed op: de context "
    "bevat twee verschillende soorten percentages — TOTAALRENDEMENT SINDS "
    "AANKOOP bij de posities en KOERSBEWEGING VANDAAG bij de macro-indicatoren. "
    "Verwar ze niet en verzin geen percentages die er niet staan. Geen "
    "beleggingsadvies, alleen duiding, in het Nederlands, beknopt (max 200 woorden)."
)

PERSPECTIEF_SYSTEM_TEMPLATE = (
    "Je redeneert vanuit het perspectief van de eigenaar van deze portefeuille, "
    "met dit risicoprofiel:\n\n\"{risicoprofiel}\"\n\n"
    "Je hebt zojuist drie onafhankelijke concept-inbrengen gelezen: een gewaagd "
    "advies, een gemiddeld-risico-advies en een macro/geo-briefing. Weeg ze tegen "
    "elkaar af vanuit bovenstaand risicoprofiel — waar volg je het gewaagde "
    "advies, waar het gemiddelde, en waarom? BELANGRIJK: gebruik uitsluitend "
    "cijfers die je terugvindt in de oorspronkelijke portefeuille-/risico-/"
    "macro-CONTEXT (die je hierna krijgt) — neem geen getallen zonder controle "
    "over uit de concept-adviezen of de briefing zelf, die kunnen fouten of "
    "verzonnen cijfers bevatten. Antwoord in het Nederlands, beknopt (max 300 "
    "woorden) maar concreet: welke acties volgen uit deze afweging?"
)

SYNTHESE_SYSTEM = (
    "Je bent de eindverantwoordelijke van een beleggingsadviesteam en een "
    "ervaren, onafhankelijke beleggingsadviseur. Je hebt de context, drie "
    "concept-adviezen en een persoonlijke afweging gekregen. Gebruik ze als "
    "input, maar het advies hieronder is aan jou — je hoeft het niet met elk "
    "concept eens te zijn. BELANGRIJK: elk cijfer in je eindadvies moet "
    "rechtstreeks terug te vinden zijn in de portefeuille-/risico-/macro-CONTEXT "
    "(vóór de conceptadviezen in de prompt) — neem geen cijfers zonder controle "
    "over uit de proza van de conceptadviseurs, die kunnen fouten bevatten. "
    "Gebruik Markdown voor opmaak. Wees direct en praktisch. Sluit altijd af met: "
    "*Dit is geen officieel financieel advies.*"
)


def _call_agent(client, model, system, user, max_tokens):
    bericht = client.messages.create(
        model=model, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
    )
    if not bericht.content:
        raise RuntimeError(f"Lege API-response van {model} (mogelijk content filtering).")
    return bericht.content[0].text


def bouw_synthese_prompt(context, gewaagd, gemiddeld, briefing, perspectief, tag_naam=None):
    if tag_naam:
        focus = (
            f"Dit is een detailadvies voor de beleggingsdoelstelling **'{tag_naam}'**. "
            f"Analyseer alleen de posities met deze tag en geef advies vanuit dat "
            f"specifieke doel.\n\n"
        )
    else:
        focus = ""
    return (
        f"{context}\n\n{focus}"
        "Hieronder de input van het team — gebruik 'm, maar het eindoordeel is aan jou:\n\n"
        f"### Concept 'Gewaagd'\n{gewaagd}\n\n"
        f"### Concept 'Gemiddeld risico'\n{gemiddeld}\n\n"
        f"### Macro/geo-briefing\n{briefing}\n\n"
        f"### Persoonlijke afweging\n{perspectief}\n\n"
        f"{FORMAAT_INSTRUCTIE}"
    )


# ── Advies genereren ──────────────────────────────────────────────

def genereer_team(gebruiker_id, tag_id=None, doel=None):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[ERROR] ANTHROPIC_API_KEY niet ingesteld.")
        sys.exit(1)

    import anthropic

    tag_naam = laad_tag_naam(tag_id) if tag_id else None
    label    = f"tag '{tag_naam}'" if tag_naam else "generiek"
    print(f"[{datetime.now():%H:%M:%S}] Team-advies genereren voor gebruiker {gebruiker_id} — {label}")

    posities = laad_portefeuille(gebruiker_id, tag_id)
    if not posities:
        print(f"[ERROR] Geen posities gevonden voor gebruiker {gebruiker_id}"
              + (f", tag {tag_naam}" if tag_naam else "") + ".")
        sys.exit(1)

    koersen, wisselkoersen = laad_koersen()

    print(f"[{datetime.now():%H:%M:%S}] Macro-context, nieuws, fundamentals, kwantitatief risico laden…")
    macro = laad_macro()
    relevante_tickers = {p[0] for p in posities}
    ticker_nieuws     = laad_nieuws(tickers=relevante_tickers)
    fundamentals      = laad_fundamentals(tickers=relevante_tickers)

    kandidaat_tickers = laad_volglijst(gebruiker_id)
    kandidaat_funds   = laad_fundamentals(tickers=set(kandidaat_tickers)) if kandidaat_tickers else {}
    kandidaten        = {t: kandidaat_funds.get(t) for t in kandidaat_tickers}

    context, totaal_waarde, totaal_kosten = bouw_context(
        posities, koersen, wisselkoersen, macro, ticker_nieuws,
        fundamentals=fundamentals, kandidaten=kandidaten, tag_naam=tag_naam
    )

    risico_cijfers = laad_risico_cijfers(posities)
    risico_tekst   = format_risico_cijfers(risico_cijfers)
    if risico_tekst:
        context += "\n\n" + risico_tekst
        print(f"  kwantitatief risico: {risico_cijfers['dekking']}/{risico_cijfers['totaal_posities']} "
              f"posities gedekt over {risico_cijfers['dagen']} handelsdagen")
    else:
        print("  [WARN] te weinig koershistorie voor kwantitatief risico — sectie overgeslagen")

    risicoprofiel = laad_risicoprofiel(gebruiker_id)
    if doel:
        # Het doel stuurt het hele team, niet alleen de synthese: een advies voor
        # 'pensioen over 20 jaar' ziet er anders uit dan voor 'sparen, 2 jaar'.
        context += (f"\n\n## Doel van de belegger\n{doel}\n"
                    "Weeg elk advies expliciet tegen dit doel; noem het als een "
                    "voorstel er niet bij past.")
        risicoprofiel = f"{risicoprofiel}\n\nGekozen doel voor dit advies: {doel}"

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

    client = anthropic.Anthropic(api_key=api_key)

    # Stap 1: drie onafhankelijke concept-agents parallel (goedkoop model).
    print(f"[{datetime.now():%H:%M:%S}] Concept-agents aanroepen ({MODEL_GOEDKOOP}, parallel)…")
    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_gewaagd  = pool.submit(_call_agent, client, MODEL_GOEDKOOP, GEWAAGD_SYSTEM,
                                    f"{context}\n\nGeef je concept-advies over deze portefeuille.", 1200)
        fut_gemiddeld = pool.submit(_call_agent, client, MODEL_GOEDKOOP, GEMIDDELD_SYSTEM,
                                     f"{context}\n\nGeef je concept-advies over deze portefeuille.", 1200)
        fut_briefing  = pool.submit(_call_agent, client, MODEL_GOEDKOOP, MACRO_SYSTEM,
                                     f"{context}\n\nDuid de macro-omgeving van vandaag specifiek voor "
                                     f"deze portefeuille.", 1200)
        gewaagd_tekst   = fut_gewaagd.result()
        gemiddeld_tekst = fut_gemiddeld.result()
        briefing_tekst  = fut_briefing.result()

    # Stap 2: persoonlijk perspectief (sterker model, sequentieel — heeft stap 1 nodig).
    print(f"[{datetime.now():%H:%M:%S}] Persoonlijk perspectief aanroepen ({MODEL_STERK})…")
    perspectief_system = PERSPECTIEF_SYSTEM_TEMPLATE.format(risicoprofiel=risicoprofiel)
    perspectief_user = (
        f"### Concept 'Gewaagd'\n{gewaagd_tekst}\n\n"
        f"### Concept 'Gemiddeld risico'\n{gemiddeld_tekst}\n\n"
        f"### Macro/geo-briefing\n{briefing_tekst}\n\n"
        f"### Oorspronkelijke context (portefeuille + kwantitatief risico + macro)\n{context}"
    )
    perspectief_tekst = _call_agent(client, MODEL_STERK, perspectief_system, perspectief_user, 1500)

    # Stap 3: eindsynthese (sterker model) — enige stap die het RISICO:/TIPS:-formaat gebruikt.
    print(f"[{datetime.now():%H:%M:%S}] Eindsynthese aanroepen ({MODEL_STERK})…")
    synthese_user = bouw_synthese_prompt(
        context, gewaagd_tekst, gemiddeld_tekst, briefing_tekst, perspectief_tekst, tag_naam)
    advies_tekst = _call_agent(client, MODEL_STERK, SYNTHESE_SYSTEM, synthese_user, 4096)

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

    team_details = json.dumps({
        "gewaagd":        {"model": MODEL_GOEDKOOP, "tekst": gewaagd_tekst},
        "gemiddeld":       {"model": MODEL_GOEDKOOP, "tekst": gemiddeld_tekst},
        "macro_briefing":  {"model": MODEL_GOEDKOOP, "tekst": briefing_tekst},
        "perspectief":     {"model": MODEL_STERK, "tekst": perspectief_tekst},
    }, ensure_ascii=False)

    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        cur = conn.execute("""
            INSERT INTO adviezen
              (gebruiker_id, tag_id, gegenereerd, portfolio_snapshot,
               advies_tekst, model, risico_score, risico_reden, team_details, doel)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (gebruiker_id, tag_id, datetime.now(), snapshot,
              advies_tekst, MODEL_STERK, risico_score, risico_reden, team_details, doel))
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
    print(f"[{datetime.now():%H:%M:%S}] Team-advies opgeslagen (id={advies_id}).\n")


# ── Entry point ───────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Genereer team-advies (4 agents) via de Claude API")
    parser.add_argument("--gebruiker", type=int, required=True,
                        help="Gebruiker-ID waarvoor het advies gegenereerd wordt")
    parser.add_argument("--tag", type=int, default=None,
                        help="Tag-ID voor detailadvies (optioneel; zonder = generiek)")
    parser.add_argument("--doel", default=None,
                        help="Waarvoor je belegt (pensioen, sparen, ...); stuurt het hele team")
    args = parser.parse_args()

    genereer_team(args.gebruiker, args.tag, args.doel)
