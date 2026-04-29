#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_SYSTEMD_DIR="$HOME/.config/systemd/user"
mkdir -p "$USER_SYSTEMD_DIR"

cat > "$USER_SYSTEMD_DIR/researchradar.service" <<SERVICE
[Unit]
Description=ResearchRadar local AI research source agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
Environment=PIP_CACHE_DIR=/home/dataset-local/.cache/pip
EnvironmentFile=-$ROOT/.env
ExecStart=$ROOT/.venv/bin/python -m uvicorn researchradar.app:app --host 0.0.0.0 --port 8765
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
SERVICE

systemctl --user daemon-reload
systemctl --user enable researchradar.service
systemctl --user restart researchradar.service

echo "Installed and started user systemd service: researchradar.service"
echo "For boot without login, an admin may need: sudo loginctl enable-linger $USER"
