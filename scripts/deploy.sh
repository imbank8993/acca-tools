#!/usr/bin/env bash
# Auto-deploy hook for ACCA TOOLS.
# Called by the aaPanel Webhook each time GitHub receives a push to `main`.
# It pulls the new code, refreshes dependencies, and gracefully reloads gunicorn.

set -e

APP_DIR="/www/wwwroot/acca-tools"
cd "$APP_DIR"

echo "[deploy] $(date) — pulling latest code..."
git pull origin main

echo "[deploy] installing dependencies..."
./.venv/bin/pip install -q -r requirements-core.txt -r requirements-prod.txt
# Optional packages may need system libs; never fail the deploy on them.
./.venv/bin/pip install -q -r requirements-optional.txt || true

# Graceful, zero-downtime reload of gunicorn workers (needs pidfile in gunicorn.conf.py).
if [ -f gunicorn.pid ]; then
  echo "[deploy] reloading gunicorn (HUP)..."
  kill -HUP "$(cat gunicorn.pid)"
else
  echo "[deploy] gunicorn.pid not found — restart the app from aaPanel Python Manager."
fi

echo "[deploy] done."
