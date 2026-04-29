#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PIP_CACHE_DIR="/home/dataset-local/.cache/pip"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

HOST="${RESEARCHRADAR_HOST:-0.0.0.0}"
PORT="${RESEARCHRADAR_PORT:-8765}"

cd "$ROOT"
exec "$ROOT/.venv/bin/python" -m uvicorn researchradar.app:app --host "$HOST" --port "$PORT"
