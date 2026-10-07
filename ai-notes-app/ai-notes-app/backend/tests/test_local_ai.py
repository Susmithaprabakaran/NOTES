# ============================================================
# Day 37 — local AI provider tests.
#
# Unlike every other tests/test_*.py in this app, these don't need
# TEST_DATABASE_URL or a running Postgres at all: local_ai.py is pure
# text analysis with no DB and no network call, so it's tested
# directly as a plain Python module. The router-level tests at the
# bottom (through ai_client.call_ai with AI_PROVIDER=local) are the
# only ones that touch Flask/env at all, and even those don't need a
# database.
# ============================================================
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import local_ai as L
from ai_client import ai_status, call_ai, AIUnavailable

NOTE = (
    "Meeting with Sarah Chen next Tuesday at the downtown Coimbatore office. "
    "We need to email the budget draft to Dr. Kim by Friday. "
    "Machine Learning uses Python and Scikit-learn for model development. "
    "A neural network is a computing system inspired by biological brains. "
    "Remember to book the conference room for the WebenoID Technologies review."
)


# ------------------------------------------------------------
# Sentence/word utilities
# ------------------------------------------------------------

def test_sentences_splits_on_terminal_punctuation():
    out = L.sentences("First sentence. Second sentence! Third one?")
    assert out == ["First sentence.", "Second sentence!", "Third one?"]


def test_sentences_does_not_split_on_title_abbreviations():
    out = L.sentences("I met Dr. Kim yesterday. It went well.")
    assert len(out) == 2
    assert "Dr. Kim" in out[0]


def test_sentences_handles_empty_text():
    assert L.sentences("") == []
    assert L.sentences("   ") == []


def test_sentences_falls_back_to_whole_text_without_terminal_punctuation():
    assert L.sentences("no punctuation here") == ["no punctuation here"]


# ------------------------------------------------------------
# Plain-text features
# ------------------------------------------------------------

def test_summarize_returns_nonempty_for_real_text():
    summary = L.local_summarize(NOTE)
    assert summary
    assert summary in NOTE or all(s in NOTE for s in summary.split(". "))


def test_summarize_handles_empty_text():
    assert L.local_summarize("") == ""


def test_summarize_short_text_returns_as_is():
    assert L.local_summarize("Buy milk.") == "Buy milk."


def test_polish_capitalizes_and_fixes_spacing():
    result = L.local_polish("this is messy   text ,without proper spacing")
    assert result.startswith("This")
    assert " ," not in result
    assert result.endswith(".")


def test_title_is_capped_and_capitalized():
    title = L.local_title("this note describes a very long meeting agenda with many items")
    assert title[0].isupper()
    assert len(title.split()) <= 8


def test_title_handles_empty_text():
    assert L.local_title("") == "Untitled note"


def test_digest_stays_under_word_cap():
    bullets = "Task due soon: Email budget\nTasks piling up: 3 open tasks older than 7 days"
    msg = L.local_digest(bullets)
    assert len(msg.split()) <= 26  # cap + ellipsis token tolerance


def test_digest_handles_no_bullets():
    assert "caught up" in L.local_digest("").lower()


def test_graph_explain_uses_concept_context():
    ctx = "Concept: Machine Learning\nRelated concepts: Python, Scikit-learn\nNotes that mention it: Standup"
    out = L.local_graph_explain(ctx)
    assert "Machine Learning" in out
    assert "Python" in out


def test_graph_explain_uses_chain_context():
    ctx = 'Chain: "A" → "B" → "C"\nHops:\n"A" → "B" (via note(s): X)\n"B" → "C"'
    out = L.local_graph_explain(ctx)
    assert "A" in out and "C" in out


# ------------------------------------------------------------
# JSON-shaped features
# ------------------------------------------------------------

def test_tags_returns_lowercase_list_within_cap():
    tags = L.local_tags(NOTE, max_tags=5)
    assert len(tags) <= 5
    assert all(t == t.lower() for t in tags)


def test_tags_handles_empty_text():
    assert L.local_tags("") == []


def test_entities_shape_has_all_four_keys():
    ents = L.local_entities(NOTE)
    assert set(ents.keys()) == {"people", "dates", "places", "organizations"}
    assert all(isinstance(v, list) for v in ents.values())


def test_entities_finds_person_and_title():
    ents = L.local_entities(NOTE)
    assert "Sarah Chen" in ents["people"]
    assert any("Kim" in p for p in ents["people"])


def test_entities_finds_dates():
    ents = L.local_entities(NOTE)
    joined = " ".join(ents["dates"]).lower()
    assert "tuesday" in joined
    assert "friday" in joined


def test_entities_finds_organization_by_suffix():
    ents = L.local_entities(NOTE)
    assert any("WebenoID" in o for o in ents["organizations"])


def test_entities_excludes_weekday_from_people():
    ents = L.local_entities("See you Friday.")
    assert "Friday" not in ents["people"]


def test_entities_handles_empty_text():
    ents = L.local_entities("")
    assert ents == {"people": [], "dates": [], "places": [], "organizations": []}


def test_concepts_capped_and_returns_list():
    concepts = L.local_concepts(NOTE, max_concepts=8)
    assert len(concepts) <= 8
    assert isinstance(concepts, list)


def test_concepts_filters_sentence_starter_noise():
    concepts = L.local_concepts("If it rains tomorrow, cancel the picnic. Otherwise go to the park.")
    assert "If" not in concepts
    assert "Otherwise" not in concepts


def test_tasks_extracts_actionable_lines():
    tasks = L.local_tasks(NOTE)
    texts = [t["text"].lower() for t in tasks]
    assert any("email" in t for t in texts)
    assert any("book" in t for t in texts)


def test_tasks_captures_due_phrase():
    tasks = L.local_tasks("We need to email the budget draft to Dr. Kim by Friday.")
    assert tasks
    assert tasks[0]["due"] == "Friday"


def test_tasks_returns_empty_for_non_actionable_text():
    tasks = L.local_tasks("The sky was a brilliant shade of orange at sunset.")
    assert tasks == []


def test_tasks_handles_empty_text():
    assert L.local_tasks("") == []


def test_flowchart_shape_has_nodes_and_edges():
    fc = L.local_flowchart(
        "First, gather requirements. Then design the schema. "
        "If the design is approved, implement the feature. Finally, deploy to production."
    )
    assert fc["nodes"]
    assert fc["nodes"][0]["type"] == "start"
    assert any(n["type"] == "decision" for n in fc["nodes"])
    assert fc["edges"]


def test_flowchart_caps_at_max_nodes():
    text = ". ".join(f"Step number {i}" for i in range(30)) + "."
    fc = L.local_flowchart(text, max_nodes=12)
    assert len(fc["nodes"]) <= 12


def test_flowchart_handles_empty_text():
    assert L.local_flowchart("") == {"nodes": [], "edges": []}


def test_flowchart_edge_ids_all_reference_real_nodes():
    fc = L.local_flowchart(NOTE)
    node_ids = {n["id"] for n in fc["nodes"]}
    for edge in fc["edges"]:
        assert edge["from"] in node_ids
        assert edge["to"] in node_ids


def test_study_pack_has_all_six_sections():
    pack = L.local_study(NOTE)
    assert set(pack.keys()) == {
        "summary", "keyPoints", "qna", "mcqs", "flashcards", "definitions",
    }


def test_study_pack_mcqs_have_valid_answer_index():
    pack = L.local_study(NOTE)
    for mcq in pack["mcqs"]:
        assert 0 <= mcq["answerIndex"] < len(mcq["options"])
        assert len(mcq["options"]) >= 2
        assert mcq["options"][mcq["answerIndex"]]


def test_study_pack_definitions_have_term_and_definition():
    pack = L.local_study(NOTE)
    for d in pack["definitions"]:
        assert d["term"]
        assert d["definition"]
        # The word-boundary fix: the definition must never start with a
        # word missing its first letter (e.g. "pproved" instead of
        # "approved") — the classic symptom of the "a/an/the" prefix bug.
        first_word = d["definition"].split()[0] if d["definition"].split() else ""
        assert not first_word.startswith("pproved")


def test_study_pack_handles_empty_text_without_crashing():
    pack = L.local_study("")
    assert pack["summary"] == ""
    assert pack["mcqs"] == []
    assert pack["definitions"] == []


# ------------------------------------------------------------
# Router (run_local) — dispatches on the system prompt
# ------------------------------------------------------------

def test_router_dispatches_summarize():
    out = L.run_local(
        "Summarize the following note in 1-2 short sentences, keeping the key points:\n\nBuy milk today.",
        "You summarize personal notes. Reply with only the summary, no preamble.",
    )
    assert "Buy milk" in out


def test_router_dispatches_tags_as_json_array():
    out = L.run_local(
        "Suggest categorization tags for this note:\n\n" + NOTE,
        'You categorize personal notes with short tags. Reply with ONLY a JSON array of tags.',
    )
    parsed = json.loads(out)
    assert isinstance(parsed, list)


def test_router_dispatches_entities_as_json_object():
    out = L.run_local(
        "Extract people, dates, places, and organizations mentioned in this note:\n\n" + NOTE,
        'You extract named entities from personal notes. Reply with ONLY a JSON object '
        'with exactly four keys — "people", "dates", "places", "organizations".',
    )
    parsed = json.loads(out)
    assert set(parsed.keys()) == {"people", "dates", "places", "organizations"}


def test_router_dispatches_flowchart_as_json_object():
    out = L.run_local(
        "Turn the following text into a flowchart:\n\nFirst, do this. Then do that.",
        "You turn a piece of text describing a process, a sequence of steps, or decision logic into a flowchart.",
    )
    parsed = json.loads(out)
    assert "nodes" in parsed and "edges" in parsed


def test_router_dispatches_study_pack_as_json_object():
    out = L.run_local(
        "Build a study pack from the following note:\n\n" + NOTE,
        "You turn a personal note into a complete study pack.",
    )
    parsed = json.loads(out)
    assert "mcqs" in parsed and "flashcards" in parsed


def test_router_raises_for_translation():
    try:
        L.run_local(
            "Translate the following text into Hindi:\n\nHello there",
            "You are a precise translator. Reply with only the translated text",
        )
        assert False, "expected LocalUnsupported"
    except L.LocalUnsupported as e:
        assert "translation" in str(e).lower()


# ------------------------------------------------------------
# ai_client integration — AI_PROVIDER=local end to end
# ------------------------------------------------------------

def test_ai_status_reports_local_as_always_available(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "local")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    available, provider, reason = ai_status()
    assert available is True
    assert provider == "local"
    assert reason is None


def test_call_ai_routes_to_local_provider(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "local")
    result = call_ai(
        "Summarize the following note in 1-2 short sentences, keeping the key points:\n\nBuy milk today.",
        system="You summarize personal notes. Reply with only the summary, no preamble.",
    )
    assert "Buy milk" in result


def test_call_ai_local_translate_raises_ai_unavailable(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "local")
    try:
        call_ai(
            "Translate the following text into Hindi:\n\nHello",
            system="You are a precise translator. Reply with only the translated text",
        )
        assert False, "expected AIUnavailable"
    except AIUnavailable as e:
        assert "translation" in str(e).lower()
