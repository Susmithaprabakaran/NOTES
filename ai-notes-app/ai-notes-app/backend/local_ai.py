# ============================================================
# AI Notes — Day 37 — Local AI provider (zero API key, zero network)
#
# Every AI feature in this app (summarize, tag, extract entities/
# concepts/tasks, build a flowchart, build a study pack, phrase a
# notification digest, explain a graph connection, polish text, title
# a note) goes through the single call_ai(prompt, system) function in
# ai_client.py. This module is the local implementation of all of
# that: plain-Python text analysis (word frequency, regex, sentence
# scoring) that produces the exact same shapes the rest of the app
# already expects from OpenAI/Gemini/Groq/Ollama — so nothing
# downstream (notes.py, tasks.py, study.py, flowchart.py, graph.py,
# notifications.py) needed to change to support it.
#
# No paid API, no signup, no internet connection, no PyTorch/Whisper-
# sized dependency — this is stdlib `re`/`collections` only, same
# "core requirements.txt" bar as ai_client.py itself. It's the same
# philosophy as Day 17's local sentence-transformers embeddings or
# Day 9's local Whisper: a genuinely-local fallback, not a demo.
#
# Set AI_PROVIDER=local in backend/.env and every AI-powered feature
# in the app works immediately, offline, for free. It won't be as
# fluent as a real LLM — summaries are extractive (sentences pulled
# from the note, not freshly composed), MCQ distractors are simpler,
# translation isn't supported at all (see LocalUnsupported below) —
# but it's real analysis of the actual note text, not a stub.
# ============================================================
import json
import re
from collections import Counter

# ------------------------------------------------------------
# Shared text utilities
# ------------------------------------------------------------

STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "so",
    "of", "in", "on", "at", "to", "for", "with", "from", "by", "as",
    "is", "are", "was", "were", "be", "been", "being", "am",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her",
    "us", "them", "my", "your", "his", "its", "our", "their", "this",
    "that", "these", "those", "there", "here", "who", "whom", "which",
    "what", "when", "where", "why", "how", "not", "no", "nor",
    "do", "does", "did", "doing", "have", "has", "had", "having",
    "will", "would", "shall", "should", "can", "could", "may", "might",
    "must", "about", "into", "over", "after", "before", "up", "down",
    "out", "off", "again", "further", "than", "too", "very", "just",
    "also", "one", "all", "each", "some", "any", "such", "own", "same",
    "so", "more", "most", "other", "into", "get", "got", "like",
}

# A tiny hand-picked list of common organization-name suffixes — good
# enough to catch "WebenoID Technologies" / "Acme Corp" / "MIT" style
# names without a real gazetteer.
ORG_SUFFIXES = (
    "inc", "inc.", "llc", "ltd", "ltd.", "corp", "corp.", "co",
    "company", "technologies", "systems", "labs", "solutions",
    "university", "college", "institute", "group", "associates",
    "industries", "enterprises", "partners", "foundation",
)

TITLE_PREFIXES = ("mr", "mrs", "ms", "dr", "prof", "sir", "madam")

WEEKDAYS = (
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
)
MONTHS = (
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec",
)
RELATIVE_DATE_WORDS = (
    "today", "tonight", "tomorrow", "yesterday", "asap",
    "next week", "next month", "next year",
)

# A tiny curated list of tech/science terms that are Title-Cased often
# enough to otherwise get misread as a person's name by the plain
# capitalization heuristic below (there's no real NER model here — see
# local_entities' docstring). These get routed to concepts instead.
TECH_WORDS = {
    "python", "java", "javascript", "typescript", "sql", "html", "css",
    "scikit-learn", "tensorflow", "pytorch", "react", "angular", "vue",
    "docker", "kubernetes", "linux", "windows", "machine", "learning",
    "artificial", "intelligence", "api", "json", "flask", "django",
    "postgresql", "mysql", "mongodb", "git", "github",
}

# Abbreviations that end in a period but don't end a sentence — without
# protecting these, "Dr. Kim" or "e.g. this" would be misread as two
# separate sentences by the splitter below.
ABBREVIATIONS = (
    "dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e", "approx",
)


def _protect_abbreviations(text):
    def repl(m):
        return m.group(1) + "\x00"

    pattern = r"\b(" + "|".join(re.escape(a) for a in ABBREVIATIONS) + r")\.(?=\s)"
    return re.sub(pattern, repl, text, flags=re.IGNORECASE)


def sentences(text):
    """Very light sentence splitter — good enough for note-length text.
    Keeps a trailing sentence even without terminal punctuation. Not a
    real NLP sentence tokenizer: it protects a short list of common
    abbreviations (Dr., Mr., e.g., ...) but a genuinely unusual
    abbreviation can still cause an early split."""
    text = (text or "").strip()
    if not text:
        return []
    protected = _protect_abbreviations(text.replace("\n", " "))
    # Split on ., !, ? followed by whitespace/end, but don't split on
    # common abbreviations/decimals (e.g. "Dr. Kim", "3.5").
    raw = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", protected)
    out = []
    for s in raw:
        s = s.replace("\x00", ".").strip()
        if s:
            out.append(s)
    return out or [text.replace("\x00", ".")]


def words(text):
    return re.findall(r"[A-Za-z][A-Za-z'-]*", text or "")


def _strip_possessive(word):
    """'Tuesday's' -> 'tuesday', so date/tech-word comparisons aren't
    fooled by a trailing possessive."""
    return re.sub(r"'s$", "", word.lower())


def word_freq(text, exclude=STOPWORDS):
    counts = Counter(w.lower() for w in words(text) if w.lower() not in exclude and len(w) > 2)
    return counts


def score_sentences(sents, freq):
    scores = []
    for s in sents:
        ws = [w.lower() for w in words(s) if w.lower() not in STOPWORDS]
        if not ws:
            scores.append(0.0)
            continue
        scores.append(sum(freq.get(w, 0) for w in ws) / len(ws))
    return scores


def top_sentences(text, n=2):
    sents = sentences(text)
    if len(sents) <= n:
        return sents
    freq = word_freq(text)
    scores = score_sentences(sents, freq)
    ranked = sorted(range(len(sents)), key=lambda i: scores[i], reverse=True)[:n]
    ranked.sort()  # preserve original reading order
    return [sents[i] for i in ranked]


_ABBREV_DOT_RE = re.compile(
    r"\b(" + "|".join(re.escape(a) for a in ABBREVIATIONS) + r")\.(?=\s*[A-Z])",
    re.IGNORECASE,
)


def capitalized_phrases(text):
    """Sequences of 1-3 Title-Case words, used as the building block for
    people/places/organizations/concepts. Returns (phrase, is_sentence_start)
    pairs so callers can weigh mid-sentence capitals more heavily (less
    likely to just be the first word of a sentence)."""
    out = []
    sent_list = sentences(text)
    for s in sent_list:
        # "Dr. Kim" -> "Dr Kim" so the title and name merge into one
        # phrase instead of the period breaking them apart.
        s = _ABBREV_DOT_RE.sub(lambda m: m.group(1), s)
        tokens = re.findall(r"[A-Z][a-zA-Z'-]*|[a-z]+|[.,;:!?]", s)
        i = 0
        # word-position within the sentence, ignoring leading punctuation
        pos = 0
        n = len(tokens)
        while i < n:
            tok = tokens[i]
            if re.match(r"^[A-Z][a-zA-Z'-]*$", tok):
                phrase = [tok]
                j = i + 1
                while j < n and re.match(r"^[A-Z][a-zA-Z'-]*$", tokens[j]) and len(phrase) < 3:
                    phrase.append(tokens[j])
                    j += 1
                joined = " ".join(phrase)
                # Skip a lone single letter (e.g. the "Q" in "Q3") — not
                # a plausible name/place/org on its own.
                if len(joined) > 1:
                    out.append((joined, pos == 0))
                pos += len(phrase)
                i = j
            else:
                if re.match(r"^[a-zA-Z'-]+$", tok):
                    pos += 1
                i += 1
    return out


class LocalUnsupported(Exception):
    """Raised for the handful of AI features the local provider genuinely
    can't do (translation, mainly) — caught by ai_client the same way
    AIUnavailable is, so it degrades gracefully instead of crashing."""


# ------------------------------------------------------------
# Plain-text features
# ------------------------------------------------------------

def local_summarize(text, max_sentences=2):
    picked = top_sentences(text, n=max_sentences)
    return " ".join(picked).strip()


def local_polish(text):
    """Not a grammar model — a genuine but modest local cleanup pass:
    collapses whitespace, fixes spacing before punctuation, capitalizes
    the start of each sentence, and ensures the text ends with a period
    if it looks like it's missing one."""
    t = re.sub(r"[ \t]+", " ", (text or "").strip())
    t = re.sub(r"\s+\n", "\n", t)
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)  # "word ," -> "word,"
    t = re.sub(r"([,.!?;:])(?=[A-Za-z])", r"\1 ", t)  # "word,next" -> "word, next"
    parts = sentences(t)
    fixed = []
    for s in parts:
        s = s.strip()
        if s:
            s = s[0].upper() + s[1:]
            if not re.search(r"[.!?]$", s):
                s += "."
        fixed.append(s)
    return " ".join(fixed).strip() or t


def local_title(text, max_words=8):
    sents = sentences(text)
    base = sents[0] if sents else (text or "").strip()
    ws = base.split()
    title = " ".join(ws[:max_words]).strip(" .,-")
    if not title:
        return "Untitled note"
    return title[0].upper() + title[1:]


def local_digest(bullets_text, max_words=25):
    bullets = [b.strip("- ").strip() for b in (bullets_text or "").split("\n") if b.strip()]
    if not bullets:
        return "You're all caught up."
    msg = "Here's what's up: " + "; ".join(bullets) + "."
    words_ = msg.split()
    if len(words_) > max_words:
        msg = " ".join(words_[:max_words]).rstrip(",;") + "…"
    return msg


def local_graph_explain(context_text):
    """The prompt already hands us fully-structured context (concept name,
    related concepts/notes, or a hop-by-hop chain) — turning that into a
    plain sentence needs formatting, not language generation, so this is
    a template rather than an extractive summary."""
    ctx = context_text or ""
    if ctx.startswith("Chain: "):
        chain_line, _, rest = ctx.partition("\n")
        chain = chain_line[len("Chain: "):].strip()
        hop_lines = [l.strip() for l in rest.split("\n") if l.strip() and l.strip() != "Hops:"]
        explanation = f"This chain links {chain} through notes that mention each pair of ideas in turn."
        if hop_lines:
            explanation += " Specifically: " + " ".join(hop_lines) + "."
        return explanation

    lines = [l for l in ctx.split("\n") if l.strip()]
    concept = None
    notes_line = None
    related_line = None
    for l in lines:
        if l.lower().startswith("concept:"):
            concept = l.split(":", 1)[1].strip()
        elif l.lower().startswith("notes that mention it:"):
            notes_line = l.split(":", 1)[1].strip()
        elif l.lower().startswith("related concepts:"):
            related_line = l.split(":", 1)[1].strip()
    concept = concept or "This concept"
    out = f"{concept} shows up in your notes"
    if notes_line:
        out += f" ({notes_line})"
    out += "."
    if related_line:
        out += f" It tends to appear alongside {related_line}."
    return out


# ------------------------------------------------------------
# JSON-shaped features
# ------------------------------------------------------------

def local_tags(text, max_tags=5):
    freq = word_freq(text)
    tags = [w for w, _ in freq.most_common(max_tags)]
    return tags


def _looks_like_date_phrase(phrase):
    p = phrase.lower()
    if p in RELATIVE_DATE_WORDS:
        return True
    if any(p == d or p.startswith(d) for d in WEEKDAYS):
        return True
    if any(m in p for m in MONTHS):
        return True
    if re.search(r"\bnext\s+\w+\b", p) or re.search(r"\bin\s+\d+\s+(day|week|month)s?\b", p):
        return True
    return False


def local_entities(text):
    people, dates, places, orgs = [], [], [], []
    t = text or ""

    # Dates: relative words, weekday/month mentions, and numeric dates.
    for phrase in RELATIVE_DATE_WORDS:
        if re.search(r"\b" + re.escape(phrase) + r"\b", t, re.IGNORECASE):
            dates.append(phrase.title() if " " not in phrase else phrase.capitalize())
    for m in re.finditer(
        r"\b(next\s+)?(" + "|".join(WEEKDAYS) + r")\b", t, re.IGNORECASE
    ):
        dates.append(m.group(0).strip().title())
    for m in re.finditer(
        r"\b(" + "|".join(MONTHS) + r")\.?\s+\d{1,2}(st|nd|rd|th)?\b", t, re.IGNORECASE
    ):
        dates.append(m.group(0).strip())
    for m in re.finditer(r"\b\d{1,2}/\d{1,2}(/\d{2,4})?\b", t):
        dates.append(m.group(0))

    # People / places / organizations, from capitalized phrases. This is
    # a plain-text heuristic, not a trained NER model (see docstring
    # limitation below) — it gets common cases right but can misfire on
    # ambiguous names.
    phrases = capitalized_phrases(t)
    seen_people, seen_places, seen_orgs = set(), set(), set()
    for phrase, is_sent_start in phrases:
        low = phrase.lower()
        if low in {"i", "the", "a", "an"}:
            continue
        # Weekday/month names are dates, not people or places (e.g. a
        # lone "Tuesday" or possessive "Tuesday's" mid-sentence).
        if any(_strip_possessive(w) in WEEKDAYS or _strip_possessive(w) in MONTHS for w in low.split()):
            continue
        # A small curated list keeps common tech terms (which are also
        # concepts, extracted separately) out of the people/places
        # buckets — see TECH_WORDS above.
        if any(_strip_possessive(w) in TECH_WORDS for w in low.split()):
            continue
        is_org = any(low.endswith(suf) or f" {suf}" in low for suf in ORG_SUFFIXES)
        if is_org:
            if low not in seen_orgs:
                orgs.append(phrase)
                seen_orgs.add(low)
            continue
        multiword = " " in phrase
        # Allow up to two lowercase modifier words between the
        # preposition and the place ("at the downtown Coimbatore
        # office" still counts as "in/at/... Coimbatore").
        looks_like_place = bool(
            re.search(r"\b(in|at|from|to)\s+(?:[a-z]+\s+){0,2}" + re.escape(phrase), t)
        )
        if looks_like_place:
            if low not in seen_places:
                places.append(phrase)
                seen_places.add(low)
        elif multiword or not is_sent_start:
            # Multi-word capitalized phrases ("Sarah Chen") and
            # mid-sentence single capitals are treated as person mentions
            # — the most common case for a stray proper noun in a
            # personal note once dates/places/orgs are ruled out above.
            if low not in seen_people:
                people.append(phrase)
                seen_people.add(low)

    # De-dupe dates case-insensitively while preserving first-seen casing.
    seen_dates, uniq_dates = set(), []
    for d in dates:
        if d.lower() not in seen_dates:
            uniq_dates.append(d)
            seen_dates.add(d.lower())

    return {
        "people": people[:8],
        "dates": uniq_dates[:8],
        "places": places[:8],
        "organizations": orgs[:8],
    }


TASK_VERBS = (
    "email", "call", "text", "message", "buy", "book", "schedule",
    "finish", "complete", "send", "review", "submit", "pay", "fix",
    "prepare", "update", "write", "draft", "follow up", "followup",
    "confirm", "cancel", "reply", "respond", "order", "renew",
    "clean", "organize", "plan", "pick up", "drop off", "return",
)

# Common sentence-starters/transition words end up capitalized purely
# by virtue of starting a sentence, not because they name a topic —
# filtered out of single-word concept candidates specifically (a
# multi-word phrase like "Machine Learning" is unambiguous either way).
_CONCEPT_NOISE_WORDS = {
    "if", "then", "first", "finally", "also", "however", "but", "and",
    "so", "next", "meanwhile", "afterward", "otherwise", "once",
    "unless", "when", "while", "this", "that", "there", "we", "i",
    "they", "it",
} | set(TASK_VERBS)


def local_concepts(text, max_concepts=8):
    phrases = capitalized_phrases(text)
    freq = Counter()
    original_case = {}
    for phrase, is_sent_start in phrases:
        low = phrase.lower()
        if low in {"i"}:
            continue
        if " " not in phrase:
            stripped = _strip_possessive(phrase)
            if stripped in WEEKDAYS or stripped in MONTHS:
                continue
            if is_sent_start and stripped in _CONCEPT_NOISE_WORDS:
                continue
        freq[low] += 1
        original_case.setdefault(low, phrase)
    # Prefer multi-word phrases and repeated mentions, same instinct as a
    # simple RAKE score.
    ranked = sorted(freq.items(), key=lambda kv: (kv[1], len(kv[0].split())), reverse=True)
    return [original_case[low] for low, _ in ranked[:max_concepts]]


TASK_CUES = (
    "need to", "needs to", "have to", "has to", "must", "should",
    "remember to", "don't forget to", "todo", "to-do", "to do:",
    "remind me to", "make sure to",
)


def _extract_due_phrase(sentence):
    low = sentence.lower()
    for phrase in RELATIVE_DATE_WORDS:
        if phrase in low:
            return phrase.capitalize()
    for wd in WEEKDAYS:
        m = re.search(r"\b(next\s+)?" + wd + r"\b", low)
        if m:
            return m.group(0).title()
    m = re.search(r"\bby\s+([a-z]+\s+\d{1,2}(st|nd|rd|th)?)\b", low)
    if m:
        return m.group(1)
    m = re.search(r"\b\d{1,2}/\d{1,2}(/\d{2,4})?\b", sentence)
    if m:
        return m.group(0)
    return None


def _strip_due_wording(sentence, due):
    if not due:
        return sentence.strip()
    # "'s" after the due phrase (e.g. "next Tuesday's review") needs to
    # go with it, or a stray possessive is left dangling in the task text.
    due_pattern = re.escape(due) + r"(?:'s)?"
    cleaned = re.sub(r"\bby\s+" + due_pattern, "", sentence, flags=re.IGNORECASE)
    cleaned = re.sub(due_pattern, "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,.-'")
    return cleaned or sentence.strip()


def _looks_like_task(sentence):
    low = sentence.lower().strip()
    if any(low.startswith(v + " ") or low.startswith(v + ":") for v in TASK_VERBS):
        return True
    if any(cue in low for cue in TASK_CUES):
        return True
    return False


def local_tasks(text):
    out = []
    for s in sentences(text):
        s = s.strip(" -*•\t")
        if not s or not _looks_like_task(s):
            continue
        due = _extract_due_phrase(s)
        task_text = _strip_due_wording(s, due)
        # Turn "need to email Sarah" into "Email Sarah" — drop the leading
        # cue phrase so tasks read as clean imperatives.
        for cue in sorted(TASK_CUES, key=len, reverse=True):
            idx = task_text.lower().find(cue)
            if idx != -1:
                task_text = task_text[idx + len(cue):].strip(" ,.-")
                break
        task_text = task_text.rstrip(".!")
        if task_text:
            task_text = task_text[0].upper() + task_text[1:]
            out.append({"text": task_text, "due": due})
    return out


NODE_START_CUES = ("first", "start", "begin")
NODE_END_CUES = ("finally", "lastly", "done", "finish", "end")
DECISION_CUES = (" if ", " if,", "if(", "whether", "either")


def local_flowchart(text, max_nodes=12):
    steps = []
    for s in sentences(text):
        s = s.strip(" -*•\t0123456789.")
        if s:
            steps.append(s)
    steps = steps[:max_nodes]
    if not steps:
        return {"nodes": [], "edges": []}

    nodes, edges = [], []
    for i, step in enumerate(steps):
        node_id = f"n{i + 1}"
        low = step.lower()
        if i == 0:
            ntype = "start"
        elif i == len(steps) - 1 and any(c in low for c in NODE_END_CUES):
            ntype = "end"
        elif any(c in f" {low} " for c in DECISION_CUES):
            ntype = "decision"
        else:
            ntype = "process"
        label = step if len(step) <= 60 else step[:57].rstrip() + "..."
        nodes.append({"id": node_id, "label": label, "type": ntype})

    for i in range(len(nodes) - 1):
        label = ""
        if nodes[i]["type"] == "decision":
            label = "Yes"
        edges.append({"from": nodes[i]["id"], "to": nodes[i + 1]["id"], "label": label})
        # A decision node also gets a "No" branch so the diagram shows a
        # real fork rather than a silently-linear guess. It can't point
        # back at itself — the frontend/backend both treat a self-loop
        # (from == to) as meaningless and drop it, which used to make
        # every local-provider decision node quietly lose its "No"
        # branch. Instead it loops back to the *previous* node when one
        # exists (a natural "try again" shape for a check/retry step),
        # or forward two steps when it's the very first node and there's
        # somewhere else to go.
        if nodes[i]["type"] == "decision":
            if i > 0:
                edges.append({"from": nodes[i]["id"], "to": nodes[i - 1]["id"], "label": "No"})
            elif i + 2 < len(nodes):
                edges.append({"from": nodes[i]["id"], "to": nodes[i + 2]["id"], "label": "No"})

    return {"nodes": nodes, "edges": edges}


DEFINITION_PATTERNS = (
    # The "(?:a|an|the)\b" word boundary matters: without it, the
    # optional article can match as a *prefix* of the next word (e.g.
    # "a" swallowing the first letter of "approved"), silently
    # corrupting the definition text.
    re.compile(r"^(?P<term>[A-Z][\w '-]{1,40}?)\s+(?:is|are)\s+(?:(?:a|an|the)\b\s*)?(?P<def>.+)$"),
    re.compile(r"^(?P<term>[A-Z][\w '-]{1,40}?)\s+(?:refers to|means)\s+(?P<def>.+)$"),
)

# A definition-style sentence ("X is a Y") only makes sense if X is
# actually a term/noun-phrase — filter out sentences that just happen
# to start with a capitalized conjunction/pronoun ("If the design is
# approved...") from matching as if "If the design" were the term.
_DEFINITION_TERM_BLOCKLIST = {
    "if", "when", "while", "this", "that", "it", "there", "then",
    "first", "next", "after", "before", "once", "unless", "so", "we",
    "i", "they", "also", "finally", "however", "but", "and", "or",
}


def _local_definitions(text, max_definitions=10):
    out, seen = [], set()
    for s in sentences(text):
        for pat in DEFINITION_PATTERNS:
            m = pat.match(s.strip())
            if m:
                term = m.group("term").strip()
                # Strip a leading article the capture group sometimes
                # includes ("A neural network" -> "Neural network").
                term = re.sub(r"^(a|an|the)\s+", "", term, flags=re.IGNORECASE)
                first_word = term.split()[0].lower() if term.split() else ""
                if not term or first_word in _DEFINITION_TERM_BLOCKLIST:
                    continue
                if len(term.split()) > 4:
                    continue
                term = term[0].upper() + term[1:]
                definition = m.group("def").strip().rstrip(".")
                if term.lower() in seen or len(term) > 60 or not definition:
                    continue
                seen.add(term.lower())
                out.append({"term": term, "definition": definition})
                break
        if len(out) >= max_definitions:
            break
    return out


def local_study(text):
    sents = sentences(text)
    freq = word_freq(text)
    keywords = [w for w, _ in freq.most_common(12)]

    summary = local_summarize(text, max_sentences=3)
    key_points = top_sentences(text, n=min(6, max(2, len(sents) // 2 or 1)))

    qna = []
    for kw in keywords[:6]:
        for s in sents:
            if kw in s.lower():
                qna.append({
                    "question": f"What does the note say about \"{kw}\"?",
                    "answer": s.strip(),
                })
                break

    definitions = _local_definitions(text)

    # Simple cloze MCQs: blank a keyword out of a sentence that contains
    # it, correct answer is that keyword, wrong options are other
    # frequent keywords from the same note.
    mcqs = []
    used_sents = set()
    for kw in keywords:
        if len(mcqs) >= 5:
            break
        for idx, s in enumerate(sents):
            if idx in used_sents:
                continue
            pattern = re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
            if pattern.search(s):
                blanked = pattern.sub("____", s, count=1)
                distractors = [w for w in keywords if w != kw][:3]
                if len(distractors) < 1:
                    break
                options = [kw] + distractors
                options = list(dict.fromkeys(options))  # dedupe, keep order
                if len(options) < 2:
                    break
                # Shuffle deterministically (no randomness dependency) by
                # rotating based on the keyword's hash, so it's not always
                # option 0 that's correct.
                rotate = sum(ord(c) for c in kw) % len(options)
                options = options[rotate:] + options[:rotate]
                answer_index = options.index(kw)
                mcqs.append({
                    "question": f"Fill in the blank: {blanked}",
                    "options": options,
                    "answerIndex": answer_index,
                    "explanation": f'The note names "{kw}" here.',
                })
                used_sents.add(idx)
                break

    flashcards = [{"front": d["term"], "back": d["definition"]} for d in definitions]
    if not flashcards:
        for s in key_points[:6]:
            words_ = s.split()
            if len(words_) < 4:
                continue
            front = " ".join(words_[:6]) + ("…" if len(words_) > 6 else "")
            flashcards.append({"front": f"Continue: \"{front}\"", "back": s})

    return {
        "summary": summary,
        "keyPoints": key_points,
        "qna": qna,
        "mcqs": mcqs,
        "flashcards": flashcards,
        "definitions": definitions,
    }


# ------------------------------------------------------------
# Router — inspects the system prompt (stable, always passed by every
# caller in this app) to decide which local feature to run, and pulls
# the underlying note text back out of the prompt (every template in
# this app is "<label>:\n\n{text}", so splitting on the first blank
# line recovers it exactly).
# ------------------------------------------------------------

def _extract_text(prompt):
    if "\n\n" in prompt:
        return prompt.split("\n\n", 1)[1]
    return prompt


def run_local(prompt, system):
    system = system or ""
    prompt = prompt or ""

    if "precise translator" in system:
        raise LocalUnsupported(
            "The local provider doesn't include a translation model. Set AI_PROVIDER to "
            "openai, gemini, groq, or ollama to use translation."
        )

    if "write a single encouraging notification line" in system:
        return local_digest(prompt)

    if "explain how" in system and "ideas connect" in system:
        context = _extract_text(prompt)
        return local_graph_explain(context)

    if "summarize personal notes" in system:
        return local_summarize(_extract_text(prompt))

    if "copy-edit personal notes" in system:
        return local_polish(_extract_text(prompt))

    if "write short titles" in system:
        return local_title(_extract_text(prompt))

    if "categorize personal notes with short tags" in system:
        return json.dumps(local_tags(_extract_text(prompt)))

    if "extract named entities" in system:
        return json.dumps(local_entities(_extract_text(prompt)))

    if "extract the key concepts" in system:
        return json.dumps(local_concepts(_extract_text(prompt)))

    if "extract action items" in system:
        return json.dumps(local_tasks(_extract_text(prompt)))

    if "turn a piece of text describing a process" in system:
        return json.dumps(local_flowchart(_extract_text(prompt)))

    if "turn a personal note into a complete study pack" in system:
        return json.dumps(local_study(_extract_text(prompt)))

    # Unknown/new prompt shape — treat it as a plain summarization
    # request rather than failing outright, so a future feature that
    # reuses call_ai() without local support degrades to "something
    # reasonable" instead of a hard error.
    return local_summarize(_extract_text(prompt) or prompt)
