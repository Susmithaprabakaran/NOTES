# ============================================================
# AI Notes — Day 22 — OCR engine (handwritten note capture)
#
#   Select an image attachment
#           |
#   Validate it's actually an image  -> Day 21's magic-byte check
#                                        already guarantees this by
#                                        the time a file reaches disk
#           |
#   Preprocess  -> grayscale, upscale small photos, autocontrast,
#                  denoise, binarize -- cheap steps that measurably
#                  improve Tesseract's accuracy on phone-camera
#                  photos of handwriting, whiteboards, and sticky notes
#           |
#   OCR         -> pytesseract, a thin wrapper around the Tesseract
#                  engine. Tesseract itself is a SEPARATE system
#                  binary, not something `pip install` can provide --
#                  see the README's Day 22 section for install steps
#                  per OS.
#           |
#   Extracted text returned to the caller (this module never touches
#   the database -- see attachments.py for storage, and app.js for
#   the "insert into note" step that feeds Day 12/14/15/18's existing
#   auto-summarize / auto-tag / extract-concepts / extract-tasks
#   pipeline, unchanged, on whatever text ends up in the note).
#
# Mirrors the Day 9 transcription.py pattern deliberately: if the
# system `tesseract` binary isn't installed, OCRUnavailable is raised
# with an actionable message instead of crashing, and nothing else in
# the app depends on this being set up. A missing OCR engine degrades
# the app to "images just don't get auto-transcribed" — not a 500.
# ============================================================
import threading

_checked = False
_available = False
_reason = None
_lock = threading.Lock()

# Tesseract's accuracy on handwriting/whiteboard photos improves a lot
# with a few cheap preprocessing steps below — none of it is
# handwriting-specific magic, just standard "make faint pencil/marker
# strokes look like crisp black-on-white text" cleanup.
MIN_DIMENSION = 1000  # upscale smaller photos so thin strokes don't fall below what OCR can resolve
BINARIZE_THRESHOLD = 150  # 0-255; pixels lighter than this become pure white, darker become pure black


class OCRUnavailable(Exception):
    """Raised when OCR can't run at all (Tesseract not installed / not on PATH)."""


class OCRFailed(Exception):
    """Raised when OCR is available but this specific image couldn't be processed
    (corrupt/unreadable image data — distinct from OCRUnavailable, which means
    the *engine* itself isn't set up)."""


def _check_once():
    """Lazily probes whether pytesseract AND the underlying Tesseract
    binary are both available. Runs at most once per process — later
    calls just return the cached result, same as transcription.py's
    model-loading guard."""
    global _checked, _available, _reason
    if _checked:
        return
    with _lock:
        if _checked:
            return
        try:
            import pytesseract
        except ImportError:
            _available, _reason = False, (
                "OCR isn't installed. Run 'pip install -r requirements-ocr.txt' "
                "in backend/ to add the Python side, then install the Tesseract "
                "engine itself (see the README's Day 22 section) — pytesseract "
                "is just a wrapper around that system binary, it doesn't bundle it."
            )
            _checked = True
            return

        try:
            pytesseract.get_tesseract_version()
            _available, _reason = True, None
        except Exception as e:
            _available, _reason = False, (
                "The pytesseract Python package is installed, but the Tesseract "
                f"engine itself isn't on this machine's PATH ({e}). Install it "
                "with your OS package manager — see the README's Day 22 section "
                "— then restart the server."
            )
        _checked = True


def ocr_status():
    """Returns (available, reason) without raising — lets the frontend
    check once (GET /api/ocr/status) before showing any OCR-related UI,
    same pattern as voice.py's /api/voice/status for Day 9's Whisper."""
    _check_once()
    return _available, _reason


def preprocess_image(path):
    """Load an image from disk and return a cleaned-up PIL Image ready
    for OCR. Assumes the file is genuinely a readable image (Day 21's
    magic-byte sniff already confirmed that before it was ever saved) —
    a corrupt file still raises, but that's OCRFailed territory, not
    something this function tries to paper over."""
    from PIL import Image, ImageOps, ImageFilter

    img = Image.open(path)
    img = ImageOps.exif_transpose(img)  # respect phone-camera rotation metadata
    img = img.convert("L")  # grayscale — color is noise for text recognition

    if max(img.size) < MIN_DIMENSION:
        scale = MIN_DIMENSION / max(img.size)
        new_size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
        img = img.resize(new_size, Image.LANCZOS)

    img = ImageOps.autocontrast(img, cutoff=1)  # stretch faint pencil marks to the full black/white range
    img = img.filter(ImageFilter.MedianFilter(size=3))  # denoise paper texture / JPEG artifacts
    img = img.point(lambda p: 255 if p > BINARIZE_THRESHOLD else 0)  # binarize: crisp text, dropped background

    return img


def extract_text(path):
    """Run the full preprocess -> OCR pipeline on an image file on disk
    and return the extracted text. An image with no legible text comes
    back as an empty string (a valid result — "nothing found" — not an
    error); only a genuinely unreadable file or a missing engine raises."""
    available, reason = ocr_status()
    if not available:
        raise OCRUnavailable(reason)

    import pytesseract
    from PIL import UnidentifiedImageError

    try:
        image = preprocess_image(path)
    except (UnidentifiedImageError, OSError) as e:
        raise OCRFailed(f"Couldn't read this image file ({e}).") from e

    # psm 6: "assume a single uniform block of text" — a good default for
    # a photographed page, sticky note, or whiteboard rather than a
    # scattered UI screenshot with multiple unrelated text regions.
    try:
        text = pytesseract.image_to_string(image, config="--psm 6")
    except pytesseract.TesseractError as e:
        raise OCRFailed(f"Tesseract couldn't process this image ({e}).") from e

    return text.strip()
