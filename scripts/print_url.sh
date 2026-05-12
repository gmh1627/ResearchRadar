#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_RESEARCHRADAR_PORT="${RESEARCHRADAR_PORT-}"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi
if [[ -n "$ENV_RESEARCHRADAR_PORT" ]]; then
  RESEARCHRADAR_PORT="$ENV_RESEARCHRADAR_PORT"
fi

PORT="${RESEARCHRADAR_PORT:-8765}"
echo "ResearchRadar is available at:"
echo "  http://127.0.0.1:$PORT"
