# ============================================================
# Day 30 — rich text formatting: content_html sanitization + the
# /api/notes create/update/get round-trip for the new field.
# ============================================================
from conftest import register

from html_sanitize import sanitize_note_html


# ---------- unit tests: html_sanitize.sanitize_note_html ----------


def test_sanitize_passes_through_allowed_formatting_tags():
    html = "<div><b>bold</b> <i>italic</i> <u>under</u> <s>strike</s></div>"
    assert sanitize_note_html(html) == html


def test_sanitize_keeps_lists_and_headings():
    html = "<h2>Title</h2><ul><li>one</li><li>two</li></ul><ol><li>a</li></ol><blockquote>quote</blockquote>"
    assert sanitize_note_html(html) == html


def test_sanitize_keeps_br_and_p():
    html = "<p>line one</p><p>line two<br>still line two</p>"
    assert sanitize_note_html(html) == html


def test_sanitize_strips_script_tag_and_its_content():
    result = sanitize_note_html('<div>hello</div><script>alert("x")</script>')
    assert "<script" not in result
    assert "alert" not in result
    assert "hello" in result


def test_sanitize_strips_style_tag_and_its_content():
    result = sanitize_note_html("<div>hello</div><style>body{background:red}</style>")
    assert "<style" not in result
    assert "background" not in result
    assert "hello" in result


def test_sanitize_drops_event_handler_attributes():
    result = sanitize_note_html('<div onclick="alert(1)">hi</div>')
    assert "onclick" not in result
    assert "alert" not in result
    assert "hi" in result
    assert result == "<div>hi</div>"


def test_sanitize_drops_disallowed_tags_but_keeps_their_text():
    result = sanitize_note_html("<iframe src='evil.com'></iframe><div>kept</div>")
    assert "iframe" not in result
    assert "kept" in result


def test_sanitize_keeps_allowed_span_style_props():
    html = '<span style="color: #ff0000; font-weight: bold;">red bold</span>'
    result = sanitize_note_html(html)
    assert "color: #ff0000" in result
    assert "font-weight: bold" in result
    assert "red bold" in result


def test_sanitize_drops_disallowed_css_properties():
    html = '<span style="color: red; position: fixed; top: 0;">x</span>'
    result = sanitize_note_html(html)
    assert "color: red" in result
    assert "position" not in result
    assert "top" not in result


def test_sanitize_blocks_css_url_and_javascript_scheme():
    html = '<span style="background-color: url(javascript:alert(1)); color: red;">x</span>'
    result = sanitize_note_html(html)
    assert "url(" not in result
    assert "color: red" in result


def test_sanitize_keeps_font_tag_with_allowed_attrs():
    html = '<font color="#00ff00" face="Arial" size="5">hi</font>'
    assert sanitize_note_html(html) == html


def test_sanitize_rejects_invalid_font_size():
    html = '<font size="99">hi</font>'
    result = sanitize_note_html(html)
    assert "size=" not in result
    assert "<font>hi</font>" == result


def test_sanitize_keeps_math_formula_span_with_data_latex():
    html = '<span class="math-formula" data-latex="E=mc^2" contenteditable="false">rendered</span>'
    result = sanitize_note_html(html)
    assert 'class="math-formula"' in result
    assert 'data-latex="E=mc^2"' in result
    # contenteditable isn't in the allow-list for span — dropped, not fatal
    assert "contenteditable" not in result


def test_sanitize_drops_disallowed_span_classes():
    html = '<span class="totally-unrelated-class">x</span>'
    result = sanitize_note_html(html)
    assert "class=" not in result
    assert "<span>x</span>" == result


def test_sanitize_escapes_text_content():
    html = "<div>1 < 2 & 3 > 2</div>"
    result = sanitize_note_html(html)
    assert "&lt;" in result
    assert "&amp;" in result
    assert "&gt;" in result


def test_sanitize_returns_none_for_non_string():
    assert sanitize_note_html(None) is None
    assert sanitize_note_html(123) is None
    assert sanitize_note_html([]) is None


def test_sanitize_returns_none_for_blank_string():
    assert sanitize_note_html("") is None
    assert sanitize_note_html("   ") is None


def test_sanitize_truncates_extremely_long_input():
    huge = "<div>" + ("a" * 600_000) + "</div>"
    result = sanitize_note_html(huge)
    assert len(result) < 600_000


# ---------- API integration: contentHtml on create/update/get ----------


def test_create_note_stores_and_returns_content_html(client):
    register(client)
    html = "<div><b>Bold</b> text with <span class=\"math-formula\" data-latex=\"x^2\">x²</span></div>"
    res = client.post("/api/notes", json={"title": "T", "text": "Bold text with x^2", "contentHtml": html})
    assert res.status_code == 201
    body = res.get_json()
    assert body["contentHtml"] == html
    # text (used by the whole AI pipeline) is completely unaffected by contentHtml
    assert body["text"] == "Bold text with x^2"


def test_create_note_without_content_html_returns_null(client):
    register(client)
    res = client.post("/api/notes", json={"title": "T", "text": "plain note"})
    assert res.status_code == 201
    assert res.get_json()["contentHtml"] is None


def test_create_note_sanitizes_malicious_content_html(client):
    register(client)
    res = client.post(
        "/api/notes",
        json={"title": "T", "text": "hi", "contentHtml": '<div>hi</div><script>alert(1)</script>'},
    )
    assert res.status_code == 201
    assert "<script" not in res.get_json()["contentHtml"]


def test_update_note_sets_content_html(client):
    register(client)
    note = client.post("/api/notes", json={"title": "T", "text": "plain"}).get_json()
    res = client.put(f"/api/notes/{note['id']}", json={"contentHtml": "<b>now bold</b>"})
    assert res.status_code == 200
    assert res.get_json()["contentHtml"] == "<b>now bold</b>"


def test_update_note_without_content_html_key_preserves_existing(client):
    register(client)
    note = client.post("/api/notes", json={"title": "T", "text": "plain", "contentHtml": "<b>kept</b>"}).get_json()
    res = client.put(f"/api/notes/{note['id']}", json={"title": "New title"})
    assert res.status_code == 200
    assert res.get_json()["contentHtml"] == "<b>kept</b>"


def test_update_note_can_clear_content_html_by_sending_empty_string(client):
    register(client)
    note = client.post("/api/notes", json={"title": "T", "text": "plain", "contentHtml": "<b>kept</b>"}).get_json()
    res = client.put(f"/api/notes/{note['id']}", json={"contentHtml": ""})
    assert res.status_code == 200
    assert res.get_json()["contentHtml"] is None


def test_get_note_includes_content_html(client):
    register(client)
    note = client.post("/api/notes", json={"title": "T", "text": "plain", "contentHtml": "<i>italic</i>"}).get_json()
    res = client.get(f"/api/notes/{note['id']}")
    assert res.status_code == 200
    assert res.get_json()["contentHtml"] == "<i>italic</i>"


def test_list_notes_includes_content_html(client):
    register(client)
    client.post("/api/notes", json={"title": "T", "text": "plain", "contentHtml": "<u>u</u>"})
    res = client.get("/api/notes")
    assert res.status_code == 200
    assert res.get_json()[0]["contentHtml"] == "<u>u</u>"
