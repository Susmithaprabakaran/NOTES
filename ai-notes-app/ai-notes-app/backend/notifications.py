# ============================================================
# AI Notes — Day 36 — Smart Notifications
#
# The feed is built from two layers:
#
#   1. Rule engine (_rule_candidates) — pure SQL, no network calls.
#      Looks for tasks due "today"/"tomorrow" by keyword, tasks that
#      have sat open for a while, and high-priority notes that have
#      gone quiet. This layer never depends on an AI model, so it's
#      the one guarantee: Smart Notifications work with zero setup.
#
#   2. Digest layer (_digest_notification) — one extra notification
#      that summarizes the day's rule hits in a single line. If an AI
#      model is configured (Day 11's ai_client) and the user's
#      aiUpdates preference is on, the model writes that line; if not,
#      a plain f-string template writes the same information instead.
#      Same notification, same place in the feed, same dedupe key —
#      only the wording differs. Nothing in this file ever 501s just
#      because no AI_PROVIDER is set.
#
# POST /generate is idempotent-ish by design: every candidate carries a
# dedupe_key scoped to today's date, so calling it repeatedly (the
# frontend does it once per app boot) only ever inserts each rule hit
# once per day — see the unique index on (user_id, dedupe_key) in
# schema.sql.
# ============================================================
import datetime
import json

from flask import Blueprint, jsonify, request

from auth import current_user_id, login_required
from db import get_db
from ai_client import AIUnavailable, ai_status, call_ai
from profile import DEFAULT_NOTIFICATION_PREFS, _parse_notification_prefs

bp = Blueprint("notifications", __name__, url_prefix="/api/notifications")

# A task counts as "going stale" once it's been open this many days.
STALE_TASK_DAYS = 7
# A high-priority note counts as "going quiet" once it's been untouched
# this many days.
STALE_NOTE_DAYS = 5
# Tasks whose due_text contains one of these (case-insensitive) are
# treated as due imminently. Deliberately simple keyword matching, not
# date parsing — due_text is freeform ("Friday", "next Tuesday", see
# Day 16's note on tasks.py) and doesn't reduce to a real date without
# guessing at the note's timezone/context.
DUE_SOON_WORDS = ("today", "tonight", "tomorrow", "asap")

DIGEST_SYSTEM_PROMPT = (
    "You write a single encouraging notification line for a notes app, "
    "summarizing the bullet points given to you. Reply with ONLY that "
    "one sentence (max 25 words) — no preamble, no quotes, no markdown."
)

LIST_LIMIT = 50


def _today():
    return datetime.date.today().isoformat()


def _prefs(user_row):
    return _parse_notification_prefs(user_row.get("notification_prefs"))


def serialize_notification(row):
    return {
        "id": row["id"],
        "type": row["type"],
        "title": row["title"],
        "message": row["message"],
        "noteId": row["note_id"],
        "taskId": row["task_id"],
        "read": row["read"],
        "aiGenerated": row["ai_generated"],
        "createdAt": row["created_at"].isoformat(),
    }


# ============================================================
# Rule engine — always available, no AI required
# ============================================================
def _rule_candidates(db, user_id):
    """Returns a list of {type, title, message, note_id, task_id, dedupe_key}."""
    candidates = []
    today = _today()

    with db.cursor() as cur:
        # ---- tasks due soon (keyword match on due_text) ----
        cur.execute(
            "SELECT t.id, t.text, t.due_text, t.note_id FROM tasks t "
            "WHERE t.user_id = %s AND t.done = false AND t.due_text IS NOT NULL "
            "AND t.due_text <> ''",
            (user_id,),
        )
        for row in cur.fetchall():
            due_lower = row["due_text"].lower()
            if any(word in due_lower for word in DUE_SOON_WORDS):
                candidates.append(
                    {
                        "type": "task_due",
                        "title": "Task due soon",
                        "message": f'"{row["text"]}" is due {row["due_text"]}.',
                        "note_id": row["note_id"],
                        "task_id": row["id"],
                        "dedupe_key": f"task_due:{row['id']}:{today}",
                    }
                )

        # ---- tasks that have been open a while ----
        cur.execute(
            "SELECT COUNT(*) AS n FROM tasks "
            "WHERE user_id = %s AND done = false "
            "AND created_at < now() - (%s || ' days')::interval",
            (user_id, STALE_TASK_DAYS),
        )
        stale_count = cur.fetchone()["n"]
        if stale_count:
            plural = "" if stale_count == 1 else "s"
            candidates.append(
                {
                    "type": "task_stale",
                    "title": "Tasks piling up",
                    "message": (
                        f"You have {stale_count} open task{plural} that's been sitting for over "
                        f"{STALE_TASK_DAYS} days — might be worth a look."
                    ),
                    "note_id": None,
                    "task_id": None,
                    "dedupe_key": f"task_stale:{today}",
                }
            )

        # ---- high-priority notes gone quiet ----
        cur.execute(
            "SELECT id, title FROM notes "
            "WHERE user_id = %s AND priority = 'high' "
            "AND updated_at < now() - (%s || ' days')::interval",
            (user_id, STALE_NOTE_DAYS),
        )
        for row in cur.fetchall():
            candidates.append(
                {
                    "type": "note_stale",
                    "title": "High-priority note going quiet",
                    "message": (
                        f'"{row["title"] or "Untitled note"}" is marked high priority but hasn\'t '
                        f"been touched in over {STALE_NOTE_DAYS} days."
                    ),
                    "note_id": row["id"],
                    "task_id": None,
                    "dedupe_key": f"note_stale:{row['id']}:{today}",
                }
            )

    return candidates, stale_count


def _digest_notification(candidates, stale_count):
    """Builds the one-line digest notification for today, AI-phrased when
    a model is configured and reachable, plain-templated otherwise.
    Returns None if there's nothing worth summarizing."""
    due_count = sum(1 for c in candidates if c["type"] == "task_due")
    note_count = sum(1 for c in candidates if c["type"] == "note_stale")

    if not due_count and not stale_count and not note_count:
        return None

    bullets = []
    if due_count:
        bullets.append(f"- {due_count} task{'s' if due_count != 1 else ''} due soon")
    if stale_count:
        bullets.append(f"- {stale_count} open task{'s' if stale_count != 1 else ''} going stale")
    if note_count:
        bullets.append(f"- {note_count} high-priority note{'s' if note_count != 1 else ''} gone quiet")

    template_message = "Today: " + "; ".join(b.lstrip("- ") for b in bullets) + "."
    ai_generated = False
    message = template_message

    available, _provider, _reason = ai_status()
    if available:
        try:
            message = call_ai("\n".join(bullets), system=DIGEST_SYSTEM_PROMPT).strip().strip('"')
            ai_generated = bool(message)
        except AIUnavailable:
            message = template_message  # fall back silently — the digest still ships
        if not message:
            message = template_message
            ai_generated = False

    return {
        "type": "digest",
        "title": "Today's overview",
        "message": message,
        "note_id": None,
        "task_id": None,
        "dedupe_key": f"digest:{_today()}",
        "ai_generated": ai_generated,
    }


@bp.post("/generate")
@login_required
def generate():
    user_id = current_user_id()
    db = get_db()

    with db.cursor() as cur:
        cur.execute("SELECT notification_prefs FROM users WHERE id = %s", (user_id,))
        user_row = cur.fetchone()
    prefs = _prefs(user_row or {})

    all_candidates, stale_count = _rule_candidates(db, user_id)
    if prefs.get("taskReminders", True):
        candidates = all_candidates
    else:
        # taskReminders is a task-specific preference — a note going quiet
        # is a separate signal, so it still surfaces even with task
        # reminders turned off, just without the task-shaped notifications.
        candidates = [c for c in all_candidates if c["type"] == "note_stale"]
        stale_count = 0

    if prefs.get("aiUpdates", False):
        digest = _digest_notification(candidates, stale_count)
        if digest:
            candidates.append(digest)

    added = 0
    with db.cursor() as cur:
        for c in candidates:
            cur.execute(
                """INSERT INTO notifications
                       (user_id, type, title, message, note_id, task_id, dedupe_key, ai_generated)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (user_id, dedupe_key) DO NOTHING
                   RETURNING id""",
                (
                    user_id,
                    c["type"],
                    c["title"],
                    c["message"],
                    c["note_id"],
                    c["task_id"],
                    c["dedupe_key"],
                    c.get("ai_generated", False),
                ),
            )
            if cur.fetchone():
                added += 1
    db.commit()

    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM notifications WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
            (user_id, LIST_LIMIT),
        )
        rows = cur.fetchall()

    return (
        jsonify(
            {
                "notifications": [serialize_notification(r) for r in rows],
                "added": added,
                "unreadCount": sum(1 for r in rows if not r["read"]),
            }
        ),
        200,
    )


@bp.get("")
@login_required
def list_notifications():
    user_id = current_user_id()
    unread_only = request.args.get("unread") == "1"

    db = get_db()
    with db.cursor() as cur:
        query = "SELECT * FROM notifications WHERE user_id = %s"
        params = [user_id]
        if unread_only:
            query += " AND read = false"
        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(LIST_LIMIT)
        cur.execute(query, params)
        rows = cur.fetchall()

        cur.execute(
            "SELECT COUNT(*) AS n FROM notifications WHERE user_id = %s AND read = false",
            (user_id,),
        )
        unread_count = cur.fetchone()["n"]

    return (
        jsonify(
            {
                "notifications": [serialize_notification(r) for r in rows],
                "unreadCount": unread_count,
            }
        ),
        200,
    )


@bp.patch("/<int:notification_id>")
@login_required
def update_notification(notification_id):
    body = request.get_json(silent=True) or {}
    if "read" not in body:
        return jsonify({"error": "Nothing to update — send {\"read\": true|false}."}), 400

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "UPDATE notifications SET read = %s WHERE id = %s AND user_id = %s RETURNING *",
            (bool(body["read"]), notification_id, current_user_id()),
        )
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "Notification not found"}), 404
    db.commit()
    return jsonify(serialize_notification(row)), 200


@bp.post("/read-all")
@login_required
def read_all():
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "UPDATE notifications SET read = true WHERE user_id = %s AND read = false RETURNING id",
            (current_user_id(),),
        )
        updated = cur.fetchall()
    db.commit()
    return jsonify({"updated": len(updated)}), 200


@bp.delete("/<int:notification_id>")
@login_required
def delete_notification(notification_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "DELETE FROM notifications WHERE id = %s AND user_id = %s RETURNING *",
            (notification_id, current_user_id()),
        )
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "Notification not found"}), 404
    db.commit()
    return jsonify({"deleted": serialize_notification(row)}), 200
