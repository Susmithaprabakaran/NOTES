import io
import os

import pytest

from conftest import register

# Minimal-but-valid bytes for each format, enough to pass both the
# extension check and the magic-byte sniff in attachments.py — these
# aren't full valid files (a real PDF viewer would choke on this PDF),
# just correct enough at the header level, which is all the backend
# actually inspects.
FAKE_PDF = b"%PDF-1.4\n%fake pdf content for testing\n%%EOF"
FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
FAKE_JPEG = b"\xff\xd8\xff" + b"0" * 32
FAKE_GIF = b"GIF89a" + b"0" * 32
FAKE_WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"0" * 16
FAKE_EXE = b"MZ\x90\x00\x03\x00\x00\x00fake exe bytes not a real pe file"


@pytest.fixture(autouse=True)
def isolated_upload_folder(tmp_path, monkeypatch):
    """Every test gets its own throwaway upload directory instead of
    writing into backend/uploads — keeps the repo clean and tests
    independent of each other."""
    import attachments

    folder = tmp_path / "uploads"
    folder.mkdir()
    monkeypatch.setattr(attachments, "upload_folder", lambda: str(folder))
    return str(folder)


def create_note(client, title="", text=""):
    res = client.post("/api/notes", json={"title": title, "text": text})
    return res.get_json()


def upload(client, note_id, content, filename, field="file"):
    data = {field: (io.BytesIO(content), filename)}
    return client.post(f"/api/notes/{note_id}/attachments", data=data, content_type="multipart/form-data")


# ============================================================
# Upload
# ============================================================
def test_upload_requires_login(client):
    res = upload(client, 1, FAKE_PDF, "doc.pdf")
    assert res.status_code == 401


def test_upload_404_for_missing_note(client):
    register(client)
    res = upload(client, 999, FAKE_PDF, "doc.pdf")
    assert res.status_code == 404


def test_upload_requires_a_file(client):
    register(client)
    note = create_note(client)
    res = client.post(f"/api/notes/{note['id']}/attachments", data={}, content_type="multipart/form-data")
    assert res.status_code == 400


def test_upload_rejects_empty_file(client):
    register(client)
    note = create_note(client)
    res = upload(client, note["id"], b"", "doc.pdf")
    assert res.status_code == 400


def test_upload_rejects_disallowed_extension(client):
    register(client)
    note = create_note(client)
    res = upload(client, note["id"], FAKE_EXE, "virus.exe")
    assert res.status_code == 400
    assert "Unsupported file type" in res.get_json()["error"]


def test_upload_rejects_content_that_doesnt_match_extension(client):
    """The core 'Validate file' step from the spec: a file renamed to
    look like an allowed type but whose actual bytes don't match should
    be rejected, not silently accepted because the extension looked fine."""
    register(client)
    note = create_note(client)
    res = upload(client, note["id"], FAKE_EXE, "totally-a-photo.png")
    assert res.status_code == 400
    assert "doesn't look like a valid" in res.get_json()["error"]


def test_upload_rejects_oversized_file(client, monkeypatch):
    import attachments

    monkeypatch.setattr(attachments, "MAX_UPLOAD_BYTES", 10)  # tiny, for the test
    register(client)
    note = create_note(client)
    res = upload(client, note["id"], FAKE_PDF, "doc.pdf")  # FAKE_PDF is well over 10 bytes
    assert res.status_code == 413


@pytest.mark.parametrize(
    "content,filename,expected_kind",
    [
        (FAKE_PDF, "report.pdf", "pdf"),
        (FAKE_PNG, "photo.png", "image"),
        (FAKE_JPEG, "photo.jpg", "image"),
        (FAKE_JPEG, "photo.jpeg", "image"),
        (FAKE_GIF, "clip.gif", "image"),
        (FAKE_WEBP, "photo.webp", "image"),
    ],
)
def test_upload_accepts_each_supported_type(client, content, filename, expected_kind):
    register(client)
    note = create_note(client)
    res = upload(client, note["id"], content, filename)
    assert res.status_code == 201
    body = res.get_json()
    assert body["kind"] == expected_kind
    assert body["originalName"] == filename
    assert body["noteId"] == note["id"]
    assert body["url"] == f"/api/attachments/{body['id']}/file"


def test_upload_saves_file_to_disk_with_a_generated_name(client, isolated_upload_folder):
    register(client)
    note = create_note(client)
    res = upload(client, note["id"], FAKE_PDF, "../../etc/passwd.pdf")
    assert res.status_code == 201
    body = res.get_json()

    # The original (attempted path-traversal) name is preserved only as
    # display text — never used as the actual on-disk filename.
    assert body["originalName"] == "../../etc/passwd.pdf"

    saved_files = os.listdir(isolated_upload_folder)
    assert len(saved_files) == 1
    assert saved_files[0] != "../../etc/passwd.pdf"
    assert saved_files[0].endswith(".pdf")


# ============================================================
# List
# ============================================================
def test_list_requires_login(client):
    res = client.get("/api/notes/1/attachments")
    assert res.status_code == 401


def test_list_404_for_missing_note(client):
    register(client)
    res = client.get("/api/notes/999/attachments")
    assert res.status_code == 404


def test_list_returns_attachments_for_a_note(client):
    register(client)
    note = create_note(client)
    upload(client, note["id"], FAKE_PDF, "a.pdf")
    upload(client, note["id"], FAKE_PNG, "b.png")

    res = client.get(f"/api/notes/{note['id']}/attachments")
    assert res.status_code == 200
    body = res.get_json()
    assert len(body) == 2
    assert {a["originalName"] for a in body} == {"a.pdf", "b.png"}


def test_list_empty_for_note_with_no_attachments(client):
    register(client)
    note = create_note(client)
    res = client.get(f"/api/notes/{note['id']}/attachments")
    assert res.get_json() == []


# ============================================================
# Download / serve file
# ============================================================
def test_get_file_requires_login(client):
    res = client.get("/api/attachments/1/file")
    assert res.status_code == 401


def test_get_file_404_for_missing_attachment(client):
    register(client)
    res = client.get("/api/attachments/999/file")
    assert res.status_code == 404


def test_get_file_returns_the_uploaded_bytes(client):
    register(client)
    note = create_note(client)
    uploaded = upload(client, note["id"], FAKE_PDF, "doc.pdf").get_json()

    res = client.get(f"/api/attachments/{uploaded['id']}/file")
    assert res.status_code == 200
    assert res.data == FAKE_PDF
    assert res.mimetype == "application/pdf"


def test_get_file_images_render_inline_by_default(client):
    register(client)
    note = create_note(client)
    uploaded = upload(client, note["id"], FAKE_PNG, "photo.png").get_json()

    res = client.get(f"/api/attachments/{uploaded['id']}/file")
    disposition = res.headers.get("Content-Disposition", "")
    assert "attachment" not in disposition


def test_get_file_images_force_download_with_query_param(client):
    register(client)
    note = create_note(client)
    uploaded = upload(client, note["id"], FAKE_PNG, "photo.png").get_json()

    res = client.get(f"/api/attachments/{uploaded['id']}/file?download=1")
    disposition = res.headers.get("Content-Disposition", "")
    assert "attachment" in disposition


def test_get_file_pdfs_render_inline_by_default_too(client):
    """Browsers have a native PDF viewer, so — like images — a PDF
    should open/preview by default; only an explicit ?download=1
    forces a save-to-disk prompt."""
    register(client)
    note = create_note(client)
    uploaded = upload(client, note["id"], FAKE_PDF, "doc.pdf").get_json()

    res = client.get(f"/api/attachments/{uploaded['id']}/file")
    disposition = res.headers.get("Content-Disposition", "")
    assert "attachment" not in disposition


def test_get_file_pdfs_force_download_with_query_param(client):
    register(client)
    note = create_note(client)
    uploaded = upload(client, note["id"], FAKE_PDF, "doc.pdf").get_json()

    res = client.get(f"/api/attachments/{uploaded['id']}/file?download=1")
    disposition = res.headers.get("Content-Disposition", "")
    assert "attachment" in disposition


# ============================================================
# Delete
# ============================================================
def test_delete_requires_login(client):
    res = client.delete("/api/attachments/1")
    assert res.status_code == 401


def test_delete_404_for_missing_attachment(client):
    register(client)
    res = client.delete("/api/attachments/999")
    assert res.status_code == 404


def test_delete_removes_db_row_and_file(client, isolated_upload_folder):
    register(client)
    note = create_note(client)
    uploaded = upload(client, note["id"], FAKE_PDF, "doc.pdf").get_json()
    assert len(os.listdir(isolated_upload_folder)) == 1

    res = client.delete(f"/api/attachments/{uploaded['id']}")
    assert res.status_code == 200

    assert os.listdir(isolated_upload_folder) == []
    assert client.get(f"/api/attachments/{uploaded['id']}/file").status_code == 404
    assert client.get(f"/api/notes/{note['id']}/attachments").get_json() == []


# ============================================================
# Ownership / scoping
# ============================================================
def test_users_cannot_upload_to_others_notes(client):
    register(client, username="owner")
    note = create_note(client)
    client.post("/api/auth/logout")

    register(client, username="intruder")
    res = upload(client, note["id"], FAKE_PDF, "doc.pdf")
    assert res.status_code == 404


def test_users_cannot_see_others_attachments(client):
    register(client, username="owner")
    note = create_note(client)
    uploaded = upload(client, note["id"], FAKE_PDF, "doc.pdf").get_json()
    client.post("/api/auth/logout")

    register(client, username="intruder")
    assert client.get(f"/api/attachments/{uploaded['id']}/file").status_code == 404
    assert client.get(f"/api/notes/{note['id']}/attachments").status_code == 404


def test_users_cannot_delete_others_attachments(client):
    register(client, username="owner")
    note = create_note(client)
    uploaded = upload(client, note["id"], FAKE_PDF, "doc.pdf").get_json()
    client.post("/api/auth/logout")

    register(client, username="intruder")
    res = client.delete(f"/api/attachments/{uploaded['id']}")
    assert res.status_code == 404


# ============================================================
# Cascade delete (note -> attachments)
# ============================================================
def test_deleting_a_note_deletes_its_attachments_row(client):
    register(client)
    note = create_note(client)
    uploaded = upload(client, note["id"], FAKE_PDF, "doc.pdf").get_json()

    client.delete(f"/api/notes/{note['id']}")

    # The DB row is gone (ON DELETE CASCADE) — confirmed indirectly via
    # the API, since the attachment can no longer be fetched.
    res = client.get(f"/api/attachments/{uploaded['id']}/file")
    assert res.status_code == 404
