# ============================================================
# Day 30 — allow-list HTML sanitizer for notes.content_html
#
# The rich text editor (frontend/app.js) only ever produces a small,
# known set of tags via document.execCommand + our own math-formula
# insertion code — but content_html still comes over the wire as plain
# JSON from the client, so it's sanitized server-side the same way any
# other user-controlled HTML would be before being stored and later
# re-rendered (via innerHTML) in someone's browser. No third-party HTML
# sanitizer (bleach, etc.) is in requirements.txt, and the allow-list
# needed here is small and fixed, so this is a self-contained
# allow-list parser built on the standard library's html.parser rather
# than adding a new dependency for it.
#
# This is NOT a general-purpose HTML sanitizer — it's deliberately
# narrow, matching exactly what the Day 30 editor can produce (plus
# graceful handling of anything else by just dropping unknown
# tags/attributes rather than erroring).
# ============================================================
import re
from html.parser import HTMLParser

ALLOWED_TAGS = {
    "div", "p", "br", "b", "strong", "i", "em", "u", "s", "span", "font",
    "ul", "ol", "li", "h1", "h2", "h3", "blockquote",
}
VOID_TAGS = {"br"}
# Tags whose content (not just the tag itself) must never survive —
# script/style content is text as far as HTMLParser is concerned, so
# dropping the *tags* alone would still leave their inner text behind.
DROP_CONTENT_TAGS = {"script", "style"}

ALLOWED_ATTRS = {
    "span": {"style", "class", "data-latex"},
    "font": {"color", "face", "size"},
}
ALLOWED_CSS_PROPS = {
    "color", "background-color", "font-family", "font-size",
    "text-align", "font-weight", "font-style", "text-decoration",
}
ALLOWED_CLASSES = {"math-formula"}
MAX_HTML_LENGTH = 500_000


def _sanitize_style(style_value):
    kept = []
    for decl in style_value.split(";"):
        if ":" not in decl:
            continue
        prop, _, val = decl.partition(":")
        prop = prop.strip().lower()
        val = val.strip()
        if prop not in ALLOWED_CSS_PROPS or not val:
            continue
        # Blocks url(...)/expression(...)/javascript: payloads and any
        # attempt to break out of the declaration with more markup.
        lowered = val.lower()
        if any(bad in lowered for bad in ("url(", "expression(", "javascript:", "<", ">")):
            continue
        kept.append(f"{prop}: {val}")
    return "; ".join(kept)


def _esc_text(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_attr(s):
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


class _NoteHtmlSanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.drop_depth = 0  # >0 while inside a <script>/<style> we're dropping entirely

    def handle_starttag(self, tag, attrs):
        if tag in DROP_CONTENT_TAGS:
            self.drop_depth += 1
            return
        if self.drop_depth:
            return
        if tag not in ALLOWED_TAGS:
            return  # drop the tag but keep processing its children/siblings
        self.out.append(self._render_tag(tag, attrs))

    def handle_startendtag(self, tag, attrs):
        if self.drop_depth or tag not in ALLOWED_TAGS:
            return
        self.out.append(self._render_tag(tag, attrs))

    def _render_tag(self, tag, attrs):
        allowed_for_tag = ALLOWED_ATTRS.get(tag, set())
        kept = []
        for name, value in attrs:
            name = (name or "").lower()
            value = value or ""
            if name not in allowed_for_tag:
                continue
            if name == "style":
                value = _sanitize_style(value)
                if not value:
                    continue
            elif name == "class":
                classes = [c for c in value.split() if c in ALLOWED_CLASSES]
                if not classes:
                    continue
                value = " ".join(classes)
            elif name == "color":
                if not re.fullmatch(r"#[0-9a-fA-F]{3,8}|[a-zA-Z]+", value):
                    continue
            elif name == "size":
                if value not in {"1", "2", "3", "4", "5", "6", "7"}:
                    continue
            elif name == "face":
                if not re.fullmatch(r"[A-Za-z0-9 ,\-'\"]+", value):
                    continue
            # data-latex: kept as-is (its own text is escaped below) —
            # it's rendered by KaTeX client-side, never executed as HTML/JS.
            kept.append((name, value))
        attr_str = "".join(f' {n}="{_esc_attr(v)}"' for n, v in kept)
        return f"<{tag}{attr_str}>"

    def handle_endtag(self, tag):
        if tag in DROP_CONTENT_TAGS:
            self.drop_depth = max(0, self.drop_depth - 1)
            return
        if self.drop_depth:
            return
        if tag in ALLOWED_TAGS and tag not in VOID_TAGS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.drop_depth:
            self.out.append(_esc_text(data))

    def handle_entityref(self, name):
        if not self.drop_depth:
            self.out.append(f"&{name};")

    def handle_charref(self, name):
        if not self.drop_depth:
            self.out.append(f"&#{name};")


def sanitize_note_html(raw_html):
    """Returns an allow-list-cleaned copy of raw_html, or None if there's
    nothing usable (not a string, empty/whitespace-only). Unknown tags
    are dropped (children kept); disallowed attributes are dropped
    (tag kept); <script>/<style> are dropped tag *and* content."""
    if not isinstance(raw_html, str) or not raw_html.strip():
        return None
    if len(raw_html) > MAX_HTML_LENGTH:
        raw_html = raw_html[:MAX_HTML_LENGTH]
    parser = _NoteHtmlSanitizer()
    parser.feed(raw_html)
    parser.close()
    return "".join(parser.out)
