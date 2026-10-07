import io

import pytest

from conftest import register

FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
FAKE_JPEG = b"\xff\xd8\xff" + b"0" * 32
FAKE_WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"0" * 16
FAKE_EXE = b"MZ\x90\x00\x03\x00\x00\x00not a real image"


@pytest.fixture(autouse=True)
def isolated_avatar_dir(tmp_path, monkeypatch):
    """Every test gets its own throwaway avatar directory instead of
    writing into backend/uploads — mirrors Day 21's attachments test
    fixture (isolated_upload_folder)."""
    import profile as profile_module

    folder = tmp_path / "avatars"
    folder.mkdir()
    monkeypatch.setattr(profile_module, "avatar_dir", lambda: str(folder))
    return str(folder)


def upload_avatar(client, content, filename, field="file"):
    data = {field: (io.BytesIO(content), filename)}
    return client.post("/api/profile/avatar", data=data, content_type="multipart/form-data")


# ---------- GET /api/profile ----------

def test_get_profile_requires_auth(client):
    res = client.get("/api/profile")
    assert res.status_code == 401


def test_get_profile_returns_defaults_for_new_account(client):
    register(client)
    res = client.get("/api/profile")
    assert res.status_code == 200
    body = res.get_json()
    assert body["username"] == "alice"
    assert body["fullName"] == ""
    assert body["email"] == ""
    assert body["avatarUrl"] is None
    assert body["themePreference"] == "dark"
    assert body["notificationPrefs"] == {
        "emailDigest": True,
        "taskReminders": True,
        "aiUpdates": False,
    }
    assert "createdAt" in body


# ---------- PATCH /api/profile ----------

def test_update_profile_text_fields(client):
    register(client)
    res = client.patch(
        "/api/profile",
        json={
            "fullName": "Alice Anderson",
            "email": "alice@example.com",
            "phone": "+1 555-123-4567",
            "role": "Product Manager",
            "bio": "Coffee, notes, and side projects.",
        },
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["fullName"] == "Alice Anderson"
    assert body["email"] == "alice@example.com"
    assert body["phone"] == "+1 555-123-4567"
    assert body["role"] == "Product Manager"
    assert body["bio"] == "Coffee, notes, and side projects."

    # persisted, not just echoed back
    again = client.get("/api/profile").get_json()
    assert again["fullName"] == "Alice Anderson"


def test_update_profile_is_partial(client):
    register(client)
    client.patch("/api/profile", json={"fullName": "Alice Anderson"})
    res = client.patch("/api/profile", json={"role": "Engineer"})
    body = res.get_json()
    assert body["fullName"] == "Alice Anderson"  # untouched by the second PATCH
    assert body["role"] == "Engineer"


def test_update_profile_rejects_invalid_email(client):
    register(client)
    res = client.patch("/api/profile", json={"email": "not-an-email"})
    assert res.status_code == 400


def test_update_profile_rejects_invalid_phone(client):
    register(client)
    res = client.patch("/api/profile", json={"phone": "abc"})
    assert res.status_code == 400


def test_update_profile_rejects_too_long_bio(client):
    register(client)
    res = client.patch("/api/profile", json={"bio": "x" * 501})
    assert res.status_code == 400


def test_update_profile_rejects_duplicate_email_across_accounts(client):
    register(client, username="alice")
    client.patch("/api/profile", json={"email": "shared@example.com"})
    client.post("/api/auth/logout")

    register(client, username="bob")
    res = client.patch("/api/profile", json={"email": "SHARED@example.com"})
    assert res.status_code == 409


def test_update_profile_allows_resaving_own_email(client):
    register(client)
    client.patch("/api/profile", json={"email": "alice@example.com"})
    res = client.patch("/api/profile", json={"email": "alice@example.com", "role": "Engineer"})
    assert res.status_code == 200


def test_update_profile_clears_email_with_empty_string(client):
    register(client)
    client.patch("/api/profile", json={"email": "alice@example.com"})
    res = client.patch("/api/profile", json={"email": ""})
    assert res.status_code == 200
    assert res.get_json()["email"] == ""


def test_update_theme_preference(client):
    register(client)
    res = client.patch("/api/profile", json={"themePreference": "light"})
    assert res.status_code == 200
    assert res.get_json()["themePreference"] == "light"


def test_update_theme_preference_rejects_unknown_value(client):
    register(client)
    res = client.patch("/api/profile", json={"themePreference": "solarized"})
    assert res.status_code == 400


def test_update_notification_prefs_merges_not_replaces(client):
    register(client)
    res = client.patch("/api/profile", json={"notificationPrefs": {"aiUpdates": True}})
    assert res.status_code == 200
    prefs = res.get_json()["notificationPrefs"]
    assert prefs["aiUpdates"] is True
    assert prefs["emailDigest"] is True  # untouched default, not dropped
    assert prefs["taskReminders"] is True


def test_update_profile_requires_auth(client):
    res = client.patch("/api/profile", json={"fullName": "x"})
    assert res.status_code == 401


# ---------- POST /api/profile/password ----------

def test_change_password_success_and_relogin(client):
    register(client, username="alice", password="correcthorsebattery")
    res = client.post(
        "/api/profile/password",
        json={"currentPassword": "correcthorsebattery", "newPassword": "newpassword123"},
    )
    assert res.status_code == 200

    client.post("/api/auth/logout")
    old_login = client.post(
        "/api/auth/login", json={"username": "alice", "password": "correcthorsebattery"}
    )
    assert old_login.status_code == 401

    new_login = client.post(
        "/api/auth/login", json={"username": "alice", "password": "newpassword123"}
    )
    assert new_login.status_code == 200


def test_change_password_rejects_wrong_current_password(client):
    register(client, password="correcthorsebattery")
    res = client.post(
        "/api/profile/password",
        json={"currentPassword": "wrongpassword", "newPassword": "newpassword123"},
    )
    assert res.status_code == 401


def test_change_password_rejects_too_short_new_password(client):
    register(client, password="correcthorsebattery")
    res = client.post(
        "/api/profile/password",
        json={"currentPassword": "correcthorsebattery", "newPassword": "short"},
    )
    assert res.status_code == 400


def test_change_password_rejects_same_password(client):
    register(client, password="correcthorsebattery")
    res = client.post(
        "/api/profile/password",
        json={"currentPassword": "correcthorsebattery", "newPassword": "correcthorsebattery"},
    )
    assert res.status_code == 400


def test_change_password_requires_auth(client):
    res = client.post(
        "/api/profile/password", json={"currentPassword": "a", "newPassword": "newpassword123"}
    )
    assert res.status_code == 401


# ---------- avatar upload/delete ----------

def test_upload_avatar_success(client):
    register(client)
    res = upload_avatar(client, FAKE_PNG, "me.png")
    assert res.status_code == 200
    url = res.get_json()["avatarUrl"]
    assert url.startswith("/api/profile/avatar/")

    profile = client.get("/api/profile").get_json()
    assert profile["avatarUrl"] == url

    fetched = client.get(url)
    assert fetched.status_code == 200


def test_upload_avatar_rejects_disallowed_extension(client):
    register(client)
    res = upload_avatar(client, FAKE_EXE, "me.exe")
    assert res.status_code == 400


def test_upload_avatar_rejects_fake_extension_via_magic_bytes(client):
    register(client)
    res = upload_avatar(client, FAKE_EXE, "me.png")
    assert res.status_code == 400


def test_upload_avatar_accepts_jpeg_and_webp(client):
    register(client)
    assert upload_avatar(client, FAKE_JPEG, "me.jpg").status_code == 200
    assert upload_avatar(client, FAKE_WEBP, "me.webp").status_code == 200


def test_reuploading_avatar_replaces_old_one(client, tmp_path):
    register(client)
    import os

    first = upload_avatar(client, FAKE_PNG, "one.png").get_json()["avatarUrl"]
    first_filename = first.rsplit("/", 1)[-1]

    second = upload_avatar(client, FAKE_JPEG, "two.jpg").get_json()["avatarUrl"]
    assert second != first

    profile = client.get("/api/profile").get_json()
    assert profile["avatarUrl"] == second

    # old file was actually cleaned up off disk, not just unlinked in the DB
    import profile as profile_module

    assert not os.path.exists(os.path.join(profile_module.avatar_dir(), first_filename))


def test_upload_avatar_requires_auth(client):
    res = upload_avatar(client, FAKE_PNG, "me.png")
    assert res.status_code == 401


def test_delete_avatar(client):
    register(client)
    upload_avatar(client, FAKE_PNG, "me.png")
    res = client.delete("/api/profile/avatar")
    assert res.status_code == 200
    profile = client.get("/api/profile").get_json()
    assert profile["avatarUrl"] is None


# ---------- DELETE /api/profile (delete account) ----------

def test_delete_account_requires_correct_password(client):
    register(client, password="correcthorsebattery")
    res = client.delete("/api/profile", json={"password": "wrongpassword"})
    assert res.status_code == 401

    still_there = client.get("/api/profile")
    assert still_there.status_code == 200


def test_delete_account_requires_password_field(client):
    register(client)
    res = client.delete("/api/profile", json={})
    assert res.status_code == 400


def test_delete_account_success_logs_out_and_removes_data(client):
    register(client, username="alice", password="correcthorsebattery")
    client.post("/api/notes", json={"title": "keepsake", "text": "hello world"})

    res = client.delete("/api/profile", json={"password": "correcthorsebattery"})
    assert res.status_code == 200

    # session was cleared server-side
    me = client.get("/api/auth/me")
    assert me.status_code == 401

    # the account is really gone — re-registering the same username works
    again = register(client, username="alice", password="correcthorsebattery")
    assert again.status_code == 201

    # and the new account starts fresh, with none of the old notes
    notes = client.get("/api/notes").get_json()
    assert notes == []


def test_delete_account_requires_auth(client):
    res = client.delete("/api/profile", json={"password": "whatever"})
    assert res.status_code == 401
