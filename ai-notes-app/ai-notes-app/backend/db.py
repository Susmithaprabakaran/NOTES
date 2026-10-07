# ============================================================
# AI Notes — Day 8 — database helper
# Thin wrapper around psycopg2: one connection per request
# (opened/closed by Flask's app context), plus a helper to
# apply schema.sql on startup so a fresh database "just works".
# ============================================================
import os
import psycopg2
import psycopg2.extras
from flask import g


def get_db():
    """Return a request-scoped psycopg2 connection, creating one if needed."""
    if "db" not in g:
        g.db = psycopg2.connect(
            os.environ["DATABASE_URL"],
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_app(app):
    app.teardown_appcontext(close_db)


def init_db(database_url):
    """Apply schema.sql. Safe to call repeatedly (uses IF NOT EXISTS)."""
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path, "r") as f:
        schema_sql = f.read()
    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(schema_sql)
        conn.commit()
    finally:
        conn.close()
