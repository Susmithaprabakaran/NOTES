# ============================================================
# Day 26 — priority & date-range filters
# ============================================================
from conftest import register


def create_note(client, title="Title", text="Some body text", priority=None):
    body = {"title": title, "text": text}
    if priority is not None:
        body["priority"] = priority
    return client.post("/api/notes", json=body)


def test_new_note_defaults_to_no_priority(client):
    register(client)
    res = create_note(client)
    assert res.status_code == 201
    assert res.get_json()["priority"] == "none"


def test_create_with_priority(client):
    register(client)
    res = create_note(client, priority="high")
    assert res.status_code == 201
    assert res.get_json()["priority"] == "high"


def test_invalid_priority_falls_back_to_none(client):
    register(client)
    res = create_note(client, priority="urgent!!")
    assert res.status_code == 201
    assert res.get_json()["priority"] == "none"


def test_set_priority_endpoint(client):
    register(client)
    note = create_note(client).get_json()

    res = client.patch(f"/api/notes/{note['id']}/priority", json={"priority": "medium"})
    assert res.status_code == 200
    assert res.get_json()["priority"] == "medium"

    fetched = client.get(f"/api/notes/{note['id']}")
    assert fetched.get_json()["priority"] == "medium"


def test_set_priority_rejects_invalid_value(client):
    register(client)
    note = create_note(client).get_json()
    res = client.patch(f"/api/notes/{note['id']}/priority", json={"priority": "urgent"})
    assert res.status_code == 400


def test_set_priority_requires_login(client):
    res = client.patch("/api/notes/1/priority", json={"priority": "high"})
    assert res.status_code == 401


def test_list_notes_filtered_by_priority(client):
    register(client)
    create_note(client, title="A", priority="high")
    create_note(client, title="B", priority="low")
    create_note(client, title="C", priority="high")

    res = client.get("/api/notes?priority=high")
    assert res.status_code == 200
    titles = {n["title"] for n in res.get_json()}
    assert titles == {"A", "C"}


def test_search_filtered_by_priority(client):
    register(client)
    create_note(client, title="Groceries", text="milk eggs bread", priority="high")
    create_note(client, title="Groceries too", text="milk again", priority="low")

    res = client.get("/api/search?q=milk&priority=high")
    assert res.status_code == 200
    titles = {n["title"] for n in res.get_json()}
    assert titles == {"Groceries"}


def test_list_notes_date_range_all_includes_everything(client):
    register(client)
    create_note(client, title="A")
    create_note(client, title="B")
    res = client.get("/api/notes?dateRange=all")
    assert res.status_code == 200
    assert len(res.get_json()) == 2


def test_list_notes_date_range_today_includes_freshly_created_note(client):
    # A note created moments ago should always show up under "today" —
    # this is the one date-range case we can assert deterministically
    # without manipulating the clock.
    register(client)
    create_note(client, title="Just now")
    res = client.get("/api/notes?dateRange=today")
    assert res.status_code == 200
    titles = {n["title"] for n in res.get_json()}
    assert "Just now" in titles


def test_combined_filters(client):
    register(client)
    create_note(client, title="Match", text="alpha", priority="high")
    create_note(client, title="Wrong priority", text="alpha", priority="low")

    res = client.get("/api/notes?priority=high&dateRange=today")
    titles = {n["title"] for n in res.get_json()}
    assert titles == {"Match"}
