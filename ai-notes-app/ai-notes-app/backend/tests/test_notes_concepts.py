import json

from conftest import register


def create_note(client, title="", text=""):
    res = client.post("/api/notes", json={"title": title, "text": text})
    return res.get_json()


ML_TEXT = "Machine Learning uses Python and Scikit-learn for model development."


def test_extract_concepts_requires_login(client):
    res = client.post("/api/notes/1/extract-concepts")
    assert res.status_code == 401


def test_extract_concepts_404_for_missing_note(client):
    register(client)
    res = client.post("/api/notes/999/extract-concepts")
    assert res.status_code == 404


def test_extract_concepts_skips_short_notes(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text="Buy milk")

    res = client.post(f"/api/notes/{note['id']}/extract-concepts")
    assert res.status_code == 200
    assert res.get_json()["skipped"] == "too_short"
    assert res.get_json()["note"]["concepts"] == []


def test_extract_concepts_returns_501_without_provider(client, monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    register(client)
    note = create_note(client, text=ML_TEXT)

    res = client.post(f"/api/notes/{note['id']}/extract-concepts")
    assert res.status_code == 501


def test_extract_concepts_success_with_mocked_ai(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=ML_TEXT)

    import notes

    fake_reply = json.dumps(["Machine Learning", "Python", "Scikit-learn"])
    monkeypatch.setattr(notes, "call_ai", lambda prompt, system=None: fake_reply)

    res = client.post(f"/api/notes/{note['id']}/extract-concepts")
    assert res.status_code == 200
    body = res.get_json()["note"]
    assert body["concepts"] == ["Machine Learning", "Python", "Scikit-learn"]
    assert body["conceptsGeneratedAt"] is not None
    assert body["conceptsStale"] is False


def test_extract_concepts_dedupes_case_insensitively_preserving_first_casing(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=ML_TEXT)

    import notes

    fake_reply = json.dumps(["Python", "python", "PYTHON"])
    monkeypatch.setattr(notes, "call_ai", lambda prompt, system=None: fake_reply)

    res = client.post(f"/api/notes/{note['id']}/extract-concepts")
    assert res.get_json()["note"]["concepts"] == ["Python"]


def test_extract_concepts_handles_prose_wrapped_reply(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=ML_TEXT)

    import notes

    fake_reply = 'Sure, here are the concepts: ["Machine Learning", "Python"]'
    monkeypatch.setattr(notes, "call_ai", lambda prompt, system=None: fake_reply)

    res = client.post(f"/api/notes/{note['id']}/extract-concepts")
    assert res.get_json()["note"]["concepts"] == ["Machine Learning", "Python"]


def test_extract_concepts_skips_ai_call_when_text_unchanged(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=ML_TEXT)

    import notes

    calls = []
    monkeypatch.setattr(notes, "call_ai", lambda prompt, system=None: calls.append(1) or "[]")

    client.post(f"/api/notes/{note['id']}/extract-concepts")
    assert len(calls) == 1

    res = client.post(f"/api/notes/{note['id']}/extract-concepts")
    assert res.get_json()["skipped"] == "unchanged"
    assert len(calls) == 1


def test_shrinking_note_below_threshold_clears_concepts(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=ML_TEXT)

    import notes

    monkeypatch.setattr(notes, "call_ai", lambda prompt, system=None: json.dumps(["Machine Learning"]))
    client.post(f"/api/notes/{note['id']}/extract-concepts")

    client.put(f"/api/notes/{note['id']}", json={"text": "Buy milk"})
    res = client.post(f"/api/notes/{note['id']}/extract-concepts")
    assert res.get_json()["skipped"] == "too_short"
    assert res.get_json()["note"]["concepts"] == []
