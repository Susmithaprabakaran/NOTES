import json

from conftest import register

TAGGABLE_TEXT = (
    "Quarterly budget review meeting notes: finance flagged a shortfall in the "
    "marketing spend and we agreed to revisit vendor contracts next month."
)
SHORT_TEXT = "Buy milk"


def create_note(client, title="", text=""):
    res = client.post("/api/notes", json={"title": title, "text": text})
    return res.get_json()


def test_auto_tag_requires_login(client):
    res = client.post("/api/notes/1/auto-tag")
    assert res.status_code == 401


def test_auto_tag_404_for_missing_note(client):
    register(client)
    res = client.post("/api/notes/999/auto-tag")
    assert res.status_code == 404


def test_auto_tag_skips_short_notes(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=SHORT_TEXT)

    res = client.post(f"/api/notes/{note['id']}/auto-tag")
    assert res.status_code == 200
    assert res.get_json()["skipped"] == "too_short"
    assert res.get_json()["note"]["tags"] == []


def test_auto_tag_returns_501_without_provider(client, monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    register(client)
    note = create_note(client, text=TAGGABLE_TEXT)

    res = client.post(f"/api/notes/{note['id']}/auto-tag")
    assert res.status_code == 501


def test_auto_tag_success_with_mocked_ai(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=TAGGABLE_TEXT)

    import notes

    monkeypatch.setattr(notes, "call_ai", lambda prompt, system=None: json.dumps(["finance", "work"]))

    res = client.post(f"/api/notes/{note['id']}/auto-tag")
    assert res.status_code == 200
    body = res.get_json()["note"]
    assert body["tags"] == ["finance", "work"]
    assert body["tagsGeneratedAt"] is not None
    assert body["tagsStale"] is False


def test_auto_tag_normalizes_case_and_punctuation(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=TAGGABLE_TEXT)

    import notes

    monkeypatch.setattr(notes, "call_ai", lambda prompt, system=None: json.dumps(["  Finance.", "#Work", "finance"]))

    res = client.post(f"/api/notes/{note['id']}/auto-tag")
    tags = res.get_json()["note"]["tags"]
    assert tags == ["finance", "work"]  # normalized + deduped, order preserved


def test_auto_tag_caps_tag_count(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=TAGGABLE_TEXT)

    import notes

    monkeypatch.setattr(
        notes, "call_ai", lambda prompt, system=None: json.dumps(["a", "b", "c", "d", "e", "f", "g"])
    )

    res = client.post(f"/api/notes/{note['id']}/auto-tag")
    assert len(res.get_json()["note"]["tags"]) == 5  # MAX_TAGS


def test_auto_tag_handles_prose_wrapped_reply(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=TAGGABLE_TEXT)

    import notes

    monkeypatch.setattr(
        notes, "call_ai", lambda prompt, system=None: 'Sure, here are the tags: ["finance", "meetings"]'
    )

    res = client.post(f"/api/notes/{note['id']}/auto-tag")
    assert res.get_json()["note"]["tags"] == ["finance", "meetings"]


def test_auto_tag_skips_ai_call_when_text_unchanged(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=TAGGABLE_TEXT)

    import notes

    calls = []
    monkeypatch.setattr(notes, "call_ai", lambda prompt, system=None: calls.append(1) or "[]")

    client.post(f"/api/notes/{note['id']}/auto-tag")
    assert len(calls) == 1

    res = client.post(f"/api/notes/{note['id']}/auto-tag")
    assert res.get_json()["skipped"] == "unchanged"
    assert len(calls) == 1


def test_shrinking_note_below_threshold_clears_tags(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=TAGGABLE_TEXT)

    import notes

    monkeypatch.setattr(notes, "call_ai", lambda prompt, system=None: json.dumps(["finance"]))
    client.post(f"/api/notes/{note['id']}/auto-tag")

    client.put(f"/api/notes/{note['id']}", json={"text": SHORT_TEXT})
    res = client.post(f"/api/notes/{note['id']}/auto-tag")
    assert res.get_json()["skipped"] == "too_short"
    assert res.get_json()["note"]["tags"] == []


def test_list_tags_requires_login(client):
    res = client.get("/api/tags")
    assert res.status_code == 401


def test_list_tags_aggregates_across_notes(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)

    import notes

    note1 = create_note(client, text=TAGGABLE_TEXT)
    monkeypatch.setattr(notes, "call_ai", lambda prompt, system=None: json.dumps(["finance", "work"]))
    client.post(f"/api/notes/{note1['id']}/auto-tag")

    note2 = create_note(client, text=TAGGABLE_TEXT + " Also discussed the travel budget for the offsite.")
    monkeypatch.setattr(notes, "call_ai", lambda prompt, system=None: json.dumps(["finance", "travel"]))
    client.post(f"/api/notes/{note2['id']}/auto-tag")

    res = client.get("/api/tags")
    assert res.status_code == 200
    tags = {t["tag"]: t["count"] for t in res.get_json()}
    assert tags["finance"] == 2
    assert tags["work"] == 1
    assert tags["travel"] == 1


def test_list_tags_excludes_stale_tags(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=TAGGABLE_TEXT)

    import notes

    monkeypatch.setattr(notes, "call_ai", lambda prompt, system=None: json.dumps(["finance"]))
    client.post(f"/api/notes/{note['id']}/auto-tag")

    # Edit the note without re-tagging — the tag is now stale relative to the new text.
    client.put(f"/api/notes/{note['id']}", json={"text": TAGGABLE_TEXT + " One more sentence."})

    res = client.get("/api/tags")
    assert res.get_json() == []


def test_list_notes_filters_by_tag(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)

    import notes

    note1 = create_note(client, title="Budget", text=TAGGABLE_TEXT)
    monkeypatch.setattr(notes, "call_ai", lambda prompt, system=None: json.dumps(["finance"]))
    client.post(f"/api/notes/{note1['id']}/auto-tag")

    note2 = create_note(client, title="Recipe", text="A completely unrelated note about cooking dinner tonight for guests.")
    monkeypatch.setattr(notes, "call_ai", lambda prompt, system=None: json.dumps(["cooking"]))
    client.post(f"/api/notes/{note2['id']}/auto-tag")

    res = client.get("/api/notes?tag=finance")
    titles = [n["title"] for n in res.get_json()]
    assert titles == ["Budget"]


def test_users_only_see_their_own_tags(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client, username="owner")

    import notes

    note = create_note(client, text=TAGGABLE_TEXT)
    monkeypatch.setattr(notes, "call_ai", lambda prompt, system=None: json.dumps(["finance"]))
    client.post(f"/api/notes/{note['id']}/auto-tag")

    client.post("/api/auth/logout")
    register(client, username="intruder")
    res = client.get("/api/tags")
    assert res.get_json() == []
