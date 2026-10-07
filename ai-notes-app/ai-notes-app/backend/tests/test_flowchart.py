import json

from conftest import register


def create_note(client, title="", text=""):
    res = client.post("/api/notes", json={"title": title, "text": text})
    return res.get_json()


LONG_TEXT = (
    "First, preheat the oven to 400 degrees. Next, chop the vegetables and toss them "
    "with olive oil. Then spread them on a baking sheet. If they look dry, add a bit "
    "more oil. Finally, bake for 25 minutes and let them cool before serving."
)

VALID_AI_JSON = json.dumps(
    {
        "nodes": [
            {"id": "n1", "label": "Preheat oven", "type": "start"},
            {"id": "n2", "label": "Chop vegetables", "type": "process"},
            {"id": "n3", "label": "Dry?", "type": "decision"},
            {"id": "n4", "label": "Add more oil", "type": "process"},
            {"id": "n5", "label": "Bake and cool", "type": "end"},
        ],
        "edges": [
            {"from": "n1", "to": "n2", "label": ""},
            {"from": "n2", "to": "n3", "label": ""},
            {"from": "n3", "to": "n4", "label": "Yes"},
            {"from": "n3", "to": "n5", "label": "No"},
            {"from": "n4", "to": "n5", "label": ""},
        ],
    }
)


def configure_ai(monkeypatch, reply=VALID_AI_JSON):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")

    import flowchart

    monkeypatch.setattr(flowchart, "call_ai", lambda prompt, system=None: reply)


# ---------- auth / ownership ----------


def test_list_requires_login(client):
    res = client.get("/api/notes/1/flowcharts")
    assert res.status_code == 401


def test_generate_requires_login(client):
    res = client.post("/api/notes/1/flowcharts", json={"text": LONG_TEXT})
    assert res.status_code == 401


def test_generate_404s_on_missing_note(client, monkeypatch):
    configure_ai(monkeypatch)
    register(client)
    res = client.post("/api/notes/999999/flowcharts", json={"text": LONG_TEXT})
    assert res.status_code == 404


def test_generate_404s_on_someone_elses_note(client, monkeypatch):
    configure_ai(monkeypatch)
    register(client, username="alice")
    note = create_note(client, text="alice's note")
    client.post("/api/auth/logout")

    register(client, username="bob")
    res = client.post(f"/api/notes/{note['id']}/flowcharts", json={"text": LONG_TEXT})
    assert res.status_code == 404


# ---------- validation ----------


def test_generate_rejects_empty_text(client, monkeypatch):
    configure_ai(monkeypatch)
    register(client)
    note = create_note(client)
    res = client.post(f"/api/notes/{note['id']}/flowcharts", json={"text": "   "})
    assert res.status_code == 400


def test_generate_rejects_too_short_selection(client, monkeypatch):
    configure_ai(monkeypatch)
    register(client)
    note = create_note(client)
    res = client.post(f"/api/notes/{note['id']}/flowcharts", json={"text": "too short"})
    assert res.status_code == 400


def test_generate_rejects_oversized_selection(client, monkeypatch):
    configure_ai(monkeypatch)
    register(client)
    note = create_note(client)
    huge = "step one, step two, and step three. " * 300
    res = client.post(f"/api/notes/{note['id']}/flowcharts", json={"text": huge})
    assert res.status_code == 400


def test_generate_returns_501_when_ai_unavailable(client, monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    register(client)
    note = create_note(client)
    res = client.post(f"/api/notes/{note['id']}/flowcharts", json={"text": LONG_TEXT})
    assert res.status_code == 501
    assert "error" in res.get_json()


def test_generate_returns_422_when_ai_finds_no_process(client, monkeypatch):
    configure_ai(monkeypatch, reply='{"nodes":[],"edges":[]}')
    register(client)
    note = create_note(client)
    res = client.post(f"/api/notes/{note['id']}/flowcharts", json={"text": LONG_TEXT})
    assert res.status_code == 422
    assert "error" in res.get_json()


def test_generate_returns_422_on_unparseable_reply(client, monkeypatch):
    configure_ai(monkeypatch, reply="Sorry, I can't help with that.")
    register(client)
    note = create_note(client)
    res = client.post(f"/api/notes/{note['id']}/flowcharts", json={"text": LONG_TEXT})
    assert res.status_code == 422


# ---------- happy path ----------


def test_generate_succeeds_and_persists(client, monkeypatch):
    configure_ai(monkeypatch)
    register(client)
    note = create_note(client)

    res = client.post(f"/api/notes/{note['id']}/flowcharts", json={"text": LONG_TEXT})
    assert res.status_code == 201
    body = res.get_json()
    assert body["noteId"] == note["id"]
    assert len(body["nodes"]) == 5
    assert len(body["edges"]) == 5
    assert body["nodes"][0]["type"] == "start"
    assert body["title"]  # non-empty, derived from the selection
    assert body["sourceText"] == LONG_TEXT

    list_res = client.get(f"/api/notes/{note['id']}/flowcharts")
    assert list_res.status_code == 200
    listed = list_res.get_json()
    assert len(listed) == 1
    assert listed[0]["id"] == body["id"]


def test_note_can_have_multiple_flowcharts(client, monkeypatch):
    configure_ai(monkeypatch)
    register(client)
    note = create_note(client)

    client.post(f"/api/notes/{note['id']}/flowcharts", json={"text": LONG_TEXT})
    client.post(f"/api/notes/{note['id']}/flowcharts", json={"text": LONG_TEXT + " Repeat as needed."})

    res = client.get(f"/api/notes/{note['id']}/flowcharts")
    assert len(res.get_json()) == 2


def test_ai_reply_wrapped_in_prose_still_parses(client, monkeypatch):
    wrapped = f"Sure, here's the flowchart:\n```json\n{VALID_AI_JSON}\n```\nHope that helps!"
    configure_ai(monkeypatch, reply=wrapped)
    register(client)
    note = create_note(client)
    res = client.post(f"/api/notes/{note['id']}/flowcharts", json={"text": LONG_TEXT})
    assert res.status_code == 201
    assert len(res.get_json()["nodes"]) == 5


def test_edges_referencing_unknown_node_ids_are_dropped(client, monkeypatch):
    reply = json.dumps(
        {
            "nodes": [{"id": "n1", "label": "Start here", "type": "start"}],
            "edges": [{"from": "n1", "to": "ghost", "label": ""}],
        }
    )
    configure_ai(monkeypatch, reply=reply)
    register(client)
    note = create_note(client)
    res = client.post(f"/api/notes/{note['id']}/flowcharts", json={"text": LONG_TEXT})
    assert res.status_code == 201
    body = res.get_json()
    assert body["edges"] == []


def test_invalid_node_type_falls_back_to_process(client, monkeypatch):
    reply = json.dumps(
        {
            "nodes": [{"id": "n1", "label": "Mystery step", "type": "wizardry"}],
            "edges": [],
        }
    )
    configure_ai(monkeypatch, reply=reply)
    register(client)
    note = create_note(client)
    res = client.post(f"/api/notes/{note['id']}/flowcharts", json={"text": LONG_TEXT})
    assert res.status_code == 201
    assert res.get_json()["nodes"][0]["type"] == "process"


def test_nodes_capped_at_max(client, monkeypatch):
    import flowchart

    nodes = [{"id": f"n{i}", "label": f"Step {i}", "type": "process"} for i in range(30)]
    reply = json.dumps({"nodes": nodes, "edges": []})
    configure_ai(monkeypatch, reply=reply)
    register(client)
    note = create_note(client)
    res = client.post(f"/api/notes/{note['id']}/flowcharts", json={"text": LONG_TEXT})
    assert res.status_code == 201
    assert len(res.get_json()["nodes"]) == flowchart.MAX_NODES


# ---------- delete ----------


def test_delete_requires_login(client):
    res = client.delete("/api/flowcharts/1")
    assert res.status_code == 401


def test_delete_404s_on_missing(client, monkeypatch):
    configure_ai(monkeypatch)
    register(client)
    res = client.delete("/api/flowcharts/999999")
    assert res.status_code == 404


def test_delete_removes_it(client, monkeypatch):
    configure_ai(monkeypatch)
    register(client)
    note = create_note(client)
    created = client.post(f"/api/notes/{note['id']}/flowcharts", json={"text": LONG_TEXT}).get_json()

    res = client.delete(f"/api/flowcharts/{created['id']}")
    assert res.status_code == 200
    assert res.get_json()["deleted"] is True

    listed = client.get(f"/api/notes/{note['id']}/flowcharts").get_json()
    assert listed == []


def test_cannot_delete_someone_elses_flowchart(client, monkeypatch):
    configure_ai(monkeypatch)
    register(client, username="alice")
    note = create_note(client)
    created = client.post(f"/api/notes/{note['id']}/flowcharts", json={"text": LONG_TEXT}).get_json()
    client.post("/api/auth/logout")

    register(client, username="bob")
    res = client.delete(f"/api/flowcharts/{created['id']}")
    assert res.status_code == 404


def test_cross_user_isolation_on_list(client, monkeypatch):
    configure_ai(monkeypatch)
    register(client, username="alice")
    note = create_note(client)
    client.post(f"/api/notes/{note['id']}/flowcharts", json={"text": LONG_TEXT})
    client.post("/api/auth/logout")

    register(client, username="bob")
    bob_note = create_note(client)
    res = client.get(f"/api/notes/{bob_note['id']}/flowcharts")
    assert res.get_json() == []
