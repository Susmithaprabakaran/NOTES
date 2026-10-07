from conftest import register

LONG_TEXT = (
    "This is a long enough note to trigger automatic summarization. "
    "It talks about the quarterly roadmap, three risks the team flagged, "
    "and a decision to delay the launch by two weeks pending more testing. "
    "There is also a reminder to follow up with design about the new icons."
)
SHORT_TEXT = "Buy milk and eggs."


def create_note(client, title="", text=""):
    res = client.post("/api/notes", json={"title": title, "text": text})
    return res.get_json()


def test_auto_summarize_requires_login(client):
    res = client.post("/api/notes/1/auto-summarize")
    assert res.status_code == 401


def test_auto_summarize_404_for_missing_note(client):
    register(client)
    res = client.post("/api/notes/999/auto-summarize")
    assert res.status_code == 404


def test_auto_summarize_skips_short_notes(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=SHORT_TEXT)

    res = client.post(f"/api/notes/{note['id']}/auto-summarize")
    assert res.status_code == 200
    body = res.get_json()
    assert body["skipped"] == "too_short"
    assert body["note"]["summary"] is None


def test_auto_summarize_returns_501_without_provider(client, monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    register(client)
    note = create_note(client, text=LONG_TEXT)

    res = client.post(f"/api/notes/{note['id']}/auto-summarize")
    assert res.status_code == 501
    assert "error" in res.get_json()


def test_auto_summarize_success_with_mocked_ai(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=LONG_TEXT)

    import notes

    monkeypatch.setattr(notes, "call_ai", lambda prompt, system=None: "Roadmap delayed two weeks; follow up with design.")

    res = client.post(f"/api/notes/{note['id']}/auto-summarize")
    assert res.status_code == 200
    body = res.get_json()["note"]
    assert body["summary"] == "Roadmap delayed two weeks; follow up with design."
    assert body["summaryGeneratedAt"] is not None
    assert body["summaryStale"] is False


def test_auto_summarize_skips_ai_call_when_text_unchanged(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=LONG_TEXT)

    import notes

    calls = []
    monkeypatch.setattr(notes, "call_ai", lambda prompt, system=None: calls.append(1) or "A summary.")

    client.post(f"/api/notes/{note['id']}/auto-summarize")
    assert len(calls) == 1

    # Second call with identical text should skip the AI entirely.
    res = client.post(f"/api/notes/{note['id']}/auto-summarize")
    assert res.status_code == 200
    assert res.get_json()["skipped"] == "unchanged"
    assert len(calls) == 1  # not called again


def test_summary_marked_stale_after_note_edited(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=LONG_TEXT)

    import notes

    monkeypatch.setattr(notes, "call_ai", lambda prompt, system=None: "A summary.")
    client.post(f"/api/notes/{note['id']}/auto-summarize")

    # Edit the note's text without re-summarizing yet.
    edit_res = client.put(f"/api/notes/{note['id']}", json={"text": LONG_TEXT + " One more sentence added."})
    assert edit_res.get_json()["summaryStale"] is True

    # Re-running auto-summarize should call the AI again since the hash changed.
    calls = []
    monkeypatch.setattr(notes, "call_ai", lambda prompt, system=None: calls.append(1) or "Updated summary.")
    res = client.post(f"/api/notes/{note['id']}/auto-summarize")
    assert len(calls) == 1
    assert res.get_json()["note"]["summary"] == "Updated summary."
    assert res.get_json()["note"]["summaryStale"] is False


def test_shrinking_note_below_threshold_clears_existing_summary(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=LONG_TEXT)

    import notes

    monkeypatch.setattr(notes, "call_ai", lambda prompt, system=None: "A summary.")
    client.post(f"/api/notes/{note['id']}/auto-summarize")

    client.put(f"/api/notes/{note['id']}", json={"text": SHORT_TEXT})
    res = client.post(f"/api/notes/{note['id']}/auto-summarize")
    assert res.get_json()["skipped"] == "too_short"
    assert res.get_json()["note"]["summary"] is None
