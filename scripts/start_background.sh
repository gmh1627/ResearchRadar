#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT/logs" "$ROOT/data"

if command -v tmux >/dev/null 2>&1; then
  if tmux has-session -t researchradar 2>/dev/null; then
    echo "ResearchRadar tmux session already running."
    exit 0
  fi
  tmux new-session -d -s researchradar "cd '$ROOT' && '$ROOT/scripts/start.sh' >> '$ROOT/logs/server.log' 2>&1"
  echo "ResearchRadar started in tmux session: researchradar"
  exit 0
fi

if [[ -f "$ROOT/data/server.pid" ]]; then
  OLD_PID="$(cat "$ROOT/data/server.pid" || true)"
  if [[ -n "${OLD_PID:-}" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "ResearchRadar already running with PID $OLD_PID"
    exit 0
  fi
fi

nohup "$ROOT/scripts/start.sh" > "$ROOT/logs/server.log" 2>&1 &
PID="$!"
echo "$PID" > "$ROOT/data/server.pid"
echo "ResearchRadar started with PID $PID"
