#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/data/server.pid"

if command -v tmux >/dev/null 2>&1 && tmux has-session -t =researchradar 2>/dev/null; then
  tmux kill-session -t =researchradar
  echo "Stopped ResearchRadar tmux session."
  exit 0
fi

if [[ ! -f "$PID_FILE" ]]; then
  echo "No PID file found."
  exit 0
fi

PID="$(cat "$PID_FILE")"
if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  echo "Stopped ResearchRadar PID $PID"
else
  echo "ResearchRadar process is not running."
fi
rm -f "$PID_FILE"
