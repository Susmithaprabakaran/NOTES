from conftest import register

NOTE_TEXT = (
    "Standup notes: talked through the Q3 roadmap. Need to email Sarah the "
    "budget draft by Friday. Also should follow up with design about the new "
    "icons, and remember to book the conference room for Monday's review."
)


def create_note(client, title="", text=""):
    res = client.post("/api/notes", json={"title": title, "text": text})
    return res.get_json()


def test_extract_tasks_requires_login(client):
    res = client.post("/api/notes/1/extract-tasks")
    assert res.status_code == 401


def test_extract_tasks_404_for_missing_note(client):
    register(client)
    res = client.post("/api/notes/999/extract-tasks")
    assert res.status_code == 404


def test_extract_tasks_rejects_empty_note(client):
    register(client)
    note = create_note(client, text="")
    res = client.post(f"/api/notes/{note['id']}/extract-tasks")
    assert res.status_code == 400


def test_extract_tasks_returns_501_without_provider(client, monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    register(client)
    note = create_note(client, text=NOTE_TEXT)

    res = client.post(f"/api/notes/{note['id']}/extract-tasks")
    assert res.status_code == 501
    assert "error" in res.get_json()


def test_extract_tasks_success_with_mocked_ai(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=NOTE_TEXT)

    import tasks

    monkeypatch.setattr(
        tasks,
        "call_ai",
        lambda prompt, system=None: '["Email Sarah the budget draft", "Follow up with design about icons", "Book the conference room for Monday"]',
    )

    res = client.post(f"/api/notes/{note['id']}/extract-tasks")
    assert res.status_code == 200
    body = res.get_json()
    assert body["added"] == 3
    assert body["found"] == 3
    assert len(body["tasks"]) == 3
    assert all(t["done"] is False for t in body["tasks"])
    assert all(t["noteId"] == note["id"] for t in body["tasks"])


def test_extract_tasks_tolerates_markdown_fenced_json(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=NOTE_TEXT)

    import tasks

    monkeypatch.setattr(
        tasks, "call_ai", lambda prompt, system=None: '```json\n["Call the plumber"]\n```'
    )

    res = client.post(f"/api/notes/{note['id']}/extract-tasks")
    assert res.status_code == 200
    body = res.get_json()
    assert body["tasks"][0]["text"] == "Call the plumber"


def test_extract_tasks_handles_empty_array(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text="Just some thoughts, nothing actionable here.")

    import tasks

    monkeypatch.setattr(tasks, "call_ai", lambda prompt, system=None: "[]")

    res = client.post(f"/api/notes/{note['id']}/extract-tasks")
    assert res.status_code == 200
    body = res.get_json()
    assert body["tasks"] == []
    assert body["found"] == 0


def test_extract_tasks_dedupes_on_repeat_run(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=NOTE_TEXT)

    import tasks

    monkeypatch.setattr(tasks, "call_ai", lambda prompt, system=None: '["Email Sarah the budget draft"]')

    first = client.post(f"/api/notes/{note['id']}/extract-tasks").get_json()
    assert first["added"] == 1

    second = client.post(f"/api/notes/{note['id']}/extract-tasks").get_json()
    assert second["added"] == 0
    assert len(second["tasks"]) == 1  # no duplicate row


def test_manual_task_create_toggle_delete(client):
    register(client)
    note = create_note(client, text=NOTE_TEXT)

    created = client.post(
        "/api/tasks", json={"noteId": note["id"], "text": "Manually added task"}
    ).get_json()
    assert created["done"] is False

    toggled = client.patch(f"/api/tasks/{created['id']}", json={"done": True}).get_json()
    assert toggled["done"] is True

    deleted = client.delete(f"/api/tasks/{created['id']}")
    assert deleted.status_code == 200

    missing = client.patch(f"/api/tasks/{created['id']}", json={"done": False})
    assert missing.status_code == 404


def test_all_tasks_view_spans_notes_and_filters_by_done(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note_a = create_note(client, title="Work note", text=NOTE_TEXT)
    note_b = create_note(client, title="Other note", text=NOTE_TEXT)

    import tasks

    monkeypatch.setattr(tasks, "call_ai", lambda prompt, system=None: '["Do thing A"]')
    client.post(f"/api/notes/{note_a['id']}/extract-tasks")
    monkeypatch.setattr(tasks, "call_ai", lambda prompt, system=None: '["Do thing B"]')
    client.post(f"/api/notes/{note_b['id']}/extract-tasks")

    all_tasks = client.get("/api/tasks").get_json()
    assert len(all_tasks) == 2
    assert {t["noteTitle"] for t in all_tasks} == {"Work note", "Other note"}

    done_task_id = next(t["id"] for t in all_tasks if t["text"] == "Do thing A")
    client.patch(f"/api/tasks/{done_task_id}", json={"done": True})

    open_only = client.get("/api/tasks?done=0").get_json()
    assert len(open_only) == 1
    assert open_only[0]["text"] == "Do thing B"


# ============================================================
# Day 34 — searching the all-notes Tasks view (`?q=`)
# ============================================================
def test_tasks_search_matches_task_text(client):
    register(client)
    note = create_note(client, title="Random note", text=NOTE_TEXT)
    client.post("/api/tasks", json={"noteId": note["id"], "text": "Email Sarah the budget draft"})
    client.post("/api/tasks", json={"noteId": note["id"], "text": "Book the conference room"})

    res = client.get("/api/tasks?q=budget").get_json()
    assert len(res) == 1
    assert res[0]["text"] == "Email Sarah the budget draft"


def test_tasks_search_matches_note_title(client):
    register(client)
    note_a = create_note(client, title="Q3 planning", text=NOTE_TEXT)
    note_b = create_note(client, title="Grocery list", text=NOTE_TEXT)
    client.post("/api/tasks", json={"noteId": note_a["id"], "text": "Do a thing"})
    client.post("/api/tasks", json={"noteId": note_b["id"], "text": "Do another thing"})

    res = client.get("/api/tasks?q=planning").get_json()
    assert len(res) == 1
    assert res[0]["noteTitle"] == "Q3 planning"


def test_tasks_search_is_case_insensitive_and_trims_whitespace(client):
    register(client)
    note = create_note(client, title="Notes", text=NOTE_TEXT)
    client.post("/api/tasks", json={"noteId": note["id"], "text": "Renew the passport"})

    res = client.get("/api/tasks?q=  PASSPORT  ").get_json()
    assert len(res) == 1


def test_tasks_search_combines_with_done_filter(client):
    register(client)
    note = create_note(client, title="Notes", text=NOTE_TEXT)
    a = client.post("/api/tasks", json={"noteId": note["id"], "text": "Buy milk"}).get_json()
    client.post("/api/tasks", json={"noteId": note["id"], "text": "Buy eggs"})
    client.patch(f"/api/tasks/{a['id']}", json={"done": True})

    res = client.get("/api/tasks?q=buy&done=0").get_json()
    assert len(res) == 1
    assert res[0]["text"] == "Buy eggs"


def test_tasks_search_with_no_matches_returns_empty_list(client):
    register(client)
    note = create_note(client, title="Notes", text=NOTE_TEXT)
    client.post("/api/tasks", json={"noteId": note["id"], "text": "Buy milk"})

    res = client.get("/api/tasks?q=nonexistentxyz")
    assert res.status_code == 200
    assert res.get_json() == []


def test_tasks_search_blank_query_returns_everything(client):
    register(client)
    note = create_note(client, title="Notes", text=NOTE_TEXT)
    client.post("/api/tasks", json={"noteId": note["id"], "text": "Buy milk"})
    client.post("/api/tasks", json={"noteId": note["id"], "text": "Buy eggs"})

    res = client.get("/api/tasks?q=   ").get_json()
    assert len(res) == 2


def test_tasks_search_scoped_per_user(client):
    register(client, username="alice")
    note = create_note(client, text=NOTE_TEXT)
    client.post("/api/tasks", json={"noteId": note["id"], "text": "Alice's secret errand"})
    client.post("/api/auth/logout")

    register(client, username="bob")
    note_b = create_note(client, text=NOTE_TEXT)
    client.post("/api/tasks", json={"noteId": note_b["id"], "text": "Bob's own errand"})

    res = client.get("/api/tasks?q=errand").get_json()
    assert len(res) == 1
    assert res[0]["text"] == "Bob's own errand"


def test_tasks_are_scoped_per_user(client):
    register(client, username="alice")
    note = create_note(client, text=NOTE_TEXT)
    client.post("/api/tasks", json={"noteId": note["id"], "text": "Alice's task"})
    client.post("/api/auth/logout")

    register(client, username="bob")
    res = client.get("/api/tasks")
    assert res.get_json() == []


# ============================================================
# Day 16 — due-date phrases on tasks
# ============================================================
def test_extract_tasks_captures_due_date_objects(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=NOTE_TEXT)

    import tasks

    fake_reply = (
        '[{"text": "Email Sarah the budget draft", "due": "Friday"}, '
        '{"text": "Follow up with design about icons", "due": null}]'
    )
    monkeypatch.setattr(tasks, "call_ai", lambda prompt, system=None: fake_reply)

    res = client.post(f"/api/notes/{note['id']}/extract-tasks")
    body = res.get_json()
    by_text = {t["text"]: t["due"] for t in body["tasks"]}
    assert by_text["Email Sarah the budget draft"] == "Friday"
    assert by_text["Follow up with design about icons"] is None


def test_extract_tasks_matches_demo_scenario(client, monkeypatch):
    """Regression test for the 'complete frontend today / test API
    tomorrow' demo prompt."""
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(
        client,
        text="I need to complete the frontend today. I also need to test the API tomorrow.",
    )

    import tasks

    fake_reply = (
        '[{"text": "Complete the frontend", "due": "Today"}, '
        '{"text": "Test the API", "due": "Tomorrow"}]'
    )
    monkeypatch.setattr(tasks, "call_ai", lambda prompt, system=None: fake_reply)

    res = client.post(f"/api/notes/{note['id']}/extract-tasks")
    body = res.get_json()
    assert body["added"] == 2
    by_text = {t["text"]: t["due"] for t in body["tasks"]}
    assert by_text["Complete the frontend"] == "Today"
    assert by_text["Test the API"] == "Tomorrow"


def test_extract_tasks_still_accepts_bare_strings(client, monkeypatch):
    """Backward compatibility: a bare-string array (no due dates) should
    still work, e.g. from a model that ignores the due-date instruction."""
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=NOTE_TEXT)

    import tasks

    monkeypatch.setattr(tasks, "call_ai", lambda prompt, system=None: '["Call the plumber"]')

    res = client.post(f"/api/notes/{note['id']}/extract-tasks")
    body = res.get_json()
    assert body["tasks"][0]["text"] == "Call the plumber"
    assert body["tasks"][0]["due"] is None


def test_extract_tasks_splits_bundled_actions(client, monkeypatch):
    """A single extracted item that bundles several distinct actions
    ('X and Y and Z') should come back as separate, smaller tasks
    instead of one oversized one."""
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=NOTE_TEXT)

    import tasks

    fake_reply = (
        '[{"text": "Email Sarah the draft and book the conference room and '
        'confirm with the printer", "due": "Friday"}]'
    )
    monkeypatch.setattr(tasks, "call_ai", lambda prompt, system=None: fake_reply)

    res = client.post(f"/api/notes/{note['id']}/extract-tasks")
    body = res.get_json()
    texts = {t["text"] for t in body["tasks"]}
    assert texts == {
        "Email Sarah the draft",
        "Book the conference room",
        "Confirm with the printer",
    }
    # The due phrase belonged to the whole bundled sentence, so every
    # split-out fragment should inherit it.
    assert all(t["due"] == "Friday" for t in body["tasks"])


def test_extract_tasks_does_not_split_a_single_natural_task(client, monkeypatch):
    """'and' inside a task's own object ('bread and milk') should NOT
    be treated as two separate tasks."""
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=NOTE_TEXT)

    import tasks

    monkeypatch.setattr(tasks, "call_ai", lambda prompt, system=None: '["Buy bread and milk"]')

    res = client.post(f"/api/notes/{note['id']}/extract-tasks")
    body = res.get_json()
    assert len(body["tasks"]) == 1
    assert body["tasks"][0]["text"] == "Buy bread and milk"


def test_manual_task_with_due_date(client):
    register(client)
    note = create_note(client, text=NOTE_TEXT)

    created = client.post(
        "/api/tasks", json={"noteId": note["id"], "text": "Renew passport", "due": "Next month"}
    ).get_json()
    assert created["due"] == "Next month"

    updated = client.patch(f"/api/tasks/{created['id']}", json={"due": "This week"}).get_json()
    assert updated["due"] == "This week"

    cleared = client.patch(f"/api/tasks/{created['id']}", json={"due": None}).get_json()
    assert cleared["due"] is None
    assert cleared["text"] == "Renew passport"  # untouched fields survive a partial update
