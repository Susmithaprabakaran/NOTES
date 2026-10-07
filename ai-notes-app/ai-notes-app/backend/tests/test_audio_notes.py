# ============================================================
# Day 27 — audio notes: upload as an attachment + transcription
# ============================================================
import io

import pytest

from conftest import register

# Minimal-but-valid header bytes for each audio format — enough to pass
# the extension + magic-byte sniff in attachments.py, same "correct at
# the header level, not a real playable file" approach test_attachments.py
# uses for PDF/PNG/etc.
FAKE_WEBM = b"\x1a\x45\xdf\xa3" + b"0" * 32  # EBML header
FAKE_OGG = b"OggS" + b"0" * 32
FAKE_WAV = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"0" * 16
FAKE_M4A = b"\x00\x00\x00\x18" + b"ftyp" + b"M4A " + b"0" * 16  # ftyp at offset 4
FAKE_MP3 = b"\xff\xfb\x90\x00" + b"0" * 32  # no fixed signature required, any bytes pass
FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


@pytest.fixture(autouse=True)
def isolated_upload_folder(tmp_path, monkeypatch):
    import attachments

    folder = tmp_path / "uploads"
    folder.mkdir()
    monkeypatch.setattr(attachments, "upload_folder", lambda: str(folder))
    return str(folder)


def create_note(client, title="", text=""):
    res = client.post("/api/notes", json={"title": title, "text": text})
    return res.get_json()


def upload(client, note_id, content, filename, extra_fields=None):
    data = {"file": (io.BytesIO(content), filename)}
    if extra_fields:
        data.update(extra_fields)
    return client.post(f"/api/notes/{note_id}/attachments", data=data, content_type="multipart/form-data")


# ============================================================
# Upload: audio is accepted like any other attachment kind
# ============================================================
@pytest.mark.parametrize(
    "content,filename",
    [
        (FAKE_WEBM, "note.webm"),
        (FAKE_OGG, "note.ogg"),
        (FAKE_WAV, "note.wav"),
        (FAKE_M4A, "note.m4a"),
        (FAKE_MP3, "note.mp3"),
    ],
)
def test_upload_each_audio_format(client, content, filename):
    register(client)
    note = create_note(client)
    res = upload(client, note["id"], content, filename)
    assert res.status_code == 201
    body = res.get_json()
    assert body["kind"] == "audio"
    assert body["originalName"] == filename


def test_audio_upload_rejects_content_that_doesnt_match_extension(client):
    register(client)
    note = create_note(client)
    # PNG bytes with a .webm extension: passes the extension allow-list,
    # fails the magic-byte sniff.
    res = upload(client, note["id"], FAKE_PNG, "fake.webm")
    assert res.status_code == 400


def test_audio_upload_with_duration(client):
    register(client)
    note = create_note(client)
    res = upload(client, note["id"], FAKE_WEBM, "note.webm", extra_fields={"duration": "12.5"})
    assert res.status_code == 201
    assert res.get_json()["durationSeconds"] == 12.5


def test_audio_upload_without_duration_is_null(client):
    register(client)
    note = create_note(client)
    res = upload(client, note["id"], FAKE_WEBM, "note.webm")
    assert res.status_code == 201
    assert res.get_json()["durationSeconds"] is None


def test_image_attachment_has_null_duration(client):
    register(client)
    note = create_note(client)
    res = upload(client, note["id"], FAKE_PNG, "photo.png")
    assert res.status_code == 201
    assert res.get_json()["durationSeconds"] is None


def test_new_audio_attachment_starts_with_no_transcript(client):
    register(client)
    note = create_note(client)
    body = upload(client, note["id"], FAKE_WEBM, "note.webm").get_json()
    assert body["transcriptStatus"] == "none"
    assert body["transcriptText"] is None
    assert body["transcriptGeneratedAt"] is None


# ============================================================
# POST /api/attachments/<id>/transcribe — auth, ownership, validation
# ============================================================
def test_transcribe_requires_login(client):
    res = client.post("/api/attachments/1/transcribe")
    assert res.status_code == 401


def test_transcribe_404_for_missing_attachment(client):
    register(client)
    res = client.post("/api/attachments/999/transcribe")
    assert res.status_code == 404


def test_transcribe_rejects_non_audio_attachments(client):
    register(client)
    note = create_note(client)
    attachment = upload(client, note["id"], FAKE_PNG, "photo.png").get_json()

    res = client.post(f"/api/attachments/{attachment['id']}/transcribe")
    assert res.status_code == 400
    assert "audio" in res.get_json()["error"].lower()


def test_transcribe_scoped_to_owner(client):
    register(client, username="alice")
    note = create_note(client)
    attachment = upload(client, note["id"], FAKE_WEBM, "note.webm").get_json()
    client.post("/api/auth/logout")

    register(client, username="bob")
    res = client.post(f"/api/attachments/{attachment['id']}/transcribe")
    assert res.status_code == 404  # bob can't see or transcribe alice's recording


# ============================================================
# Mocked engine — exercises the route's DB read/write and response
# shape independent of whether faster-whisper is actually installed,
# same approach test_voice.py and test_ocr.py already use.
# ============================================================
def test_transcribe_succeeds_with_mocked_engine(client, monkeypatch):
    register(client)
    note = create_note(client)
    attachment = upload(client, note["id"], FAKE_WEBM, "note.webm").get_json()

    def fake_transcribe(path):
        assert path  # a real on-disk path was passed through
        return "Buy milk and eggs"

    import attachments

    monkeypatch.setattr(attachments, "transcribe_audio_file", fake_transcribe)

    res = client.post(f"/api/attachments/{attachment['id']}/transcribe")
    assert res.status_code == 200
    body = res.get_json()["attachment"]
    assert body["transcriptStatus"] == "done"
    assert body["transcriptText"] == "Buy milk and eggs"
    assert body["transcriptGeneratedAt"] is not None

    # persisted, not just returned once
    fetched = client.get(f"/api/notes/{note['id']}/attachments").get_json()
    assert fetched[0]["transcriptText"] == "Buy milk and eggs"


def test_transcribe_unavailable_engine_marks_attachment(client, monkeypatch):
    register(client)
    note = create_note(client)
    attachment = upload(client, note["id"], FAKE_WEBM, "note.webm").get_json()

    import attachments

    def fake_unavailable(path):
        raise attachments.TranscriptionUnavailable("faster-whisper isn't installed.")

    monkeypatch.setattr(attachments, "transcribe_audio_file", fake_unavailable)

    res = client.post(f"/api/attachments/{attachment['id']}/transcribe")
    assert res.status_code == 501
    body = res.get_json()["attachment"]
    assert body["transcriptStatus"] == "unavailable"
    assert body["transcriptText"] is None


def test_transcribe_engine_error_marks_attachment(client, monkeypatch):
    register(client)
    note = create_note(client)
    attachment = upload(client, note["id"], FAKE_WEBM, "note.webm").get_json()

    import attachments

    def fake_error(path):
        raise RuntimeError("corrupt audio stream")

    monkeypatch.setattr(attachments, "transcribe_audio_file", fake_error)

    res = client.post(f"/api/attachments/{attachment['id']}/transcribe")
    assert res.status_code == 422
    body = res.get_json()["attachment"]
    assert body["transcriptStatus"] == "error"
    assert "corrupt audio stream" in body["transcriptError"]


def test_deleting_audio_attachment_removes_file_from_disk(client, isolated_upload_folder):
    import os

    register(client)
    note = create_note(client)
    attachment = upload(client, note["id"], FAKE_WEBM, "note.webm").get_json()

    files_before = os.listdir(isolated_upload_folder)
    assert len(files_before) == 1

    res = client.delete(f"/api/attachments/{attachment['id']}")
    assert res.status_code == 200
    assert os.listdir(isolated_upload_folder) == []
