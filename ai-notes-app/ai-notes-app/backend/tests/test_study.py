import json

from conftest import register


def create_note(client, title="", text=""):
    res = client.post("/api/notes", json={"title": title, "text": text})
    return res.get_json()


LONG_TEXT = (
    "Mitosis is the process by which a single eukaryotic cell divides into two genetically "
    "identical daughter cells. It happens in five stages: prophase, when chromatin condenses "
    "into visible chromosomes; metaphase, when chromosomes line up along the cell's equator; "
    "anaphase, when sister chromatids are pulled to opposite poles; telophase, when two new "
    "nuclei form; and cytokinesis, when the cytoplasm finally splits into two cells. Mitosis is "
    "essential for growth, tissue repair, and asexual reproduction in many organisms."
)

VALID_AI_JSON = json.dumps(
    {
        "summary": "Mitosis divides one eukaryotic cell into two identical daughter cells "
        "through five stages, supporting growth and repair.",
        "keyPoints": [
            "Mitosis produces two genetically identical daughter cells.",
            "It has five stages: prophase, metaphase, anaphase, telophase, cytokinesis.",
            "Chromosomes condense during prophase.",
        ],
        "qna": [
            {"question": "What happens during metaphase?", "answer": "Chromosomes line up along the equator."},
            {"question": "Why is mitosis important?", "answer": "It supports growth, repair, and reproduction."},
        ],
        "mcqs": [
            {
                "question": "Which stage involves chromatids being pulled to opposite poles?",
                "options": ["Prophase", "Metaphase", "Anaphase", "Telophase"],
                "answerIndex": 2,
                "explanation": "Anaphase is when sister chromatids separate.",
            }
        ],
        "flashcards": [
            {"front": "Prophase", "back": "Chromatin condenses into visible chromosomes."},
            {"front": "Cytokinesis", "back": "The cytoplasm splits into two cells."},
        ],
        "definitions": [
            {"term": "Mitosis", "definition": "Division of a cell into two identical daughter cells."},
        ],
    }
)


def configure_ai(monkeypatch, reply=VALID_AI_JSON):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key-for-test")

    import study

    monkeypatch.setattr(study, "call_ai", lambda prompt, system=None: reply)


# ---------- auth / ownership ----------


def test_get_requires_login(client):
    res = client.get("/api/notes/1/study")
    assert res.status_code == 401


def test_generate_requires_login(client):
    res = client.post("/api/notes/1/study/generate")
    assert res.status_code == 401


def test_get_404s_on_missing_note(client):
    register(client)
    res = client.get("/api/notes/999999/study")
    assert res.status_code == 404


def test_generate_404s_on_someone_elses_note(client, monkeypatch):
    configure_ai(monkeypatch)
    register(client, username="alice")
    note = create_note(client, text=LONG_TEXT)
    client.post("/api/auth/logout")

    register(client, username="bob")
    res = client.post(f"/api/notes/{note['id']}/study/generate")
    assert res.status_code == 404


# ---------- validation ----------


def test_get_reports_not_generated_yet(client, monkeypatch):
    configure_ai(monkeypatch)
    register(client)
    note = create_note(client, text=LONG_TEXT)
    res = client.get(f"/api/notes/{note['id']}/study")
    assert res.status_code == 200
    assert res.get_json() == {"generated": False}


def test_generate_rejects_empty_note(client, monkeypatch):
    configure_ai(monkeypatch)
    register(client)
    note = create_note(client, text="   ")
    res = client.post(f"/api/notes/{note['id']}/study/generate")
    assert res.status_code == 400


def test_generate_rejects_too_short_note(client, monkeypatch):
    configure_ai(monkeypatch)
    register(client)
    note = create_note(client, text="Too short to study.")
    res = client.post(f"/api/notes/{note['id']}/study/generate")
    assert res.status_code == 400


def test_generate_returns_501_when_ai_unavailable(client, monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    register(client)
    note = create_note(client, text=LONG_TEXT)
    res = client.post(f"/api/notes/{note['id']}/study/generate")
    assert res.status_code == 501
    assert "error" in res.get_json()


def test_generate_returns_422_on_empty_ai_reply(client, monkeypatch):
    configure_ai(monkeypatch, reply='{"summary":"","keyPoints":[],"qna":[],"mcqs":[],"flashcards":[],"definitions":[]}')
    register(client)
    note = create_note(client, text=LONG_TEXT)
    res = client.post(f"/api/notes/{note['id']}/study/generate")
    assert res.status_code == 422


def test_generate_returns_422_on_unparseable_reply(client, monkeypatch):
    configure_ai(monkeypatch, reply="Sorry, I can't help with that.")
    register(client)
    note = create_note(client, text=LONG_TEXT)
    res = client.post(f"/api/notes/{note['id']}/study/generate")
    assert res.status_code == 422


# ---------- happy path ----------


def test_generate_succeeds_and_persists(client, monkeypatch):
    configure_ai(monkeypatch)
    register(client)
    note = create_note(client, text=LONG_TEXT)

    res = client.post(f"/api/notes/{note['id']}/study/generate")
    assert res.status_code == 200
    body = res.get_json()
    assert body["generated"] is True
    assert body["noteId"] == note["id"]
    assert body["summary"]
    assert len(body["keyPoints"]) == 3
    assert len(body["qna"]) == 2
    assert len(body["mcqs"]) == 1
    assert body["mcqs"][0]["answerIndex"] == 2
    assert len(body["flashcards"]) == 2
    assert len(body["definitions"]) == 1
    assert body["stale"] is False

    get_res = client.get(f"/api/notes/{note['id']}/study")
    assert get_res.status_code == 200
    fetched = get_res.get_json()
    assert fetched["generated"] is True
    assert fetched["summary"] == body["summary"]


def test_regenerate_overwrites_instead_of_duplicating(client, monkeypatch):
    configure_ai(monkeypatch)
    register(client)
    note = create_note(client, text=LONG_TEXT)

    client.post(f"/api/notes/{note['id']}/study/generate")

    second_reply = json.dumps(
        {
            "summary": "Updated summary.",
            "keyPoints": ["Only one point now."],
            "qna": [],
            "mcqs": [],
            "flashcards": [],
            "definitions": [],
        }
    )
    configure_ai(monkeypatch, reply=second_reply)
    res = client.post(f"/api/notes/{note['id']}/study/generate")
    assert res.status_code == 200
    body = res.get_json()
    assert body["summary"] == "Updated summary."
    assert body["keyPoints"] == ["Only one point now."]

    fetched = client.get(f"/api/notes/{note['id']}/study").get_json()
    assert fetched["summary"] == "Updated summary."


def test_stale_flag_flips_after_note_is_edited(client, monkeypatch):
    configure_ai(monkeypatch)
    register(client)
    note = create_note(client, text=LONG_TEXT)
    client.post(f"/api/notes/{note['id']}/study/generate")

    client.put(f"/api/notes/{note['id']}", json={"text": LONG_TEXT + " Extra sentence added later."})

    fetched = client.get(f"/api/notes/{note['id']}/study").get_json()
    assert fetched["generated"] is True
    assert fetched["stale"] is True


def test_ai_reply_wrapped_in_prose_still_parses(client, monkeypatch):
    wrapped = f"Sure, here's the study pack:\n```json\n{VALID_AI_JSON}\n```\nGood luck studying!"
    configure_ai(monkeypatch, reply=wrapped)
    register(client)
    note = create_note(client, text=LONG_TEXT)
    res = client.post(f"/api/notes/{note['id']}/study/generate")
    assert res.status_code == 200
    assert len(res.get_json()["keyPoints"]) == 3


def test_mcq_with_bad_answer_index_is_dropped(client, monkeypatch):
    reply = json.dumps(
        {
            "summary": "A summary that is long enough to count as real content here.",
            "keyPoints": [],
            "qna": [],
            "mcqs": [
                {
                    "question": "Bad question?",
                    "options": ["A", "B"],
                    "answerIndex": 5,
                    "explanation": "",
                }
            ],
            "flashcards": [],
            "definitions": [],
        }
    )
    configure_ai(monkeypatch, reply=reply)
    register(client)
    note = create_note(client, text=LONG_TEXT)
    res = client.post(f"/api/notes/{note['id']}/study/generate")
    assert res.status_code == 200
    assert res.get_json()["mcqs"] == []


def test_mcq_with_too_few_options_is_dropped(client, monkeypatch):
    reply = json.dumps(
        {
            "summary": "A summary that is long enough to count as real content here.",
            "keyPoints": [],
            "qna": [],
            "mcqs": [{"question": "Bad question?", "options": ["Only one"], "answerIndex": 0}],
            "flashcards": [],
            "definitions": [],
        }
    )
    configure_ai(monkeypatch, reply=reply)
    register(client)
    note = create_note(client, text=LONG_TEXT)
    res = client.post(f"/api/notes/{note['id']}/study/generate")
    assert res.status_code == 200
    assert res.get_json()["mcqs"] == []


def test_lists_capped_at_max(client, monkeypatch):
    import study

    reply = json.dumps(
        {
            "summary": "A summary that is long enough to count as real content here.",
            "keyPoints": [f"Point {i}" for i in range(30)],
            "qna": [],
            "mcqs": [],
            "flashcards": [],
            "definitions": [],
        }
    )
    configure_ai(monkeypatch, reply=reply)
    register(client)
    note = create_note(client, text=LONG_TEXT)
    res = client.post(f"/api/notes/{note['id']}/study/generate")
    assert res.status_code == 200
    assert len(res.get_json()["keyPoints"]) == study.MAX_KEY_POINTS


def test_duplicate_definition_terms_are_deduped(client, monkeypatch):
    reply = json.dumps(
        {
            "summary": "A summary that is long enough to count as real content here.",
            "keyPoints": [],
            "qna": [],
            "mcqs": [],
            "flashcards": [],
            "definitions": [
                {"term": "Mitosis", "definition": "First definition."},
                {"term": "mitosis", "definition": "Duplicate, different case."},
            ],
        }
    )
    configure_ai(monkeypatch, reply=reply)
    register(client)
    note = create_note(client, text=LONG_TEXT)
    res = client.post(f"/api/notes/{note['id']}/study/generate")
    assert res.status_code == 200
    assert len(res.get_json()["definitions"]) == 1


# ---------- delete ----------


def test_delete_requires_login(client):
    res = client.delete("/api/notes/1/study")
    assert res.status_code == 401


def test_delete_404s_when_nothing_generated(client, monkeypatch):
    register(client)
    note = create_note(client, text=LONG_TEXT)
    res = client.delete(f"/api/notes/{note['id']}/study")
    assert res.status_code == 404


def test_delete_removes_it(client, monkeypatch):
    configure_ai(monkeypatch)
    register(client)
    note = create_note(client, text=LONG_TEXT)
    client.post(f"/api/notes/{note['id']}/study/generate")

    res = client.delete(f"/api/notes/{note['id']}/study")
    assert res.status_code == 200
    assert res.get_json()["deleted"] is True

    fetched = client.get(f"/api/notes/{note['id']}/study").get_json()
    assert fetched == {"generated": False}


def test_cross_user_isolation(client, monkeypatch):
    configure_ai(monkeypatch)
    register(client, username="alice")
    note = create_note(client, text=LONG_TEXT)
    client.post(f"/api/notes/{note['id']}/study/generate")
    client.post("/api/auth/logout")

    register(client, username="bob")
    bob_note = create_note(client, text=LONG_TEXT)
    res = client.get(f"/api/notes/{bob_note['id']}/study")
    assert res.get_json() == {"generated": False}
