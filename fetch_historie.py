#!/usr/bin/env python3
"""fetch_historie.py — Dagelijkse EUR-koershistorie cachen voor risico-analyse.

Haalt ~14 maanden dagkoersen op voor alle holdings (+ de MSCI World-benchmark
voor beta) via yfinance en schrijft ze als EUR naar de tabel `koers_historie`.
USD-noteringen worden met een historische USD→EUR-reeks omgerekend; crypto
`*-EUR`-paren en EUR-ETF's worden ongewijzigd opgeslagen.

Gebruik:
  python3 fetch_historie.py
"""

import bisect
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

SCRIPT_DIR  = Path(__file__).parent
DB_PATH     = SCRIPT_DIR / "data" / "app.db"
LOOKBACK    = 430                       # kalenderdagen (~14 maanden)
BENCHMARK   = "IWDA.AS"                  # MSCI World, voor beta


def get_tickers(conn):
    tickers = set()
    try:
        for (t,) in conn.execute("SELECT DISTINCT ticker FROM posities WHERE aantal > 0"):
            if t:
                tickers.add(t)
    except sqlite3.OperationalError:
        pass
    tickers.add(BENCHMARK)
    return tickers


def _closes(ticker, start):
    """Geef (valuta, [(date, close)]) voor een ticker, of (None, []) bij mislukking."""
    import yfinance as yf
    t = yf.Ticker(ticker)
    valuta = None
    try:
        valuta = t.fast_info.currency
    except Exception:
        pass
    hist = t.history(start=start.isoformat(),
                     end=(date.today() + timedelta(days=1)).isoformat())
    punten = []
    if "Close" in hist:
        for ts, koers in hist["Close"].items():
            if koers == koers:                       # niet-NaN
                d = ts.date() if hasattr(ts, "date") else ts
                punten.append((d, float(koers)))
    return valuta, punten


def _fx_reeks(valuta, start, cache):
    """Sorted (datums, koersen) van {valuta}→EUR, gecachet per valuta in de run."""
    if valuta in cache:
        return cache[valuta]
    _, punten = _closes(f"{valuta.upper()}EUR=X", start)
    punten.sort()
    paar = ([d for d, _ in punten], [k for _, k in punten])
    cache[valuta] = paar
    return paar


def _fx_op(reeks, datum):
    datums, koersen = reeks
    i = bisect.bisect_right(datums, datum) - 1
    return koersen[i] if i >= 0 else None


def sla_op(conn, ticker, punten):
    for d, koers in punten:
        conn.execute(
            "INSERT OR REPLACE INTO koers_historie (ticker, datum, koers) "
            "VALUES (?, ?, ?)", (ticker, d.isoformat(), koers))
    conn.commit()
    return len(punten)


def run_once():
    if not DB_PATH.exists():
        print("[WARN] Database niet gevonden")
        return
    conn  = sqlite3.connect(DB_PATH, timeout=10)
    start = date.today() - timedelta(days=LOOKBACK)
    fx_cache = {}
    try:
        tickers = get_tickers(conn)
        print(f"[{datetime.now():%H:%M:%S}] Koershistorie vanaf {start}: {', '.join(sorted(tickers))}")
        for ticker in sorted(tickers):
            try:
                valuta, punten = _closes(ticker, start)
            except Exception as e:
                print(f"  {ticker}: ✗ {e}")
                continue
            if not punten:
                print(f"  {ticker}: ✗ geen data")
                continue
            # Naar EUR omrekenen indien nodig
            if valuta and valuta.upper() != "EUR":
                reeks = _fx_reeks(valuta, start, fx_cache)
                omgerekend = []
                for d, koers in punten:
                    fx = _fx_op(reeks, d)
                    if fx:
                        omgerekend.append((d, koers * fx))
                punten = omgerekend
                if not punten:
                    print(f"  {ticker}: ✗ geen FX ({valuta})")
                    continue
            n = sla_op(conn, ticker, punten)
            print(f"  {ticker}: {n} punten ({valuta or '?'})")
    finally:
        conn.close()
    print(f"  Klaar op {datetime.now():%H:%M:%S}\n")


def main():
    print("Beleggingsadviseur — koershistorie service\n")
    run_once()


if __name__ == "__main__":
    main()
