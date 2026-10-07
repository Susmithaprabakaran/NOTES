# ============================================================
# AI Notes — Day 33 — "Study Mode"
#
#   Open a note (or pick one from the Study Mode page)
#           |
#   Click "Generate study pack" -> POST /api/notes/<id>/study/generate
#           |
#   One AI call, asked for a single JSON object covering all six
#   study artifacts at once (summary, key points, Q&A, MCQs,
#   flashcards, definitions) — one round trip instead of six, so
#   generating a pack doesn't cost 6x the latency or 6x the API bill.
#           |
#   Tolerant parse + per-section validation (same "scan for the outer
#   braces, then cap/clean every field" approach as flowchart.py's
#   parse_ai_flowchart and notes.py's parse_ai_entities)
#           |
#   Saved as ONE row per note (like the summary/tags/entities columns
#   on `notes`, not the one-to-many shape of attachments/flowcharts —
#   a note has a single current study pack, and regenerating replaces
#   it rather than piling up old copies) and handed back to the
#   frontend's dedicated Study Mode page.
#
# source_hash mirrors notes.py's summary_source_hash pattern: it lets
# the frontend show "this was generated before your last edit" instead
# of silently showing stale study material as if it were current.
# ============================================================
import json

from flask import Blueprint, jsonify, request

from auth import login_required, current_user_id
from db import get_db
from ai_client import AIUnavailable, call_ai
from notes import text_hash

bp = Blueprint("study", __name__, url_prefix="/api")

MIN_WORDS = 25  # below this there's rarely enough material for a meaningful study pack
MAX_TEXT_LENGTH = 20000  # same cap as ai.py's /assist — a whole note, not a short selection

MAX_SUMMARY_LENGTH = 1500
MAX_POINT_LENGTH = 220
MAX_KEY_POINTS = 10
MAX_QNA = 8
MAX_QUESTION_LENGTH = 220
MAX_ANSWER_LENGTH = 500
MAX_MCQS = 8
MAX_OPTION_LENGTH = 160
MIN_OPTIONS = 2
MAX_OPTIONS = 6
MAX_EXPLANATION_LENGTH = 300
MAX_FLASHCARDS = 12
MAX_FLASHCARD_SIDE_LENGTH = 220
MAX_DEFINITIONS = 10
MAX_TERM_LENGTH = 80
MAX_DEFINITION_LENGTH = 300

SYSTEM_PROMPT = (
    "You turn a personal note into a complete study pack. Reply with ONLY a JSON object of "
    "this exact shape, no markdown fences, no prose before or after:\n"
    "{\n"
    '  "summary": "2-4 sentence overview",\n'
    '  "keyPoints": ["short standalone fact or idea", "..."],\n'
    '  "qna": [{"question": "...", "answer": "..."}],\n'
    '  "mcqs": [{"question": "...", "options": ["...", "...", "...", "..."], '
    '"answerIndex": 0, "explanation": "why that option is correct"}],\n'
    '  "flashcards": [{"front": "term or question", "back": "answer or explanation"}],\n'
    '  "definitions": [{"term": "...", "definition": "..."}]\n'
    "}\n"
    "Rules:\n"
    "- Base everything strictly on the note's content — never invent facts that aren't in it "
    "or reasonably implied by it.\n"
    f"- keyPoints: {MAX_KEY_POINTS} or fewer, each one short standalone sentence.\n"
    f"- qna: {MAX_QNA} or fewer question/answer pairs testing understanding of the material.\n"
    f"- mcqs: {MAX_MCQS} or fewer multiple-choice questions, each with exactly 4 options, "
    "exactly one correct, and answerIndex as the 0-based index of the correct option. Make "
    "the wrong options plausible, not obviously silly.\n"
    f"- flashcards: {MAX_FLASHCARDS} or fewer, short front/back pairs good for memorization.\n"
    "- definitions: only the important terms, concepts, or jargon actually named in the note "
    f"({MAX_DEFINITIONS} or fewer) — return an empty list if the note doesn't name any.\n"
    "- If the note is too short or vague to support a section, return an empty list for that "
    "section rather than padding it with filler."
)
PROMPT_TEMPLATE = "Build a study pack from the following note:\n\n{text}"


def _word_count(text):
    text = (text or "").strip()
    return len(text.split()) if text else 0


def _clip(value, max_len):
    value = " ".join(str(value or "").strip().split())
    return value[:max_len].rstrip()


def _clean_str_list(raw, max_items, max_len):
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        cleaned = _clip(item, max_len)
        if cleaned:
            out.append(cleaned)
        if len(out) >= max_items:
            break
    return out


def _clean_qna(raw):
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        question = _clip(item.get("question"), MAX_QUESTION_LENGTH)
        answer = _clip(item.get("answer"), MAX_ANSWER_LENGTH)
        if not question or not answer:
            continue
        out.append({"question": question, "answer": answer})
        if len(out) >= MAX_QNA:
            break
    return out


def _clean_mcqs(raw):
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        question = _clip(item.get("question"), MAX_QUESTION_LENGTH)
        raw_options = item.get("options")
        if not question or not isinstance(raw_options, list):
            continue
        options = [_clip(o, MAX_OPTION_LENGTH) for o in raw_options]
        options = [o for o in options if o][:MAX_OPTIONS]
        if len(options) < MIN_OPTIONS:
            continue
        try:
            answer_index = int(item.get("answerIndex"))
        except (TypeError, ValueError):
            continue
        if not (0 <= answer_index < len(options)):
            continue
        explanation = _clip(item.get("explanation"), MAX_EXPLANATION_LENGTH)
        out.append(
            {
                "question": question,
                "options": options,
                "answerIndex": answer_index,
                "explanation": explanation,
            }
        )
        if len(out) >= MAX_MCQS:
            break
    return out


def _clean_flashcards(raw):
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        front = _clip(item.get("front"), MAX_FLASHCARD_SIDE_LENGTH)
        back = _clip(item.get("back"), MAX_FLASHCARD_SIDE_LENGTH)
        if not front or not back:
            continue
        out.append({"front": front, "back": back})
        if len(out) >= MAX_FLASHCARDS:
            break
    return out


def _clean_definitions(raw):
    if not isinstance(raw, list):
        return []
    out = []
    seen_terms = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        term = _clip(item.get("term"), MAX_TERM_LENGTH)
        definition = _clip(item.get("definition"), MAX_DEFINITION_LENGTH)
        key = term.lower()
        if not term or not definition or key in seen_terms:
            continue
        seen_terms.add(key)
        out.append({"term": term, "definition": definition})
        if len(out) >= MAX_DEFINITIONS:
            break
    return out


def parse_ai_study_pack(raw_reply):
    """Tolerant parse of the AI's study-pack JSON reply, same scan-for-
    the-outer-braces approach as flowchart.py's parse_ai_flowchart
    (models occasionally wrap JSON in prose despite instructions not
    to). Never raises — a malformed/empty reply yields a pack that's
    empty everywhere, which the route above treats as "couldn't build
    a study pack from that" rather than a crash."""
    raw = (raw_reply or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        data = {}
    else:
        try:
            parsed = json.loads(raw[start : end + 1])
            data = parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            data = {}

    return {
        "summary": _clip(data.get("summary"), MAX_SUMMARY_LENGTH),
        "keyPoints": _clean_str_list(data.get("keyPoints"), MAX_KEY_POINTS, MAX_POINT_LENGTH),
        "qna": _clean_qna(data.get("qna")),
        "mcqs": _clean_mcqs(data.get("mcqs")),
        "flashcards": _clean_flashcards(data.get("flashcards")),
        "definitions": _clean_definitions(data.get("definitions")),
    }


def _is_empty_pack(pack):
    return not (
        pack["summary"]
        or pack["keyPoints"]
        or pack["qna"]
        or pack["mcqs"]
        or pack["flashcards"]
        or pack["definitions"]
    )


def serialize_study(row, current_text):
    return {
        "noteId": row["note_id"],
        "summary": row["summary"],
        "keyPoints": json.loads(row["key_points"]),
        "qna": json.loads(row["qna"]),
        "mcqs": json.loads(row["mcqs"]),
        "flashcards": json.loads(row["flashcards"]),
        "definitions": json.loads(row["definitions"]),
        "stale": row["source_hash"] != text_hash(current_text),
        "createdAt": row["created_at"].isoformat(),
        "updatedAt": row["updated_at"].isoformat(),
    }


def _get_note_or_404(note_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT * FROM notes WHERE id = %s AND user_id = %s", (note_id, current_user_id()))
        return cur.fetchone()


@bp.get("/notes/<int:note_id>/study")
@login_required
def get_study(note_id):
    note = _get_note_or_404(note_id)
    if not note:
        return jsonify({"error": "Note not found"}), 404

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM study_materials WHERE note_id = %s AND user_id = %s",
            (note_id, current_user_id()),
        )
        row = cur.fetchone()

    if not row:
        return jsonify({"generated": False}), 200

    body = serialize_study(row, note["text"])
    body["generated"] = True
    return jsonify(body), 200


@bp.post("/notes/<int:note_id>/study/generate")
@login_required
def generate_study(note_id):
    note = _get_note_or_404(note_id)
    if not note:
        return jsonify({"error": "Note not found"}), 404

    text = (note["text"] or "").strip()
    if not text:
        return jsonify({"error": "This note has no text to build a study pack from."}), 400
    if len(text) > MAX_TEXT_LENGTH:
        return jsonify({"error": f"Note is too long (max {MAX_TEXT_LENGTH} characters)."}), 400
    if _word_count(text) < MIN_WORDS:
        return (
            jsonify({"error": f"This note needs at least {MIN_WORDS} words to build a study pack from."}),
            400,
        )

    try:
        raw_reply = call_ai(PROMPT_TEMPLATE.format(text=text), system=SYSTEM_PROMPT)
    except AIUnavailable as e:
        return jsonify({"error": str(e)}), 501

    pack = parse_ai_study_pack(raw_reply)
    if _is_empty_pack(pack):
        return (
            jsonify({"error": "Couldn't build a study pack from this note. Try adding more detail to it."}),
            422,
        )

    source_hash = text_hash(text)
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO study_materials
                   (user_id, note_id, summary, key_points, qna, mcqs, flashcards, definitions, source_hash)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (note_id) DO UPDATE SET
                   summary = EXCLUDED.summary,
                   key_points = EXCLUDED.key_points,
                   qna = EXCLUDED.qna,
                   mcqs = EXCLUDED.mcqs,
                   flashcards = EXCLUDED.flashcards,
                   definitions = EXCLUDED.definitions,
                   source_hash = EXCLUDED.source_hash,
                   updated_at = now()
               RETURNING *""",
            (
                current_user_id(),
                note_id,
                pack["summary"],
                json.dumps(pack["keyPoints"]),
                json.dumps(pack["qna"]),
                json.dumps(pack["mcqs"]),
                json.dumps(pack["flashcards"]),
                json.dumps(pack["definitions"]),
                source_hash,
            ),
        )
        row = cur.fetchone()
    db.commit()

    body = serialize_study(row, text)
    body["generated"] = True
    return jsonify(body), 200


@bp.delete("/notes/<int:note_id>/study")
@login_required
def delete_study(note_id):
    if not _get_note_or_404(note_id):
        return jsonify({"error": "Note not found"}), 404

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "DELETE FROM study_materials WHERE note_id = %s AND user_id = %s RETURNING id",
            (note_id, current_user_id()),
        )
        deleted = cur.fetchone()
    db.commit()

    if not deleted:
        return jsonify({"error": "No study material for this note"}), 404
    return jsonify({"deleted": True}), 200
