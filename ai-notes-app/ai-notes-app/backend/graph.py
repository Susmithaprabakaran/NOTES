# ============================================================
# AI Notes — Day 18 — knowledge graph
#
# Builds a graph connecting notes through the concepts they discuss
# (see notes.py's extract-concepts). Nothing here is stored as its own
# table — the graph is derived on every request from each note's
# `concepts` column, so it's always consistent with the notes
# themselves (no separate edge table that could fall out of sync when
# a note is edited or deleted).
#
# Two kinds of edges:
#   - note --mentions--> concept        (bipartite, one per concept)
#   - concept --co-occurs--> concept    (two concepts mentioned
#                                         together in the same note)
#
# The co-occurrence edges are what let the graph chain ideas *across*
# notes that never directly share a concept — e.g. "Machine Learning"
# and "Evaluation Metrics" can turn out to be connected via
# Python -> Scikit-learn -> Classification even though no single note
# mentions all of them, which is exactly the "discover hidden
# connections" pitch. See /api/graph/path.
# ============================================================
from collections import Counter, defaultdict, deque

from flask import Blueprint, jsonify, request

from auth import login_required, current_user_id
from db import get_db
from notes import parse_concepts_json, parse_tags_json, concept_key, text_hash
from embeddings import decode_json, cosine_similarity
from ai_client import AIUnavailable, call_ai

bp = Blueprint("graph", __name__, url_prefix="/api")

# A note without at least this shared signal to another note isn't
# worth surfacing as "related" — otherwise every note in the account
# would show up as weakly related to every other note via embedding
# noise alone. Concept/tag overlap has no such threshold (any real
# overlap is meaningful); this only gates the embedding-only case.
MIN_SEMANTIC_ONLY_SIMILARITY = 0.55
MAX_RELATED_NOTES = 8
MAX_NOTES_PER_CONCEPT_IN_RESPONSE = 6

# Day 29 — AI graph insights (see /api/graph/explain).
MAX_EXPLAIN_CONNECTED_CONCEPTS = 6
MAX_EXPLAIN_NOTES = 6
MAX_EXPLAIN_PATH_LENGTH = 12


def _resolve_concept(concept_labels, query_text):
    """Same lookup find_path used inline: exact concept_key() match first,
    falling back to a substring match on the display label — concept
    casing/phrasing is AI-generated, not something the person necessarily
    remembers verbatim."""
    key = concept_key(query_text)
    if key in concept_labels:
        return key
    query_lower = query_text.lower()
    for k, label in concept_labels.items():
        if query_lower in label.lower():
            return k
    return None

# Day 19: score alone is an internal sort key (unbounded, and mixes
# three different kinds of signal — it wouldn't mean anything useful
# printed as-is in the UI). This turns it into a three-level label the
# "related notes" panel can actually show next to each note, so
# relevance is something the person can see, not just something that
# silently affects ordering.
def _relatedness_label(score):
    if score >= 2:
        return "strong"
    if score >= 1:
        return "moderate"
    return "light"


def _build_concept_graph(rows):
    """Derives the whole concept graph from a user's note rows in one
    pass. Returns four dicts, all keyed by concept_key():
      concept_notes[key]     -> list[{"id", "title"}] of notes mentioning it
      concept_labels[key]    -> display label (most common casing seen)
      adjacency[key][other]  -> int, how many notes mention both
      note_concepts[note_id] -> list[key] for that note's fresh concepts
    Notes with stale or empty concepts contribute nothing (same
    "don't present outdated AI output as current" rule used everywhere
    else — see *Stale fields in notes.py's serialize()).
    """
    concept_notes = defaultdict(list)
    label_counts = defaultdict(Counter)
    adjacency = defaultdict(lambda: defaultdict(int))
    note_concepts = {}

    for row in rows:
        concepts = parse_concepts_json(row["concepts"])
        stale = bool(concepts) and row["concepts_source_hash"] != text_hash(row["text"])
        if not concepts or stale:
            continue

        keys = []
        for c in concepts:
            k = concept_key(c)
            if not k:
                continue
            keys.append(k)
            label_counts[k][c] += 1
            concept_notes[k].append({"id": row["id"], "title": row["title"] or "Untitled note"})

        unique_keys = sorted(set(keys))
        note_concepts[row["id"]] = unique_keys
        for i in range(len(unique_keys)):
            for j in range(i + 1, len(unique_keys)):
                a, b = unique_keys[i], unique_keys[j]
                adjacency[a][b] += 1
                adjacency[b][a] += 1

    concept_labels = {k: counter.most_common(1)[0][0] for k, counter in label_counts.items()}
    return concept_notes, concept_labels, adjacency, note_concepts


def _fetch_user_notes(db):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM notes WHERE user_id = %s", (current_user_id(),))
        return cur.fetchall()


@bp.get("/graph")
@login_required
def get_graph():
    """Returns the whole graph for the current account: every note that
    has at least one current concept, every distinct concept, and the
    mentions/co-occurs edges between them. Notes with no concepts yet
    (never extracted, or too short to bother) simply don't appear —
    they'd just be a disconnected dot with no edges."""
    db = get_db()
    rows = _fetch_user_notes(db)
    row_by_id = {r["id"]: r for r in rows}

    concept_notes, concept_labels, adjacency, note_concepts = _build_concept_graph(rows)

    nodes = []
    for note_id, keys in note_concepts.items():
        r = row_by_id[note_id]
        nodes.append(
            {
                "id": f"note:{note_id}",
                "type": "note",
                "label": r["title"] or "Untitled note",
                "noteId": note_id,
                "pinned": r["pinned"],
                "conceptCount": len(keys),
            }
        )
    for key, label in concept_labels.items():
        nodes.append(
            {
                "id": f"concept:{key}",
                "type": "concept",
                "label": label,
                "noteCount": len(concept_notes[key]),
            }
        )

    edges = []
    for note_id, keys in note_concepts.items():
        for k in keys:
            edges.append({"source": f"note:{note_id}", "target": f"concept:{k}", "type": "mentions"})

    seen_pairs = set()
    for a, neighbors in adjacency.items():
        for b, weight in neighbors.items():
            pair = tuple(sorted((a, b)))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            edges.append(
                {"source": f"concept:{pair[0]}", "target": f"concept:{pair[1]}", "type": "co-occurs", "weight": weight}
            )

    return jsonify({"nodes": nodes, "edges": edges}), 200


@bp.get("/graph/path")
@login_required
def find_path():
    """BFS shortest path between two concepts, e.g.
    ?from=Machine+Learning&to=Evaluation+Metrics — the concrete
    "discover a hidden connection across notes" feature: the two
    concepts don't need to ever appear in the same note, as long as a
    chain of co-occurrences connects them. Each hop in the response
    lists which note(s) actually establish that link, so the
    connection is traceable back to real notes, not just asserted."""
    from_q = (request.args.get("from") or "").strip()
    to_q = (request.args.get("to") or "").strip()
    if not from_q or not to_q:
        return jsonify({"error": "Both 'from' and 'to' query params are required"}), 400

    db = get_db()
    rows = _fetch_user_notes(db)
    concept_notes, concept_labels, adjacency, _ = _build_concept_graph(rows)

    def resolve(query_text):
        return _resolve_concept(concept_labels, query_text)

    from_key = resolve(from_q)
    to_key = resolve(to_q)
    if not from_key or not to_key:
        return (
            jsonify(
                {
                    "path": None,
                    "error": "Concept not found",
                    "foundFrom": from_key is not None,
                    "foundTo": to_key is not None,
                }
            ),
            404,
        )

    if from_key == to_key:
        return jsonify({"path": [{"concept": concept_labels[from_key], "notes": concept_notes[from_key][:MAX_NOTES_PER_CONCEPT_IN_RESPONSE]}], "hops": []}), 200

    visited = {from_key}
    parent = {}
    queue = deque([from_key])
    found = False
    while queue:
        current = queue.popleft()
        if current == to_key:
            found = True
            break
        for neighbor in adjacency.get(current, {}):
            if neighbor in visited:
                continue
            visited.add(neighbor)
            parent[neighbor] = current
            queue.append(neighbor)

    if not found:
        return (
            jsonify(
                {
                    "path": None,
                    "message": (
                        f'No connection found (yet) between "{concept_labels[from_key]}" '
                        f'and "{concept_labels[to_key]}".'
                    ),
                }
            ),
            200,
        )

    path_keys = [to_key]
    while path_keys[-1] != from_key:
        path_keys.append(parent[path_keys[-1]])
    path_keys.reverse()

    steps = [
        {"concept": concept_labels[k], "notes": concept_notes[k][:MAX_NOTES_PER_CONCEPT_IN_RESPONSE]}
        for k in path_keys
    ]

    hops = []
    for i in range(len(path_keys) - 1):
        a, b = path_keys[i], path_keys[i + 1]
        notes_a = {n["id"]: n for n in concept_notes[a]}
        notes_b_ids = {n["id"] for n in concept_notes[b]}
        connecting = [n for nid, n in notes_a.items() if nid in notes_b_ids]
        hops.append({"from": concept_labels[a], "to": concept_labels[b], "connectingNotes": connecting})

    return jsonify({"path": steps, "hops": hops}), 200


@bp.get("/notes/<int:note_id>/related")
@login_required
def related_notes(note_id):
    """Notes related to `note_id`, ranked by how many concepts/tags they
    share, with embedding similarity (Day 17) as a secondary signal —
    both for fine-grained ordering between otherwise-tied notes, and to
    surface a genuinely related note that just doesn't share an exact
    concept string yet (e.g. AI phrased one note's concept as "Neural
    Networks" and another's as "Deep Learning")."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT * FROM notes WHERE id = %s AND user_id = %s", (note_id, current_user_id()))
        target = cur.fetchone()
    if not target:
        return jsonify({"error": "Note not found"}), 404

    target_concepts = parse_concepts_json(target["concepts"])
    target_concepts_stale = bool(target_concepts) and target["concepts_source_hash"] != text_hash(target["text"])
    target_concept_keys = set() if target_concepts_stale else {concept_key(c) for c in target_concepts}

    target_tags = set(parse_tags_json(target["tags"]))
    target_tags_stale = bool(target_tags) and target["tags_source_hash"] != text_hash(target["text"])
    if target_tags_stale:
        target_tags = set()

    target_embedding = None
    if target["embedding"] and target["embedding_source_hash"] == text_hash(target["text"]):
        target_embedding = decode_json(target["embedding"])

    rows = [r for r in _fetch_user_notes(db) if r["id"] != note_id]

    results = []
    for r in rows:
        concepts_r = parse_concepts_json(r["concepts"])
        stale_r = bool(concepts_r) and r["concepts_source_hash"] != text_hash(r["text"])
        concepts_r = [] if stale_r else concepts_r
        shared_concepts = [c for c in concepts_r if concept_key(c) in target_concept_keys]

        tags_r = parse_tags_json(r["tags"])
        tags_r_stale = bool(tags_r) and r["tags_source_hash"] != text_hash(r["text"])
        tags_r = [] if tags_r_stale else tags_r
        shared_tags = sorted(set(tags_r) & target_tags)

        semantic_sim = 0.0
        if target_embedding and r["embedding"] and r["embedding_source_hash"] == text_hash(r["text"]):
            vec_r = decode_json(r["embedding"])
            if vec_r:
                semantic_sim = max(0.0, cosine_similarity(target_embedding, vec_r))

        has_structural_overlap = bool(shared_concepts) or bool(shared_tags)
        if not has_structural_overlap and semantic_sim < MIN_SEMANTIC_ONLY_SIMILARITY:
            continue

        score = 2 * len(shared_concepts) + len(shared_tags) + semantic_sim
        results.append(
            {
                "note": {
                    "id": r["id"],
                    "title": r["title"] or "Untitled note",
                    "updatedAt": r["updated_at"].isoformat(),
                    "pinned": r["pinned"],
                },
                "sharedConcepts": shared_concepts,
                "sharedTags": shared_tags,
                "score": round(score, 4),
                "relatedness": _relatedness_label(score),
            }
        )

    results.sort(key=lambda x: x["score"], reverse=True)
    return jsonify(results[:MAX_RELATED_NOTES]), 200


@bp.post("/graph/explain")
@login_required
def explain_graph():
    """Day 29 — AI-powered graph insight. Two mutually-exclusive modes,
    both built entirely from the graph's own derived data (never free
    text from the client), so the prompt can't be hijacked:

      {"concept": "Machine Learning"}
        -> explains that one concept using its directly co-occurring
           concepts and a few notes that mention it.

      {"path": ["Machine Learning", "Python", "Evaluation Metrics"]}
        -> explains the chain a path-finder search already returned,
           i.e. *why* those hops connect (same connecting-notes data
           /graph/path itself computed).

    Both are read straight off this account's own notes, so there's
    nothing here for a prompt-injection attempt to reach beyond what
    /graph and /graph/path already expose.
    """
    body = request.get_json(silent=True) or {}
    concept_q = (body.get("concept") or "").strip()
    path_q = body.get("path")

    if concept_q and path_q:
        return jsonify({"error": "Provide either 'concept' or 'path', not both."}), 400
    if not concept_q and not path_q:
        return jsonify({"error": "Provide a 'concept' name or a 'path' list of concept names."}), 400
    if path_q is not None:
        if not isinstance(path_q, list) or len(path_q) < 2:
            return jsonify({"error": "'path' must be a list of at least 2 concept names."}), 400
        if len(path_q) > MAX_EXPLAIN_PATH_LENGTH:
            return jsonify({"error": f"'path' is too long (max {MAX_EXPLAIN_PATH_LENGTH} concepts)."}), 400
        if not all(isinstance(p, str) and p.strip() for p in path_q):
            return jsonify({"error": "'path' entries must be non-empty strings."}), 400

    db = get_db()
    rows = _fetch_user_notes(db)
    concept_notes, concept_labels, adjacency, _ = _build_concept_graph(rows)

    if concept_q:
        key = _resolve_concept(concept_labels, concept_q)
        if not key:
            return jsonify({"error": f'No concept matching "{concept_q}" was found.'}), 404

        label = concept_labels[key]
        neighbors = sorted(adjacency.get(key, {}).items(), key=lambda kv: kv[1], reverse=True)
        neighbor_labels = [concept_labels[k] for k, _ in neighbors[:MAX_EXPLAIN_CONNECTED_CONCEPTS]]
        note_titles = [n["title"] for n in concept_notes.get(key, [])[:MAX_EXPLAIN_NOTES]]

        context_lines = [f'Concept: "{label}"']
        if neighbor_labels:
            context_lines.append("Directly related concepts (from the same notes): " + ", ".join(neighbor_labels))
        if note_titles:
            context_lines.append("Notes that mention it: " + "; ".join(note_titles))
        context = "\n".join(context_lines)

        system = (
            "You explain how ideas connect inside someone's personal notes app, using only the "
            "context given — never invent facts beyond it. Reply with only the explanation, no preamble."
        )
        prompt = (
            "Using only the context below, write a 2-3 sentence plain-language explanation of what "
            "this concept is about and how it relates to the other concepts/notes listed:\n\n" + context
        )
    else:
        resolved = [(_resolve_concept(concept_labels, q), q) for q in path_q]
        missing = [q for k, q in resolved if not k]
        if missing:
            return jsonify({"error": f"Concept(s) not found: {', '.join(missing)}."}), 404
        keys = [k for k, _ in resolved]
        labels = [concept_labels[k] for k in keys]

        hop_lines = []
        for i in range(len(keys) - 1):
            a, b = keys[i], keys[i + 1]
            notes_a = {n["id"]: n["title"] for n in concept_notes.get(a, [])}
            notes_b_ids = {n["id"] for n in concept_notes.get(b, [])}
            connecting = [title for nid, title in notes_a.items() if nid in notes_b_ids]
            hop_desc = f'"{concept_labels[a]}" → "{concept_labels[b]}"'
            if connecting:
                hop_desc += " (via note(s): " + "; ".join(connecting[:MAX_EXPLAIN_NOTES]) + ")"
            hop_lines.append(hop_desc)

        context = "Chain: " + " → ".join(labels) + "\nHops:\n" + "\n".join(hop_lines)
        system = (
            "You explain how a chain of ideas connects inside someone's personal notes app, using "
            "only the context given — never invent facts beyond it. Reply with only the explanation, "
            "no preamble."
        )
        prompt = (
            "Using only the context below, write a short (2-4 sentence) plain-language explanation of "
            "how this chain of concepts links together across the person's notes:\n\n" + context
        )

    try:
        result = call_ai(prompt, system=system)
        return jsonify({"result": result}), 200
    except AIUnavailable as e:
        return jsonify({"error": str(e)}), 501
