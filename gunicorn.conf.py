# Gunicorn config for ACCA TOOLS in production (behind aaPanel / Nginx on the VPS).
# Start with:  gunicorn -c gunicorn.conf.py app:app
#
# Only used on the Linux VPS. Local dev on Windows/macOS still uses the
# built-in Flask server via the run.* launchers.

# Bind to localhost only — aaPanel's Nginx reverse-proxies the public subdomain to this.
bind = "127.0.0.1:3003"

# Write the master PID here so the auto-deploy hook (scripts/deploy.sh) can send
# a graceful reload signal (kill -HUP) after pulling new code — zero downtime.
pidfile = "gunicorn.pid"

# Worker processes. 4 is a good default; raise if the VPS has many cores.
workers = 4

# File-conversion tools (LibreOffice, FFmpeg, OCR, Whisper) can run for a while,
# so allow long requests before a worker is killed.
timeout = 300
graceful_timeout = 30

# Recycle workers periodically so memory from heavy libraries doesn't accumulate.
max_requests = 200
max_requests_jitter = 50

# Log to stdout/stderr so aaPanel's process log captures everything.
accesslog = "-"
errorlog = "-"
