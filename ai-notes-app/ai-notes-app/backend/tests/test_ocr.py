import io

import pytest

from conftest import register

# A real, tiny, PIL-readable PNG (not just a magic-byte stub like
# test_attachments.py uses for upload validation) — OCR actually opens
# the file with Pillow, so the corrupt-image error path needs a file
# that passes the upload's magic-byte sniff but fails to decode, and
# the success path needs one that genuinely decodes.
def _real_png_bytes(text="Buy milk"):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (400, 120), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((10, 40), text, fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# Passes the extension + magic-byte checks in attachments.py (correct
# PNG header) but isn't real image data after that — good for exercising
# OCR's "file doesn't actually decode" error path.
FAKE_PNG_HEADER_ONLY = b"\x89PNG\r\n\x1a\n" + b"not real png pixel data" * 4


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


def upload_image(client, note_id, content=None, filename="handwriting.png"):
    data = {"file": (io.BytesIO(content or _real_png_bytes()), filename)}
    return client.post(f"/api/notes/{note_id}/attachments", data=data, content_type="multipart/form-data")


def upload_pdf(client, note_id):
    data = {"file": (io.BytesIO(b"%PDF-1.4\n%fake\n%%EOF"), "doc.pdf")}
    return client.post(f"/api/notes/{note_id}/attachments", data=data, content_type="multipart/form-data")


# ============================================================
# /api/ocr/status
# ============================================================
def test_ocr_status_requires_login(client):
    res = client.get("/api/ocr/status")
    assert res.status_code == 401


def test_ocr_status_reports_availability(client):
    register(client)
    res = client.get("/api/ocr/status")
    assert res.status_code == 200
    body = res.get_json()
    assert "available" in body
    if not body["available"]:
        assert "reason" in body


# ============================================================
# POST /api/attachments/<id>/ocr — auth, ownership, validation
# ============================================================
def test_run_ocr_requires_login(client):
    res = client.post("/api/attachments/1/ocr")
    assert res.status_code == 401


def test_run_ocr_404_for_missing_attachment(client):
    register(client)
    res = client.post("/api/attachments/999/ocr")
    assert res.status_code == 404


def test_run_ocr_rejects_pdf_attachments(client):
    register(client)
    note = create_note(client)
    attachment = upload_pdf(client, note["id"]).get_json()

    res = client.post(f"/api/attachments/{attachment['id']}/ocr")
    assert res.status_code == 400
    assert "image" in res.get_json()["error"].lower()


def test_run_ocr_scoped_to_owner(client):
    register(client, username="alice")
    note = create_note(client)
    attachment = upload_image(client, note["id"]).get_json()
    client.post("/api/auth/logout")

    register(client, username="bob")
    res = client.post(f"/api/attachments/{attachment['id']}/ocr")
    assert res.status_code == 404  # bob can't see or OCR alice's attachment


# ============================================================
# Attachment shape includes OCR fields from the moment it's created
# ============================================================
def test_new_attachment_starts_with_no_ocr_result(client):
    register(client)
    note = create_note(client)
    attachment = upload_image(client, note["id"]).get_json()

    assert attachment["ocrStatus"] == "none"
    assert attachment["ocrText"] is None
    assert attachment["ocrGeneratedAt"] is None


# ============================================================
# Mocked engine — exercises the route's DB read/write and response
# shape independent of whether Tesseract is actually installed,
# same reasoning as test_voice.py's fake_transcribe.
# ============================================================
def test_run_ocr_succeeds_with_mocked_engine(client, monkeypatch):
    register(client)
    note = create_note(client)
    attachment = upload_image(client, note["id"]).get_json()

    def fake_extract_text(path):
        assert path  # a real on-disk path was passed through
        return "Buy milk and eggs"

    import attachments

    monkeypatch.setattr(attachments, "extract_text", fake_extract_text)

    res = client.post(f"/api/attachments/{attachment['id']}/ocr")
    assert res.status_code == 200
    body = res.get_json()["attachment"]
    assert body["ocrStatus"] == "done"
    assert body["ocrText"] == "Buy milk and eggs"
    assert body["ocrGeneratedAt"] is not None


def test_run_ocr_mocked_empty_result_is_still_done_not_error(client, monkeypatch):
    # A photo with no legible text is a legitimate "done, found nothing"
    # result, not a failure — the frontend needs to tell these apart.
    register(client)
    note = create_note(client)
    attachment = upload_image(client, note["id"]).get_json()

    import attachments

    monkeypatch.setattr(attachments, "extract_text", lambda path: "")

    res = client.post(f"/api/attachments/{attachment['id']}/ocr")
    assert res.status_code == 200
    body = res.get_json()["attachment"]
    assert body["ocrStatus"] == "done"
    assert body["ocrText"] == ""


def test_run_ocr_reports_unavailable_engine(client, monkeypatch):
    register(client)
    note = create_note(client)
    attachment = upload_image(client, note["id"]).get_json()

    import attachments
    from ocr import OCRUnavailable

    def raise_unavailable(path):
        raise OCRUnavailable("Tesseract isn't installed on this machine.")

    monkeypatch.setattr(attachments, "extract_text", raise_unavailable)

    res = client.post(f"/api/attachments/{attachment['id']}/ocr")
    assert res.status_code == 501
    body = res.get_json()
    assert "Tesseract" in body["error"]
    assert body["attachment"]["ocrStatus"] == "unavailable"


def test_run_ocr_reports_processing_failure(client, monkeypatch):
    register(client)
    note = create_note(client)
    attachment = upload_image(client, note["id"]).get_json()

    import attachments
    from ocr import OCRFailed

    def raise_failed(path):
        raise OCRFailed("Couldn't read this image file.")

    monkeypatch.setattr(attachments, "extract_text", raise_failed)

    res = client.post(f"/api/attachments/{attachment['id']}/ocr")
    assert res.status_code == 422
    body = res.get_json()
    assert body["attachment"]["ocrStatus"] == "error"
    assert body["attachment"]["ocrText"] is None


# ============================================================
# Real end-to-end pass — runs against whatever is ACTUALLY installed.
# If Tesseract is present, this confirms the full preprocess -> OCR
# pipeline genuinely reads text out of a real generated image, not
# just that the route plumbing works. If it isn't installed, this
# confirms the app degrades gracefully (501, clear message) instead
# of crashing — mirroring test_voice.py's real-Whisper-availability test.
# ============================================================
def test_run_ocr_end_to_end_real_engine(client):
    register(client)
    note = create_note(client)
    attachment = upload_image(client, note["id"], content=_real_png_bytes("Call Alex tomorrow")).get_json()

    res = client.post(f"/api/attachments/{attachment['id']}/ocr")

    from ocr import ocr_status

    available, _ = ocr_status()
    if available:
        assert res.status_code == 200
        body = res.get_json()["attachment"]
        assert body["ocrStatus"] == "done"
        # Loose assertion on purpose — OCR accuracy on a rendered test
        # image is good but not pixel-perfect guaranteed across every
        # Tesseract version; the meaningful thing is that SOME
        # recognizable fragment of the real text came back.
        assert "alex" in body["ocrText"].lower() or "call" in body["ocrText"].lower()
    else:
        assert res.status_code == 501
        assert body_has_actionable_message(res.get_json())


def body_has_actionable_message(body):
    return "error" in body and ("tesseract" in body["error"].lower() or "install" in body["error"].lower())


def test_run_ocr_unreadable_file_returns_422_with_real_engine(client):
    # Passes upload's magic-byte sniff (correct PNG header) but isn't
    # decodable image data — only meaningful against the real engine, so
    # skip if Tesseract isn't installed (the unavailable path is already
    # covered by the mocked test above).
    from ocr import ocr_status

    available, _ = ocr_status()
    if not available:
        pytest.skip("Tesseract not installed in this environment")

    register(client)
    note = create_note(client)
    attachment = upload_image(client, note["id"], content=FAKE_PNG_HEADER_ONLY).get_json()

    res = client.post(f"/api/attachments/{attachment['id']}/ocr")
    assert res.status_code == 422
    assert res.get_json()["attachment"]["ocrStatus"] == "error"
