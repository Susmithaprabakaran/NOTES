# ============================================================
# /api/search?mode=title — the sidebar search box (find a note by NAME).
# ============================================================
from conftest import register
from notes import title_match_score


def make_note(client, title, text=""):
    return client.post("/api/notes", json={"title": title, "text": text}).get_json()


def titles(res):
    return [n["title"] for n in res.get_json()]


# ---------- the scoring function on its own ----------

def test_score_ordering_exact_prefix_wordprefix_substring():
    exact = title_match_score("machine learning", "Machine Learning")
    prefix = title_match_score("machine", "Machine Learning")
    word_prefix = title_match_score("learn", "Machine Learning")
    substring = title_match_score("earn", "Machine Learning")
    assert exact > prefix > word_prefix > substring > 0


def test_score_is_case_and_whitespace_insensitive():
    assert title_match_score("  MACHINE   learning ", "machine learning") == 100


def test_score_any_word_order():
    assert title_match_score("learning machine", "Machine Learning basics") > 0


def test_score_forgives_small_typos_but_not_nonsense():
    assert title_match_score("machin lerning", "Machine Learning basics") > 0
    assert title_match_score("groceries", "Machine Learning basics") == 0
    assert title_match_score("zz", "Machine Learning") == 0


def test_score_empty_query_matches_nothing():
    assert title_match_score("", "Anything") == 0
    assert title_match_score("   ", "Anything") == 0


def test_untitled_notes_match_untitled():
    assert title_match_score("untitled", "") > 0


# ---------- the endpoint ----------

def test_title_search_requires_login(client):
    assert client.get("/api/search?mode=title&q=x").status_code == 401


def test_partial_word_matches_title(client):
    register(client)
    make_note(client, "Machine Learning basics")
    make_note(client, "Grocery list")
    res = client.get("/api/search?mode=title&q=mach")
    assert res.status_code == 200
    assert titles(res) == ["Machine Learning basics"]
    assert res.get_json()[0]["searchMatch"] == "title"


def test_case_insensitive(client):
    register(client)
    make_note(client, "Project Roadmap")
    assert titles(client.get("/api/search?mode=title&q=ROADMAP")) == ["Project Roadmap"]


def test_only_title_is_searched_not_body_text(client):
    register(client)
    make_note(client, "Shopping", "remember the budget review")
    assert titles(client.get("/api/search?mode=title&q=budget")) == []
    # ...while the default (content) search still finds it by body text
    assert titles(client.get("/api/search?q=budget")) == ["Shopping"]


def test_better_matches_rank_first(client):
    register(client)
    make_note(client, "Notes about plans")           # substring "plan"
    make_note(client, "Plan")                         # exact
    make_note(client, "Planning the launch")         # prefix
    assert titles(client.get("/api/search?mode=title&q=plan")) == [
        "Plan",
        "Planning the launch",
        "Notes about plans",
    ]


def test_typo_tolerance_through_the_endpoint(client):
    register(client)
    make_note(client, "Quarterly Budget")
    assert titles(client.get("/api/search?mode=title&q=quaterly")) == ["Quarterly Budget"]


def test_no_match_returns_empty_list(client):
    register(client)
    make_note(client, "Something")
    res = client.get("/api/search?mode=title&q=zzzzzz")
    assert res.status_code == 200
    assert res.get_json() == []


def test_untitled_note_found_by_word_untitled(client):
    register(client)
    make_note(client, "", "body text")
    assert len(client.get("/api/search?mode=title&q=untitled").get_json()) == 1


def test_only_own_notes_are_searched(client, app):
    register(client, "alice")
    make_note(client, "Alice secret plan")
    other = app.test_client()
    register(other, "bob")
    make_note(other, "Bob shopping")
    assert titles(other.get("/api/search?mode=title&q=secret")) == []
    assert titles(client.get("/api/search?mode=title&q=secret")) == ["Alice secret plan"]


def test_other_filters_still_apply_in_title_mode(client):
    register(client)
    a = make_note(client, "Alpha plan")
    make_note(client, "Beta plan")
    client.patch(f"/api/notes/{a['id']}/pin")
    assert titles(client.get("/api/search?mode=title&q=plan&pinned=1")) == ["Alpha plan"]


def test_empty_query_in_title_mode_lists_everything(client):
    register(client)
    make_note(client, "One")
    make_note(client, "Two")
    assert len(client.get("/api/search?mode=title").get_json()) == 2


def test_default_mode_is_unchanged(client):
    """No `mode` param = the original hybrid content search."""
    register(client)
    make_note(client, "Budget notes", "We finalized the annual budget today.")
    body = client.get("/api/search?q=budget").get_json()
    assert len(body) == 1
    assert body[0]["searchMatch"] in ("keyword", "semantic", "both")
