#!/usr/bin/env python3
"""
fetch_news.py — Nieuws ophalen via yfinance voor de adviesmodule

Gebruik:
  python3 fetch_news.py            # eenmalig
  python3 fetch_news.py --loop     # elk uur herhalen
"""

import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR   = Path(__file__).parent
DATA_DIR     = SCRIPT_DIR / "data"
DB_PATH      = DATA_DIR / "app.db"
BEWAAR_DAGEN = 7


def get_tickers():
    """Haal alle portfolio-tickers op uit de database (live posities)."""
    if not DB_PATH.exists():
        print("[WARN] Database niet gevonden")
        return set()

    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        tickers = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT ticker FROM posities WHERE koers_type = 'live'"
            ).fetchall() if r[0]
        }
    finally:
        conn.close()

    return tickers


def haal_nieuws_op(ticker: str) -> list:
    """Haal recente nieuwsartikelen op voor een ticker via yfinance."""
    import yfinance as yf
    try:
        return yf.Ticker(ticker).news or []
    except Exception as e:
        print(f"  [WARN] Nieuws voor {ticker} mislukt: {e}")
        return []


def _normaliseer(art: dict) -> dict:
    """Haal velden uit een yfinance-artikel.

    Ondersteunt zowel het oude platte formaat (title/link/publisher/
    providerPublishTime) als het nieuwe formaat waarbij alles onder
    een 'content'-sleutel zit.
    """
    c = art.get("content") if isinstance(art.get("content"), dict) else art

    titel = (c.get("title") or art.get("titel") or "").strip()

    url = (c.get("link") or c.get("url") or "").strip()
    if not url:
        for sleutel in ("canonicalUrl", "clickThroughUrl"):
            sub = c.get(sleutel)
            if isinstance(sub, dict) and sub.get("url"):
                url = sub["url"].strip()
                break

    bron = c.get("publisher") or ""
    prov = c.get("provider")
    if not bron and isinstance(prov, dict):
        bron = prov.get("displayName") or ""

    gepubliceerd = None
    ts = c.get("providerPublishTime")
    if isinstance(ts, (int, float)):
        gepubliceerd = datetime.fromtimestamp(ts)
    else:
        datum_str = c.get("pubDate") or c.get("displayTime")
        if isinstance(datum_str, str) and datum_str:
            try:
                gepubliceerd = datetime.fromisoformat(
                    datum_str.replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except ValueError:
                pass

    return {
        "titel":        titel,
        "url":          url,
        "samenvatting": c.get("summary") or c.get("description") or "",
        "bron":         bron,
        "gepubliceerd": gepubliceerd,
    }


def sla_op(artikelen: list, ticker=None) -> int:
    """Schrijf nieuwe artikelen naar de database; sla duplicaten over."""
    if not artikelen:
        return 0

    conn = sqlite3.connect(DB_PATH, timeout=10)
    nieuw = 0
    try:
        for art in artikelen:
            data = _normaliseer(art)
            if not data["url"] or not data["titel"]:
                continue

            if conn.execute(
                "SELECT 1 FROM nieuws_cache WHERE url = ?", (data["url"],)
            ).fetchone():
                continue

            conn.execute("""
                INSERT INTO nieuws_cache
                  (ticker, titel, samenvatting, url, bron, gepubliceerd, opgeslagen)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                ticker,
                data["titel"][:500],
                (data["samenvatting"] or "")[:2000],
                data["url"][:1000],
                (data["bron"] or "")[:100],
                data["gepubliceerd"],
                datetime.now(),
            ))
            nieuw += 1

        conn.commit()
    finally:
        conn.close()

    return nieuw


def ruim_op():
    """Verwijder artikelen ouder dan BEWAAR_DAGEN dagen."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        grens   = datetime.now() - timedelta(days=BEWAAR_DAGEN)
        deleted = conn.execute(
            "DELETE FROM nieuws_cache WHERE opgeslagen < ?", (grens,)
        ).rowcount
        conn.commit()
        if deleted:
            print(f"  {deleted} verlopen artikelen verwijderd")
    finally:
        conn.close()


def run_once():
    tickers = get_tickers()

    if tickers:
        print(f"[{datetime.now():%H:%M:%S}] Live tickers: {', '.join(sorted(tickers))}")
        for ticker in sorted(tickers):
            n = sla_op(haal_nieuws_op(ticker), ticker=ticker)
            print(f"  {ticker}: {n} nieuwe artikelen")
    else:
        print(f"[{datetime.now():%H:%M:%S}] Geen live tickers gevonden in database.")

    ruim_op()
    print(f"\n  Klaar op {datetime.now():%H:%M:%S}\n")


def main():
    loop     = "--loop" in sys.argv
    interval = 60
    print("Beleggingsadviseur — nieuws service")
    print(f"Mode: {'herhalen elke ' + str(interval) + ' min' if loop else 'eenmalig'}\n")

    if loop:
        while True:
            try:
                run_once()
            except KeyboardInterrupt:
                print("\nGestopt.")
                break
            except Exception as e:
                print(f"[ERROR] {e}")
            try:
                time.sleep(interval * 60)
            except KeyboardInterrupt:
                print("\nGestopt.")
                break
    else:
        run_once()


if __name__ == "__main__":
    main()
