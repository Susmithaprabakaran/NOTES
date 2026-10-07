from conftest import register, login


def test_register_creates_user_and_logs_in(client):
    res = register(client)
    assert res.status_code == 201
    assert res.get_json()["username"] == "alice"

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.get_json()["username"] == "alice"


def test_register_rejects_duplicate_username(client):
    register(client)
    res = register(client)
    assert res.status_code == 409


def test_register_rejects_short_password(client):
    res = client.post("/api/auth/register", json={"username": "bob", "password": "short"})
    assert res.status_code == 400


def test_register_rejects_invalid_username_characters(client):
    res = client.post(
        "/api/auth/register", json={"username": "bad name!", "password": "correcthorsebattery"}
    )
    assert res.status_code == 400


def test_login_with_correct_credentials(client):
    register(client)
    client.post("/api/auth/logout")
    res = login(client)
    assert res.status_code == 200
    assert res.get_json()["username"] == "alice"


def test_login_with_wrong_password_is_rejected(client):
    register(client)
    client.post("/api/auth/logout")
    res = login(client, password="totally-wrong-password")
    assert res.status_code == 401


def test_login_with_unknown_username_is_rejected(client):
    res = login(client, username="ghost")
    assert res.status_code == 401


def test_repeated_failed_logins_are_rate_limited(client):
    """Suspicious-activity guard: too many wrong-password attempts in a
    short window blocks further attempts (429) instead of letting a
    brute-forcer keep guessing indefinitely."""
    import security

    register(client)
    client.post("/api/auth/logout")

    last = None
    for _ in range(security.MAX_FAILURES_PER_USERNAME + 1):
        last = login(client, password="definitely-wrong")
    assert last.status_code == 429
    assert "error" in last.get_json()


def test_rate_limit_does_not_block_a_different_username(client):
    """The per-username bucket shouldn't punish an unrelated account
    just because someone else's login is being guessed at."""
    import security

    register(client, username="alice")
    register(client, username="bob", password="correcthorsebattery")
    client.post("/api/auth/logout")

    for _ in range(security.MAX_FAILURES_PER_USERNAME + 1):
        login(client, username="alice", password="definitely-wrong")

    res = login(client, username="bob", password="correcthorsebattery")
    assert res.status_code == 200


def test_successful_login_reports_suspicious_activity_after_failures(client):
    """A successful login right after a burst of failed attempts on the
    same account surfaces a warning in the response, so the account
    owner can tell someone else may have been guessing."""
    import security

    register(client)
    client.post("/api/auth/logout")

    for _ in range(security.SUSPICIOUS_FAILURE_THRESHOLD):
        login(client, password="definitely-wrong")

    res = login(client)
    assert res.status_code == 200
    assert "suspiciousActivity" in res.get_json()


def test_me_requires_login(client):
    res = client.get("/api/auth/me")
    assert res.status_code == 401


def test_logout_ends_the_session(client):
    register(client)
    assert client.get("/api/auth/me").status_code == 200

    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_passwords_are_hashed_not_stored_in_plaintext(client, app):
    register(client, username="carol", password="correcthorsebattery")
    with app.app_context():
        from db import get_db

        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT password_hash FROM users WHERE username = %s", ("carol",))
            row = cur.fetchone()
    assert row["password_hash"] != "correcthorsebattery"
    # werkzeug's default hashing method (scrypt in recent versions, pbkdf2
    # in older ones) — either is fine, the point is it's not plaintext.
    assert row["password_hash"].startswith(("pbkdf2:", "scrypt:"))
