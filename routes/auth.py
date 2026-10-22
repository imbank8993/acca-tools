"""SSO gate for ACCA TOOLS.

When SSO is enabled (the ``TOOLS_SSO_SECRET`` env var is set), every page is
locked behind an acca-app login. Unauthenticated visitors are bounced to
acca-app's ``/api/sso/tools`` entry, which signs a short-lived JWT and sends
them back to ``/auth/sso`` here to open a local session.

The local session carries an absolute age limit (``SSO_MAX_AGE_SECONDS``): once
it expires the gate re-checks with acca-app, so logging out of acca-app cuts
access here within that window.

When SSO is disabled (no secret — e.g. local development) the gate is a no-op
and the app behaves exactly as the upstream open-source project.
"""
import time
from urllib.parse import urlsplit

import jwt
from flask import Blueprint, current_app, jsonify, make_response, redirect, render_template, request, session, url_for

bp = Blueprint("auth", __name__)

# Paths that must stay reachable without a session.
_OPEN_PATHS = {"/auth/sso", "/auth/denied", "/auth/single-logout", "/logout"}
_OPEN_PREFIXES = ("/static/",)


def _login_entry():
    return f"{current_app.config['ACCA_APP_URL']}/api/sso/tools"


def _session_valid():
    user = session.get("user")
    if not user:
        return False
    # Absolute expiry — forces a periodic re-check against acca-app so a logout
    # there blocks access here within SSO_MAX_AGE_SECONDS.
    age = time.time() - user.get("t", 0)
    if age > current_app.config["SSO_MAX_AGE_SECONDS"]:
        session.clear()
        return False
    return True


@bp.before_app_request
def enforce_login():
    if not current_app.config.get("SSO_ENABLED"):
        return  # SSO off (local dev) — leave the app fully open.

    path = request.path
    if path in _OPEN_PATHS or path.startswith(_OPEN_PREFIXES):
        return
    if _session_valid():
        return

    return redirect(_login_entry())


@bp.route("/auth/sso")
def sso():
    """Receive the signed token from acca-app and open a local session."""
    if not current_app.config.get("SSO_ENABLED"):
        return redirect(url_for("index"))  # SSO disabled — nothing to verify.

    token = request.args.get("token", "")
    if not token:
        return redirect(url_for("auth.denied"))

    try:
        payload = jwt.decode(
            token,
            current_app.config["TOOLS_SSO_SECRET"],
            algorithms=["HS256"],
        )
    except jwt.PyJWTError:
        # Expired / bad signature / malformed / bad key.
        return redirect(url_for("auth.denied"))

    session.permanent = True
    session["user"] = {
        "email": payload.get("email", ""),
        "name": payload.get("name") or payload.get("email", ""),
        "t": int(time.time()),
    }
    return redirect(url_for("index"))


@bp.route("/auth/denied")
def denied():
    """Elegant 'please log in at ACCA' page."""
    return render_template("denied.html"), 403


@bp.route("/logout")
def logout():
    """Clear the local session. Called directly, and by acca-app's logout so a
    single logout there also ends the ACCA TOOLS session immediately."""
    session.clear()
    return redirect(f"{current_app.config['ACCA_APP_URL']}/login")


@bp.route("/auth/single-logout", methods=["POST", "OPTIONS"])
def single_logout():
    """Only ACCA's exact browser origin may clear the current Tools session."""
    configured = urlsplit(current_app.config["ACCA_APP_URL"])
    expected = f"{configured.scheme}://{configured.netloc}"
    origin = request.headers.get("Origin", "")
    if origin != expected:
        return jsonify(ok=False, error="Origin tidak diizinkan."), 403
    if request.method == "OPTIONS":
        response = make_response("", 204)
    else:
        session.clear()
        response = make_response(jsonify(ok=True))
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Origin"
    return response
