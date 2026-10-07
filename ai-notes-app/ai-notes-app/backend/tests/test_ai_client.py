# ============================================================
# Day 29 — unit tests for ai_client.py's Ollama provider.
#
# These test ai_client directly (no Flask client, no DB) since the
# behavior under test — which host gets picked, what headers go out,
# how various HTTP responses map to errors — lives entirely inside
# ai_client.py itself. requests.post is monkeypatched so nothing here
# makes a real network call or needs a real Ollama server.
# ============================================================
import pytest

import ai_client


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._json_data


def ok_response(content="Hello from Ollama"):
    return FakeResponse(200, {"choices": [{"message": {"content": content}}]})


# ---------- ai_status() ----------


def test_status_available_without_api_key(monkeypatch):
    # Unlike the other providers, "ollama" needs no key at all — a
    # local `ollama serve` has no auth of its own.
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    available, provider, reason = ai_client.ai_status()
    assert available is True
    assert provider == "ollama"
    assert reason is None


def test_status_available_with_api_key(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_API_KEY", "fake-ollama-key")
    available, provider, reason = ai_client.ai_status()
    assert available is True
    assert provider == "ollama"


# ---------- _call_ollama() / call_ai() routing ----------


def test_call_ollama_uses_cloud_host_and_bearer_key_when_key_set(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_API_KEY", "fake-ollama-key")
    monkeypatch.delenv("OLLAMA_HOST", raising=False)

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return ok_response("cloud reply")

    monkeypatch.setattr(ai_client.requests, "post", fake_post)

    result = ai_client.call_ai("hello there")
    assert result == "cloud reply"
    assert captured["url"] == "https://ollama.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer fake-ollama-key"
    assert captured["json"]["messages"][-1] == {"role": "user", "content": "hello there"}


def test_call_ollama_uses_local_host_and_placeholder_key_when_no_key(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return ok_response("local reply")

    monkeypatch.setattr(ai_client.requests, "post", fake_post)

    result = ai_client.call_ai("hello there")
    assert result == "local reply"
    assert captured["url"] == "http://localhost:11434/v1/chat/completions"
    # Local ollama ignores the key, but the OpenAI-compatible layer's
    # client libraries generally require *some* non-empty string.
    assert captured["headers"]["Authorization"] == "Bearer ollama"


def test_call_ollama_respects_explicit_host_override(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_API_KEY", "fake-ollama-key")
    monkeypatch.setenv("OLLAMA_HOST", "http://my-ollama-box:11434")

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        return ok_response()

    monkeypatch.setattr(ai_client.requests, "post", fake_post)
    ai_client.call_ai("hello")
    assert captured["url"] == "http://my-ollama-box:11434/v1/chat/completions"


def test_call_ollama_respects_model_override(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_API_KEY", "fake-ollama-key")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.1")

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return ok_response()

    monkeypatch.setattr(ai_client.requests, "post", fake_post)
    ai_client.call_ai("hello")
    assert captured["json"]["model"] == "llama3.1"


def test_call_ollama_includes_system_prompt(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_API_KEY", "fake-ollama-key")

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return ok_response()

    monkeypatch.setattr(ai_client.requests, "post", fake_post)
    ai_client.call_ai("hello", system="You are terse.")
    assert captured["json"]["messages"][0] == {"role": "system", "content": "You are terse."}
    assert captured["json"]["messages"][1] == {"role": "user", "content": "hello"}


def test_call_ollama_raises_on_401(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_API_KEY", "wrong-key")
    monkeypatch.setattr(ai_client.requests, "post", lambda *a, **k: FakeResponse(401, text="unauthorized"))
    with pytest.raises(ai_client.AIUnavailable, match="rejected the API key"):
        ai_client.call_ai("hello")


def test_call_ollama_raises_on_404_with_helpful_pull_hint(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_API_KEY", "fake-ollama-key")
    monkeypatch.setenv("OLLAMA_MODEL", "not-a-real-model")
    monkeypatch.setattr(ai_client.requests, "post", lambda *a, **k: FakeResponse(404, text="not found"))
    with pytest.raises(ai_client.AIUnavailable, match="ollama pull not-a-real-model"):
        ai_client.call_ai("hello")


def test_call_ollama_raises_on_429(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_API_KEY", "fake-ollama-key")
    monkeypatch.setattr(ai_client.requests, "post", lambda *a, **k: FakeResponse(429, text="slow down"))
    with pytest.raises(ai_client.AIUnavailable, match="rate-limited"):
        ai_client.call_ai("hello")


def test_call_ollama_raises_with_connect_hint_for_local_server(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)

    import requests as real_requests

    def raise_connection_error(*a, **k):
        raise real_requests.ConnectionError("refused")

    monkeypatch.setattr(ai_client.requests, "post", raise_connection_error)
    with pytest.raises(ai_client.AIUnavailable, match="ollama serve"):
        ai_client.call_ai("hello")


def test_call_ollama_raises_on_unexpected_response_shape(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_API_KEY", "fake-ollama-key")
    monkeypatch.setattr(ai_client.requests, "post", lambda *a, **k: FakeResponse(200, {"unexpected": "shape"}))
    with pytest.raises(ai_client.AIUnavailable, match="Unexpected Ollama response shape"):
        ai_client.call_ai("hello")


def test_unknown_provider_still_rejected(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "not-a-real-provider")
    available, provider, reason = ai_client.ai_status()
    assert available is False
    assert "ollama" in reason  # error message lists all 4 valid providers
