# ============================================================
# AI Notes — Day 21 — file attachments (PDF, images)
#
#   User creates/opens a note
#           |
#   Click "Attach File"
#           |
#   Select PDF / Image
#           |
#   Frontend uploads file         -> multipart/form-data POST
#           |
#   Flask backend receives file   -> request.files["file"]
#           |
#   Validate file                 -> extension + size + magic-byte sniff
#           |
#   Save file                     -> disk, under a server-generated name
#           |
#   Store file info in PostgreSQL -> attachments table
#           |
#   Display attachment in the note -> GET .../attachments, rendered as
#                                      an image thumbnail or a PDF chip
#
# The uploaded file's ORIGINAL filename is never trusted as the on-disk
# name — it's only kept as display text. The on-disk name is always
# server-generated (uuid4 + a validated extension), which rules out path
# traversal ("../../etc/passwd") and collisions between two people's
# "resume.pdf" by construction, not by sanitizing user input and hoping.
# ============================================================
import os
import uuid

from flask import Blueprint, current_app, jsonify, request, send_from_directory

from auth import login_required, current_user_id
from db import get_db
from ocr import extract_text, ocr_status, OCRUnavailable, OCRFailed
from transcription import transcribe_audio_file, TranscriptionUnavailable

bp = Blueprint("attachments", __name__, url_prefix="/api")

# Extension -> (kind, expected MIME type). Deliberately a small allow-list
# (not a block-list) — anything not named here is rejected, which is the
# safer default for a file upload feature.
ALLOWED_EXTENSIONS = {
    "pdf": ("pdf", "application/pdf"),
    "png": ("image", "image/png"),
    "jpg": ("image", "image/jpeg"),
    "jpeg": ("image", "image/jpeg"),
    "gif": ("image", "image/gif"),
    "webp": ("image", "image/webp"),
    # Day 27 — audio notes. Same upload/storage/delete pipeline as every
    # other attachment kind above; "audio" just gets its own player UI
    # and an optional transcribe pass (see run_transcription() below)
    # instead of OCR.
    "webm": ("audio", "audio/webm"),
    "ogg": ("audio", "audio/ogg"),
    "wav": ("audio", "audio/wav"),
    "mp3": ("audio", "audio/mpeg"),
    "m4a": ("audio", "audio/mp4"),
}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25MB — generous for a scanned PDF, a photo, or a several-minute voice note

# Magic-byte signatures for the same file types. Checked in addition to
# the extension, not instead of it — belt and suspenders. A renamed
# "virus.exe" saved as "photo.png" will pass the extension check but
# fail this one, since its actual bytes don't start with a PNG header.
MAGIC_SIGNATURES = {
    "pdf": [b"%PDF-"],
    "png": [b"\x89PNG\r\n\x1a\n"],
    "jpg": [b"\xff\xd8\xff"],
    "jpeg": [b"\xff\xd8\xff"],
    "gif": [b"GIF87a", b"GIF89a"],
    "webp": [b"RIFF"],  # full check (bytes 8-12 == b"WEBP") done in code below
    # Audio: webm/ogg/wav/m4a all have a real container signature we can
    # check the same way as the formats above. mp3 is the one exception —
    # plenty of legitimate mp3s have no ID3 tag and start directly with a
    # frame-sync byte pattern that varies with bitrate/sample rate, so
    # there's no single fixed magic number to check; it's allow-listed by
    # extension + MIME only, same trust level browsers themselves use.
    "webm": [b"\x1a\x45\xdf\xa3"],  # EBML header (also matches .mkv, but we only accept the .webm extension)
    "ogg": [b"OggS"],
    "wav": [b"RIFF"],  # full check (bytes 8-12 == b"WAVE") done in code below
    "m4a": [b"ftyp"],  # checked at offset 4, not 0 — see _sniff_matches
}


def upload_folder():
    """Resolves the directory attachments are stored in. Configurable via
    the UPLOAD_FOLDER env var (handy for pointing at a persistent volume
    in production); defaults to backend/uploads/ next to this file."""
    configured = current_app.config.get("UPLOAD_FOLDER") or os.environ.get("UPLOAD_FOLDER")
    folder = configured or os.path.join(os.path.dirname(__file__), "uploads")
    os.makedirs(folder, exist_ok=True)
    return folder


def _extension_of(filename):
    if not filename or "." not in filename:
        return None
    return filename.rsplit(".", 1)[-1].lower()


def _sniff_matches(head_bytes, ext):
    """Checks the first bytes of the upload against the expected magic
    number for `ext`. WEBP and WAV get a slightly deeper check since their
    first 4 bytes (a generic RIFF container header) are shared with
    several unrelated formats; M4A's signature sits at a 4-byte offset
    rather than the very start of the file. mp3 has no fixed signature to
    check at all (see the comment on MAGIC_SIGNATURES above) so it always
    passes this step — its extension + MIME allow-listing is the check.
    """
    if ext == "mp3":
        return True
    signatures = MAGIC_SIGNATURES.get(ext)
    if not signatures:
        return False
    if ext == "m4a":
        return head_bytes[4:8] == b"ftyp"
    if not any(head_bytes.startswith(sig) for sig in signatures):
        return False
    if ext == "webp":
        return head_bytes[8:12] == b"WEBP"
    if ext == "wav":
        return head_bytes[8:12] == b"WAVE"
    return True


def serialize_attachment(row):
    return {
        "id": row["id"],
        "noteId": row["note_id"],
        "originalName": row["original_name"],
        "mimeType": row["mime_type"],
        "kind": row["kind"],
        "sizeBytes": row["size_bytes"],
        "createdAt": row["created_at"].isoformat(),
        "url": f"/api/attachments/{row['id']}/file",
        # Day 22 — OCR. ocrText is None until an OCR pass has actually run
        # (status 'none'); once it runs, ocrText is always a string (possibly
        # empty — "ran, found nothing legible" is different from "never ran").
        "ocrStatus": row["ocr_status"],
        "ocrText": row["ocr_text"],
        "ocrError": row["ocr_error"],
        "ocrGeneratedAt": row["ocr_generated_at"].isoformat() if row["ocr_generated_at"] else None,
        # Day 27 — audio notes. durationSeconds is None for non-audio
        # attachments (and for audio ones uploaded before duration capture
        # existed). transcript* mirrors the ocr* fields above exactly,
        # same "'none' until it's actually run" semantics.
        "durationSeconds": float(row["duration_seconds"]) if row["duration_seconds"] is not None else None,
        "transcriptStatus": row["transcript_status"],
        "transcriptText": row["transcript_text"],
        "transcriptError": row["transcript_error"],
        "transcriptGeneratedAt": row["transcript_generated_at"].isoformat() if row["transcript_generated_at"] else None,
    }


def _get_note_or_404(note_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT id FROM notes WHERE id = %s AND user_id = %s", (note_id, current_user_id()))
        return cur.fetchone()


@bp.get("/notes/<int:note_id>/attachments")
@login_required
def list_attachments(note_id):
    if not _get_note_or_404(note_id):
        return jsonify({"error": "Note not found"}), 404

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM attachments WHERE note_id = %s AND user_id = %s ORDER BY created_at",
            (note_id, current_user_id()),
        )
        rows = cur.fetchall()
    return jsonify([serialize_attachment(r) for r in rows]), 200


@bp.post("/notes/<int:note_id>/attachments")
@login_required
def upload_attachment(note_id):
    if not _get_note_or_404(note_id):
        return jsonify({"error": "Note not found"}), 404

    if "file" not in request.files:
        return jsonify({"error": "No file provided (expected form field 'file')."}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Empty filename."}), 400

    ext = _extension_of(file.filename)
    if ext not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted({e for e in ALLOWED_EXTENSIONS}))
        return jsonify({"error": f"Unsupported file type '.{ext or '?'}'. Allowed: {allowed}."}), 400

    # ---- validate: size ----
    file.stream.seek(0, os.SEEK_END)
    size = file.stream.tell()
    file.stream.seek(0)
    if size == 0:
        return jsonify({"error": "Empty file."}), 400
    if size > MAX_UPLOAD_BYTES:
        max_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        return jsonify({"error": f"File too large (max {max_mb}MB)."}), 413

    # ---- validate: actual file content, not just its name ----
    head = file.stream.read(16)
    file.stream.seek(0)
    if not _sniff_matches(head, ext):
        return (
            jsonify(
                {
                    "error": (
                        f"This file doesn't look like a valid .{ext} file "
                        "(its content doesn't match its extension)."
                    )
                }
            ),
            400,
        )

    kind, canonical_mime = ALLOWED_EXTENSIONS[ext]

    # Day 27 — optional client-supplied duration, for audio recordings
    # (the browser knows exactly how long a MediaRecorder capture ran;
    # re-deriving that server-side would mean decoding the audio just for
    # a display number, so we trust it — worst case a wrong duration
    # shown next to a play button, not a security issue).
    duration_seconds = None
    if kind == "audio":
        raw_duration = request.form.get("duration")
        if raw_duration:
            try:
                duration_seconds = max(0.0, float(raw_duration))
            except (TypeError, ValueError):
                duration_seconds = None

    # ---- save: server-generated filename, never the client's ----
    on_disk_name = f"{uuid.uuid4().hex}.{ext}"
    dest_path = os.path.join(upload_folder(), on_disk_name)
    file.save(dest_path)

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO attachments (user_id, note_id, filename, original_name, mime_type, kind, size_bytes, duration_seconds)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (
                current_user_id(),
                note_id,
                on_disk_name,
                file.filename[:255],
                canonical_mime,
                kind,
                size,
                duration_seconds,
            ),
        )
        row = cur.fetchone()
    db.commit()

    return jsonify(serialize_attachment(row)), 201


@bp.get("/attachments/count")
@login_required
def attachments_count():
    """Day 23 — total attachment count for the current user, used by the
    new dashboard's stat cards. Deliberately a single COUNT(*) rather than
    pulling every row (and every file) down to the browser just to get a
    number."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS c FROM attachments WHERE user_id = %s", (current_user_id(),))
        row = cur.fetchone()
    return jsonify({"count": row["c"] if row else 0}), 200


@bp.get("/attachments/<int:attachment_id>/file")
@login_required
def get_attachment_file(attachment_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM attachments WHERE id = %s AND user_id = %s", (attachment_id, current_user_id())
        )
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "Attachment not found"}), 404

    # Default: render inline. Modern browsers have a built-in PDF viewer,
    # so a PDF attachment behaves the same way an image does — click it,
    # it opens/previews. Only an explicit ?download=1 (the "Download"
    # button, not the click-to-preview path) forces a save-to-disk
    # prompt with the original filename attached.
    as_attachment = request.args.get("download") == "1"
    return send_from_directory(
        upload_folder(),
        row["filename"],
        mimetype=row["mime_type"],
        as_attachment=as_attachment,
        download_name=row["original_name"] if as_attachment else None,
    )


@bp.delete("/attachments/<int:attachment_id>")
@login_required
def delete_attachment(attachment_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM attachments WHERE id = %s AND user_id = %s", (attachment_id, current_user_id())
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Attachment not found"}), 404

        cur.execute("DELETE FROM attachments WHERE id = %s AND user_id = %s", (attachment_id, current_user_id()))
    db.commit()

    file_path = os.path.join(upload_folder(), row["filename"])
    if os.path.exists(file_path):
        os.remove(file_path)

    return jsonify({"deleted": True}), 200


# ============================================================
# Day 22 — OCR for handwritten notes
#
#   Select an image attachment (already uploaded + validated by the
#   Day 21 flow above)
#           |
#   Load it from disk, preprocess (grayscale, upscale, autocontrast,
#   denoise, binarize — see ocr.py)
#           |
#   Run Tesseract OCR
#           |
#   Store the extracted text on the attachment row
#           |
#   Frontend shows it as a preview with "Insert into note" — clicking
#   that appends it to the note's text and lets the note's EXISTING
#   auto-summarize / auto-tag / auto-entities / auto-concepts / task-
#   extraction pipeline (Days 12-18) pick it up completely unchanged,
#   rather than this endpoint reaching into the note directly.
# ============================================================
@bp.get("/ocr/status")
@login_required
def ocr_status_route():
    """Lets the frontend check once (at boot, and again after upload)
    whether OCR can run at all, instead of discovering a missing
    Tesseract install only after every single image upload fails."""
    available, reason = ocr_status()
    body = {"available": available}
    if not available:
        body["reason"] = reason
    return jsonify(body), 200


@bp.post("/attachments/<int:attachment_id>/ocr")
@login_required
def run_ocr(attachment_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM attachments WHERE id = %s AND user_id = %s", (attachment_id, current_user_id())
        )
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "Attachment not found"}), 404

    if row["kind"] != "image":
        return jsonify({"error": "OCR only runs on image attachments, not PDFs."}), 400

    file_path = os.path.join(upload_folder(), row["filename"])
    if not os.path.exists(file_path):
        return jsonify({"error": "The uploaded file is missing from disk."}), 404

    try:
        text = extract_text(file_path)
    except OCRUnavailable as e:
        with db.cursor() as cur:
            cur.execute(
                """UPDATE attachments SET ocr_status = 'unavailable', ocr_error = %s,
                   ocr_text = NULL, ocr_generated_at = now()
                   WHERE id = %s AND user_id = %s RETURNING *""",
                (str(e), attachment_id, current_user_id()),
            )
            row = cur.fetchone()
        db.commit()
        return jsonify({"error": str(e), "attachment": serialize_attachment(row)}), 501
    except OCRFailed as e:
        with db.cursor() as cur:
            cur.execute(
                """UPDATE attachments SET ocr_status = 'error', ocr_error = %s,
                   ocr_text = NULL, ocr_generated_at = now()
                   WHERE id = %s AND user_id = %s RETURNING *""",
                (str(e), attachment_id, current_user_id()),
            )
            row = cur.fetchone()
        db.commit()
        return jsonify({"error": str(e), "attachment": serialize_attachment(row)}), 422

    with db.cursor() as cur:
        cur.execute(
            """UPDATE attachments SET ocr_status = 'done', ocr_text = %s, ocr_error = NULL,
               ocr_generated_at = now() WHERE id = %s AND user_id = %s RETURNING *""",
            (text, attachment_id, current_user_id()),
        )
        row = cur.fetchone()
    db.commit()

    return jsonify({"attachment": serialize_attachment(row)}), 200


# ============================================================
# Day 27 — audio notes: server-side transcription
#
#   Record (or attach) an audio file — same upload pipeline as every
#   other attachment kind, "audio" is just another entry in
#   ALLOWED_EXTENSIONS above
#           |
#   Optional transcribe pass, triggered the same way OCR is: either
#   automatically right after upload (frontend's call) or via the
#   retry/"Transcribe" button
#           |
#   Reuses transcribe_audio_file() from transcription.py — the exact
#   same faster-whisper pipeline Day 9's live "Record & transcribe"
#   button already used, just pointed at a saved file on disk instead
#   of a throwaway upload, and persisted on the attachment row instead
#   of being inserted straight into the note text
#           |
#   Frontend shows it as a preview with "Insert into note", same
#   pattern as OCR results — the person decides whether a given
#   recording's words belong in the note, nothing is auto-inserted
#
#   Availability is checked via the existing GET /api/voice/status
#   route (voice.py, Day 9) — reused as-is rather than duplicated here.
# ============================================================
@bp.post("/attachments/<int:attachment_id>/transcribe")
@login_required
def run_transcription(attachment_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM attachments WHERE id = %s AND user_id = %s", (attachment_id, current_user_id())
        )
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "Attachment not found"}), 404

    if row["kind"] != "audio":
        return jsonify({"error": "Transcription only runs on audio attachments."}), 400

    file_path = os.path.join(upload_folder(), row["filename"])
    if not os.path.exists(file_path):
        return jsonify({"error": "The uploaded file is missing from disk."}), 404

    try:
        text = transcribe_audio_file(file_path)
    except TranscriptionUnavailable as e:
        with db.cursor() as cur:
            cur.execute(
                """UPDATE attachments SET transcript_status = 'unavailable', transcript_error = %s,
                   transcript_text = NULL, transcript_generated_at = now()
                   WHERE id = %s AND user_id = %s RETURNING *""",
                (str(e), attachment_id, current_user_id()),
            )
            row = cur.fetchone()
        db.commit()
        return jsonify({"error": str(e), "attachment": serialize_attachment(row)}), 501
    except Exception as e:
        with db.cursor() as cur:
            cur.execute(
                """UPDATE attachments SET transcript_status = 'error', transcript_error = %s,
                   transcript_text = NULL, transcript_generated_at = now()
                   WHERE id = %s AND user_id = %s RETURNING *""",
                (str(e), attachment_id, current_user_id()),
            )
            row = cur.fetchone()
        db.commit()
        return jsonify({"error": f"Transcription failed: {e}", "attachment": serialize_attachment(row)}), 422

    with db.cursor() as cur:
        cur.execute(
            """UPDATE attachments SET transcript_status = 'done', transcript_text = %s, transcript_error = NULL,
               transcript_generated_at = now() WHERE id = %s AND user_id = %s RETURNING *""",
            (text, attachment_id, current_user_id()),
        )
        row = cur.fetchone()
    db.commit()

    return jsonify({"attachment": serialize_attachment(row)}), 200
