# ============================================================
# AI Notes — Day 8 — authentication
#
# Session-based auth (Flask's signed cookie session, not JWT):
# on login we store the user's id in the session; the cookie is
# HttpOnly + SameSite=Lax by default via Flask's config, so it's
# not readable from JS and isn't sent on top-level cross-site
# navigations. Passwords are hashed with werkzeug's
# generate_password_hash (PBKDF2-SHA256 with a per-password
# salt) — never stored or logged in plain text.
# ============================================================
import functools
import re

from flask import Blueprint, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_db
import security

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")
MIN_PASSWORD_LENGTH = 8


def login_required(view):
    """Decorator for routes that require an authenticated session."""

    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("user_id") is None:
            return jsonify({"error": "Not logged in."}), 401
        return view(*args, **kwargs)

    return wrapped


def current_user_id():
    return session.get("user_id")


def _validate_credentials(username, password):
    if not username or not isinstance(username, str):
        return "Username is required."
    if not USERNAME_RE.match(username):
        return "Username must be 3-32 characters: letters, numbers, underscore, dot, or hyphen."
    if not password or not isinstance(password, str):
        return "Password is required."
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    return None


@bp.post("/register")
def register():
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    error = _validate_credentials(username, password)
    if error:
        return jsonify({"error": error}), 400

    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE username = %s", (username,))
        if cur.fetchone():
            return jsonify({"error": "That username is already taken."}), 409

        password_hash = generate_password_hash(password)
        cur.execute(
            "INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING id, username, created_at",
            (username, password_hash),
        )
        user = cur.fetchone()
    db.commit()

    session.clear()
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session.permanent = True

    return jsonify({"id": user["id"], "username": user["username"]}), 201


@bp.post("/login")
def login():
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    if not username or not password:
        return jsonify({"error": "Username and password are required."}), 400

    # Suspicious-activity guard: too many recent failures for this
    # username OR this caller's IP blocks the attempt before it ever
    # touches the password check — a locked-out brute-forcer can't
    # learn anything from timing differences either.
    if security.is_rate_limited(username):
        return (
            jsonify(
                {
                    "error": (
                        "Too many failed login attempts. Please wait a few minutes "
                        "before trying again."
                    )
                }
            ),
            429,
        )

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, username, password_hash FROM users WHERE username = %s", (username,)
        )
        user = cur.fetchone()

    # Same generic error whether the username doesn't exist or the
    # password is wrong — don't leak which one it was.
    if not user or not check_password_hash(user["password_hash"], password):
        security.record_attempt(username, success=False, user_id=user["id"] if user else None)
        return jsonify({"error": "Invalid username or password."}), 401

    # Check for a burst of recent failures BEFORE recording this
    # success, so the count reflects "other attempts before this one",
    # not this login itself.
    recent_failures = security.recent_failure_count(username)
    security.record_attempt(username, success=True, user_id=user["id"])

    session.clear()
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session.permanent = True

    response = {"id": user["id"], "username": user["username"]}
    if recent_failures >= security.SUSPICIOUS_FAILURE_THRESHOLD:
        response["suspiciousActivity"] = (
            f"Heads up: {recent_failures} failed login attempts were made on this account "
            "shortly before this one. If that wasn't you, change your password."
        )
    return jsonify(response), 200


@bp.post("/logout")
def logout():
    session.clear()
    return jsonify({"ok": True}), 200


@bp.get("/me")
def me():
    if session.get("user_id") is None:
        return jsonify({"error": "Not logged in."}), 401
    return jsonify({"id": session["user_id"], "username": session["username"]}), 200
