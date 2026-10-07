# ============================================================
# AI Notes — Day 11 — AI API
# Two routes, same shape as the Day 9 voice blueprint: a /status
# route the frontend checks before showing any AI button, and one
# action route that requires login like everything else here.
# ============================================================
from flask import Blueprint, jsonify, request

from auth import login_required
from ai_client import AIUnavailable, ai_status, call_ai

bp = Blueprint("ai", __name__, url_prefix="/api/ai")

MAX_TEXT_LENGTH = 20000  # generous for a note; keeps prompts/costs bounded

# action -> (system prompt, prompt template). Kept short and specific so
# results are predictable and don't wander into a full chat conversation.
ACTIONS = {
    "summarize": (
        "You summarize personal notes. Reply with only the summary, no preamble.",
        "Summarize the following note in 2-3 concise sentences, keeping the key points:\n\n{text}",
    ),
    "polish": (
        "You copy-edit personal notes. Reply with only the corrected text, no preamble or explanation.",
        "Fix the grammar, punctuation, and clarity of the following text without changing its "
        "meaning, tone, or the ideas in it:\n\n{text}",
    ),
    "title": (
        "You write short titles for personal notes. Reply with only the title, no quotes, no preamble.",
        "Suggest a short, descriptive title (max 8 words) for the following note:\n\n{text}",
    ),
}

# Day 28 — audio note translation. A fixed allow-list (name -> code) rather
# than trusting an arbitrary free-text language from the client, same
# reasoning as ACTIONS above: predictable prompts, and no risk of someone
# passing prompt-injection text through what's supposed to be a language
# name field.
TRANSLATE_LANGUAGES = {
    "english": "English",
    "hindi": "Hindi",
    "tamil": "Tamil",
    "telugu": "Telugu",
    "malayalam": "Malayalam",
    "kannada": "Kannada",
    "bengali": "Bengali",
    "marathi": "Marathi",
    "gujarati": "Gujarati",
    "urdu": "Urdu",
}

# Day 28 (fix): the allow-list above covers the 10 languages people ask
# for most, but it's still a *fixed* list — there was no way to translate
# into a language that isn't one of the ten. "custom" is a special code
# (like "all") that means "the client is naming their own language" via
# a separate targetLanguageName field, so this stays open-ended without
# giving up validation: the free-text name is still length-capped and
# stripped of newlines so it can't be used to smuggle extra instructions
# into the prompt.
CUSTOM_LANGUAGE_CODE = "custom"
MAX_CUSTOM_LANGUAGE_LENGTH = 40


@bp.get("/status")
@login_required
def status():
    """Lets the frontend check whether an AI model is connected before
    showing the AI-assist button, instead of the user discovering it's
    unavailable only after clicking it."""
    available, provider, reason = ai_status()
    body = {"available": available, "provider": provider}
    if reason:
        body["reason"] = reason
    return jsonify(body), 200


@bp.post("/assist")
@login_required
def assist():
    body = request.get_json(silent=True) or {}
    action = body.get("action")
    text = (body.get("text") or "").strip()

    if action not in ACTIONS:
        return jsonify({"error": f"Unknown action. Expected one of: {', '.join(ACTIONS)}."}), 400
    if not text:
        return jsonify({"error": "No text provided to work with."}), 400
    if len(text) > MAX_TEXT_LENGTH:
        return jsonify({"error": f"Text is too long (max {MAX_TEXT_LENGTH} characters)."}), 400

    system, template = ACTIONS[action]
    prompt = template.format(text=text)

    try:
        result = call_ai(prompt, system=system)
        return jsonify({"result": result, "action": action}), 200
    except AIUnavailable as e:
        return jsonify({"error": str(e)}), 501


@bp.get("/languages")
@login_required
def languages():
    """Lets the frontend build the translate dropdown from the same
    allow-list the backend actually validates against, instead of
    hardcoding (and risking drifting from) the list twice. "All
    Languages" is included as its own entry (code 'all') so the
    dropdown doesn't need to special-case it separately."""
    langs = [{"code": "all", "name": "All Languages"}]
    langs += [{"code": code, "name": name} for code, name in TRANSLATE_LANGUAGES.items()]
    langs.append({"code": CUSTOM_LANGUAGE_CODE, "name": "Other language…"})
    return jsonify({"languages": langs}), 200


def _clean_custom_language_name(raw):
    """Validate a free-text language name for the 'custom' translate path.
    Returns (name, error_message) — exactly one of which is not None."""
    name = (raw or "").strip()
    if not name:
        return None, "Type a language to translate into."
    if len(name) > MAX_CUSTOM_LANGUAGE_LENGTH:
        return None, f"Language name is too long (max {MAX_CUSTOM_LANGUAGE_LENGTH} characters)."
    # A prompt is built as an f-string around this value, so strip
    # line breaks — a language name is always one line, and this keeps
    # the value from reshaping the prompt's structure.
    if "\n" in name or "\r" in name:
        return None, "Language name can't contain line breaks."
    return name, None


@bp.post("/translate")
@login_required
def translate():
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    target_code = (body.get("targetLanguage") or "").strip().lower()

    custom_name = None
    if target_code == CUSTOM_LANGUAGE_CODE:
        custom_name, err = _clean_custom_language_name(body.get("targetLanguageName"))
        if err:
            return jsonify({"error": err}), 400
    elif target_code != "all" and target_code not in TRANSLATE_LANGUAGES:
        return jsonify(
            {
                "error": (
                    f"Unknown target language. Expected 'all', 'custom' (with targetLanguageName), "
                    f"or one of: {', '.join(TRANSLATE_LANGUAGES)}."
                )
            }
        ), 400
    if not text:
        return jsonify({"error": "No text provided to translate."}), 400
    if len(text) > MAX_TEXT_LENGTH:
        return jsonify({"error": f"Text is too long (max {MAX_TEXT_LENGTH} characters)."}), 400

    system = (
        "You are a precise translator. Reply with only the translated text — no preamble, no "
        "quotation marks, no explanation of what you changed. Preserve names, numbers, and "
        "formatting (line breaks, bullet points) from the source text."
    )

    # "All Languages" — same sentence, translated into every supported
    # language in one request. One call_ai() round trip per language
    # (there's no single provider call that returns 10 languages at
    # once reliably), collected into one response so the frontend can
    # render them together instead of the person clicking Translate 10
    # times.
    if target_code == "all":
        results = []
        for code, name in TRANSLATE_LANGUAGES.items():
            prompt = f"Translate the following text into {name}:\n\n{text}"
            try:
                result = call_ai(prompt, system=system)
                results.append({"code": code, "name": name, "text": result, "error": None})
            except AIUnavailable as e:
                # If the provider is unreachable, every language in this
                # batch will fail the same way — no point burning 10 failed
                # requests to find that out. Report it once, immediately.
                return jsonify({"error": str(e)}), 501
            except Exception as e:
                # A single language failing (e.g. an odd provider response
                # for that one prompt) shouldn't sink the other 9 — record
                # it inline and keep going.
                results.append({"code": code, "name": name, "text": None, "error": str(e)})
        return jsonify({"targetLanguage": "all", "results": results}), 200

    if target_code == CUSTOM_LANGUAGE_CODE:
        prompt = f"Translate the following text into {custom_name}:\n\n{text}"
        try:
            result = call_ai(prompt, system=system)
            return jsonify(
                {"result": result, "targetLanguage": CUSTOM_LANGUAGE_CODE, "targetLanguageName": custom_name}
            ), 200
        except AIUnavailable as e:
            return jsonify({"error": str(e)}), 501

    target_name = TRANSLATE_LANGUAGES[target_code]
    prompt = f"Translate the following text into {target_name}:\n\n{text}"

    try:
        result = call_ai(prompt, system=system)
        return jsonify({"result": result, "targetLanguage": target_code}), 200
    except AIUnavailable as e:
        return jsonify({"error": str(e)}), 501
