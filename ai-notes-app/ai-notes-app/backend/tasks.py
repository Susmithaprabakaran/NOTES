# ============================================================
# AI Notes — Day 13 — task extraction
#
# Turns freeform note text into a structured task list. Two ways in:
#   - POST /notes/<id>/extract-tasks : sends the note's text to the AI
#     model and asks it to pull out concrete action items, then stores
#     each one as a row in `tasks`, linked back to the note.
#   - The rest of the routes here are plain CRUD over that table
#     (per-note list, all-tasks list, manual add, toggle done, delete)
#     — no AI involved, so they keep working even without a model
#     configured (Day 11 setup).
#
# Extraction is additive: re-running it on a note dedupes against tasks
# already stored for that note (case-insensitive exact match) instead
# of creating duplicate rows every time the person clicks the button.
# ============================================================
import json
import re

from flask import Blueprint, jsonify, request

from auth import login_required, current_user_id
from db import get_db
from ai_client import AIUnavailable, ai_status, call_ai

bp = Blueprint("tasks", __name__, url_prefix="/api")

MAX_TASK_LENGTH = 500
MAX_DUE_LENGTH = 40

EXTRACT_SYSTEM_PROMPT = (
    "You extract action items from personal notes. An action item is "
    "something the note's author still needs to DO — a task, follow-up, "
    "errand, deadline, or commitment — not just a fact, idea, or "
    'observation. Reply with ONLY a JSON array of objects, each shaped '
    'like {"text": "Email Sarah the budget draft", "due": "Friday"}. '
    'The "text" field is the task phrased as a clear imperative, with '
    'any due-date wording removed from it. The "due" field is a short '
    "due-date phrase copied from the note if the task mentions one "
    '("Tomorrow", "Friday", "next Tuesday", "Aug 18") — use null if no '
    "due date is mentioned. "
    "Keep every task ATOMIC — one single action per item. If a sentence "
    'bundles several actions together ("email Sarah the draft and book '
    'the conference room and confirm with the printer"), split it into '
    "separate task objects, one per action, rather than one long combined "
    "task. Prefer several short, clearly-scoped tasks over one large, "
    "vague one. If there are no action items, reply with "
    "exactly []. No preamble, no markdown formatting, no explanation — "
    "just the JSON array."
)

# A task extracted as one long sentence bundling multiple actions is
# hard to actually check off one at a time — this splits it into
# several smaller, atomic tasks after the fact, as a safety net on top
# of the prompt instruction above (every provider, including the local
# one, benefits from this since none of them are perfectly reliable
# about following "keep it atomic").
MAX_SPLIT_TASKS_PER_ITEM = 5
MIN_FRAGMENT_WORDS = 2  # a fragment shorter than this isn't a real standalone task
_SPLIT_CONJUNCTIONS = re.compile(
    r"\s*(?:,?\s+and then\s+|,?\s+then\s+|;\s*|,?\s+and\s+|\s+&\s+)\s*", re.IGNORECASE
)
# A fragment only counts as its own task if it plausibly starts a new
# action (an imperative verb, or a task cue like "need to") — otherwise
# splitting on "and" would wrongly chop up phrases like "bread and milk"
# into two fake tasks.
_TASK_VERB_START = re.compile(
    r"^(email|call|text|message|send|book|schedule|pay|buy|order|pick up|drop off|"
    r"review|finish|complete|submit|file|update|fix|check|confirm|cancel|reschedule|"
    r"follow up|remind|prepare|draft|write|read|clean|organize|renew|register|apply|"
    r"install|set up|setup|reply|respond|return|print|sign|upload|download|backup|"
    r"back up|need to|needs to|have to|has to|must|should|remember to|make sure to)\b",
    re.IGNORECASE,
)


def _split_compound_task_text(text):
    """Splits one bundled task string into several atomic ones when it
    clearly contains multiple distinct actions. Conservative on
    purpose: only splits on an explicit conjunction/semicolon AND only
    when the piece after it looks like the start of its own action
    (see _TASK_VERB_START) — so "buy bread and milk" stays one task,
    but "email Sarah the draft and book the conference room" becomes
    two. Never returns an empty list; falls back to [text] whenever a
    split doesn't clearly apply."""
    text = (text or "").strip()
    if not text:
        return []

    parts = _SPLIT_CONJUNCTIONS.split(text)
    parts = [p.strip(" ,.-") for p in parts if p and p.strip(" ,.-")]
    if len(parts) < 2:
        return [text]

    # Only actually split if every fragment after the first looks like
    # its own imperative action — otherwise this is very likely a
    # single task whose object happens to contain "and" ("coordinate
    # marketing and sales") rather than two tasks.
    if not all(
        len(p.split()) >= MIN_FRAGMENT_WORDS and _TASK_VERB_START.match(p) for p in parts[1:]
    ):
        return [text]

    out = [p[0].upper() + p[1:] if p else p for p in parts[:MAX_SPLIT_TASKS_PER_ITEM]]
    return out


def split_compound_tasks(items):
    """Applies _split_compound_task_text across a whole extracted list,
    keeping each split fragment's due date (the due phrase belongs to
    the sentence as a whole, so every fragment inherits it)."""
    out = []
    for item in items:
        fragments = _split_compound_task_text(item.get("text"))
        for frag in fragments:
            out.append({"text": frag[:MAX_TASK_LENGTH], "due": item.get("due")})
    return out
EXTRACT_PROMPT_TEMPLATE = "Extract the action items from this note:\n\n{text}"


def serialize_task(row, note_title=None):
    out = {
        "id": row["id"],
        "noteId": row["note_id"],
        "text": row["text"],
        "done": row["done"],
        "due": row["due_text"] if "due_text" in row.keys() else None,
        "createdAt": row["created_at"].isoformat(),
        "updatedAt": row["updated_at"].isoformat(),
    }
    if note_title is not None:
        out["noteTitle"] = note_title
    return out


def _get_note_or_404(note_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM notes WHERE id = %s AND user_id = %s", (note_id, current_user_id())
        )
        return cur.fetchone()


def _parse_task_list(raw):
    """The model is asked for a JSON array of {"text", "due"} objects but
    sometimes wraps it in ```json fences, adds a stray sentence, or (for
    older prompts / other callers) replies with bare strings instead —
    tolerate all of that instead of failing the whole extraction over
    formatting noise. Returns a list of {"text": str, "due": str|None}."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]

    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        items = json.loads(raw[start : end + 1])
    except (ValueError, TypeError):
        return []
    if not isinstance(items, list):
        return []

    cleaned = []
    for item in items:
        due = None
        if isinstance(item, str):
            text = item.strip().lstrip("-•").strip()
        elif isinstance(item, dict):
            text = str(item.get("text") or item.get("task") or "").strip()
            due_raw = item.get("due")
            if isinstance(due_raw, str) and due_raw.strip() and due_raw.strip().lower() != "null":
                due = " ".join(due_raw.strip().split())[:MAX_DUE_LENGTH]
        else:
            continue
        if text:
            cleaned.append({"text": text[:MAX_TASK_LENGTH], "due": due})
    return cleaned


# ============================================================
# Extraction
# ============================================================
@bp.post("/notes/<int:note_id>/extract-tasks")
@login_required
def extract_tasks(note_id):
    note = _get_note_or_404(note_id)
    if not note:
        return jsonify({"error": "Note not found"}), 404

    text = (note["text"] or "").strip()
    if not text:
        return jsonify({"error": "Nothing to work with yet — write something first."}), 400

    available, provider, reason = ai_status()
    if not available:
        return jsonify({"error": reason}), 501

    try:
        raw = call_ai(EXTRACT_PROMPT_TEMPLATE.format(text=text), system=EXTRACT_SYSTEM_PROMPT)
    except AIUnavailable as e:
        return jsonify({"error": str(e)}), 501

    extracted = _parse_task_list(raw)
    # Break up any bundled/too-large task into smaller atomic ones —
    # applies regardless of which AI_PROVIDER produced the list (real
    # model or the local heuristic engine), so a task never has to be
    # split by hand after the fact.
    extracted = split_compound_tasks(extracted)

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT text FROM tasks WHERE note_id = %s AND user_id = %s",
            (note_id, current_user_id()),
        )
        existing = {r["text"].strip().lower() for r in cur.fetchall()}

    new_items = [item for item in extracted if item["text"].strip().lower() not in existing]

    if new_items:
        with db.cursor() as cur:
            for item in new_items:
                cur.execute(
                    "INSERT INTO tasks (user_id, note_id, text, due_text) VALUES (%s, %s, %s, %s)",
                    (current_user_id(), note_id, item["text"], item["due"]),
                )
        db.commit()

    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM tasks WHERE note_id = %s AND user_id = %s ORDER BY created_at",
            (note_id, current_user_id()),
        )
        all_tasks = cur.fetchall()

    return (
        jsonify(
            {
                "tasks": [serialize_task(t) for t in all_tasks],
                "added": len(new_items),
                "found": len(extracted),
            }
        ),
        200,
    )


# ============================================================
# Per-note task list
# ============================================================
@bp.get("/notes/<int:note_id>/tasks")
@login_required
def list_note_tasks(note_id):
    note = _get_note_or_404(note_id)
    if not note:
        return jsonify({"error": "Note not found"}), 404
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM tasks WHERE note_id = %s AND user_id = %s ORDER BY created_at",
            (note_id, current_user_id()),
        )
        rows = cur.fetchall()
    return jsonify([serialize_task(r) for r in rows]), 200


@bp.post("/tasks")
@login_required
def create_task():
    """Lets someone add a task by hand too — e.g. one the AI missed —
    not just accept whatever got extracted."""
    body = request.get_json(silent=True) or {}
    note_id = body.get("noteId")
    text = (body.get("text") or "").strip()
    due = body.get("due")
    due = " ".join(due.strip().split())[:MAX_DUE_LENGTH] if isinstance(due, str) and due.strip() else None
    if not note_id:
        return jsonify({"error": "noteId is required"}), 400
    if not text:
        return jsonify({"error": "text is required"}), 400

    note = _get_note_or_404(note_id)
    if not note:
        return jsonify({"error": "Note not found"}), 404

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO tasks (user_id, note_id, text, due_text) VALUES (%s, %s, %s, %s) RETURNING *",
            (current_user_id(), note_id, text[:MAX_TASK_LENGTH], due),
        )
        row = cur.fetchone()
    db.commit()
    return jsonify(serialize_task(row)), 201


# ============================================================
# All-tasks view (across every note)
# ============================================================
@bp.get("/tasks")
@login_required
def list_all_tasks():
    done_param = request.args.get("done")
    # Day 34: the all-notes Tasks view has no ceiling on how many rows it
    # can grow to (a term's worth of course notes routinely produces a
    # couple hundred extracted action items) and used to dump every one
    # of them into a single flat, unsearchable scroll. `q` lets the
    # frontend's new search box narrow that down server-side rather than
    # filtering a huge already-fetched array client-side — same ILIKE-
    # over-text-and-title approach the pre-Day-17 keyword search used.
    q = (request.args.get("q") or "").strip()
    db = get_db()
    with db.cursor() as cur:
        query = (
            "SELECT t.*, n.title AS note_title FROM tasks t "
            "JOIN notes n ON n.id = t.note_id "
            "WHERE t.user_id = %s"
        )
        params = [current_user_id()]
        if done_param == "1":
            query += " AND t.done = true"
        elif done_param == "0":
            query += " AND t.done = false"
        if q:
            query += " AND (t.text ILIKE %s OR n.title ILIKE %s)"
            like = f"%{q}%"
            params.extend([like, like])
        query += " ORDER BY t.done ASC, t.created_at DESC"
        cur.execute(query, params)
        rows = cur.fetchall()
    return jsonify([serialize_task(r, note_title=r["note_title"]) for r in rows]), 200


@bp.patch("/tasks/<int:task_id>")
@login_required
def update_task(task_id):
    body = request.get_json(silent=True) or {}
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM tasks WHERE id = %s AND user_id = %s", (task_id, current_user_id())
        )
        existing = cur.fetchone()
        if not existing:
            return jsonify({"error": "Task not found"}), 404

        done = body["done"] if "done" in body else existing["done"]
        text = (
            body["text"].strip()[:MAX_TASK_LENGTH]
            if "text" in body and body["text"].strip()
            else existing["text"]
        )
        if "due" in body:
            due_raw = body["due"]
            due = (
                " ".join(due_raw.strip().split())[:MAX_DUE_LENGTH]
                if isinstance(due_raw, str) and due_raw.strip()
                else None
            )
        else:
            due = existing["due_text"]

        cur.execute(
            """UPDATE tasks SET done = %s, text = %s, due_text = %s, updated_at = now()
               WHERE id = %s AND user_id = %s RETURNING *""",
            (done, text, due, task_id, current_user_id()),
        )
        row = cur.fetchone()
    db.commit()
    return jsonify(serialize_task(row)), 200


@bp.delete("/tasks/<int:task_id>")
@login_required
def delete_task(task_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "DELETE FROM tasks WHERE id = %s AND user_id = %s RETURNING *",
            (task_id, current_user_id()),
        )
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "Task not found"}), 404
    db.commit()
    return jsonify({"deleted": serialize_task(row)}), 200
