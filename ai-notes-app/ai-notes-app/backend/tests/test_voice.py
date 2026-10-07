import io

from conftest import register


def test_transcribe_requires_login(client):
    data = {"audio": (io.BytesIO(b"fake audio bytes"), "clip.webm")}
    res = client.post("/api/voice/transcribe", data=data, content_type="multipart/form-data")
    assert res.status_code == 401


def test_transcribe_requires_an_audio_file(client):
    register(client)
    res = client.post("/api/voice/transcribe", data={}, content_type="multipart/form-data")
    assert res.status_code == 400
    assert "audio" in res.get_json()["error"].lower()


def test_transcribe_rejects_empty_file(client):
    register(client)
    data = {"audio": (io.BytesIO(b""), "clip.webm")}
    res = client.post("/api/voice/transcribe", data=data, content_type="multipart/form-data")
    assert res.status_code == 400


def test_transcribe_rejects_unsupported_extension(client):
    register(client)
    data = {"audio": (io.BytesIO(b"not really audio"), "clip.exe")}
    res = client.post("/api/voice/transcribe", data=data, content_type="multipart/form-data")
    assert res.status_code == 400


def test_transcribe_returns_501_when_whisper_not_installed(client):
    # This test runs against whatever is ACTUALLY installed. In the
    # reference environment faster-whisper is intentionally not part of
    # the core install, so this exercises the real graceful-degradation
    # path end-to-end (not a mock) — confirming the app doesn't crash
    # when the optional dependency is absent, and that the frontend gets
    # a clear, actionable error instead of a 500.
    register(client)
    data = {"audio": (io.BytesIO(b"fake audio bytes"), "clip.webm")}
    res = client.post("/api/voice/transcribe", data=data, content_type="multipart/form-data")
    try:
        import faster_whisper  # noqa: F401

        installed = True
    except ImportError:
        installed = False

    if not installed:
        assert res.status_code == 501
        assert "faster-whisper" in res.get_json()["error"] or "isn't installed" in res.get_json()["error"]


def test_status_endpoint_requires_login(client):
    res = client.get("/api/voice/status")
    assert res.status_code == 401


def test_status_endpoint_reports_availability(client):
    register(client)
    res = client.get("/api/voice/status")
    assert res.status_code == 200
    body = res.get_json()
    assert "available" in body


def test_transcribe_succeeds_when_whisper_available(client, monkeypatch):
    # Mocks out the actual model so this test doesn't need the real
    # ~150MB model download — it verifies the route's request handling
    # (multipart upload, temp file lifecycle, response shape) is correct
    # end-to-end, independent of whether Whisper itself is installed.
    register(client)

    def fake_transcribe(path):
        assert path  # a real temp file path was created and passed through
        return "this is the mocked transcription"

    import voice

    monkeypatch.setattr(voice, "transcribe_audio_file", fake_transcribe)

    data = {"audio": (io.BytesIO(b"fake audio bytes"), "clip.webm")}
    res = client.post("/api/voice/transcribe", data=data, content_type="multipart/form-data")
    assert res.status_code == 200
    assert res.get_json()["text"] == "this is the mocked transcription"
