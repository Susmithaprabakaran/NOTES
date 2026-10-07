# ============================================================
# AI Notes — Day 8 — entry point
# Loads .env, ensures the database schema exists, then starts
# the dev server.
# ============================================================
import os
from pathlib import Path

from dotenv import load_dotenv

root_dir = Path(__file__).resolve().parent
load_dotenv(root_dir / ".env")

from app import create_app  # noqa: E402  (must come after load_dotenv)
from db import init_db  # noqa: E402

if __name__ == "__main__":
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit(
            "No DATABASE_URL set. Create backend/.env (see .env.example) first."
        )

    print("Applying database schema (safe if already applied)...")
    init_db(database_url)

    app = create_app()
    port = int(os.environ.get("PORT", 5000))
    print(f"AI Notes (Day 9) listening on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
