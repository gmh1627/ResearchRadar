#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${RESEARCHRADAR_PORT:-8766}"
LINE="@reboot cd $ROOT && RESEARCHRADAR_PORT=$PORT $ROOT/scripts/start_background.sh"
TMP="$(mktemp)"

crontab -l 2>/dev/null | grep -v "ResearchRadar/scripts/start_background.sh" > "$TMP" || true
echo "$LINE" >> "$TMP"
crontab "$TMP"
rm -f "$TMP"

echo "Installed @reboot autostart via crontab."
