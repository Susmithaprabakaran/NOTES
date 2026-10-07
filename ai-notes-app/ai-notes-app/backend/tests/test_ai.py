from conftest import register


def test_status_requires_login(client):
    res = client.get("/api/ai/status")
    assert res.status_code == 401


def test_status_reports_unavailable_when_unconfigured(client, monkeypatch):
    # No AI_PROVIDER set in this test env — exercises the real
    # graceful-degradation path, same spirit as the Day 9 voice test.
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    register(client)
    res = client.get("/api/ai/status")
    assert res.status_code == 200
    body = res.get_json()
    assert body["available"] is False
    assert "reason" in body


def test_status_reports_available_when_openai_configured(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)
    res = client.get("/api/ai/status")
    assert res.status_code == 200
    body = res.get_json()
    assert body["available"] is True
    assert body["provider"] == "openai"


def test_assist_requires_login(client):
    res = client.post("/api/ai/assist", json={"text": "hello", "action": "summarize"})
    assert res.status_code == 401


def test_assist_rejects_unknown_action(client):
    register(client)
    res = client.post("/api/ai/assist", json={"text": "hello", "action": "translate"})
    assert res.status_code == 400


def test_assist_rejects_empty_text(client):
    register(client)
    res = client.post("/api/ai/assist", json={"text": "  ", "action": "summarize"})
    assert res.status_code == 400


def test_assist_returns_501_when_no_provider_configured(client, monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    register(client)
    res = client.post("/api/ai/assist", json={"text": "some note text", "action": "summarize"})
    assert res.status_code == 501
    assert "error" in res.get_json()


def test_assist_succeeds_with_mocked_provider(client, monkeypatch):
    # Mocks the actual network call so this test doesn't need a real API
    # key or make a real request — it verifies the route's validation,
    # prompt dispatch, and response shape independent of any provider.
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)

    def fake_call_ai(prompt, system=None):
        assert "my note text" in prompt
        return "A short mocked summary."

    import ai

    monkeypatch.setattr(ai, "call_ai", fake_call_ai)

    res = client.post("/api/ai/assist", json={"text": "my note text", "action": "summarize"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["result"] == "A short mocked summary."
    assert body["action"] == "summarize"


def test_assist_maps_action_to_different_prompts(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")
    register(client)

    seen_prompts = []

    def fake_call_ai(prompt, system=None):
        seen_prompts.append(prompt)
        return "ok"

    import ai

    monkeypatch.setattr(ai, "call_ai", fake_call_ai)

    for action in ("summarize", "polish", "title"):
        client.post("/api/ai/assist", json={"text": "shared note text", "action": action})

    assert len(seen_prompts) == 3
    assert len(set(seen_prompts)) == 3  # each action produced a distinct prompt
