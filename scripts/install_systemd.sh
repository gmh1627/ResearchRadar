#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_SRC="$ROOT/deploy/researchradar.service"
SERVICE_DST="/etc/systemd/system/researchradar.service"

sudo cp "$SERVICE_SRC" "$SERVICE_DST"

if [[ "$(ps -p 1 -o comm=)" != "systemd" ]]; then
  sudo mkdir -p /etc/systemd/system/multi-user.target.wants
  sudo ln -sfn "$SERVICE_DST" /etc/systemd/system/multi-user.target.wants/researchradar.service
  echo "Installed $SERVICE_DST and enabled the multi-user.target symlink."
  echo "This host is not currently booted with systemd as PID 1, so systemctl start/restart cannot run in this session."
  exit 0
fi

sudo systemctl daemon-reload
sudo systemctl enable researchradar.service
sudo systemctl restart researchradar.service
sudo systemctl --no-pager --full status researchradar.service
