# ============================================================
# Production entry point (gunicorn / any WSGI server).
#
#   cd backend && gunicorn --workers 1 --threads 4 wsgi:app
#
# run.py is the *development* launcher (Flask's debug server, debug=True —
# never expose that to the internet). This does the same two jobs it does —
# make sure the database schema exists, then build the app — minus the
# debug server.
# ============================================================
import os
from pathlib import Path

from dotenv import load_dotenv

# On a host, settings come from real environment variables (which
# load_dotenv never overrides). Locally, this picks up backend/.env.
load_dotenv(Path(__file__).resolve().parent / ".env")

from app import create_app  # noqa: E402  (must come after load_dotenv)
from db import init_db  # noqa: E402

database_url = os.environ.get("DATABASE_URL")
if not database_url:
    raise RuntimeError("DATABASE_URL is not set — point it at your PostgreSQL database.")

# SESSION_COOKIE_SECURE=1 is what marks a real HTTPS deployment. There, the
# built-in fallback SECRET_KEY (public, in app.py) would let anyone forge a
# login cookie, so refuse to start rather than run unsafely.
if os.environ.get("SESSION_COOKIE_SECURE") == "1" and os.environ.get(
    "SECRET_KEY", "dev-secret-change-me"
) in ("", "dev-secret-change-me", "replace-with-your-own-generated-secret"):
    raise RuntimeError(
        "SECRET_KEY must be set to your own random value in production. "
        'Generate one: python3 -c "import secrets; print(secrets.token_hex(32))"'
    )

init_db(database_url)  # idempotent: creates/updates tables, safe on every start
app = create_app()
