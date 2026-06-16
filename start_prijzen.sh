#!/bin/bash
# start_prijzen.sh — Installeer dependencies en start de prijzen-service
#
# Eerste keer:  chmod +x start_prijzen.sh && ./start_prijzen.sh
# Daarna:       ./start_prijzen.sh            (eenmalig)
#               ./start_prijzen.sh --loop     (elke 15 min herhalen)

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

# Maak venv aan als die er nog niet is
if [ ! -d "$VENV" ]; then
    echo "→ Python venv aanmaken…"
    python3 -m venv "$VENV"
fi

# Activeer venv
source "$VENV/bin/activate"

# Installeer/update dependencies
echo "→ Dependencies controleren…"
pip install --quiet --upgrade yfinance requests

echo "→ Starten…"
python3 "$SCRIPT_DIR/fetch_prices.py" "$@"
