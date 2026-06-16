#!/usr/bin/env python3
"""
fetch_prices.py — Koersen ophalen voor Beleggingsadviseur
Haalt live koersen op en schrijft ze naar data/prijzen.json

Gebruik:
  python3 fetch_prices.py            # eenmalig
  python3 fetch_prices.py --loop     # elke 15 minuten herhalen

Vereisten: pip install yfinance requests
"""

import json
import sys
import time
import os
from datetime import datetime
from pathlib import Path

# ── Configuratie ────────────────────────────────────────────────
SCRIPT_DIR   = Path(__file__).parent
DATA_DIR     = SCRIPT_DIR / "data"
PRIJZEN_FILE = DATA_DIR / "prijzen.json"
APP_DATA     = DATA_DIR / "app_data.json"
INTERVAL_MIN = 15   # minuten tussen updates bij --loop

COINGECKO_IDS = {
    "BTC":   "bitcoin",
    "ETH":   "ethereum",
    "SOL":   "solana",
    "ADA":   "cardano",
    "DOT":   "polkadot",
    "MATIC": "matic-network",
    "LINK":  "chainlink",
    "XRP":   "ripple",
    "LTC":   "litecoin",
    "BNB":   "binancecoin",
    "AVAX":  "avalanche-2",
}

# ── Tickers ophalen uit SQLite-database ──────────────────────────
def get_tickers():
    import sqlite3
    db_path = DATA_DIR / "app.db"
    if db_path.exists():
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT DISTINCT ticker FROM posities").fetchall()
        tickers = {r[0] for r in rows if r[0] and r[0] != "HANDMATIG"}
        # ook door adviezen aangeraden tickers volgen (voor rendement-tracking)
        try:
            tip_rows = conn.execute("SELECT DISTINCT ticker FROM aanbevelingen").fetchall()
            tickers |= {r[0] for r in tip_rows if r[0]}
        except sqlite3.OperationalError:
            pass   # tabel bestaat nog niet
        conn.close()
        if tickers:
            return tickers
    print("[WARN] Geen database gevonden — gebruik standaard tickers")
    return {"VWCE.AS", "BTC-EUR"}


# ── Koers ophalen ────────────────────────────────────────────────
def is_crypto(ticker: str) -> bool:
    """Crypto-ticker = 'BASE-QUOTE' waarbij BASE een bekende crypto is.
    Zo worden aandelen met een koppelteken (bijv. NOVO-B.CO, BRK-B) NIET
    abusievelijk als crypto behandeld."""
    base = ticker.split("-", 1)[0].upper()
    return "-" in ticker and base in COINGECKO_IDS


def fetch_crypto(ticker: str) -> dict:
    import requests
    parts = ticker.split("-", 1)
    if len(parts) != 2:
        return {"fout": f"Ongeldig crypto-ticker formaat: {ticker}"}
    from_cur, to_cur = parts
    cg_id = COINGECKO_IDS.get(from_cur.upper())
    if not cg_id:
        return {"fout": f"Onbekende crypto: {from_cur}"}
    vs  = to_cur.lower()
    url = (f"https://api.coingecko.com/api/v3/simple/price"
           f"?ids={cg_id}&vs_currencies={vs}&include_24hr_change=true")
    r   = requests.get(url, timeout=10)
    r.raise_for_status()
    cd  = r.json().get(cg_id, {})
    if not cd:
        return {"fout": "Geen CoinGecko data"}
    koers   = float(cd[vs])
    dag_pct = float(cd.get(f"{vs}_24h_change", 0))
    prev    = koers / (1 + dag_pct / 100) if dag_pct != -100 else koers
    return {
        "koers":   round(koers, 2),
        "prev":    round(prev, 2),
        "dag":     round(koers - prev, 2),
        "dag_pct": round(dag_pct, 2),
        "valuta":  to_cur,
    }


def _veilig_getal(waarde):
    """Naar float; None bij None/NaN/ongeldig (f == f is False bij NaN)."""
    try:
        f = float(waarde)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _fast_attr(fi, naam):
    """Lees een attribuut uit fast_info; dat kan KeyError gooien bij lege data."""
    try:
        return getattr(fi, naam)
    except Exception:
        return None


def fetch_equity(ticker: str) -> dict:
    import yfinance as yf
    t  = yf.Ticker(ticker)
    fi = t.fast_info

    lp     = _veilig_getal(_fast_attr(fi, "last_price"))
    pc     = _veilig_getal(_fast_attr(fi, "previous_close"))
    valuta = _fast_attr(fi, "currency")

    # Dun verhandelde listings geven geen fast_info-koers terug.
    # Val dan terug op de laatste slotkoersen uit de historie.
    if lp is None or pc is None:
        try:
            closes = [c for c in t.history(period="5d")["Close"].tolist()
                      if _veilig_getal(c) is not None]
        except Exception:
            closes = []
        if lp is None and closes:
            lp = float(closes[-1])
        if pc is None and len(closes) >= 2:
            pc = float(closes[-2])

    if lp is None:
        return {"fout": "geen koers beschikbaar"}
    if pc is None:
        pc = lp   # geen vorige slotkoers bekend → dagverandering 0

    pct = round((lp - pc) / pc * 100, 2) if pc else 0
    return {
        "koers":   round(lp, 4),
        "prev":    round(pc, 4),
        "dag":     round(lp - pc, 4),
        "dag_pct": pct,
        "valuta":  str(valuta) if valuta else "EUR",
    }


def fetch_fx_naar_eur(valuta: str):
    """Wisselkoers: 1 eenheid `valuta` = X euro. None als ophalen mislukt."""
    if not valuta or valuta.upper() == "EUR":
        return 1.0
    import yfinance as yf
    try:
        fi   = yf.Ticker(f"{valuta.upper()}EUR=X").fast_info
        rate = float(fi.last_price)
        if rate > 0:
            return round(rate, 6)
    except Exception:
        pass
    return None


# ── Hoofd-loop ───────────────────────────────────────────────────
def run_once():
    tickers = get_tickers()
    print(f"[{datetime.now():%H:%M:%S}] Ophalen: {', '.join(sorted(tickers))}")

    result = {}
    for t in tickers:
        try:
            if is_crypto(t):
                result[t] = fetch_crypto(t)
            else:
                result[t] = fetch_equity(t)
            status = "✓" if "fout" not in result[t] else f"✗ {result[t]['fout']}"
            print(f"  {t}: {status}")
        except Exception as e:
            result[t] = {"fout": str(e)}
            print(f"  {t}: ✗ {e}")

    # Wisselkoersen naar EUR ophalen voor alle voorkomende valuta's
    valutas = {v.get("valuta") for v in result.values()
               if isinstance(v, dict) and v.get("valuta")}
    wisselkoersen = {"EUR": 1.0}
    for cur in sorted(valutas):
        if cur and cur.upper() != "EUR":
            rate = fetch_fx_naar_eur(cur)
            wisselkoersen[cur] = rate
            print(f"  FX {cur}→EUR: {rate if rate is not None else '✗ mislukt'}")

    DATA_DIR.mkdir(exist_ok=True)
    output = {
        "bijgewerkt":    datetime.now().isoformat(),
        "koersen":       result,
        "wisselkoersen": wisselkoersen,
    }
    tmp = PRIJZEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PRIJZEN_FILE)
    ok = sum(1 for v in result.values() if "fout" not in v)
    print(f"  → {ok}/{len(tickers)} koersen opgehaald, weggeschreven naar {PRIJZEN_FILE}\n")


def main():
    loop = "--loop" in sys.argv
    print("Beleggingsadviseur — prijzen service")
    print(f"Mode: {'herhalen elke ' + str(INTERVAL_MIN) + ' min' if loop else 'eenmalig'}\n")

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
                time.sleep(INTERVAL_MIN * 60)
            except KeyboardInterrupt:
                print("\nGestopt.")
                break
    else:
        run_once()


if __name__ == "__main__":
    main()
