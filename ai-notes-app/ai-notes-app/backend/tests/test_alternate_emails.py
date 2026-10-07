# ============================================================
# Alternate (backup) emails on the profile — PATCH /api/profile with
# "alternateEmails": [{"email", "label"}], max 3.
# ============================================================
from conftest import register


def patch(client, **body):
    return client.patch("/api/profile", json=body)


def test_new_account_has_no_alternate_emails(client):
    register(client)
    body = client.get("/api/profile").get_json()
    assert body["alternateEmails"] == []


def test_set_alternate_emails_with_labels(client):
    register(client)
    res = patch(
        client,
        alternateEmails=[
            {"email": "me@work.com", "label": "Work"},
            {"email": "me@school.edu", "label": ""},
        ],
    )
    assert res.status_code == 200
    assert res.get_json()["alternateEmails"] == [
        {"email": "me@work.com", "label": "Work"},
        {"email": "me@school.edu", "label": ""},
    ]
    # persisted, not just echoed
    assert client.get("/api/profile").get_json()["alternateEmails"][0]["email"] == "me@work.com"


def test_bare_strings_are_accepted(client):
    register(client)
    res = patch(client, alternateEmails=["backup@example.com"])
    assert res.status_code == 200
    assert res.get_json()["alternateEmails"] == [{"email": "backup@example.com", "label": ""}]


def test_blank_rows_are_skipped_not_rejected(client):
    register(client)
    res = patch(client, alternateEmails=[{"email": "", "label": "Work"}, {"email": "  ", "label": ""}, {"email": "a@b.co", "label": ""}])
    assert res.status_code == 200
    assert [a["email"] for a in res.get_json()["alternateEmails"]] == ["a@b.co"]


def test_empty_list_clears_alternate_emails(client):
    register(client)
    patch(client, alternateEmails=[{"email": "a@b.co", "label": ""}])
    res = patch(client, alternateEmails=[])
    assert res.status_code == 200
    assert res.get_json()["alternateEmails"] == []


def test_other_updates_leave_alternate_emails_alone(client):
    register(client)
    patch(client, alternateEmails=[{"email": "a@b.co", "label": "Work"}])
    res = patch(client, fullName="Alice Example")
    assert res.status_code == 200
    assert res.get_json()["alternateEmails"] == [{"email": "a@b.co", "label": "Work"}]


def test_invalid_alternate_email_rejected(client):
    register(client)
    res = patch(client, alternateEmails=[{"email": "not-an-email", "label": ""}])
    assert res.status_code == 400
    assert "valid email" in res.get_json()["error"]


def test_more_than_three_rejected(client):
    register(client)
    emails = [{"email": f"a{i}@example.com", "label": ""} for i in range(4)]
    res = patch(client, alternateEmails=emails)
    assert res.status_code == 400
    assert "up to 3" in res.get_json()["error"]


def test_exactly_three_allowed(client):
    register(client)
    emails = [{"email": f"a{i}@example.com", "label": ""} for i in range(3)]
    assert patch(client, alternateEmails=emails).status_code == 200


def test_duplicates_within_list_rejected_case_insensitively(client):
    register(client)
    res = patch(client, alternateEmails=[{"email": "A@b.co", "label": ""}, {"email": "a@B.co", "label": ""}])
    assert res.status_code == 400
    assert "more than once" in res.get_json()["error"]


def test_label_too_long_rejected(client):
    register(client)
    res = patch(client, alternateEmails=[{"email": "a@b.co", "label": "x" * 31}])
    assert res.status_code == 400


def test_not_a_list_rejected(client):
    register(client)
    assert patch(client, alternateEmails="a@b.co").status_code == 400
    assert patch(client, alternateEmails={"email": "a@b.co"}).status_code == 400
    assert patch(client, alternateEmails=[123]).status_code == 400


def test_alternate_cannot_equal_primary(client):
    register(client)
    patch(client, email="main@example.com")
    res = patch(client, alternateEmails=[{"email": "MAIN@example.com", "label": ""}])
    assert res.status_code == 400
    assert "primary" in res.get_json()["error"]


def test_alternate_and_primary_in_same_request_cannot_match(client):
    register(client)
    res = patch(client, email="x@example.com", alternateEmails=[{"email": "x@example.com", "label": ""}])
    assert res.status_code == 400


def test_changing_primary_to_an_existing_alternate_is_rejected(client):
    register(client)
    patch(client, email="main@example.com", alternateEmails=[{"email": "alt@example.com", "label": ""}])
    res = patch(client, email="alt@example.com")
    assert res.status_code == 400
    assert "primary" in res.get_json()["error"]


def test_swap_primary_with_alternate_in_one_request(client):
    """What the UI's 'Make primary' button sends."""
    register(client)
    patch(client, email="main@example.com", alternateEmails=[{"email": "alt@example.com", "label": "Work"}])
    res = patch(
        client,
        email="alt@example.com",
        alternateEmails=[{"email": "main@example.com", "label": "Former primary"}],
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["email"] == "alt@example.com"
    assert body["alternateEmails"] == [{"email": "main@example.com", "label": "Former primary"}]


def test_alternate_emails_are_per_account(client, app):
    register(client, "alice")
    patch(client, alternateEmails=[{"email": "alice.alt@example.com", "label": ""}])
    other = app.test_client()
    register(other, "bob")
    assert other.get("/api/profile").get_json()["alternateEmails"] == []


def test_alternate_emails_require_login(client):
    assert client.patch("/api/profile", json={"alternateEmails": []}).status_code == 401


def test_corrupt_stored_value_does_not_break_profile(client, app):
    from db import get_db

    register(client)
    with app.app_context():
        db = get_db()
        with db.cursor() as cur:
            cur.execute("UPDATE users SET alternate_emails = %s", ("{not json",))
        db.commit()
    res = client.get("/api/profile")
    assert res.status_code == 200
    assert res.get_json()["alternateEmails"] == []
