# ============================================================
# Shared pytest fixtures.
#
# Tests run against a SEPARATE database (ainotes_day8_test) so
# they never touch your real notes. Each test gets the schema
# freshly re-created and truncated before it runs.
# ============================================================
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from app import create_app
from db import init_db, get_db

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://ainotes:ainotes_dev_pw@localhost:5432/ainotes_day8_test",
)


@pytest.fixture()
def app():
    init_db(TEST_DATABASE_URL)
    flask_app = create_app(
        test_config={
            "DATABASE_URL": TEST_DATABASE_URL,
            "TESTING": True,
            "SECRET_KEY": "test-secret",
        }
    )

    with flask_app.app_context():
        db = get_db()
        with db.cursor() as cur:
            cur.execute("TRUNCATE tasks, notes, users RESTART IDENTITY CASCADE")
        db.commit()

    yield flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, username="alice", password="correcthorsebattery"):
    return client.post(
        "/api/auth/register", json={"username": username, "password": password}
    )


def login(client, username="alice", password="correcthorsebattery"):
    return client.post("/api/auth/login", json={"username": username, "password": password})
