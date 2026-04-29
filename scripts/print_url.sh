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
echo "ResearchRadar is available at:"
echo "  http://127.0.0.1:$PORT"
