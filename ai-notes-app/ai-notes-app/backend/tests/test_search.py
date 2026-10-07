import json

from conftest import register

# ------------------------------------------------------------
# A tiny fake embedding model for tests: maps text to a fixed-length
# "concept vector" based on keyword groups, so we can construct notes
# that are semantically related but share zero literal words — the
# exact scenario semantic search is supposed to catch that TF-IDF
# keyword search can't. No real model download needed to run these.
# ------------------------------------------------------------
CONCEPTS = [
    ["pricing", "price", "cost", "tiered", "budget"],
    ["meeting", "standup", "sync", "roadmap"],
    ["travel", "flight", "trip", "hotel"],
    ["grocery", "milk", "eggs", "bread"],
]


def fake_embed_text(text):
    text = (text or "").lower()
    vec = [1.0 if any(kw in text for kw in group) else 0.0 for group in CONCEPTS]
    return vec if any(vec) else [0.0] * len(CONCEPTS)


def fake_embed_texts(texts):
    return [fake_embed_text(t) if (t or "").strip() else None for t in texts]


def patch_embeddings_available(monkeypatch):
    import notes

    monkeypatch.setattr(notes, "embeddings_status", lambda: (True, None))
    monkeypatch.setattr(notes, "embed_text", fake_embed_text)
    monkeypatch.setattr(notes, "embed_texts", fake_embed_texts)


def patch_embeddings_unavailable(monkeypatch):
    import notes

    monkeypatch.setattr(notes, "embeddings_status", lambda: (False, "sentence-transformers is not installed"))
    monkeypatch.setattr(notes, "embed_text", lambda text: None)
    monkeypatch.setattr(notes, "embed_texts", lambda texts: [None] * len(texts))


def create_note(client, title="", text=""):
    res = client.post("/api/notes", json={"title": title, "text": text})
    return res.get_json()


# ============================================================
# /api/search — hybrid ranking
# ============================================================
def test_search_requires_login(client):
    res = client.get("/api/search?q=pricing")
    assert res.status_code == 401


def test_search_with_no_query_behaves_like_list_notes(client):
    register(client)
    create_note(client, title="First", text="Some text here")
    res = client.get("/api/search")
    assert res.status_code == 200
    assert len(res.get_json()) == 1


def test_keyword_only_search_works_without_embeddings(client, monkeypatch):
    patch_embeddings_unavailable(monkeypatch)
    register(client)
    create_note(client, title="Budget notes", text="We finalized the annual budget today.")
    create_note(client, title="Grocery list", text="Milk, eggs, bread.")

    res = client.get("/api/search?q=budget")
    assert res.status_code == 200
    body = res.get_json()
    assert len(body) == 1
    assert body[0]["title"] == "Budget notes"
    assert body[0]["searchMatch"] == "keyword"


def test_search_finds_semantic_match_without_keyword_overlap(client, monkeypatch):
    patch_embeddings_available(monkeypatch)
    register(client)
    pricing_note = create_note(
        client, title="Product sync", text="We settled on a tiered structure for the product line."
    )
    unrelated_note = create_note(client, title="Shopping", text="Milk, eggs, and bread for the week.")

    # Query shares zero literal words with pricing_note's text but maps
    # to the same "pricing" concept in our fake embedder via "cost".
    res = client.get("/api/search?q=what was the cost decision")
    assert res.status_code == 200
    body = res.get_json()
    ids = [n["id"] for n in body]
    assert pricing_note["id"] in ids
    assert unrelated_note["id"] not in ids

    match = next(n for n in body if n["id"] == pricing_note["id"])
    assert match["searchMatch"] == "semantic"
    assert match["searchScore"] > 0


def test_search_labels_both_when_keyword_and_semantic_agree(client, monkeypatch):
    patch_embeddings_available(monkeypatch)
    register(client)
    note = create_note(client, title="Pricing", text="We finalized pricing tiers for the new plan.")

    res = client.get("/api/search?q=pricing")
    body = res.get_json()
    match = next(n for n in body if n["id"] == note["id"])
    assert match["searchMatch"] == "both"


def test_search_falls_back_to_keyword_only_when_embeddings_unavailable(client, monkeypatch):
    patch_embeddings_unavailable(monkeypatch)
    register(client)
    pricing_note = create_note(
        client, title="Product sync", text="We settled on a tiered structure for the product line."
    )

    # Same semantically-related-but-no-shared-words query as above — this
    # time it should find nothing, since there's no embedding model to
    # catch the semantic link and zero literal keyword overlap.
    res = client.get("/api/search?q=what was the cost decision")
    assert res.status_code == 200
    ids = [n["id"] for n in res.get_json()]
    assert pricing_note["id"] not in ids


def test_search_backfills_missing_embeddings_inline(client, monkeypatch):
    patch_embeddings_available(monkeypatch)
    register(client)
    note = create_note(client, title="Trip", text="Booked flights and a hotel for the trip.")

    before = client.get(f"/api/notes/{note['id']}").get_json()
    assert before["hasEmbedding"] is False

    client.get("/api/search?q=travel plans")

    after = client.get(f"/api/notes/{note['id']}").get_json()
    assert after["hasEmbedding"] is True


def test_search_respects_pinned_filter_with_query(client, monkeypatch):
    patch_embeddings_available(monkeypatch)
    register(client)
    pinned = create_note(client, title="Pinned budget", text="Budget review, pinned for later.")
    client.patch(f"/api/notes/{pinned['id']}/pin")
    create_note(client, title="Unpinned budget", text="Budget notes, not pinned.")

    res = client.get("/api/search?q=budget&pinned=1")
    body = res.get_json()
    assert len(body) == 1
    assert body[0]["id"] == pinned["id"]


def test_search_respects_tag_filter_with_query(client, monkeypatch):
    patch_embeddings_available(monkeypatch)
    register(client)
    note = create_note(client, title="Budget", text="Budget planning notes.")

    db_conn_note = client.get(f"/api/notes/{note['id']}").get_json()
    assert db_conn_note is not None

    # Manually stamp a tag onto the note the way auto-tag would, without
    # needing a real AI provider configured for this test.
    import notes as notes_module

    app = client.application
    with app.app_context():
        db = notes_module.get_db()
        with db.cursor() as cur:
            cur.execute(
                "UPDATE notes SET tags = %s, tags_source_hash = %s WHERE id = %s",
                (json.dumps(["finance"]), notes_module.text_hash(note["text"]), note["id"]),
            )
        db.commit()

    matching = client.get("/api/search?q=budget&tag=finance").get_json()
    assert len(matching) == 1

    non_matching = client.get("/api/search?q=budget&tag=travel").get_json()
    assert non_matching == []


def test_users_only_search_their_own_notes(client, monkeypatch):
    patch_embeddings_available(monkeypatch)
    register(client, username="alice")
    create_note(client, title="Alice budget", text="Alice's budget notes.")
    client.post("/api/auth/logout")

    register(client, username="bob")
    res = client.get("/api/search?q=budget")
    assert res.get_json() == []


# ============================================================
# /api/notes/<id>/auto-embed
# ============================================================
def test_auto_embed_requires_login(client):
    res = client.post("/api/notes/1/auto-embed")
    assert res.status_code == 401


def test_auto_embed_404_for_missing_note(client):
    register(client)
    res = client.post("/api/notes/999/auto-embed")
    assert res.status_code == 404


def test_auto_embed_skips_empty_note(client, monkeypatch):
    patch_embeddings_available(monkeypatch)
    register(client)
    note = create_note(client, text="")

    res = client.post(f"/api/notes/{note['id']}/auto-embed")
    assert res.status_code == 200
    assert res.get_json()["skipped"] == "empty"


def test_auto_embed_returns_501_without_model(client, monkeypatch):
    patch_embeddings_unavailable(monkeypatch)
    register(client)
    note = create_note(client, text="Some note text that is long enough.")

    res = client.post(f"/api/notes/{note['id']}/auto-embed")
    assert res.status_code == 501
    assert "error" in res.get_json()


def test_auto_embed_success(client, monkeypatch):
    patch_embeddings_available(monkeypatch)
    register(client)
    note = create_note(client, text="Booked flights and a hotel for the trip.")

    res = client.post(f"/api/notes/{note['id']}/auto-embed")
    assert res.status_code == 200
    body = res.get_json()["note"]
    assert body["hasEmbedding"] is True
    assert body["embeddingStale"] is False


def test_auto_embed_skips_when_text_unchanged(client, monkeypatch):
    patch_embeddings_available(monkeypatch)
    register(client)
    note = create_note(client, text="Booked flights and a hotel for the trip.")

    import notes

    calls = []
    original = notes.embed_text

    def counting_embed(text):
        calls.append(1)
        return original(text)

    monkeypatch.setattr(notes, "embed_text", counting_embed)

    client.post(f"/api/notes/{note['id']}/auto-embed")
    assert len(calls) == 1

    res = client.post(f"/api/notes/{note['id']}/auto-embed")
    assert res.get_json()["skipped"] == "unchanged"
    assert len(calls) == 1


# ============================================================
# /api/search/compare — Day 17's search-quality evaluation endpoint
# ============================================================
def test_compare_search_requires_login(client):
    res = client.get("/api/search/compare?q=budget")
    assert res.status_code == 401


def test_compare_search_requires_query(client):
    register(client)
    res = client.get("/api/search/compare")
    assert res.status_code == 400


def test_compare_search_shows_semantic_only_matches(client, monkeypatch):
    patch_embeddings_available(monkeypatch)
    register(client)
    pricing_note = create_note(
        client, title="Product sync", text="We settled on a tiered structure for the product line."
    )

    res = client.get("/api/search/compare?q=what was the cost decision")
    assert res.status_code == 200
    body = res.get_json()

    assert body["semanticSearchAvailable"] is True
    keyword_ids = {r["id"] for r in body["keywordResults"]}
    hybrid_ids = {r["id"] for r in body["hybridResults"]}
    assert pricing_note["id"] not in keyword_ids
    assert pricing_note["id"] in hybrid_ids
    assert body["metrics"]["onlyInHybrid"] >= 1


def test_compare_search_empty_when_no_notes(client, monkeypatch):
    patch_embeddings_available(monkeypatch)
    register(client)
    res = client.get("/api/search/compare?q=anything")
    body = res.get_json()
    assert body["keywordResults"] == []
    assert body["hybridResults"] == []
    assert body["metrics"]["keywordCount"] == 0
