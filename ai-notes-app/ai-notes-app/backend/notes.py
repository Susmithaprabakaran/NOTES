# ============================================================
# AI Notes — Day 8 — notes API  (+ Day 12 summaries, Day 14 tags)
# Every route requires a logged-in session (via auth.login_required)
# and every query is scoped to the current user's own notes —
# users can never see or modify each other's notes.
# ============================================================
import difflib
import hashlib
import json

from flask import Blueprint, jsonify, request, session

from auth import login_required, current_user_id
from db import get_db
from ai_client import AIUnavailable, ai_status, call_ai
from html_sanitize import sanitize_note_html
from embeddings import (
    embed_text,
    embed_texts,
    cosine_similarity,
    encode_json,
    decode_json,
    embeddings_status,
)

bp = Blueprint("notes", __name__, url_prefix="/api")

# Day 26: manual priority levels, plus the date-range presets used by the
# new filter bar. Both validated here rather than trusted from the client.
PRIORITY_VALUES = ("none", "low", "medium", "high")
DATE_RANGE_VALUES = ("today", "week", "month", "all")

# Notes shorter than this aren't worth summarizing — a 6-word shopping
# list doesn't need an AI-generated summary of itself.
MIN_WORDS_FOR_SUMMARY = 25

SUMMARY_SYSTEM_PROMPT = "You summarize personal notes. Reply with only the summary, no preamble."
SUMMARY_PROMPT_TEMPLATE = (
    "Summarize the following note in 1-2 short sentences, keeping the key points:\n\n{text}"
)

# Tagging can work on shorter notes than summarization — even a single
# clear sentence has enough signal to categorize ("groceries" -> #shopping).
MIN_WORDS_FOR_TAGS = 12
MAX_TAGS = 5
MAX_TAG_LENGTH = 24

TAGS_SYSTEM_PROMPT = (
    "You categorize personal notes with short tags. Reply with ONLY a JSON array of "
    '1-5 short lowercase tags (single words or short two-word phrases, e.g. '
    '["work", "travel", "recipe", "finance"]). Pick general, reusable categories a '
    "person might use to browse their notes later — not a restatement of the note's "
    "content. No preamble, no markdown formatting, no explanation — just the JSON array."
)
TAGS_PROMPT_TEMPLATE = "Suggest categorization tags for this note:\n\n{text}"

# Entity recognition can work on fairly short notes too — even a single
# sentence often names a person, date, place, or organization worth
# surfacing.
MIN_WORDS_FOR_ENTITIES = 10
MAX_ENTITIES_PER_CATEGORY = 8
MAX_ENTITY_LENGTH = 60
ENTITY_CATEGORIES = ("people", "dates", "places", "organizations")

ENTITIES_SYSTEM_PROMPT = (
    "You extract named entities from personal notes. Reply with ONLY a JSON object "
    'with exactly four keys — "people", "dates", "places", "organizations" — each a '
    "JSON array of short strings copied or lightly normalized from the note (e.g. "
    '{"people": ["Sarah", "Dr. Kim"], "dates": ["next Friday", "March 3rd"], '
    '"places": ["Coimbatore"], "organizations": ["WebenoID Technologies"]}). '
    '"people" covers named individuals (not roles like "the manager"); "dates" '
    'covers explicit dates, days, or relative times ("March 3rd", "tomorrow", '
    '"next Tuesday"); "places" covers named locations (cities, countries, '
    'addresses); "organizations" covers named companies, teams, or institutions. '
    "Only include entities actually mentioned in the note. Use empty arrays for "
    "any category with nothing to report. No preamble, no markdown formatting, "
    "no explanation — just the JSON object."
)
ENTITIES_PROMPT_TEMPLATE = (
    "Extract people, dates, places, and organizations mentioned in this note:\n\n{text}"
)

# Concepts feed the Day 18 knowledge graph, so they skew toward specific,
# reusable topical nouns/noun-phrases (technologies, fields, methods,
# named ideas) rather than broad categories (that's what tags are for)
# or people/dates/places (that's what entities are for).
MIN_WORDS_FOR_CONCEPTS = 8
MAX_CONCEPTS = 8
MAX_CONCEPT_LENGTH = 60

CONCEPTS_SYSTEM_PROMPT = (
    "You extract the key concepts discussed in a personal note — specific "
    "topics, technologies, fields, methods, or named ideas (e.g. "
    '"Machine Learning", "Python", "Scikit-learn", "Classification", '
    '"Evaluation Metrics"). Reply with ONLY a JSON array of 2-8 short '
    "noun phrases, each 1-4 words, in the same capitalization style the "
    "note uses for that term when possible. Do NOT include people, "
    "dates, places, or organizations (those are handled separately) — "
    "only ideas/topics/technologies actually discussed. Do NOT include "
    "broad generic categories like \"work\" or \"personal\" (also handled "
    "separately). If there are no clear concepts, reply with exactly []. "
    "No preamble, no markdown formatting, no explanation — just the JSON "
    "array."
)
CONCEPTS_PROMPT_TEMPLATE = "Extract the key concepts/topics discussed in this note:\n\n{text}"

# ------------------------------------------------------------
# Day 17 — semantic search
#
# Every note gets embedded (title + text) once its content settles, same
# hash-caching pattern as summary/tags/entities. A search combines two
# independent relevance signals:
#   - a semantic score: cosine similarity between the query's embedding
#     and each note's embedding, so "what did we decide about pricing"
#     can match a note that says "we landed on tiered pricing" even
#     though the words barely overlap
#   - a keyword score: classic TF-IDF cosine similarity over the note
#     corpus, which still wins for exact names, numbers, and jargon
#     that a semantic model tends to blur together
# combined into one hybrid ranking. If the embedding model isn't
# available (see embeddings.embeddings_status), the semantic term drops
# out and hybrid search quietly becomes keyword-only search rather than
# breaking.
# ------------------------------------------------------------
SEMANTIC_WEIGHT = 0.6
KEYWORD_WEIGHT = 0.4
MAX_SEARCH_RESULTS = 50
# How many notes with a missing/stale embedding a single search request
# will backfill inline before ranking. Keeps a search over a large,
# never-before-embedded note collection from turning into a slow
# one-off batch job on someone's very first Day 17 search — the rest
# just fall back to keyword-only scoring for that request and pick up
# an embedding the next time they're touched (auto-embed) or searched.
MAX_INLINE_EMBED_BACKFILL = 40


def word_count(text):
    text = (text or "").strip()
    return len(text.split()) if text else 0


def text_hash(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def parse_tags_json(raw):
    """Parses the stored `tags` column (a JSON string) into a plain list
    of strings. Never raises — a corrupt/empty value just yields an
    empty list, since a broken tag list shouldn't break the note."""
    try:
        data = json.loads(raw) if raw else []
        return [t for t in data if isinstance(t, str) and t.strip()] if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def parse_ai_tag_list(raw_reply):
    """Tolerant parse of the AI's reply into a normalized list of tags.
    Models sometimes wrap the array in prose or ```json fences despite
    instructions not to — scan for the outermost [...] instead of
    requiring the whole reply to be clean JSON."""
    raw = (raw_reply or "").strip()
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        items = json.loads(raw[start : end + 1])
    except (ValueError, TypeError):
        return []
    if not isinstance(items, list):
        return []

    tags = []
    seen = set()
    for item in items:
        if not isinstance(item, str):
            continue
        # Normalize: lowercase, collapse whitespace, strip stray punctuation
        # the model sometimes adds ("#work" or "Work." -> "work").
        tag = " ".join(item.lower().strip().strip("#.").split())
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag[:MAX_TAG_LENGTH])
        if len(tags) >= MAX_TAGS:
            break
    return tags


def parse_entities_json(raw):
    """Parses the stored `entities` column into a normalized dict with
    all three category keys always present. Never raises — a corrupt
    value just yields all-empty lists."""
    empty = {cat: [] for cat in ENTITY_CATEGORIES}
    try:
        data = json.loads(raw) if raw else {}
        if not isinstance(data, dict):
            return empty
        return {
            cat: [v for v in data.get(cat, []) if isinstance(v, str) and v.strip()]
            for cat in ENTITY_CATEGORIES
        }
    except (json.JSONDecodeError, TypeError):
        return empty


def parse_ai_entities(raw_reply):
    """Tolerant parse of the AI's {"people":[...],"dates":[...],"places":[...]}
    reply, same scan-for-the-outer-braces approach as parse_ai_tag_list
    (models occasionally wrap JSON in prose despite instructions not to)."""
    empty = {cat: [] for cat in ENTITY_CATEGORIES}
    raw = (raw_reply or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return empty
    try:
        data = json.loads(raw[start : end + 1])
    except (ValueError, TypeError):
        return empty
    if not isinstance(data, dict):
        return empty

    result = {}
    for cat in ENTITY_CATEGORIES:
        items = data.get(cat, [])
        if not isinstance(items, list):
            items = []
        values, seen = [], set()
        for item in items:
            if not isinstance(item, str):
                continue
            # Light normalization only — unlike tags, entities are proper
            # nouns/dates whose original casing matters ("Sarah", not "sarah").
            value = " ".join(item.strip().split())
            key = value.lower()
            if value and key not in seen:
                seen.add(key)
                values.append(value[:MAX_ENTITY_LENGTH])
            if len(values) >= MAX_ENTITIES_PER_CATEGORY:
                break
        result[cat] = values
    return result


def concept_key(concept):
    """Canonical form of a concept string, used to decide whether two
    concepts (possibly from different notes, possibly cased/spaced
    differently — "Scikit-learn" vs "scikit learn") are the same graph
    node. Display keeps the original casing; only this key is used for
    matching/deduping."""
    return " ".join((concept or "").strip().lower().split())


def parse_concepts_json(raw):
    """Parses the stored `concepts` column into a plain list of display
    strings. Never raises — mirrors parse_tags_json."""
    try:
        data = json.loads(raw) if raw else []
        return [c for c in data if isinstance(c, str) and c.strip()] if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def parse_ai_concept_list(raw_reply):
    """Tolerant parse of the AI's concept-list reply, same
    scan-for-outer-brackets approach as parse_ai_tag_list. Unlike tags,
    concepts keep their original casing (they're meant to read as
    proper technical/topical terms — "Scikit-learn", not "scikit-learn")
    but are deduped case-insensitively within one note's list."""
    raw = (raw_reply or "").strip()
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        items = json.loads(raw[start : end + 1])
    except (ValueError, TypeError):
        return []
    if not isinstance(items, list):
        return []

    concepts = []
    seen = set()
    for item in items:
        if not isinstance(item, str):
            continue
        value = " ".join(item.strip().strip("#.").split())
        key = concept_key(value)
        if value and key not in seen:
            seen.add(key)
            concepts.append(value[:MAX_CONCEPT_LENGTH])
        if len(concepts) >= MAX_CONCEPTS:
            break
    return concepts


def serialize(row):
    tags = parse_tags_json(row["tags"])
    entities = parse_entities_json(row["entities"])
    has_entities = any(entities[cat] for cat in ENTITY_CATEGORIES)
    concepts = parse_concepts_json(row["concepts"])
    return {
        "id": row["id"],
        "title": row["title"],
        "text": row["text"],
        # Day 30 — the rich-text HTML rendering of `text` above (bold/
        # italic/fonts/colors/lists/alignment/math formulas). May be
        # None for notes created before Day 30 or via the API directly;
        # the frontend falls back to rendering `text` as plain lines.
        "contentHtml": row["content_html"],
        "pinned": row["pinned"],
        "priority": row["priority"] if row["priority"] in PRIORITY_VALUES else "none",
        "createdAt": row["created_at"].isoformat(),
        "updatedAt": row["updated_at"].isoformat(),
        "wordCount": word_count(row["text"]),
        "summary": row["summary"],
        "summaryGeneratedAt": row["summary_generated_at"].isoformat() if row["summary_generated_at"] else None,
        # Tells the frontend whether the stored summary still matches the
        # current text. Only meaningful when a summary actually exists —
        # a note that's never been summarized isn't "stale", it's just new.
        "summaryStale": bool(row["summary"]) and row["summary_source_hash"] != text_hash(row["text"]),
        "tags": tags,
        "tagsGeneratedAt": row["tags_generated_at"].isoformat() if row["tags_generated_at"] else None,
        "tagsStale": bool(tags) and row["tags_source_hash"] != text_hash(row["text"]),
        "entities": entities,
        "entitiesGeneratedAt": row["entities_generated_at"].isoformat() if row["entities_generated_at"] else None,
        "entitiesStale": has_entities and row["entities_source_hash"] != text_hash(row["text"]),
        "concepts": concepts,
        "conceptsGeneratedAt": row["concepts_generated_at"].isoformat() if row["concepts_generated_at"] else None,
        "conceptsStale": bool(concepts) and row["concepts_source_hash"] != text_hash(row["text"]),
        # Day 17 — not the vector itself (no reason to ship 384 floats to
        # the browser), just whether one exists and is current. Search
        # works fine without this ever being true (falls back to
        # keyword-only ranking); it's exposed mainly for diagnostics.
        "hasEmbedding": bool(row["embedding"]),
        "embeddingStale": bool(row["embedding"]) and row["embedding_source_hash"] != text_hash(row["text"]),
    }


def clean_text(value):
    return value if isinstance(value, str) else ""


def clean_priority(value, default="none"):
    if value in PRIORITY_VALUES:
        return value
    return default


def date_range_sql(date_range):
    """Day 26: translate a preset ('today'|'week'|'month'|'all'/None) into
    a SQL clause + params filtering on updated_at, so "when was this last
    touched" drives the date filter rather than created_at — matches how
    the notes grid is already sorted (most-recently-updated first)."""
    if date_range == "today":
        return " AND updated_at >= date_trunc('day', now())", []
    if date_range == "week":
        return " AND updated_at >= now() - interval '7 days'", []
    if date_range == "month":
        return " AND updated_at >= now() - interval '30 days'", []
    return "", []


@bp.get("/health")
def health():
    return jsonify({"status": "ok"}), 200


@bp.get("/notes")
@login_required
def list_notes():
    pinned_only = request.args.get("pinned") == "1"
    tag = (request.args.get("tag") or "").strip().lower()
    priority = (request.args.get("priority") or "").strip().lower()
    date_range = (request.args.get("dateRange") or "").strip().lower()

    query = "SELECT * FROM notes WHERE user_id = %s"
    params = [current_user_id()]
    if pinned_only:
        query += " AND pinned = true"
    if priority in PRIORITY_VALUES:
        query += " AND priority = %s"
        params.append(priority)
    extra_sql, extra_params = date_range_sql(date_range)
    query += extra_sql
    params.extend(extra_params)
    query += " ORDER BY pinned DESC, updated_at DESC"

    db = get_db()
    with db.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    notes = [serialize(r) for r in rows]
    if tag:
        # Tags are stored as a JSON array per note rather than a normalized
        # column, so filtering happens here rather than in SQL — fine at
        # this scale (a personal notes app), and keeps the tag-matching
        # logic (normalization, etc.) in one place with parse_ai_tag_list.
        notes = [n for n in notes if tag in n["tags"]]
    return jsonify(notes), 200


@bp.get("/notes/<int:note_id>")
@login_required
def get_note(note_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM notes WHERE id = %s AND user_id = %s", (note_id, current_user_id())
        )
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "Note not found"}), 404
    return jsonify(serialize(row)), 200


@bp.post("/notes")
@login_required
def create_note():
    body = request.get_json(silent=True) or {}
    title = clean_text(body.get("title"))
    text = clean_text(body.get("text"))
    priority = clean_priority(body.get("priority"))
    content_html = sanitize_note_html(body.get("contentHtml"))

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO notes (user_id, title, text, priority, content_html)
               VALUES (%s, %s, %s, %s, %s) RETURNING *""",
            (current_user_id(), title, text, priority, content_html),
        )
        row = cur.fetchone()
    db.commit()
    return jsonify(serialize(row)), 201


@bp.put("/notes/<int:note_id>")
@login_required
def update_note(note_id):
    body = request.get_json(silent=True) or {}
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM notes WHERE id = %s AND user_id = %s", (note_id, current_user_id())
        )
        existing = cur.fetchone()
        if not existing:
            return jsonify({"error": "Note not found"}), 404

        title = clean_text(body["title"]) if "title" in body else existing["title"]
        text = clean_text(body["text"]) if "text" in body else existing["text"]
        priority = clean_priority(body["priority"], existing["priority"]) if "priority" in body else existing["priority"]
        content_html = (
            sanitize_note_html(body["contentHtml"]) if "contentHtml" in body else existing["content_html"]
        )

        cur.execute(
            """UPDATE notes SET title = %s, text = %s, priority = %s, content_html = %s, updated_at = now()
               WHERE id = %s AND user_id = %s RETURNING *""",
            (title, text, priority, content_html, note_id, current_user_id()),
        )
        row = cur.fetchone()
    db.commit()
    return jsonify(serialize(row)), 200


@bp.patch("/notes/<int:note_id>/pin")
@login_required
def toggle_pin(note_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM notes WHERE id = %s AND user_id = %s", (note_id, current_user_id())
        )
        existing = cur.fetchone()
        if not existing:
            return jsonify({"error": "Note not found"}), 404

        cur.execute(
            """UPDATE notes SET pinned = %s, updated_at = now()
               WHERE id = %s AND user_id = %s RETURNING *""",
            (not existing["pinned"], note_id, current_user_id()),
        )
        row = cur.fetchone()
    db.commit()
    return jsonify(serialize(row)), 200


@bp.patch("/notes/<int:note_id>/priority")
@login_required
def set_priority(note_id):
    """Day 26 — quick priority change from the note card / editor,
    same one-field-PATCH shape as toggle_pin above rather than routing
    through the full PUT (which would require sending title+text too)."""
    body = request.get_json(silent=True) or {}
    priority = body.get("priority")
    if priority not in PRIORITY_VALUES:
        return jsonify({"error": f"priority must be one of {list(PRIORITY_VALUES)}"}), 400

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT id FROM notes WHERE id = %s AND user_id = %s", (note_id, current_user_id())
        )
        if not cur.fetchone():
            return jsonify({"error": "Note not found"}), 404

        cur.execute(
            """UPDATE notes SET priority = %s, updated_at = now()
               WHERE id = %s AND user_id = %s RETURNING *""",
            (priority, note_id, current_user_id()),
        )
        row = cur.fetchone()
    db.commit()
    return jsonify(serialize(row)), 200


@bp.delete("/notes/<int:note_id>")
@login_required
def delete_note(note_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "DELETE FROM notes WHERE id = %s AND user_id = %s RETURNING *",
            (note_id, current_user_id()),
        )
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "Note not found"}), 404
    db.commit()
    return jsonify({"deleted": serialize(row)}), 200


def _tfidf_scores(query, corpus_texts):
    """TF-IDF cosine similarity between `query` and each text in
    `corpus_texts`, as a list of floats in [0, 1], same length/order as
    corpus_texts. Falls back to all zeros if scikit-learn isn't
    installed or there's nothing usable to vectorize (e.g. every note
    is empty) — keyword scoring degrading silently beats search
    breaking entirely."""
    if not corpus_texts:
        return []
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity as sk_cosine_similarity
    except ImportError:
        return [0.0] * len(corpus_texts)

    try:
        vectorizer = TfidfVectorizer(stop_words="english")
        matrix = vectorizer.fit_transform(corpus_texts + [query])
    except ValueError:
        # e.g. every document is empty or pure stopwords after cleaning
        return [0.0] * len(corpus_texts)

    query_vec = matrix[-1]
    corpus_matrix = matrix[:-1]
    sims = sk_cosine_similarity(corpus_matrix, query_vec).ravel()
    return [float(max(0.0, s)) for s in sims]


def _rank_by_hybrid_search(db, rows, notes, q):
    """Ranks `notes` (already scoped to one user, and already filtered
    by any pinned/tag params) against query `q` using a hybrid of
    TF-IDF keyword similarity and semantic embedding similarity.
    Returns a new list of note dicts (each with `searchScore` and
    `searchMatch` attached), sorted by score descending, with
    zero-score notes dropped.

    Missing/stale embeddings are backfilled inline (capped by
    MAX_INLINE_EMBED_BACKFILL, batched for speed) so semantic ranking
    works even for notes that predate Day 17 or were edited since their
    last embed — see the module docstring at the top of this section.
    """
    if not rows:
        return []

    keyword_scores = _tfidf_scores(q, [f"{r['title']}\n{r['text']}" for r in rows])

    semantic_available, _ = embeddings_status()
    semantic_scores = [0.0] * len(rows)

    if semantic_available:
        to_backfill = [
            i
            for i, r in enumerate(rows)
            if not r["embedding"] or r["embedding_source_hash"] != text_hash(r["text"])
        ][:MAX_INLINE_EMBED_BACKFILL]

        if to_backfill:
            texts = [f"{rows[i]['title']}\n{rows[i]['text']}".strip() for i in to_backfill]
            vecs = embed_texts(texts)
            with db.cursor() as cur:
                for idx, vec in zip(to_backfill, vecs):
                    if vec is None:
                        continue
                    current_hash = text_hash(rows[idx]["text"])
                    cur.execute(
                        """UPDATE notes SET embedding = %s, embedding_source_hash = %s,
                           embedding_generated_at = now() WHERE id = %s""",
                        (encode_json(vec), current_hash, rows[idx]["id"]),
                    )
                    # Keep the in-memory row in sync so the scoring loop
                    # below sees the embedding we just computed, without
                    # a second round-trip to the database.
                    rows[idx] = dict(rows[idx])
                    rows[idx]["embedding"] = encode_json(vec)
                    rows[idx]["embedding_source_hash"] = current_hash
            db.commit()

        query_vec = embed_text(q)
        if query_vec is not None:
            for i, r in enumerate(rows):
                note_vec = decode_json(r["embedding"]) if r["embedding"] else None
                if note_vec is not None:
                    semantic_scores[i] = max(0.0, cosine_similarity(query_vec, note_vec))

    have_any_semantic_signal = semantic_available and any(semantic_scores)
    if have_any_semantic_signal:
        hybrid_scores = [
            SEMANTIC_WEIGHT * s + KEYWORD_WEIGHT * k for s, k in zip(semantic_scores, keyword_scores)
        ]
    else:
        # No embeddings to lean on for this request (model unavailable,
        # or every candidate's embedding failed/was skipped) — fall back
        # to pure keyword ranking rather than multiplying every score by
        # a phantom 40% weight.
        hybrid_scores = keyword_scores

    scored = []
    for note, k, s, h in zip(notes, keyword_scores, semantic_scores, hybrid_scores):
        if h <= 0:
            continue
        if k > 0.05 and s > 0.05:
            match = "both"
        elif s > k:
            match = "semantic"
        else:
            match = "keyword"
        enriched = dict(note)
        enriched["searchScore"] = round(h, 4)
        enriched["searchMatch"] = match
        scored.append(enriched)

    scored.sort(key=lambda n: n["searchScore"], reverse=True)
    return scored


# ------------------------------------------------------------
# Search by note NAME (title)
#
# The sidebar search box is for finding a note by what it's called, which
# is a different job from the hybrid content search above: it has to work
# on half-typed words ("mach" -> "Machine Learning"), ignore capitalisation,
# not care about word order, and forgive a small typo. TF-IDF can't do the
# first (it only matches whole words), so this is its own small ranker:
#
#   100  the title is exactly the query
#    90  the title starts with the query
#    80  the query matches the start of a word inside the title
#    60  the query appears somewhere inside the title
#    50  every word of the query appears in the title, in any order
#  30-40 every word of the query is *close to* a word in the title (typo)
#
# Titles only — the note body is never looked at in this mode (that's what
# mode=content, the hybrid search above, is for). A note with no title is
# matched as "Untitled note", which is how the UI displays it.
# ------------------------------------------------------------
TITLE_FUZZY_CUTOFF = 0.8


def _normalize_for_title_match(value):
    return " ".join((value or "").lower().split())


def title_match_score(query, title):
    """How well `title` matches `query`, 0 (no match) to 100 (exact)."""
    q = _normalize_for_title_match(query)
    if not q:
        return 0
    t = _normalize_for_title_match(title) or "untitled note"

    if t == q:
        return 100
    if t.startswith(q):
        return 90
    if (" " + q) in (" " + t):
        return 80
    if q in t:
        return 60

    q_tokens = q.split()
    if all(tok in t for tok in q_tokens):
        return 50

    # Typo tolerance: every query word must be close to *some* title word.
    # Words under 3 characters are too short for "close" to mean anything.
    title_words = t.split()
    ratios = []
    for tok in q_tokens:
        if len(tok) < 3:
            return 0
        best = max(
            (difflib.SequenceMatcher(None, tok, w).ratio() for w in title_words),
            default=0,
        )
        if best < TITLE_FUZZY_CUTOFF:
            return 0
        ratios.append(best)
    return int(30 + 10 * (sum(ratios) / len(ratios)))


def _rank_by_title(notes, q):
    """Notes whose title matches `q`, best match first. Ties keep the
    incoming order (pinned first, then most recently updated), because
    Python's sort is stable."""
    scored = []
    for note in notes:
        score = title_match_score(q, note["title"])
        if score > 0:
            enriched = dict(note)
            enriched["searchScore"] = score
            enriched["searchMatch"] = "title"
            scored.append(enriched)
    scored.sort(key=lambda n: n["searchScore"], reverse=True)
    return scored


@bp.get("/search")
@login_required
def search_notes():
    q = (request.args.get("q") or "").strip()
    pinned_only = request.args.get("pinned") == "1"
    tag = (request.args.get("tag") or "").strip().lower()
    priority = (request.args.get("priority") or "").strip().lower()
    date_range = (request.args.get("dateRange") or "").strip().lower()
    # "title" = match note names only (what the sidebar search box uses);
    # anything else = the original hybrid keyword+semantic search over the
    # note's whole text ("content"), which stays the default so existing
    # API callers behave exactly as before.
    mode = (request.args.get("mode") or "content").strip().lower()

    db = get_db()
    with db.cursor() as cur:
        query = "SELECT * FROM notes WHERE user_id = %s"
        params = [current_user_id()]
        if pinned_only:
            query += " AND pinned = true"
        if priority in PRIORITY_VALUES:
            query += " AND priority = %s"
            params.append(priority)
        extra_sql, extra_params = date_range_sql(date_range)
        query += extra_sql
        params.extend(extra_params)
        query += " ORDER BY pinned DESC, updated_at DESC"
        cur.execute(query, params)
        rows = cur.fetchall()

    notes = [serialize(r) for r in rows]
    if tag:
        # Tags are stored as a JSON array per note rather than a normalized
        # column, so filtering happens here rather than in SQL — fine at
        # this scale (a personal notes app), and keeps the tag-matching
        # logic (normalization, etc.) in one place with parse_ai_tag_list.
        keep_ids = {n["id"] for n in notes if tag in n["tags"]}
        rows = [r for r in rows if r["id"] in keep_ids]
        notes = [n for n in notes if n["id"] in keep_ids]

    if not q:
        return jsonify(notes), 200

    if mode == "title":
        return jsonify(_rank_by_title(notes, q)[:MAX_SEARCH_RESULTS]), 200

    ranked = _rank_by_hybrid_search(db, rows, notes, q)
    return jsonify(ranked[:MAX_SEARCH_RESULTS]), 200


@bp.get("/search/compare")
@login_required
def compare_search():
    """Day 17 evaluation endpoint: runs one query through keyword-only
    (TF-IDF) ranking and hybrid (semantic + TF-IDF) ranking side by
    side, plus a few honest, computable comparison numbers — not a
    substitute for a real user study, but enough to see concretely what
    semantic search adds (or doesn't) over keyword search alone for a
    given query against this account's actual notes."""
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"error": "q is required"}), 400

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM notes WHERE user_id = %s ORDER BY updated_at DESC",
            (current_user_id(),),
        )
        rows = cur.fetchall()

    semantic_available, semantic_reason = embeddings_status()

    if not rows:
        return (
            jsonify(
                {
                    "query": q,
                    "keywordResults": [],
                    "hybridResults": [],
                    "metrics": {"keywordCount": 0, "hybridCount": 0, "onlyInHybrid": 0, "top5Overlap": None},
                    "semanticSearchAvailable": semantic_available,
                    "semanticSearchReason": None if semantic_available else semantic_reason,
                }
            ),
            200,
        )

    notes = [serialize(r) for r in rows]
    keyword_scores = _tfidf_scores(q, [f"{r['title']}\n{r['text']}" for r in rows])
    keyword_ranked = sorted(
        (
            {"id": n["id"], "title": n["title"] or "Untitled note", "score": round(sc, 4)}
            for n, sc in zip(notes, keyword_scores)
            if sc > 0
        ),
        key=lambda x: x["score"],
        reverse=True,
    )

    hybrid_full = _rank_by_hybrid_search(db, rows, notes, q)
    hybrid_ranked = [
        {
            "id": n["id"],
            "title": n["title"] or "Untitled note",
            "score": n["searchScore"],
            "match": n["searchMatch"],
        }
        for n in hybrid_full
    ]

    keyword_ids = {r["id"] for r in keyword_ranked}
    hybrid_ids_top5 = {r["id"] for r in hybrid_ranked[:5]}
    keyword_ids_top5 = {r["id"] for r in keyword_ranked[:5]}
    only_in_hybrid = sum(1 for r in hybrid_ranked if r["id"] not in keyword_ids)
    top5_overlap = (
        round(len(hybrid_ids_top5 & keyword_ids_top5) / len(hybrid_ids_top5), 2) if hybrid_ids_top5 else None
    )

    return (
        jsonify(
            {
                "query": q,
                "keywordResults": keyword_ranked,
                "hybridResults": hybrid_ranked,
                "metrics": {
                    "keywordCount": len(keyword_ranked),
                    "hybridCount": len(hybrid_ranked),
                    "onlyInHybrid": only_in_hybrid,
                    "top5Overlap": top5_overlap,
                },
                "semanticSearchAvailable": semantic_available,
                "semanticSearchReason": None if semantic_available else semantic_reason,
            }
        ),
        200,
    )


# ============================================================
# Day 17 — automatic embedding generation
# Same "no button" background pattern as Day 12/14/15 — shares the same
# debounce timer on the frontend (see scheduleAutoSummary). Keeps each
# note's embedding fresh as it's edited so most searches don't need to
# backfill anything inline; entirely invisible in the UI (no panel),
# it's infrastructure for /api/search, not user-facing content.
# ============================================================
@bp.post("/notes/<int:note_id>/auto-embed")
@login_required
def auto_embed(note_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM notes WHERE id = %s AND user_id = %s", (note_id, current_user_id())
        )
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "Note not found"}), 404

    text_for_embedding = f"{row['title']}\n{row['text']}".strip()
    current_hash = text_hash(row["text"])

    if not text_for_embedding:
        return jsonify({"note": serialize(row), "skipped": "empty"}), 200

    if row["embedding"] and row["embedding_source_hash"] == current_hash:
        return jsonify({"note": serialize(row), "skipped": "unchanged"}), 200

    available, reason = embeddings_status()
    if not available:
        return jsonify({"error": reason, "note": serialize(row)}), 501

    vec = embed_text(text_for_embedding)
    if vec is None:
        return jsonify({"error": "Failed to generate embedding", "note": serialize(row)}), 501

    with db.cursor() as cur:
        cur.execute(
            """UPDATE notes SET embedding = %s, embedding_source_hash = %s,
               embedding_generated_at = now() WHERE id = %s AND user_id = %s RETURNING *""",
            (encode_json(vec), current_hash, note_id, current_user_id()),
        )
        row = cur.fetchone()
    db.commit()
    return jsonify({"note": serialize(row)}), 200


# ============================================================
# Day 12 — automatic summarization
#
# Not a user-facing "click to summarize" button (that's the Day 11
# ✨ menu). This is called automatically by the frontend a few seconds
# after the person stops editing a note, so a summary just appears —
# no action required. It's deliberately cheap to call repeatedly:
#   - notes under MIN_WORDS_FOR_SUMMARY skip the AI call entirely
#   - unchanged text (same hash as last time) skips the AI call too,
#     so re-triggering this on an idle note never re-bills a request
# ============================================================
@bp.post("/notes/<int:note_id>/auto-summarize")
@login_required
def auto_summarize(note_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM notes WHERE id = %s AND user_id = %s", (note_id, current_user_id())
        )
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "Note not found"}), 404

    text = row["text"] or ""

    if word_count(text) < MIN_WORDS_FOR_SUMMARY:
        # Too short to bother — clear any now-stale summary from before
        # the note was edited down, rather than showing an outdated one.
        if row["summary"] is not None:
            with db.cursor() as cur:
                cur.execute(
                    """UPDATE notes SET summary = NULL, summary_source_hash = NULL,
                       summary_generated_at = NULL WHERE id = %s AND user_id = %s RETURNING *""",
                    (note_id, current_user_id()),
                )
                row = cur.fetchone()
            db.commit()
        return jsonify({"note": serialize(row), "skipped": "too_short"}), 200

    current_hash = text_hash(text)
    if row["summary"] and row["summary_source_hash"] == current_hash:
        return jsonify({"note": serialize(row), "skipped": "unchanged"}), 200

    available, provider, reason = ai_status()
    if not available:
        # Not an error state for the app — just means no AI model is
        # connected (see Day 11 setup). The note itself is fine.
        return jsonify({"error": reason, "note": serialize(row)}), 501

    try:
        summary = call_ai(SUMMARY_PROMPT_TEMPLATE.format(text=text), system=SUMMARY_SYSTEM_PROMPT)
    except AIUnavailable as e:
        return jsonify({"error": str(e), "note": serialize(row)}), 501

    with db.cursor() as cur:
        cur.execute(
            """UPDATE notes SET summary = %s, summary_source_hash = %s, summary_generated_at = now()
               WHERE id = %s AND user_id = %s RETURNING *""",
            (summary, current_hash, note_id, current_user_id()),
        )
        row = cur.fetchone()
    db.commit()
    return jsonify({"note": serialize(row)}), 200


# ============================================================
# Day 14 — automatic tagging and categorization
#
# Same automatic, no-button pattern as Day 12's auto-summarize: the
# frontend calls this a couple seconds after the person stops editing,
# and tags just appear. Skips the AI call for very short notes or
# unchanged text, same as summarization and (Day 13) task extraction.
# ============================================================
@bp.post("/notes/<int:note_id>/auto-tag")
@login_required
def auto_tag(note_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM notes WHERE id = %s AND user_id = %s", (note_id, current_user_id())
        )
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "Note not found"}), 404

    text = row["text"] or ""

    if word_count(text) < MIN_WORDS_FOR_TAGS:
        if row["tags"] and row["tags"] != "[]":
            with db.cursor() as cur:
                cur.execute(
                    """UPDATE notes SET tags = '[]', tags_source_hash = NULL,
                       tags_generated_at = NULL WHERE id = %s AND user_id = %s RETURNING *""",
                    (note_id, current_user_id()),
                )
                row = cur.fetchone()
            db.commit()
        return jsonify({"note": serialize(row), "skipped": "too_short"}), 200

    current_hash = text_hash(text)
    if row["tags_source_hash"] == current_hash:
        return jsonify({"note": serialize(row), "skipped": "unchanged"}), 200

    available, provider, reason = ai_status()
    if not available:
        return jsonify({"error": reason, "note": serialize(row)}), 501

    try:
        raw_reply = call_ai(TAGS_PROMPT_TEMPLATE.format(text=text), system=TAGS_SYSTEM_PROMPT)
    except AIUnavailable as e:
        return jsonify({"error": str(e), "note": serialize(row)}), 501

    tags = parse_ai_tag_list(raw_reply)

    with db.cursor() as cur:
        cur.execute(
            """UPDATE notes SET tags = %s, tags_source_hash = %s, tags_generated_at = now()
               WHERE id = %s AND user_id = %s RETURNING *""",
            (json.dumps(tags), current_hash, note_id, current_user_id()),
        )
        row = cur.fetchone()
    db.commit()
    return jsonify({"note": serialize(row)}), 200


@bp.get("/tags")
@login_required
def list_tags():
    """Aggregates tags across all of the current user's notes, for
    building a browsable tag list/cloud in the sidebar. Counts only
    tags on notes whose tags are still fresh (not stale relative to
    the note's current text) — a stale tag reflects an older version
    of the note and shouldn't be presented as a live category."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT * FROM notes WHERE user_id = %s", (current_user_id(),))
        rows = cur.fetchall()

    counts = {}
    for row in rows:
        serialized = serialize(row)
        if serialized["tagsStale"]:
            continue
        for tag in serialized["tags"]:
            counts[tag] = counts.get(tag, 0) + 1

    tags = [{"tag": t, "count": c} for t, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return jsonify(tags), 200


# ============================================================
# Day 15 — automatic entity recognition (people, dates, places)
#
# Same automatic, no-button, debounced-from-the-frontend pattern as
# Day 12's summary and Day 14's tags: a few seconds after the person
# stops editing, this runs and the results just appear. Skips the AI
# call for very short notes or unchanged text, same reasoning as the
# other two.
#
# One thing this endpoint is deliberately NOT doing: no attempt to
# resolve entities to canonical identities (e.g. merging "Sarah" and
# "Sarah Chen" as the same person, or parsing "next Friday" into an
# actual date). It surfaces what the AI found, as the AI phrased it —
# good enough for skimming a note at a glance, not a full NER/coref
# pipeline. That's a real scope limit, not an oversight.
# ============================================================
@bp.post("/notes/<int:note_id>/extract-entities")
@login_required
def extract_entities(note_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM notes WHERE id = %s AND user_id = %s", (note_id, current_user_id())
        )
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "Note not found"}), 404

    text = row["text"] or ""
    empty_json = json.dumps({cat: [] for cat in ENTITY_CATEGORIES})

    if word_count(text) < MIN_WORDS_FOR_ENTITIES:
        if row["entities"] and row["entities"] != empty_json:
            with db.cursor() as cur:
                cur.execute(
                    """UPDATE notes SET entities = %s, entities_source_hash = NULL,
                       entities_generated_at = NULL WHERE id = %s AND user_id = %s RETURNING *""",
                    (empty_json, note_id, current_user_id()),
                )
                row = cur.fetchone()
            db.commit()
        return jsonify({"note": serialize(row), "skipped": "too_short"}), 200

    current_hash = text_hash(text)
    if row["entities_source_hash"] == current_hash:
        return jsonify({"note": serialize(row), "skipped": "unchanged"}), 200

    available, provider, reason = ai_status()
    if not available:
        return jsonify({"error": reason, "note": serialize(row)}), 501

    try:
        raw_reply = call_ai(ENTITIES_PROMPT_TEMPLATE.format(text=text), system=ENTITIES_SYSTEM_PROMPT)
    except AIUnavailable as e:
        return jsonify({"error": str(e), "note": serialize(row)}), 501

    entities = parse_ai_entities(raw_reply)

    with db.cursor() as cur:
        cur.execute(
            """UPDATE notes SET entities = %s, entities_source_hash = %s, entities_generated_at = now()
               WHERE id = %s AND user_id = %s RETURNING *""",
            (json.dumps(entities), current_hash, note_id, current_user_id()),
        )
        row = cur.fetchone()
    db.commit()
    return jsonify({"note": serialize(row)}), 200


# ============================================================
# Day 18 — concept extraction (feeds the knowledge graph)
#
# Same automatic, no-button, debounced-from-the-frontend pattern as
# tags/entities. This endpoint only writes the per-note `concepts`
# column — the graph itself (nodes/edges across every note) is built
# on demand by GET /api/graph in graph.py, not stored as its own
# table. That keeps this endpoint simple and keeps the graph always
# consistent with each note's current concepts (no separate edge table
# to fall out of sync).
# ============================================================
@bp.post("/notes/<int:note_id>/extract-concepts")
@login_required
def extract_concepts(note_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM notes WHERE id = %s AND user_id = %s", (note_id, current_user_id())
        )
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "Note not found"}), 404

    text = row["text"] or ""

    if word_count(text) < MIN_WORDS_FOR_CONCEPTS:
        if row["concepts"] and row["concepts"] != "[]":
            with db.cursor() as cur:
                cur.execute(
                    """UPDATE notes SET concepts = '[]', concepts_source_hash = NULL,
                       concepts_generated_at = NULL WHERE id = %s AND user_id = %s RETURNING *""",
                    (note_id, current_user_id()),
                )
                row = cur.fetchone()
            db.commit()
        return jsonify({"note": serialize(row), "skipped": "too_short"}), 200

    current_hash = text_hash(text)
    if row["concepts_source_hash"] == current_hash:
        return jsonify({"note": serialize(row), "skipped": "unchanged"}), 200

    available, provider, reason = ai_status()
    if not available:
        return jsonify({"error": reason, "note": serialize(row)}), 501

    try:
        raw_reply = call_ai(CONCEPTS_PROMPT_TEMPLATE.format(text=text), system=CONCEPTS_SYSTEM_PROMPT)
    except AIUnavailable as e:
        return jsonify({"error": str(e), "note": serialize(row)}), 501

    concepts = parse_ai_concept_list(raw_reply)

    with db.cursor() as cur:
        cur.execute(
            """UPDATE notes SET concepts = %s, concepts_source_hash = %s, concepts_generated_at = now()
               WHERE id = %s AND user_id = %s RETURNING *""",
            (json.dumps(concepts), current_hash, note_id, current_user_id()),
        )
        row = cur.fetchone()
    db.commit()
    return jsonify({"note": serialize(row)}), 200
