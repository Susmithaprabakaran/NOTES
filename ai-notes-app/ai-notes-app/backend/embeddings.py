# ============================================================
# AI Notes — Day 17 — sentence embeddings for semantic search
#
# Uses sentence-transformers' all-MiniLM-L6-v2 model to turn note text
# and search queries into 384-dimensional vectors, entirely locally —
# no paid embedding API, no per-request cost, fits the existing
# Python + Flask + PostgreSQL + scikit-learn stack.
#
# The model (~90MB) downloads once on first use and is cached locally
# by the sentence-transformers/huggingface_hub libraries (~/.cache by
# default). That first call can take a few seconds; every call after
# that is fast (CPU inference on a short note is tens of milliseconds).
#
# Loading is lazy and guarded: if sentence-transformers isn't installed,
# or the model can't be downloaded (e.g. no internet on first run),
# semantic search degrades gracefully to TF-IDF-only keyword search
# instead of crashing the app — see notes.py's hybrid ranking in
# search_notes(), which checks embeddings_status() before relying on
# vectors and just weights keyword score at 100% when they're
# unavailable.
# ============================================================
import json
import threading

MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

_model = None
_model_lock = threading.Lock()
_load_error = None


def _get_model():
    """Lazily loads (and caches) the sentence-transformers model. Safe to
    call from every request — only does real work once."""
    global _model, _load_error
    if _model is not None or _load_error is not None:
        return _model
    with _model_lock:
        if _model is not None or _load_error is not None:
            return _model
        try:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(MODEL_NAME)
        except Exception as e:  # noqa: BLE001 - any failure just disables semantic search
            _load_error = str(e) or e.__class__.__name__
    return _model


def embeddings_status():
    """Returns (available: bool, reason: str|None) — mirrors the shape
    of ai_client.ai_status() so the two "is this AI feature configured"
    checks read the same way across the codebase."""
    model = _get_model()
    if model is not None:
        return True, None
    return False, _load_error or "sentence-transformers is not installed"


def embed_text(text):
    """Returns a list[float] embedding for a single string, or None if
    the model isn't available."""
    model = _get_model()
    if model is None or not (text or "").strip():
        return None
    vec = model.encode(text, normalize_embeddings=True)
    return vec.tolist()


def embed_texts(texts):
    """Batch version — much faster than calling embed_text in a loop
    when (re)embedding many notes at once, e.g. backfilling notes that
    predate this feature. Returns a list the same length as `texts`;
    entries for blank strings are None even though the model is loaded,
    same as embed_text would return for a single blank string."""
    model = _get_model()
    if model is None:
        return [None] * len(texts)
    non_blank_idx = [i for i, t in enumerate(texts) if (t or "").strip()]
    if not non_blank_idx:
        return [None] * len(texts)
    vecs = model.encode([texts[i] for i in non_blank_idx], normalize_embeddings=True)
    out = [None] * len(texts)
    for i, vec in zip(non_blank_idx, vecs):
        out[i] = vec.tolist()
    return out


def cosine_similarity(a, b):
    """Cosine similarity between two equal-length vectors as a plain
    Python float in [-1, 1]. Embeddings from embed_text/embed_texts are
    already L2-normalized (normalize_embeddings=True), so this reduces
    to a dot product — written out fully anyway in case a caller ever
    passes in unnormalized vectors (e.g. from a different model)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    denom = norm_a * norm_b
    if denom == 0:
        return 0.0
    return dot / denom


def to_pgvector_literal(vec):
    """Formats a Python list as a pgvector literal string, e.g.
    '[0.1,0.2,0.3]' — used only on the optional pgvector fast path."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def encode_json(vec):
    """How embeddings are stored in the (portable, always-available)
    TEXT column — plain JSON, so the app works even on a Postgres
    without the pgvector extension."""
    return json.dumps(vec)


def decode_json(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
