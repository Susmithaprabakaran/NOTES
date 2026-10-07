from conftest import register, login


def create_note(client, title="Title", text="Some body text"):
    return client.post("/api/notes", json={"title": title, "text": text})


def test_notes_require_login(client):
    assert client.get("/api/notes").status_code == 401
    assert create_note(client).status_code == 401


def test_create_and_list_note(client):
    register(client)
    res = create_note(client, title="Groceries", text="milk eggs bread")
    assert res.status_code == 201
    body = res.get_json()
    assert body["title"] == "Groceries"
    assert body["wordCount"] == 3
    assert body["pinned"] is False

    listed = client.get("/api/notes")
    assert listed.status_code == 200
    assert len(listed.get_json()) == 1


def test_create_defaults_missing_fields_to_empty_strings(client):
    register(client)
    res = client.post("/api/notes", json={})
    assert res.status_code == 201
    body = res.get_json()
    assert body["title"] == ""
    assert body["text"] == ""


def test_null_fields_are_coerced_to_strings_not_crash(client):
    register(client)
    res = client.post("/api/notes", json={"title": None, "text": None})
    assert res.status_code == 201
    assert res.get_json()["title"] == ""

    # would previously be a place a naive implementation could crash
    search = client.get("/api/search?q=anything")
    assert search.status_code == 200


def test_get_single_note(client):
    register(client)
    created = create_note(client).get_json()
    res = client.get(f"/api/notes/{created['id']}")
    assert res.status_code == 200
    assert res.get_json()["id"] == created["id"]


def test_get_unknown_note_is_404(client):
    register(client)
    res = client.get("/api/notes/999999")
    assert res.status_code == 404


def test_update_note(client):
    register(client)
    created = create_note(client, title="Old", text="old text").get_json()
    res = client.put(f"/api/notes/{created['id']}", json={"title": "New", "text": "new body copy"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["title"] == "New"
    assert body["wordCount"] == 3


def test_update_unknown_note_is_404(client):
    register(client)
    res = client.put("/api/notes/999999", json={"title": "x"})
    assert res.status_code == 404


def test_toggle_pin(client):
    register(client)
    created = create_note(client).get_json()
    pinned = client.patch(f"/api/notes/{created['id']}/pin")
    assert pinned.get_json()["pinned"] is True
    unpinned = client.patch(f"/api/notes/{created['id']}/pin")
    assert unpinned.get_json()["pinned"] is False


def test_pinned_notes_sort_first(client):
    register(client)
    a = create_note(client, title="A").get_json()
    create_note(client, title="B").get_json()
    client.patch(f"/api/notes/{a['id']}/pin")

    listed = client.get("/api/notes").get_json()
    assert listed[0]["id"] == a["id"]


def test_delete_note(client):
    register(client)
    created = create_note(client).get_json()
    res = client.delete(f"/api/notes/{created['id']}")
    assert res.status_code == 200

    after = client.get(f"/api/notes/{created['id']}")
    assert after.status_code == 404


def test_delete_unknown_note_is_404(client):
    register(client)
    res = client.delete("/api/notes/999999")
    assert res.status_code == 404


def test_search_matches_title_or_body_case_insensitively(client):
    register(client)
    create_note(client, title="Grocery List", text="milk, eggs, bread")
    create_note(client, title="Workout Plan", text="squats and lunges")

    by_title = client.get("/api/search?q=grocery").get_json()
    assert len(by_title) == 1
    assert by_title[0]["title"] == "Grocery List"

    by_body = client.get("/api/search?q=LUNGES").get_json()
    assert len(by_body) == 1
    assert by_body[0]["title"] == "Workout Plan"

    no_match = client.get("/api/search?q=nope").get_json()
    assert no_match == []


def test_search_respects_pinned_filter(client):
    register(client)
    a = create_note(client, title="Recipe A", text="pasta").get_json()
    create_note(client, title="Recipe B", text="pasta")
    client.patch(f"/api/notes/{a['id']}/pin")

    res = client.get("/api/search?q=pasta&pinned=1").get_json()
    assert len(res) == 1
    assert res[0]["id"] == a["id"]


# ============================================================
# Cross-user isolation — the whole point of Day 8
# ============================================================
def test_users_cannot_see_each_others_notes(client):
    register(client, username="alice")
    create_note(client, title="Alice's secret")
    client.post("/api/auth/logout")

    register(client, username="bob")
    listed = client.get("/api/notes").get_json()
    assert listed == []


def test_users_cannot_read_each_others_note_by_id(client):
    register(client, username="alice")
    created = create_note(client, title="Alice's secret").get_json()
    client.post("/api/auth/logout")

    register(client, username="bob")
    res = client.get(f"/api/notes/{created['id']}")
    assert res.status_code == 404  # not 403 — don't reveal it exists


def test_users_cannot_edit_each_others_notes(client):
    register(client, username="alice")
    created = create_note(client, title="Alice's secret").get_json()
    client.post("/api/auth/logout")

    register(client, username="bob")
    res = client.put(f"/api/notes/{created['id']}", json={"title": "Hacked"})
    assert res.status_code == 404


def test_users_cannot_delete_each_others_notes(client):
    register(client, username="alice")
    created = create_note(client, title="Alice's secret").get_json()
    client.post("/api/auth/logout")

    register(client, username="bob")
    res = client.delete(f"/api/notes/{created['id']}")
    assert res.status_code == 404

    client.post("/api/auth/logout")
    login(client, username="alice")
    still_there = client.get(f"/api/notes/{created['id']}")
    assert still_there.status_code == 200
