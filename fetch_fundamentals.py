#!/usr/bin/env python3
"""fetch_fundamentals.py — Fundamentals per positie cachen voor de adviesmodule.

Haalt per live-ticker waardering/groei/marges/analisten-koersdoel op via
yfinance (Ticker.info) en schrijft ze naar de tabel `fundamentals`. De
adviesmodule injecteert deze cijfers in de context zodat het houden/verkopen-
advies op echte data steunt i.p.v. alleen koppen en macro.

Gebruik:
  python3 fetch_fundamentals.py
"""

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
DB_PATH    = SCRIPT_DIR / "data" / "app.db"

# yfinance .info-sleutel → kolom in `fundamentals`
VELDEN = {
    "naam":               ("longName", "shortName"),
    "sector":             ("sector",),
    "land":               ("country",),
    "markt_kap":          ("marketCap",),
    "pe":                 ("trailingPE",),
    "forward_pe":         ("forwardPE",),
    "koers_boekwaarde":   ("priceToBook",),
    "dividend_rendement": ("dividendYield",),
    "winstmarge":         ("profitMargins",),
    "omzetgroei":         ("revenueGrowth",),
    "winstgroei":         ("earningsGrowth",),
    "rendement_ev":       ("returnOnEquity",),
    "schuld_ev":          ("debtToEquity",),
    "koersdoel":          ("targetMeanPrice",),
    "aanbeveling":        ("recommendationKey",),
    "valuta":             ("currency",),
}
_NUMERIEK = {"markt_kap", "pe", "forward_pe", "koers_boekwaarde", "dividend_rendement",
             "winstmarge", "omzetgroei", "winstgroei", "rendement_ev", "schuld_ev",
             "koersdoel"}


def get_live_tickers(conn):
    """Live-positie-tickers + volglijst-kandidaten (beide krijgen fundamentals)."""
    tickers = set()
    try:
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM posities WHERE koers_type='live' AND aantal > 0"
        ).fetchall()
        tickers |= {r[0] for r in rows if r[0]}
    except sqlite3.OperationalError:
        pass
    try:
        rows = conn.execute("SELECT DISTINCT ticker FROM volglijst").fetchall()
        tickers |= {r[0] for r in rows if r[0]}
    except sqlite3.OperationalError:
        pass
    return tickers


def _coerce(kolom, waarde):
    if waarde is None:
        return None
    if kolom in _NUMERIEK:
        try:
            f = float(waarde)
        except (TypeError, ValueError):
            return None
        return f if f == f else None    # NaN → None
    return str(waarde)


def fetch_one(ticker):
    """Geef een dict {kolom: waarde} voor één ticker, of None bij mislukking."""
    import yfinance as yf
    info = yf.Ticker(ticker).info
    if not isinstance(info, dict) or not info:
        return None
    rij = {}
    for kolom, sleutels in VELDEN.items():
        waarde = None
        for s in sleutels:
            if info.get(s) is not None:
                waarde = info.get(s)
                break
        rij[kolom] = _coerce(kolom, waarde)
    # Alleen bewaren als er iets bruikbaars is opgehaald
    if not any(v is not None for k, v in rij.items() if k not in ("naam", "valuta")):
        return None
    return rij


def sla_op(conn, ticker, rij):
    kolommen = ["ticker"] + list(VELDEN.keys()) + ["opgehaald"]
    waarden  = [ticker] + [rij.get(k) for k in VELDEN] + [datetime.now()]
    plaats   = ", ".join("?" * len(kolommen))
    conn.execute(
        f"INSERT OR REPLACE INTO fundamentals ({', '.join(kolommen)}) VALUES ({plaats})",
        waarden,
    )
    conn.commit()


def run_once():
    if not DB_PATH.exists():
        print("[WARN] Database niet gevonden")
        return
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        tickers = get_live_tickers(conn)
        if not tickers:
            print(f"[{datetime.now():%H:%M:%S}] Geen live tickers gevonden.")
            return
        print(f"[{datetime.now():%H:%M:%S}] Fundamentals ophalen: {', '.join(sorted(tickers))}")
        for ticker in sorted(tickers):
            try:
                rij = fetch_one(ticker)
            except Exception as e:
                print(f"  {ticker}: ✗ {e}")
                continue
            if not rij:
                print(f"  {ticker}: ✗ geen fundamentals")
                continue
            sla_op(conn, ticker, rij)
            print(f"  {ticker}: ✓ {rij.get('sector') or ''}".rstrip())
    finally:
        conn.close()
    print(f"  Klaar op {datetime.now():%H:%M:%S}\n")


def main():
    print("Beleggingsadviseur — fundamentals service\n")
    run_once()


if __name__ == "__main__":
    main()
