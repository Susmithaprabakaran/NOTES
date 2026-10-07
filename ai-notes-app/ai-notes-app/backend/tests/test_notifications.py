from conftest import register
from db import get_db


def create_note(client, title="", text="", priority="none"):
    res = client.post("/api/notes", json={"title": title, "text": text, "priority": priority})
    return res.get_json()


def create_task(client, note_id, text="Do the thing", due=None):
    res = client.post("/api/tasks", json={"noteId": note_id, "text": text, "due": due})
    return res.get_json()


def backdate(app, table, row_id, column, days):
    with app.app_context():
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                f"UPDATE {table} SET {column} = now() - (%s || ' days')::interval WHERE id = %s",
                (days, row_id),
            )
        db.commit()


# ============================================================
# Auth
# ============================================================
def test_generate_requires_login(client):
    res = client.post("/api/notifications/generate")
    assert res.status_code == 401


def test_list_requires_login(client):
    res = client.get("/api/notifications")
    assert res.status_code == 401


# ============================================================
# Rule engine — works with zero AI setup
# ============================================================
def test_generate_works_without_any_ai_provider_configured(client, monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    register(client)
    res = client.post("/api/notifications/generate")
    assert res.status_code == 200
    body = res.get_json()
    assert "notifications" in body
    assert "unreadCount" in body


def test_task_due_soon_detected_by_keyword(client, monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    register(client)
    note = create_note(client, text="stuff")
    create_task(client, note["id"], text="Email Sarah", due="Tomorrow")

    res = client.post("/api/notifications/generate")
    body = res.get_json()
    types = [n["type"] for n in body["notifications"]]
    assert "task_due" in types
    due_note = next(n for n in body["notifications"] if n["type"] == "task_due")
    assert "Email Sarah" in due_note["message"]


def test_task_without_due_soon_wording_is_not_flagged(client, monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    register(client)
    note = create_note(client, text="stuff")
    create_task(client, note["id"], text="Someday task", due="next quarter")

    res = client.post("/api/notifications/generate")
    types = [n["type"] for n in res.get_json()["notifications"]]
    assert "task_due" not in types


def test_stale_open_tasks_are_detected(client, monkeypatch, app):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    register(client)
    note = create_note(client, text="stuff")
    task = create_task(client, note["id"], text="Old task")
    backdate(app, "tasks", task["id"], "created_at", 10)

    res = client.post("/api/notifications/generate")
    body = res.get_json()
    stale = [n for n in body["notifications"] if n["type"] == "task_stale"]
    assert len(stale) == 1
    assert "1 open task" in stale[0]["message"]


def test_high_priority_stale_note_is_detected(client, monkeypatch, app):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    register(client)
    note = create_note(client, title="Thesis outline", text="stuff", priority="high")
    backdate(app, "notes", note["id"], "updated_at", 10)

    res = client.post("/api/notifications/generate")
    body = res.get_json()
    stale_notes = [n for n in body["notifications"] if n["type"] == "note_stale"]
    assert len(stale_notes) == 1
    assert "Thesis outline" in stale_notes[0]["message"]
    assert stale_notes[0]["noteId"] == note["id"]


def test_low_priority_stale_note_is_not_flagged(client, monkeypatch, app):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    register(client)
    note = create_note(client, title="Random note", text="stuff", priority="low")
    backdate(app, "notes", note["id"], "updated_at", 10)

    res = client.post("/api/notifications/generate")
    types = [n["type"] for n in res.get_json()["notifications"]]
    assert "note_stale" not in types


def test_generate_is_deduped_per_day(client, monkeypatch, app):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    register(client)
    note = create_note(client, text="stuff")
    task = create_task(client, note["id"], text="Old task")
    backdate(app, "tasks", task["id"], "created_at", 10)

    first = client.post("/api/notifications/generate").get_json()
    second = client.post("/api/notifications/generate").get_json()

    assert first["added"] >= 1
    assert second["added"] == 0
    stale_rows = [n for n in second["notifications"] if n["type"] == "task_stale"]
    assert len(stale_rows) == 1  # not duplicated


def test_task_reminders_pref_off_hides_task_notifications(client, monkeypatch, app):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    register(client)
    client.patch("/api/profile", json={"notificationPrefs": {"taskReminders": False}})
    note = create_note(client, text="stuff")
    task = create_task(client, note["id"], text="Old task")
    backdate(app, "tasks", task["id"], "created_at", 10)

    res = client.post("/api/notifications/generate")
    types = [n["type"] for n in res.get_json()["notifications"]]
    assert "task_stale" not in types
    assert "task_due" not in types


# ============================================================
# Digest layer — AI-phrased when available, templated when not
# ============================================================
def test_digest_skipped_when_ai_updates_pref_is_off(client, monkeypatch, app):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    register(client)  # aiUpdates defaults to False
    note = create_note(client, text="stuff")
    task = create_task(client, note["id"], text="Old task")
    backdate(app, "tasks", task["id"], "created_at", 10)

    res = client.post("/api/notifications/generate")
    types = [n["type"] for n in res.get_json()["notifications"]]
    assert "digest" not in types


def test_digest_uses_template_when_no_ai_provider_configured(client, monkeypatch, app):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    register(client)
    client.patch("/api/profile", json={"notificationPrefs": {"aiUpdates": True}})
    note = create_note(client, text="stuff")
    task = create_task(client, note["id"], text="Old task")
    backdate(app, "tasks", task["id"], "created_at", 10)

    res = client.post("/api/notifications/generate")
    body = res.get_json()
    digest = next(n for n in body["notifications"] if n["type"] == "digest")
    assert digest["aiGenerated"] is False
    assert "Today:" in digest["message"]


def test_digest_uses_mocked_ai_when_provider_configured(client, monkeypatch, app):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    client.patch("/api/profile", json={"notificationPrefs": {"aiUpdates": True}})
    note = create_note(client, text="stuff")
    task = create_task(client, note["id"], text="Old task")
    backdate(app, "tasks", task["id"], "created_at", 10)

    import notifications

    monkeypatch.setattr(notifications, "call_ai", lambda prompt, system=None: "You've got a few things piling up!")

    res = client.post("/api/notifications/generate")
    body = res.get_json()
    digest = next(n for n in body["notifications"] if n["type"] == "digest")
    assert digest["aiGenerated"] is True
    assert digest["message"] == "You've got a few things piling up!"


def test_digest_falls_back_to_template_when_ai_call_fails(client, monkeypatch, app):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    client.patch("/api/profile", json={"notificationPrefs": {"aiUpdates": True}})
    note = create_note(client, text="stuff")
    task = create_task(client, note["id"], text="Old task")
    backdate(app, "tasks", task["id"], "created_at", 10)

    import notifications
    from ai_client import AIUnavailable

    def broken_call_ai(prompt, system=None):
        raise AIUnavailable("OpenAI rejected the API key (401).")

    monkeypatch.setattr(notifications, "call_ai", broken_call_ai)

    res = client.post("/api/notifications/generate")
    body = res.get_json()
    digest = next(n for n in body["notifications"] if n["type"] == "digest")
    assert digest["aiGenerated"] is False
    assert "Today:" in digest["message"]


# ============================================================
# Listing / read state / delete
# ============================================================
def test_list_reports_unread_count(client, monkeypatch, app):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    register(client)
    note = create_note(client, text="stuff")
    task = create_task(client, note["id"], text="Old task")
    backdate(app, "tasks", task["id"], "created_at", 10)
    client.post("/api/notifications/generate")

    res = client.get("/api/notifications")
    body = res.get_json()
    assert body["unreadCount"] == len(body["notifications"])
    assert body["unreadCount"] >= 1


def test_mark_read_and_unread_filter(client, monkeypatch, app):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    register(client)
    note = create_note(client, text="stuff")
    task = create_task(client, note["id"], text="Old task")
    backdate(app, "tasks", task["id"], "created_at", 10)
    gen = client.post("/api/notifications/generate").get_json()
    notif_id = gen["notifications"][0]["id"]

    res = client.patch(f"/api/notifications/{notif_id}", json={"read": True})
    assert res.status_code == 200
    assert res.get_json()["read"] is True

    unread = client.get("/api/notifications?unread=1").get_json()
    assert notif_id not in [n["id"] for n in unread["notifications"]]


def test_mark_read_404_for_missing_or_other_users_notification(client):
    register(client)
    res = client.patch("/api/notifications/999", json={"read": True})
    assert res.status_code == 404


def test_mark_read_requires_read_field(client):
    register(client)
    res = client.patch("/api/notifications/1", json={})
    assert res.status_code == 400


def test_read_all_marks_everything_read(client, monkeypatch, app):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    register(client)
    note = create_note(client, text="stuff")
    task = create_task(client, note["id"], text="Old task")
    backdate(app, "tasks", task["id"], "created_at", 10)
    client.post("/api/notifications/generate")

    res = client.post("/api/notifications/read-all")
    assert res.status_code == 200
    assert res.get_json()["updated"] >= 1

    listed = client.get("/api/notifications").get_json()
    assert listed["unreadCount"] == 0


def test_delete_notification(client, monkeypatch, app):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    register(client)
    note = create_note(client, text="stuff")
    task = create_task(client, note["id"], text="Old task")
    backdate(app, "tasks", task["id"], "created_at", 10)
    gen = client.post("/api/notifications/generate").get_json()
    notif_id = gen["notifications"][0]["id"]

    res = client.delete(f"/api/notifications/{notif_id}")
    assert res.status_code == 200

    listed = client.get("/api/notifications").get_json()
    assert notif_id not in [n["id"] for n in listed["notifications"]]


def test_delete_404_for_missing_notification(client):
    register(client)
    res = client.delete("/api/notifications/999")
    assert res.status_code == 404


def test_notification_survives_note_deletion(client, monkeypatch, app):
    # note_id is ON DELETE SET NULL, not CASCADE — the notification stays
    # in the feed even after the note that triggered it is gone.
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    register(client)
    note = create_note(client, title="Thesis outline", text="stuff", priority="high")
    backdate(app, "notes", note["id"], "updated_at", 10)
    client.post("/api/notifications/generate")

    client.delete(f"/api/notes/{note['id']}")

    listed = client.get("/api/notifications").get_json()
    stale_notes = [n for n in listed["notifications"] if n["type"] == "note_stale"]
    assert len(stale_notes) == 1
    assert stale_notes[0]["noteId"] is None
