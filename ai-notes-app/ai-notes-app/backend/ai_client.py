# ============================================================
# AI Notes — Day 11 — AI model connection (OpenAI / Gemini)
#
# Provider-agnostic wrapper: reads AI_PROVIDER from the environment
# ("openai" or "gemini") plus that provider's API key, and exposes a
# single call_ai(prompt) function that hits the right REST endpoint.
#
# Mirrors the Day 9 transcription.py pattern on purpose: if nothing
# is configured, ai_status() reports that clearly and call_ai() raises
# AIUnavailable with an actionable message instead of crashing —
# nothing else in the app depends on this being set up.
#
# Only the standard library + `requests` are used (no heavy SDKs),
# so this stays in the core requirements.txt, unlike the optional
# Whisper dependency.
# ============================================================
import os

import requests

from local_ai import LocalUnsupported, run_local

REQUEST_TIMEOUT = 30  # seconds — these are short prompts (note text), not huge documents

OPENAI_DEFAULT_MODEL = "gpt-4o-mini"
GEMINI_DEFAULT_MODEL = "gemini-1.5-flash"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"
# Day 29: Ollama exposes an OpenAI-compatible endpoint both for its
# hosted cloud service (https://ollama.com — needs OLLAMA_API_KEY) and
# for a locally-running `ollama serve` (http://localhost:11434 — no key
# needed, local server has no auth of its own). "gpt-oss:120b" is one
# of the models ollama.com's own docs use for cloud access; if you're
# running models locally instead, override with OLLAMA_MODEL to
# whatever you've `ollama pull`ed (e.g. "llama3.1").
OLLAMA_DEFAULT_MODEL = "gpt-oss:120b"
OLLAMA_DEFAULT_CLOUD_HOST = "https://ollama.com"
OLLAMA_DEFAULT_LOCAL_HOST = "http://localhost:11434"


class AIUnavailable(Exception):
    """Raised when no AI provider is configured, or the call to it fails."""


def _provider():
    return (os.environ.get("AI_PROVIDER") or "").strip().lower()


def ai_status():
    """Returns (available, provider, reason) without making a network call."""
    provider = _provider()

    if provider == "openai":
        if os.environ.get("OPENAI_API_KEY"):
            return True, "openai", None
        return False, "openai", "AI_PROVIDER is set to 'openai' but OPENAI_API_KEY is missing from .env."

    if provider == "gemini":
        if os.environ.get("GEMINI_API_KEY"):
            return True, "gemini", None
        return False, "gemini", "AI_PROVIDER is set to 'gemini' but GEMINI_API_KEY is missing from .env."

    if provider == "groq":
        if os.environ.get("GROQ_API_KEY"):
            return True, "groq", None
        return False, "groq", "AI_PROVIDER is set to 'groq' but GROQ_API_KEY is missing from .env."

    if provider == "ollama":
        # Unlike the other providers, Ollama doesn't require a key at
        # all when talking to a local `ollama serve` — only ollama.com's
        # hosted cloud API needs OLLAMA_API_KEY. So there's no
        # "missing config" state to report here; whichever host ends up
        # selected (cloud vs. local) is decided in _call_ollama, and any
        # actual connectivity problem surfaces from that request instead.
        return True, "ollama", None

    if provider == "local":
        # Day 37: a fifth provider that needs no key, no signup, and no
        # network connection at all — see local_ai.py. Always "available"
        # once selected; there's no external service to be misconfigured.
        return True, "local", None

    if provider:
        return False, provider, (
            f"Unknown AI_PROVIDER '{provider}'. Use 'openai', 'gemini', 'groq', 'ollama', or 'local'."
        )

    return False, None, (
        "No AI model connected. Set AI_PROVIDER=openai (with OPENAI_API_KEY), "
        "AI_PROVIDER=gemini (with GEMINI_API_KEY), AI_PROVIDER=groq (with GROQ_API_KEY), "
        "AI_PROVIDER=ollama (with OLLAMA_API_KEY for ollama.com, or none for a local "
        "`ollama serve`), or AI_PROVIDER=local (works immediately, no key, no internet — "
        "see README) in backend/.env, then restart the server."
    )


def call_ai(prompt, system=None):
    """Send a prompt to whichever provider is configured and return the text reply."""
    available, provider, reason = ai_status()
    if not available:
        raise AIUnavailable(reason)

    if provider == "openai":
        return _call_openai(prompt, system)
    if provider == "gemini":
        return _call_gemini(prompt, system)
    if provider == "groq":
        return _call_groq(prompt, system)
    if provider == "ollama":
        return _call_ollama(prompt, system)
    if provider == "local":
        return _call_local(prompt, system)

    # Shouldn't be reachable — ai_status() already validated the provider name.
    raise AIUnavailable(reason or f"Unsupported AI_PROVIDER '{provider}'.")


def _call_local(prompt, system):
    # Day 37: no network call at all — run_local() inspects the system
    # prompt to figure out which feature is being asked for (summarize,
    # tags, entities, tasks, flowchart, study pack, digest, graph
    # explain, ...) and runs local text analysis over the note text
    # pulled back out of the prompt. See local_ai.py for the actual
    # implementations.
    try:
        return run_local(prompt, system)
    except LocalUnsupported as e:
        raise AIUnavailable(str(e)) from e


def _call_openai(prompt, system):
    api_key = os.environ["OPENAI_API_KEY"]
    model = os.environ.get("OPENAI_MODEL", OPENAI_DEFAULT_MODEL)

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"model": model, "messages": messages, "temperature": 0.4},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        raise AIUnavailable(f"Couldn't reach OpenAI: {e}") from e

    if resp.status_code == 401:
        raise AIUnavailable("OpenAI rejected the API key (401). Check OPENAI_API_KEY in .env.")
    if resp.status_code == 429:
        raise AIUnavailable("OpenAI rate-limited this request (429). Try again shortly.")
    if not resp.ok:
        raise AIUnavailable(f"OpenAI request failed ({resp.status_code}): {resp.text[:200]}")

    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as e:
        raise AIUnavailable(f"Unexpected OpenAI response shape: {data}") from e


def _call_gemini(prompt, system):
    api_key = os.environ["GEMINI_API_KEY"]
    model = os.environ.get("GEMINI_MODEL", GEMINI_DEFAULT_MODEL)

    full_prompt = f"{system}\n\n{prompt}" if system else prompt
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    try:
        resp = requests.post(
            url,
            json={"contents": [{"parts": [{"text": full_prompt}]}]},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        raise AIUnavailable(f"Couldn't reach Gemini: {e}") from e

    if resp.status_code in (401, 403):
        raise AIUnavailable(f"Gemini rejected the API key ({resp.status_code}). Check GEMINI_API_KEY in .env.")
    if resp.status_code == 429:
        raise AIUnavailable("Gemini rate-limited this request (429). Try again shortly.")
    if not resp.ok:
        raise AIUnavailable(f"Gemini request failed ({resp.status_code}): {resp.text[:200]}")

    data = resp.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError) as e:
        raise AIUnavailable(f"Unexpected Gemini response shape: {data}") from e


def _call_groq(prompt, system):
    # Groq exposes an OpenAI-compatible chat completions endpoint, so this
    # mirrors _call_openai almost exactly — just a different base URL,
    # default model, and error copy.
    api_key = os.environ["GROQ_API_KEY"]
    model = os.environ.get("GROQ_MODEL", GROQ_DEFAULT_MODEL)

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"model": model, "messages": messages, "temperature": 0.4},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        raise AIUnavailable(f"Couldn't reach Groq: {e}") from e

    if resp.status_code == 401:
        raise AIUnavailable("Groq authentication failed (401). Create a new Groq API key, set GROQ_API_KEY in backend/.env, and restart Flask.")
    if resp.status_code == 403:
        raise AIUnavailable(f"Groq denied this request (403). Check model permissions for {model} or choose another supported model.")
    if resp.status_code == 404:
        raise AIUnavailable(f"Groq model not found (404): {model}. Update GROQ_MODEL in backend/.env.")
    if resp.status_code == 429:
        raise AIUnavailable("Groq rate-limited this request (429). Try again shortly.")
    if not resp.ok:
        raise AIUnavailable(f"Groq request failed ({resp.status_code}): {resp.text[:200]}")

    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as e:
        raise AIUnavailable(f"Unexpected Groq response shape: {data}") from e


def _call_ollama(prompt, system):
    # Ollama exposes an OpenAI-compatible /v1/chat/completions endpoint
    # on both its hosted cloud service and a local `ollama serve` — so
    # this mirrors _call_openai/_call_groq, just with the host picked
    # based on whether an API key was configured:
    #   - OLLAMA_API_KEY set   -> https://ollama.com (cloud), key sent
    #     as a Bearer token, same as ollama's own docs show.
    #   - OLLAMA_API_KEY unset -> http://localhost:11434 (local server),
    #     which has no auth of its own — a placeholder "ollama" value is
    #     sent anyway because the OpenAI-compatible layer's client
    #     libraries generally require *some* non-empty api_key string,
    #     even though the local server ignores it.
    # OLLAMA_HOST always overrides the default for either case, e.g. a
    # self-hosted Ollama reachable at a different address.
    api_key = os.environ.get("OLLAMA_API_KEY", "").strip()
    default_host = OLLAMA_DEFAULT_CLOUD_HOST if api_key else OLLAMA_DEFAULT_LOCAL_HOST
    host = (os.environ.get("OLLAMA_HOST") or default_host).rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", OLLAMA_DEFAULT_MODEL)

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        resp = requests.post(
            f"{host}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key or 'ollama'}",
                "Content-Type": "application/json",
            },
            json={"model": model, "messages": messages, "temperature": 0.4, "stream": False},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        hint = (
            " Is `ollama serve` running?" if host == OLLAMA_DEFAULT_LOCAL_HOST else ""
        )
        raise AIUnavailable(f"Couldn't reach Ollama at {host}: {e}.{hint}") from e

    if resp.status_code in (401, 403):
        raise AIUnavailable(f"Ollama rejected the API key ({resp.status_code}). Check OLLAMA_API_KEY in .env.")
    if resp.status_code == 404:
        raise AIUnavailable(
            f"Ollama model '{model}' not found at {host} (404). Pull it first (`ollama pull {model}`) "
            "or set OLLAMA_MODEL to one you have."
        )
    if resp.status_code == 429:
        raise AIUnavailable("Ollama rate-limited this request (429). Try again shortly.")
    if not resp.ok:
        raise AIUnavailable(f"Ollama request failed ({resp.status_code}): {resp.text[:200]}")

    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as e:
        raise AIUnavailable(f"Unexpected Ollama response shape: {data}") from e
