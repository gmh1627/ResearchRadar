#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_SYSTEMD_DIR="$HOME/.config/systemd/user"
mkdir -p "$USER_SYSTEMD_DIR"
PORT="${RESEARCHRADAR_PORT:-8766}"

cat > "$USER_SYSTEMD_DIR/researchradar.service" <<SERVICE
[Unit]
Description=ResearchRadar local AI research source agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
Environment=PIP_CACHE_DIR=/home/dataset-local/.cache/pip
Environment=RESEARCHRADAR_HOST=0.0.0.0
Environment=RESEARCHRADAR_PORT=$PORT
Environment=NO_PROXY=localhost,127.0.0.1,::1,0.0.0.0,127.0.0.0/8
Environment=no_proxy=localhost,127.0.0.1,::1,0.0.0.0,127.0.0.0/8
EnvironmentFile=-$ROOT/.env
ExecStart=/usr/bin/env RESEARCHRADAR_HOST=0.0.0.0 RESEARCHRADAR_PORT=$PORT NO_PROXY=localhost,127.0.0.1,::1,0.0.0.0,127.0.0.0/8 no_proxy=localhost,127.0.0.1,::1,0.0.0.0,127.0.0.0/8 $ROOT/scripts/start.sh
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
