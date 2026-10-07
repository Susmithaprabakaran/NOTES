# ============================================================
# AI Notes — Day 9 — voice API
# One route: upload a short audio recording, get back the
# transcribed text. Requires login, like everything else here.
# ============================================================
import os
import tempfile

from flask import Blueprint, jsonify, request

from auth import login_required
from transcription import transcribe_audio_file, TranscriptionUnavailable

bp = Blueprint("voice", __name__, url_prefix="/api/voice")

# MediaRecorder in the browser most commonly produces webm/ogg; we also
# accept a few others in case the client sends something else.
ALLOWED_EXTENSIONS = {"webm", "ogg", "wav", "mp3", "m4a", "mp4"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25MB — generous for a short voice note


@bp.get("/status")
@login_required
def status():
    """Lets the frontend check whether server-side transcription is
    available before showing that option, instead of the user
    discovering it only after clicking Record."""
    try:
        from transcription import _get_model  # noqa: F401 — import check only

        import faster_whisper  # noqa: F401

        return jsonify({"available": True}), 200
    except ImportError:
        return jsonify(
            {
                "available": False,
                "reason": "faster-whisper isn't installed on the server. "
                "Live browser dictation still works.",
            }
        ), 200


@bp.post("/transcribe")
@login_required
def transcribe():
    if "audio" not in request.files:
        return jsonify({"error": "No audio file provided (expected form field 'audio')."}), 400

    file = request.files["audio"]
    if file.filename == "":
        return jsonify({"error": "Empty filename."}), 400

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "webm"
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": f"Unsupported audio format: .{ext}"}), 400

    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size == 0:
        return jsonify({"error": "Empty audio file."}), 400
    if size > MAX_UPLOAD_BYTES:
        return jsonify({"error": "Audio file too large (max 25MB)."}), 413

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
            file.save(tmp.name)
            tmp_path = tmp.name

        text = transcribe_audio_file(tmp_path)
        return jsonify({"text": text}), 200

    except TranscriptionUnavailable as e:
        return jsonify({"error": str(e)}), 501
    except Exception as e:
        return jsonify({"error": f"Transcription failed: {e}"}), 500
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
