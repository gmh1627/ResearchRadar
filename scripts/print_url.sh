#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

PORT="${RESEARCHRADAR_PORT:-8765}"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [[ -z "${IP:-}" ]]; then
  IP="127.0.0.1"
fi

echo "ResearchRadar is available on this server at:"
echo "  http://$IP:$PORT"
echo "  http://127.0.0.1:$PORT"
echo
echo "If http://$IP:$PORT is not reachable from your browser, use SSH port forwarding:"
echo "  ssh -L $PORT:127.0.0.1:$PORT <user>@<server>"
echo "Then open:"
echo "  http://127.0.0.1:$PORT"
