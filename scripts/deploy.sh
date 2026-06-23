#!/usr/bin/env bash
# Auto-deploy hook for ACCA TOOLS.
# Called by the aaPanel Webhook each time GitHub receives a push to `main`.
# Pulls new code, refreshes dependencies, and reloads the app (zero-downtime).

set -e

APP_DIR="/www/wwwroot/acca-tools"
cd "$APP_DIR"

echo "[deploy] $(date '+%Y-%m-%d %H:%M:%S') — pulling latest code..."
git fetch --quiet origin main
git reset --hard origin/main

echo "[deploy] installing dependencies..."
./.venv/bin/pip install -q -r requirements-core.txt -r requirements-prod.txt
# Optional packages may need system libs; never fail the deploy on them.
./.venv/bin/pip install -q -r requirements-optional.txt || true

# Reload the app. systemd is the process manager (see deploy/acca-tools.service);
# `reload` sends HUP to gunicorn's master = graceful worker reload, no downtime.
if systemctl is-active --quiet acca-tools; then
  echo "[deploy] reloading via systemd..."
  systemctl reload acca-tools || systemctl restart acca-tools
elif [ -f gunicorn.pid ]; then
  echo "[deploy] reloading gunicorn (HUP)..."
  kill -HUP "$(cat gunicorn.pid)"
else
  echo "[deploy] WARNING: app not running under systemd and no gunicorn.pid found."
fi

echo "[deploy] done."
