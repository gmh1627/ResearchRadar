#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PIP_CACHE_DIR="/home/dataset-local/.cache/pip"
ENV_RESEARCHRADAR_HOST="${RESEARCHRADAR_HOST-}"
ENV_RESEARCHRADAR_PORT="${RESEARCHRADAR_PORT-}"
ENV_NO_PROXY="${NO_PROXY-}"
ENV_no_proxy="${no_proxy-}"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi
if [[ -n "$ENV_RESEARCHRADAR_HOST" ]]; then
  RESEARCHRADAR_HOST="$ENV_RESEARCHRADAR_HOST"
fi
if [[ -n "$ENV_RESEARCHRADAR_PORT" ]]; then
  RESEARCHRADAR_PORT="$ENV_RESEARCHRADAR_PORT"
fi
if [[ -n "$ENV_NO_PROXY" ]]; then
  NO_PROXY="$ENV_NO_PROXY"
fi
if [[ -n "$ENV_no_proxy" ]]; then
  no_proxy="$ENV_no_proxy"
fi

HOST="${RESEARCHRADAR_HOST:-0.0.0.0}"
PORT="${RESEARCHRADAR_PORT:-8765}"
LOCAL_NO_PROXY="localhost,127.0.0.1,::1,0.0.0.0,127.0.0.0/8"
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}$LOCAL_NO_PROXY"
export no_proxy="${no_proxy:+$no_proxy,}$LOCAL_NO_PROXY"

cd "$ROOT"
"$ROOT/scripts/print_url.sh"
exec "$ROOT/.venv/bin/python" -m uvicorn researchradar.app:app --host "$HOST" --port "$PORT"
