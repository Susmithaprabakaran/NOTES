import json

from conftest import register


def create_note(client, title="", text=""):
    res = client.post("/api/notes", json={"title": title, "text": text})
    return res.get_json()


def stamp_concepts(client, note, concepts):
    """Writes concepts directly onto a note's DB row, bypassing the AI
    call — mirrors the same trick test_search.py uses for tags, so
    these tests don't need a real/mocked AI provider wired up per note."""
    import notes as notes_module

    app = client.application
    with app.app_context():
        db = notes_module.get_db()
        with db.cursor() as cur:
            cur.execute(
                "SELECT text FROM notes WHERE id = %s",
                (note["id"],),
            )
            text = cur.fetchone()["text"]
            cur.execute(
                "UPDATE notes SET concepts = %s, concepts_source_hash = %s WHERE id = %s",
                (json.dumps(concepts), notes_module.text_hash(text), note["id"]),
            )
        db.commit()


def stamp_tags(client, note, tags):
    import notes as notes_module

    app = client.application
    with app.app_context():
        db = notes_module.get_db()
        with db.cursor() as cur:
            cur.execute("SELECT text FROM notes WHERE id = %s", (note["id"],))
            text = cur.fetchone()["text"]
            cur.execute(
                "UPDATE notes SET tags = %s, tags_source_hash = %s WHERE id = %s",
                (json.dumps(tags), notes_module.text_hash(text), note["id"]),
            )
        db.commit()


def build_demo_scenario(client):
    """The exact three-note chain from the Day 18 spec:
    Machine Learning -> Python -> Scikit-learn -> Classification -> Evaluation Metrics
    """
    note1 = create_note(
        client, title="ML intro", text="Machine Learning uses Python and Scikit-learn for model development."
    )
    note2 = create_note(
        client, title="Scikit-learn overview", text="Scikit-learn provides tools for classification and regression."
    )
    note3 = create_note(
        client,
        title="Evaluating models",
        text="Classification models can be evaluated using accuracy, precision, and recall.",
    )
    stamp_concepts(client, note1, ["Machine Learning", "Python", "Scikit-learn"])
    stamp_concepts(client, note2, ["Scikit-learn", "Classification", "Regression"])
    stamp_concepts(client, note3, ["Classification", "Evaluation Metrics"])
    return note1, note2, note3


# ============================================================
# GET /api/graph
# ============================================================
def test_graph_requires_login(client):
    res = client.get("/api/graph")
    assert res.status_code == 401


def test_graph_empty_for_new_account(client):
    register(client)
    res = client.get("/api/graph")
    assert res.status_code == 200
    assert res.get_json() == {"nodes": [], "edges": []}


def test_graph_builds_nodes_and_edges_for_demo_scenario(client):
    register(client)
    note1, note2, note3 = build_demo_scenario(client)

    res = client.get("/api/graph")
    body = res.get_json()

    note_node_ids = {n["id"] for n in body["nodes"] if n["type"] == "note"}
    assert note_node_ids == {f"note:{note1['id']}", f"note:{note2['id']}", f"note:{note3['id']}"}

    concept_labels = {n["label"] for n in body["nodes"] if n["type"] == "concept"}
    assert concept_labels == {
        "Machine Learning",
        "Python",
        "Scikit-learn",
        "Classification",
        "Regression",
        "Evaluation Metrics",
    }

    mentions_edges = [e for e in body["edges"] if e["type"] == "mentions"]
    assert len(mentions_edges) == 3 + 3 + 2  # note1, note2, note3 concept counts

    cooccurs_edges = {
        tuple(sorted((e["source"].split(":", 1)[1], e["target"].split(":", 1)[1])))
        for e in body["edges"]
        if e["type"] == "co-occurs"
    }
    # note1: ML-Python, ML-Scikit-learn, Python-Scikit-learn
    assert ("machine learning", "python") in cooccurs_edges
    assert ("machine learning", "scikit-learn") in cooccurs_edges
    assert ("python", "scikit-learn") in cooccurs_edges
    # note2: Scikit-learn-Classification, Scikit-learn-Regression, Classification-Regression
    assert ("classification", "scikit-learn") in cooccurs_edges
    # note3: Classification-Evaluation Metrics
    assert ("classification", "evaluation metrics") in cooccurs_edges
    # never co-occur directly anywhere
    assert ("machine learning", "evaluation metrics") not in cooccurs_edges


def test_graph_excludes_stale_concepts(client):
    register(client)
    note = create_note(client, text="Machine Learning uses Python for model development.")
    stamp_concepts(client, note, ["Machine Learning", "Python"])

    # Edit the note without re-extracting — concepts are now stale.
    client.put(f"/api/notes/{note['id']}", json={"text": "Machine Learning uses Python. Extra sentence added."})

    res = client.get("/api/graph")
    assert res.get_json() == {"nodes": [], "edges": []}


def test_graph_excludes_notes_without_concepts(client):
    register(client)
    create_note(client, text="Just a note with no extracted concepts yet, plenty of words in it though.")
    res = client.get("/api/graph")
    assert res.get_json()["nodes"] == []


def test_users_only_see_their_own_graph(client):
    register(client, username="owner")
    build_demo_scenario(client)
    client.post("/api/auth/logout")

    register(client, username="intruder")
    res = client.get("/api/graph")
    assert res.get_json() == {"nodes": [], "edges": []}


# ============================================================
# GET /api/graph/path
# ============================================================
def test_path_requires_login(client):
    res = client.get("/api/graph/path?from=A&to=B")
    assert res.status_code == 401


def test_path_requires_both_params(client):
    register(client)
    res = client.get("/api/graph/path?from=A")
    assert res.status_code == 400


def test_path_404_for_unknown_concept(client):
    register(client)
    build_demo_scenario(client)
    res = client.get("/api/graph/path?from=Machine+Learning&to=Quantum+Computing")
    assert res.status_code == 404
    body = res.get_json()
    assert body["foundFrom"] is True
    assert body["foundTo"] is False


def test_path_finds_multi_hop_chain_across_notes(client):
    register(client)
    build_demo_scenario(client)

    res = client.get("/api/graph/path?from=Machine+Learning&to=Evaluation+Metrics")
    assert res.status_code == 200
    body = res.get_json()

    labels = [step["concept"] for step in body["path"]]
    assert labels[0] == "Machine Learning"
    assert labels[-1] == "Evaluation Metrics"
    # Every consecutive pair in the chain must be a real edge — check
    # via the hops list rather than assuming exact intermediate content,
    # since Python/Scikit-learn could both be valid 2nd hops.
    assert len(body["hops"]) == len(labels) - 1
    for hop in body["hops"]:
        assert len(hop["connectingNotes"]) >= 1


def test_path_case_insensitive_and_fuzzy(client):
    register(client)
    build_demo_scenario(client)

    res = client.get("/api/graph/path?from=machine learning&to=learn")
    assert res.status_code == 200


def test_path_same_concept(client):
    register(client)
    build_demo_scenario(client)

    res = client.get("/api/graph/path?from=Python&to=Python")
    body = res.get_json()
    assert len(body["path"]) == 1
    assert body["hops"] == []


def test_path_no_connection_between_disconnected_concepts(client):
    register(client)
    note = create_note(client, text="A note about gardening and tomatoes in the backyard.")
    stamp_concepts(client, note, ["Gardening"])
    build_demo_scenario(client)

    res = client.get("/api/graph/path?from=Gardening&to=Python")
    assert res.status_code == 200
    body = res.get_json()
    assert body["path"] is None
    assert "message" in body


# ============================================================
# GET /api/notes/<id>/related
# ============================================================
def test_related_requires_login(client):
    res = client.get("/api/notes/1/related")
    assert res.status_code == 401


def test_related_404_for_missing_note(client):
    register(client)
    res = client.get("/api/notes/999/related")
    assert res.status_code == 404


def test_related_ranks_by_shared_concepts(client):
    register(client)
    note1, note2, note3 = build_demo_scenario(client)

    res = client.get(f"/api/notes/{note1['id']}/related")
    assert res.status_code == 200
    body = res.get_json()

    ids = [r["note"]["id"] for r in body]
    assert note2["id"] in ids  # shares "Scikit-learn"
    # note3 shares nothing with note1 directly (no common concept/tag),
    # and without embeddings configured in this test there's no semantic
    # signal either, so it shouldn't appear.
    assert note3["id"] not in ids

    match = next(r for r in body if r["note"]["id"] == note2["id"])
    assert "Scikit-learn" in match["sharedConcepts"]
    # 1 shared concept -> score 2 -> "strong" (see _relatedness_label)
    assert match["relatedness"] == "strong"


def test_related_includes_shared_tags_as_secondary_signal(client):
    register(client)
    note1 = create_note(client, text="Machine Learning uses Python for everything these days.")
    note2 = create_note(client, text="A completely unrelated note about something else entirely here.")
    stamp_concepts(client, note1, ["Machine Learning"])
    stamp_tags(client, note1, ["work"])
    stamp_tags(client, note2, ["work"])

    res = client.get(f"/api/notes/{note1['id']}/related")
    body = res.get_json()
    ids = [r["note"]["id"] for r in body]
    assert note2["id"] in ids
    match = next(r for r in body if r["note"]["id"] == note2["id"])
    assert match["sharedTags"] == ["work"]
    assert match["sharedConcepts"] == []
    # 1 shared tag alone -> score 1 -> "moderate", one tier below a
    # shared concept — concepts are the stronger, more specific signal.
    assert match["relatedness"] == "moderate"


def test_relatedness_label_thresholds():
    from graph import _relatedness_label

    assert _relatedness_label(0.0) == "light"
    assert _relatedness_label(0.55) == "light"
    assert _relatedness_label(0.99) == "light"
    assert _relatedness_label(1.0) == "moderate"
    assert _relatedness_label(1.5) == "moderate"
    assert _relatedness_label(2.0) == "strong"
    assert _relatedness_label(5.0) == "strong"


def test_related_excludes_the_note_itself(client):
    register(client)
    note1, note2, note3 = build_demo_scenario(client)
    res = client.get(f"/api/notes/{note1['id']}/related").get_json()
    ids = [r["note"]["id"] for r in res]
    assert note1["id"] not in ids


def test_users_only_see_their_own_related_notes(client):
    register(client, username="owner")
    note1, note2, note3 = build_demo_scenario(client)
    owner_note1_id = note1["id"]
    client.post("/api/auth/logout")

    register(client, username="intruder")
    res = client.get(f"/api/notes/{owner_note1_id}/related")
    # It's someone else's note id — should 404, not leak related notes.
    assert res.status_code == 404


# ============================================================
# Day 29 — /api/graph/explain (AI-powered graph insights)
# ============================================================
def test_explain_requires_login(client):
    res = client.post("/api/graph/explain", json={"concept": "Python"})
    assert res.status_code == 401


def test_explain_rejects_when_neither_concept_nor_path_given(client):
    register(client)
    res = client.post("/api/graph/explain", json={})
    assert res.status_code == 400


def test_explain_rejects_when_both_concept_and_path_given(client):
    register(client)
    res = client.post("/api/graph/explain", json={"concept": "Python", "path": ["Python", "Machine Learning"]})
    assert res.status_code == 400


def test_explain_rejects_path_with_one_item(client):
    register(client)
    res = client.post("/api/graph/explain", json={"path": ["Python"]})
    assert res.status_code == 400


def test_explain_rejects_unknown_concept(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake-key-for-test")
    register(client)
    build_demo_scenario(client)
    res = client.post("/api/graph/explain", json={"concept": "Underwater Basket Weaving"})
    assert res.status_code == 404


def test_explain_rejects_unknown_concept_in_path(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake-key-for-test")
    register(client)
    build_demo_scenario(client)
    res = client.post("/api/graph/explain", json={"path": ["Python", "Underwater Basket Weaving"]})
    assert res.status_code == 404


def test_explain_returns_501_when_no_provider_configured(client, monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    register(client)
    build_demo_scenario(client)
    res = client.post("/api/graph/explain", json={"concept": "Python"})
    assert res.status_code == 501
    assert "error" in res.get_json()


def test_explain_concept_sends_neighbors_and_notes_as_context(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake-key-for-test")
    register(client)
    build_demo_scenario(client)

    seen_prompt = {}

    def fake_call_ai(prompt, system=None):
        seen_prompt["text"] = prompt
        return "Python is a programming language used here for Machine Learning."

    import graph

    monkeypatch.setattr(graph, "call_ai", fake_call_ai)

    res = client.post("/api/graph/explain", json={"concept": "python"})
    assert res.status_code == 200
    assert res.get_json()["result"] == "Python is a programming language used here for Machine Learning."
    # Substring-matched concept, and the neighbor concept should be in
    # the context sent to the model.
    assert "Python" in seen_prompt["text"]
    assert "Machine Learning" in seen_prompt["text"]


def test_explain_path_sends_hop_chain_as_context(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake-key-for-test")
    register(client)
    build_demo_scenario(client)

    seen_prompt = {}

    def fake_call_ai(prompt, system=None):
        seen_prompt["text"] = prompt
        return "These ideas link together through the notes on Python and Scikit-learn."

    import graph

    monkeypatch.setattr(graph, "call_ai", fake_call_ai)

    res = client.post(
        "/api/graph/explain",
        json={"path": ["Machine Learning", "Python", "Scikit-learn"]},
    )
    assert res.status_code == 200
    assert "result" in res.get_json()
    assert "Machine Learning" in seen_prompt["text"]
    assert "Scikit-learn" in seen_prompt["text"]


def test_explain_path_too_long_is_rejected(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake-key-for-test")
    register(client)
    res = client.post("/api/graph/explain", json={"path": ["a"] * 13})
    assert res.status_code == 400


def test_explain_path_rejects_non_string_entries(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake-key-for-test")
    register(client)
    res = client.post("/api/graph/explain", json={"path": ["Python", 42]})
    assert res.status_code == 400
