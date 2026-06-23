#!/usr/bin/env bash
# Auto-deploy hook for ACCA TOOLS (called by the aaPanel Webhook on push to main).
# Pulls new code, installs core deps, and reloads the app (zero-downtime).

set -e

APP_DIR="/www/wwwroot/acca-tools"
cd "$APP_DIR"

echo "[deploy] $(date '+%Y-%m-%d %H:%M:%S') — pulling latest code..."
git fetch --quiet origin main
git reset --hard origin/main

echo "[deploy] installing core dependencies..."
./.venv/bin/pip install -q -r requirements-core.txt -r requirements-prod.txt
# NOTE: heavy OPTIONAL deps (rembg, whisper, etc.) are intentionally NOT installed
# on every deploy — they are large/slow and rarely change, which would make the
# webhook hang. Install them manually once when needed:
#   ./.venv/bin/pip install -r requirements-optional.txt

echo "[deploy] reloading app..."
if systemctl is-active --quiet acca-tools; then
  systemctl reload acca-tools || systemctl restart acca-tools
elif [ -f gunicorn.pid ]; then
  kill -HUP "$(cat gunicorn.pid)"
else
  echo "[deploy] WARNING: app not running under systemd and no gunicorn.pid found."
fi

echo "[deploy] done."
