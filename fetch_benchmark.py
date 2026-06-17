#!/usr/bin/env python3
"""fetch_benchmark.py — Historische benchmark-koersen cachen.

Voor de "had ik beter de index kunnen kopen?"-vergelijking (deposit-matched).
Haalt dagelijkse EUR-slotkoersen op per benchmark en schrijft ze naar de tabel
benchmark_punten. Periode: vanaf de vroegste storting/opname tot vandaag.

Gebruik:
  python3 fetch_benchmark.py
"""

import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from models import BENCHMARKS

SCRIPT_DIR      = Path(__file__).parent
DB_PATH         = SCRIPT_DIR / "data" / "app.db"
STANDAARD_START = date(2018, 1, 1)   # fallback als er geen stortingen zijn


def _parse_datum(waarde):
    return datetime.strptime(str(waarde)[:10], "%Y-%m-%d").date()


def _start_datum(conn):
    """Vroegste externe-flow-datum (met een weekje buffer), of de fallback."""
    try:
        row = conn.execute(
            "SELECT MIN(datum) FROM transacties WHERE type IN ('storting','opname')"
        ).fetchone()
    except sqlite3.OperationalError:
        return STANDAARD_START   # tabel bestaat nog niet
    if row and row[0]:
        try:
            return _parse_datum(row[0]) - timedelta(days=7)
        except ValueError:
            pass
    return STANDAARD_START


def fetch_ticker(ticker, start):
    """Geef ([(date, koers)], valuta) voor een benchmark-ticker via yfinance."""
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
            if koers != koers:          # NaN
                continue
            d = ts.date() if hasattr(ts, "date") else ts
            punten.append((d, float(koers)))
    return punten, valuta


def sla_op(conn, ticker, punten):
    for d, koers in punten:
        conn.execute(
            "INSERT OR REPLACE INTO benchmark_punten (ticker, datum, koers) "
            "VALUES (?, ?, ?)", (ticker, d.isoformat(), koers))
    conn.commit()
    return len(punten)


def run_once():
    if not DB_PATH.exists():
        print("[WARN] Database niet gevonden")
        return
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        start = _start_datum(conn)
        print(f"[{datetime.now():%H:%M:%S}] Benchmark-historie vanaf {start}")
        for bm in BENCHMARKS:
            ticker = bm["ticker"]
            try:
                punten, valuta = fetch_ticker(ticker, start)
            except Exception as e:
                print(f"  {ticker}: ✗ {e}")
                continue
            if valuta and valuta != "EUR":
                print(f"  {ticker}: ⚠ valuta {valuta} (niet EUR) — vergelijking kan afwijken")
            n = sla_op(conn, ticker, punten)
            laatste = punten[-1] if punten else None
            extra = f", laatste {laatste[0]} = €{laatste[1]:.2f}" if laatste else ""
            print(f"  {ticker}: {n} punten{extra}")
    finally:
        conn.close()
    print(f"  Klaar op {datetime.now():%H:%M:%S}\n")


def main():
    print("Beleggingsadviseur — benchmark service\n")
    run_once()


if __name__ == "__main__":
    main()
