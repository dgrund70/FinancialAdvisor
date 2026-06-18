#!/bin/bash
# update_nacht.sh — draait de zwaardere data-fetches één keer per nacht (via de
# systemd-timer beleggen-nacht.timer). De koersen draaien apart, elke 15 min.
#
# Locatie-onafhankelijk: gebruikt de venv in de repo-map waar dit script staat.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"   # repo-root (deploy/ ligt eronder)
PY="$SCRIPT_DIR/.venv/bin/python"

echo "[$(date '+%F %T')] Nachtelijke update gestart"
for script in fetch_news.py fetch_fundamentals.py fetch_historie.py fetch_benchmark.py; do
    echo "→ $script"
    # Elke fetch onafhankelijk: faalt er één (bv. netwerk), dan gaan de rest door.
    "$PY" "$SCRIPT_DIR/$script" || echo "  [WAARSCHUWING] $script faalde, ga door"
done
echo "[$(date '+%F %T')] Nachtelijke update klaar"
