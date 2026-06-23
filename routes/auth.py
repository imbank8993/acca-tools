"""SSO gate for ACCA TOOLS.

When SSO is enabled (the ``TOOLS_SSO_SECRET`` env var is set), every page is
locked behind an acca-app login. Unauthenticated visitors are bounced to
acca-app's ``/api/sso/tools`` entry, which signs a short-lived JWT and sends
them back to ``/auth/sso`` here to open a local session.

When SSO is disabled (no secret — e.g. local development) the gate is a no-op
and the app behaves exactly as the upstream open-source project.
"""
import jwt
from flask import Blueprint, current_app, redirect, request, session, url_for

bp = Blueprint("auth", __name__)

# Paths that must stay reachable without a session.
_OPEN_PATHS = {"/auth/sso", "/logout"}
_OPEN_PREFIXES = ("/static/",)


def _login_entry():
    return f"{current_app.config['ACCA_APP_URL']}/api/sso/tools"


@bp.before_app_request
def enforce_login():
    if not current_app.config.get("SSO_ENABLED"):
        return  # SSO off (local dev) — leave the app fully open.

    path = request.path
    if path in _OPEN_PATHS or path.startswith(_OPEN_PREFIXES):
        return
    if session.get("user"):
        return

    return redirect(_login_entry())


@bp.route("/auth/sso")
def sso():
    """Receive the signed token from acca-app and open a local session."""
    token = request.args.get("token", "")
    if not token:
        return redirect(_login_entry())

    try:
        payload = jwt.decode(
            token,
            current_app.config["TOOLS_SSO_SECRET"],
            algorithms=["HS256"],
        )
    except jwt.InvalidTokenError:
        # Covers expired (tokens live ~2 min), bad signature, malformed, etc.
        # Just restart the hand-off with acca-app.
        return redirect(_login_entry())

    session.permanent = True
    session["user"] = {
        "email": payload.get("email", ""),
        "name": payload.get("name") or payload.get("email", ""),
    }
    return redirect(url_for("index"))


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(_login_entry())
