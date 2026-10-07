# ============================================================
# AI Notes — Day 32 — "Generate Flowchart with AI"
#
#   Select some text inside a note
#           |
#   Click "Generate Flowchart with AI" (floating toolbar)
#           |
#   Frontend sends the selected text -> POST /api/notes/<id>/flowcharts
#           |
#   AI is asked for a small graph: {"nodes":[...], "edges":[...]}
#           |
#   Tolerant parse + validation (same "scan for the outer braces,
#   models sometimes wrap JSON in prose" approach as notes.py's
#   parse_ai_tag_list / parse_ai_entities)
#           |
#   Saved as its own row (a note can have several flowcharts — one per
#   selection someone's diagrammed, same one-to-many shape as
#   attachments.py) and handed back to the frontend, which lays it out
#   and renders it as an SVG.
#
# The layout (positioning nodes/edges on a canvas) is entirely a
# frontend concern — this module only produces and stores the graph
# structure (which steps, which type, which connections), the same
# division of responsibility as graph.py producing the knowledge-graph
# edges while app.js does the force-directed layout.
# ============================================================
import json

from flask import Blueprint, jsonify, request

from auth import login_required, current_user_id
from db import get_db
from ai_client import AIUnavailable, call_ai

bp = Blueprint("flowchart", __name__, url_prefix="/api")

MIN_WORDS = 6  # below this there's rarely an actual sequence of steps to diagram
MAX_TEXT_LENGTH = 4000  # a selection, not a whole note — Day 11's 20000-char cap would be overkill here
MAX_NODES = 12
MAX_EDGES = 24
MAX_LABEL_LENGTH = 80
MAX_EDGE_LABEL_LENGTH = 24
MAX_TITLE_LENGTH = 70

# Shapes the frontend knows how to draw: pill (start/end), rectangle
# (process), diamond (decision), parallelogram (io). Anything the AI
# sends that isn't one of these collapses to "process" rather than
# being rejected outright — an unrecognized shape shouldn't sink an
# otherwise-good flowchart.
NODE_TYPES = {"start", "end", "process", "decision", "io"}

SYSTEM_PROMPT = (
    "You turn a piece of text describing a process, a sequence of steps, or decision logic into "
    "a flowchart. Reply with ONLY a JSON object of this exact shape, no markdown fences, no prose "
    'before or after: {"nodes":[{"id":"n1","label":"...","type":"start"}],'
    '"edges":[{"from":"n1","to":"n2","label":""}]}\n'
    "Rules:\n"
    "- Each node id is a short string (\"n1\", \"n2\", ...), referenced exactly by the edges.\n"
    "- Each label is a short imperative phrase (max ~8 words) describing that step.\n"
    "- type is one of: start, end, process, decision, io. Use \"start\" for the first step and "
    "\"end\" for a genuine terminal step only if the text has a clear beginning/end — otherwise "
    "use \"process\" throughout. Use \"decision\" for a yes/no or branching question, and label "
    "its outgoing edges (e.g. \"Yes\"/\"No\") so the branches read clearly. Use \"io\" for a step "
    "that's mainly about receiving input or producing output.\n"
    "- Keep it to at most 12 nodes — merge minor sub-steps into their parent step rather than "
    "exploding into dozens of tiny boxes.\n"
    "- If the text doesn't describe any kind of sequence, steps, process, or decision at all, "
    'reply with exactly {"nodes":[],"edges":[]} — do not invent a flowchart that isn\'t there.'
)
PROMPT_TEMPLATE = "Turn the following text into a flowchart:\n\n{text}"


def _word_count(text):
    text = (text or "").strip()
    return len(text.split()) if text else 0


def _title_from_text(text):
    """Short label for the flowcharts panel list — first bit of the
    source selection, not an extra AI call just to name it."""
    collapsed = " ".join((text or "").strip().split())
    if len(collapsed) <= MAX_TITLE_LENGTH:
        return collapsed
    return collapsed[:MAX_TITLE_LENGTH].rstrip() + "…"


def parse_ai_flowchart(raw_reply):
    """Tolerant parse of the AI's {"nodes":[...],"edges":[...]} reply,
    same scan-for-the-outer-braces approach as notes.py's
    parse_ai_entities (models occasionally wrap JSON in prose despite
    instructions not to). Never raises — a malformed/empty reply just
    yields ([], []), which the route above treats as "couldn't find a
    flowchart here" rather than a crash.

    Returns (nodes, edges), each already capped, deduped, and
    cross-referenced: every edge's from/to is guaranteed to name a node
    that made it into the returned node list."""
    raw = (raw_reply or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return [], []
    try:
        data = json.loads(raw[start : end + 1])
    except (ValueError, TypeError):
        return [], []
    if not isinstance(data, dict):
        return [], []

    raw_nodes = data.get("nodes")
    raw_edges = data.get("edges")
    if not isinstance(raw_nodes, list):
        raw_nodes = []
    if not isinstance(raw_edges, list):
        raw_edges = []

    nodes = []
    seen_ids = set()
    for item in raw_nodes:
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("id") or "").strip()
        label = " ".join(str(item.get("label") or "").strip().split())
        if not node_id or not label or node_id in seen_ids:
            continue
        node_type = str(item.get("type") or "process").strip().lower()
        if node_type not in NODE_TYPES:
            node_type = "process"
        seen_ids.add(node_id)
        nodes.append({"id": node_id, "label": label[:MAX_LABEL_LENGTH], "type": node_type})
        if len(nodes) >= MAX_NODES:
            break

    node_ids = {n["id"] for n in nodes}
    edges = []
    seen_edges = set()
    for item in raw_edges:
        if not isinstance(item, dict):
            continue
        src = str(item.get("from") or "").strip()
        dst = str(item.get("to") or "").strip()
        # Self-loops aren't meaningful in a step-by-step flowchart, and
        # an edge naming a node that got trimmed above (over the
        # MAX_NODES cap, or malformed) would leave the frontend layout
        # pointing at nothing.
        if src not in node_ids or dst not in node_ids or src == dst:
            continue
        label = " ".join(str(item.get("label") or "").strip().split())[:MAX_EDGE_LABEL_LENGTH]
        edge_key = (src, dst, label)
        if edge_key in seen_edges:
            continue
        seen_edges.add(edge_key)
        edges.append({"from": src, "to": dst, "label": label})
        if len(edges) >= MAX_EDGES:
            break

    return nodes, edges


def serialize_flowchart(row):
    return {
        "id": row["id"],
        "noteId": row["note_id"],
        "title": row["title"],
        "sourceText": row["source_text"],
        "nodes": json.loads(row["nodes"]),
        "edges": json.loads(row["edges"]),
        "createdAt": row["created_at"].isoformat(),
    }


def _get_note_or_404(note_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT id FROM notes WHERE id = %s AND user_id = %s", (note_id, current_user_id()))
        return cur.fetchone()


@bp.get("/notes/<int:note_id>/flowcharts")
@login_required
def list_flowcharts(note_id):
    if not _get_note_or_404(note_id):
        return jsonify({"error": "Note not found"}), 404

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM flowcharts WHERE note_id = %s AND user_id = %s ORDER BY created_at",
            (note_id, current_user_id()),
        )
        rows = cur.fetchall()
    return jsonify([serialize_flowchart(r) for r in rows]), 200


@bp.post("/notes/<int:note_id>/flowcharts")
@login_required
def generate_flowchart(note_id):
    if not _get_note_or_404(note_id):
        return jsonify({"error": "Note not found"}), 404

    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()

    if not text:
        return jsonify({"error": "Select some text to generate a flowchart from."}), 400
    if len(text) > MAX_TEXT_LENGTH:
        return jsonify({"error": f"Selection is too long (max {MAX_TEXT_LENGTH} characters)."}), 400
    if _word_count(text) < MIN_WORDS:
        return (
            jsonify(
                {
                    "error": (
                        f"Select a longer passage (at least {MIN_WORDS} words) describing some "
                        "steps or a process."
                    )
                }
            ),
            400,
        )

    try:
        raw_reply = call_ai(PROMPT_TEMPLATE.format(text=text), system=SYSTEM_PROMPT)
    except AIUnavailable as e:
        return jsonify({"error": str(e)}), 501

    nodes, edges = parse_ai_flowchart(raw_reply)
    if not nodes:
        return (
            jsonify(
                {
                    "error": (
                        "Couldn't find a clear sequence of steps in that selection. Try selecting "
                        "text that describes a process, a set of instructions, or a decision."
                    )
                }
            ),
            422,
        )

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO flowcharts (user_id, note_id, source_text, title, nodes, edges)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
            (current_user_id(), note_id, text, _title_from_text(text), json.dumps(nodes), json.dumps(edges)),
        )
        row = cur.fetchone()
    db.commit()

    return jsonify(serialize_flowchart(row)), 201


@bp.delete("/flowcharts/<int:flowchart_id>")
@login_required
def delete_flowchart(flowchart_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT id FROM flowcharts WHERE id = %s AND user_id = %s", (flowchart_id, current_user_id()))
        if not cur.fetchone():
            return jsonify({"error": "Flowchart not found"}), 404
        cur.execute("DELETE FROM flowcharts WHERE id = %s AND user_id = %s", (flowchart_id, current_user_id()))
    db.commit()
    return jsonify({"deleted": True}), 200
