import json

from conftest import register

ENTITY_TEXT = (
    "Had lunch with Sarah Chen on Tuesday to talk about the Seattle office move. "
    "We agreed to follow up with the landlord by March 3rd and loop in David "
    "before the next board meeting."
)
NO_ENTITY_TEXT = "Feeling pretty good today, just a quiet afternoon reading and relaxing at home."
SHORT_TEXT = "Call Sam"


def create_note(client, title="", text=""):
    res = client.post("/api/notes", json={"title": title, "text": text})
    return res.get_json()


def test_extract_entities_requires_login(client):
    res = client.post("/api/notes/1/extract-entities")
    assert res.status_code == 401


def test_extract_entities_404_for_missing_note(client):
    register(client)
    res = client.post("/api/notes/999/extract-entities")
    assert res.status_code == 404


def test_extract_entities_skips_short_notes(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=SHORT_TEXT)

    res = client.post(f"/api/notes/{note['id']}/extract-entities")
    assert res.status_code == 200
    assert res.get_json()["skipped"] == "too_short"
    body = res.get_json()["note"]
    assert body["entities"] == {"people": [], "dates": [], "places": [], "organizations": []}


def test_extract_entities_returns_501_without_provider(client, monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    register(client)
    note = create_note(client, text=ENTITY_TEXT)

    res = client.post(f"/api/notes/{note['id']}/extract-entities")
    assert res.status_code == 501


def test_extract_entities_success_with_mocked_ai(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=ENTITY_TEXT)

    import notes

    fake_reply = json.dumps(
        {
            "people": ["Sarah Chen", "David"],
            "dates": ["Tuesday", "March 3rd"],
            "places": ["Seattle office"],
            "organizations": ["Acme Corp"],
        }
    )
    monkeypatch.setattr(notes, "call_ai", lambda prompt, system=None: fake_reply)

    res = client.post(f"/api/notes/{note['id']}/extract-entities")
    assert res.status_code == 200
    body = res.get_json()["note"]
    assert body["entities"]["people"] == ["Sarah Chen", "David"]
    assert body["entities"]["dates"] == ["Tuesday", "March 3rd"]
    assert body["entities"]["places"] == ["Seattle office"]
    assert body["entities"]["organizations"] == ["Acme Corp"]
    assert body["entitiesGeneratedAt"] is not None
    assert body["entitiesStale"] is False


def test_extract_entities_matches_demo_scenario(client, monkeypatch):
    """Regression test for the WebenoID Technologies demo transcript —
    all four categories should be populated correctly."""
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)

    demo_text = (
        "On August 13, 2026, Susmitha met John and Sarah at WebenoID Technologies "
        "in Coimbatore. They discussed the AI Notes application and its NLP "
        "features. John will complete the Speech-to-Text testing by August 18, "
        "2026. Sarah will test the entity recognition module tomorrow. The next "
        "review meeting will take place in Chennai on August 25, 2026."
    )
    note = create_note(client, text=demo_text)

    import notes

    fake_reply = json.dumps(
        {
            "people": ["Susmitha", "John", "Sarah"],
            "organizations": ["WebenoID Technologies"],
            "places": ["Coimbatore", "Chennai"],
            "dates": ["August 13, 2026", "August 18, 2026", "tomorrow", "August 25, 2026"],
        }
    )
    monkeypatch.setattr(notes, "call_ai", lambda prompt, system=None: fake_reply)

    res = client.post(f"/api/notes/{note['id']}/extract-entities")
    entities = res.get_json()["note"]["entities"]
    assert entities["people"] == ["Susmitha", "John", "Sarah"]
    assert entities["organizations"] == ["WebenoID Technologies"]
    assert entities["places"] == ["Coimbatore", "Chennai"]
    assert entities["dates"] == ["August 13, 2026", "August 18, 2026", "tomorrow", "August 25, 2026"]


def test_extract_entities_handles_all_empty_reply(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=NO_ENTITY_TEXT + " " + NO_ENTITY_TEXT)

    import notes

    monkeypatch.setattr(
        notes, "call_ai", lambda prompt, system=None: json.dumps({"people": [], "dates": [], "places": [], "organizations": []})
    )

    res = client.post(f"/api/notes/{note['id']}/extract-entities")
    assert res.status_code == 200
    body = res.get_json()["note"]
    assert body["entities"] == {"people": [], "dates": [], "places": [], "organizations": []}
    assert body["entitiesStale"] is False  # nothing found isn't "stale", just empty


def test_extract_entities_preserves_casing_and_dedupes_case_insensitively(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=ENTITY_TEXT)

    import notes

    fake_reply = json.dumps(
        {"people": ["Sarah Chen", "sarah chen", "  David  "], "dates": [], "places": []}
    )
    monkeypatch.setattr(notes, "call_ai", lambda prompt, system=None: fake_reply)

    res = client.post(f"/api/notes/{note['id']}/extract-entities")
    people = res.get_json()["note"]["entities"]["people"]
    assert people == ["Sarah Chen", "David"]  # original casing kept, case-insensitive dedup


def test_extract_entities_handles_prose_wrapped_reply(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=ENTITY_TEXT)

    import notes

    fake_reply = 'Sure, here you go: {"people": ["David"], "dates": [], "places": ["the office"]} hope that helps!'
    monkeypatch.setattr(notes, "call_ai", lambda prompt, system=None: fake_reply)

    res = client.post(f"/api/notes/{note['id']}/extract-entities")
    body = res.get_json()["note"]["entities"]
    assert body["people"] == ["David"]
    assert body["places"] == ["the office"]


def test_extract_entities_caps_per_category(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=ENTITY_TEXT)

    import notes

    many_people = [f"Person {i}" for i in range(12)]
    monkeypatch.setattr(
        notes, "call_ai", lambda prompt, system=None: json.dumps({"people": many_people, "dates": [], "places": []})
    )

    res = client.post(f"/api/notes/{note['id']}/extract-entities")
    assert len(res.get_json()["note"]["entities"]["people"]) == 8  # MAX_ENTITIES_PER_CATEGORY


def test_extract_entities_skips_ai_call_when_text_unchanged(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=ENTITY_TEXT)

    import notes

    calls = []
    monkeypatch.setattr(
        notes,
        "call_ai",
        lambda prompt, system=None: calls.append(1) or json.dumps({"people": [], "dates": [], "places": [], "organizations": []}),
    )

    client.post(f"/api/notes/{note['id']}/extract-entities")
    assert len(calls) == 1

    res = client.post(f"/api/notes/{note['id']}/extract-entities")
    assert res.get_json()["skipped"] == "unchanged"
    assert len(calls) == 1


def test_shrinking_note_below_threshold_clears_entities(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=ENTITY_TEXT)

    import notes

    monkeypatch.setattr(
        notes,
        "call_ai",
        lambda prompt, system=None: json.dumps({"people": ["Sarah"], "dates": [], "places": []}),
    )
    client.post(f"/api/notes/{note['id']}/extract-entities")

    client.put(f"/api/notes/{note['id']}", json={"text": SHORT_TEXT})
    res = client.post(f"/api/notes/{note['id']}/extract-entities")
    assert res.get_json()["skipped"] == "too_short"
    assert res.get_json()["note"]["entities"]["people"] == []


def test_entities_marked_stale_after_note_edited(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    note = create_note(client, text=ENTITY_TEXT)

    import notes

    monkeypatch.setattr(
        notes,
        "call_ai",
        lambda prompt, system=None: json.dumps({"people": ["Sarah"], "dates": [], "places": []}),
    )
    client.post(f"/api/notes/{note['id']}/extract-entities")

    edit_res = client.put(f"/api/notes/{note['id']}", json={"text": ENTITY_TEXT + " One more sentence."})
    assert edit_res.get_json()["entitiesStale"] is True


def test_entities_cannot_cross_users(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client, username="owner")
    note = create_note(client, text=ENTITY_TEXT)

    import notes

    monkeypatch.setattr(
        notes,
        "call_ai",
        lambda prompt, system=None: json.dumps({"people": ["Sarah"], "dates": [], "places": []}),
    )
    client.post(f"/api/notes/{note['id']}/extract-entities")

    client.post("/api/auth/logout")
    register(client, username="intruder")
    res = client.post(f"/api/notes/{note['id']}/extract-entities")
    assert res.status_code == 404
