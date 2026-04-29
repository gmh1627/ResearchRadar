#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PIP_CACHE_DIR="/home/dataset-local/.cache/pip"
mkdir -p "$PIP_CACHE_DIR"

cd "$ROOT"
python3 -m venv .venv
".venv/bin/python" -m pip install --upgrade pip
".venv/bin/python" -m pip install -r requirements.txt
".venv/bin/python" -m researchradar init-db

echo "Virtualenv ready: $ROOT/.venv"
