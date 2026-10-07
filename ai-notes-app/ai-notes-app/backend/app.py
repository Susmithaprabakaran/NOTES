# ============================================================
# AI Notes — Day 8 — Flask app factory
#
# Serves the frontend AND the API from the same Flask process on
# the same origin (http://localhost:5000). That's a deliberate
# choice: session cookies get complicated across origins (extra
# CORS config, SameSite/Secure interactions, browsers treating
# file:// pages as an unpredictable "null" origin). Same-origin
# sidesteps all of that — the browser just sends the cookie back
# automatically, no special fetch() options needed beyond
# credentials: 'same-origin' (the default).
# ============================================================
import os

from flask import Flask, send_from_directory

import db
from auth import bp as auth_bp
from notes import bp as notes_bp
from voice import bp as voice_bp
from ai import bp as ai_bp
from tasks import bp as tasks_bp
from graph import bp as graph_bp
from attachments import bp as attachments_bp
from flowchart import bp as flowchart_bp
from study import bp as study_bp
from profile import bp as profile_bp
from notifications import bp as notifications_bp

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")


def create_app(test_config=None):
    app = Flask(__name__, static_folder=None)

    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
        DATABASE_URL=os.environ.get("DATABASE_URL"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Cookie only sent over HTTPS once you're actually serving this
        # over HTTPS in production — set SESSION_COOKIE_SECURE=1 in
        # .env for that deployment. Left off by default so it still
        # works over plain localhost http during development.
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 7,  # 7 days
        # Day 21: hard request-body cap, on top of attachments.py's own
        # per-file size check — this one rejects an oversized request
        # before Flask even finishes reading the body into memory,
        # rather than after. A little bigger than MAX_UPLOAD_BYTES to
        # leave room for the rest of a multipart request's overhead
        # (headers, boundary strings, the noteId field, etc).
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,
        UPLOAD_FOLDER=os.environ.get("UPLOAD_FOLDER"),
    )

    if test_config:
        app.config.update(test_config)

    os.environ["DATABASE_URL"] = app.config["DATABASE_URL"]
    db.init_app(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(notes_bp)
    app.register_blueprint(voice_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(graph_bp)
    app.register_blueprint(attachments_bp)
    app.register_blueprint(flowchart_bp)
    app.register_blueprint(study_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(notifications_bp)

    # Day 21: without this, a request that trips MAX_CONTENT_LENGTH gets
    # Werkzeug's default HTML error page instead of the JSON error body
    # every other endpoint in this API returns — the frontend's error
    # handling expects res.json() to work.
    @app.errorhandler(413)
    def too_large(_e):
        from flask import jsonify

        return jsonify({"error": "Upload too large."}), 413

    # Security headers on every response. None of these need a new
    # dependency — they're just response headers browsers already
    # understand:
    #   X-Content-Type-Options    stops a browser from "helpfully"
    #                             re-sniffing an upload's declared type
    #   X-Frame-Options           blocks this app being framed by
    #                             another site (clickjacking)
    #   Referrer-Policy           don't leak full URLs (which can
    #                             contain note IDs) to third-party
    #                             origins referenced from the page
    #   Permissions-Policy        this app never needs geolocation/
    #                             camera-by-default access beyond the
    #                             mic prompt the user explicitly triggers
    @app.after_request
    def set_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), camera=()")
        return response

    # ---------- serve the frontend ----------
    @app.get("/")
    def index():
        return send_from_directory(FRONTEND_DIR, "index.html")

    @app.get("/<path:filename>")
    def frontend_files(filename):
        return send_from_directory(FRONTEND_DIR, filename)

    return app
