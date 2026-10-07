# ============================================================
# AI Notes — Day 9 — server-side speech-to-text (Whisper)
#
# Uses faster-whisper, a CPU/GPU-friendly implementation of
# OpenAI's open-source Whisper model. This runs ENTIRELY on your
# own machine — no API key, no per-request cost, no data sent to
# a third party. The trade-off: the model itself (~150MB for
# "base") has to download once on first use, and faster-whisper
# is a fairly heavy optional dependency — so it's not in the
# core requirements.txt. See requirements-voice.txt.
#
# If it isn't installed, transcribe_audio_file() raises
# TranscriptionUnavailable with a clear, actionable message
# instead of crashing the whole app — the frontend's browser-
# based dictation keeps working either way.
# ============================================================
import threading

_model = None
_model_lock = threading.Lock()

WHISPER_MODEL_SIZE = "base"  # tiny | base | small | medium | large-v3 — bigger = more accurate, slower


class TranscriptionUnavailable(Exception):
    """Raised when server-side transcription can't run (dependency missing, etc)."""


def _get_model():
    global _model
    if _model is not None:
        return _model

    with _model_lock:
        if _model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:
                raise TranscriptionUnavailable(
                    "Server-side transcription isn't installed. Run "
                    "'pip install -r requirements-voice.txt' in backend/ to enable it "
                    "(one-time ~150MB model download on first use). "
                    "Live dictation in the browser (the mic button) works without this."
                ) from e

            try:
                _model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
            except Exception as e:
                raise TranscriptionUnavailable(
                    f"Whisper model failed to load ({e}). Check your internet connection "
                    "for the first-time model download, then try again."
                ) from e

    return _model


def transcribe_audio_file(path):
    """Transcribe an audio file on disk and return the recognized text."""
    model = _get_model()
    segments, _info = model.transcribe(path)
    return " ".join(seg.text.strip() for seg in segments).strip()
