"""Safe Groq configuration checker for AI Notes.
Does not print the API key.
"""
from pathlib import Path
import os
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
key = os.getenv("GROQ_API_KEY", "").strip()
model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()

print("AI Notes - Groq configuration check")
print("-----------------------------------")
print(".env found:", (ROOT / ".env").exists())
print("GROQ_API_KEY loaded:", bool(key))
print("Key prefix:", key[:4] if key else "<missing>")
print("Key length:", len(key))
print("Model:", model)

if not key:
    raise SystemExit(
        "\nERROR: GROQ_API_KEY is missing. Add it to backend/.env and restart the server."
    )

try:
    r = requests.get(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization": f"Bearer {key}"},
        timeout=15,
    )
except requests.RequestException as exc:
    raise SystemExit(f"\nERROR: Could not reach Groq: {exc}")

print("Groq API status:", r.status_code)
if r.status_code == 200:
    ids = {item.get("id") for item in r.json().get("data", [])}
    print("Key is valid: YES")
    print("Configured model available:", model in ids)
    if model not in ids:
        print("Tip: update GROQ_MODEL in backend/.env to a model returned by Groq.")
    print("\nGroq configuration looks good.")
elif r.status_code == 401:
    raise SystemExit(
        "\nERROR: Groq rejected the API key (401). Create a new key at "
        "https://console.groq.com/keys and replace GROQ_API_KEY in backend/.env."
    )
else:
    print("Response:", r.text[:500])
    raise SystemExit("\nERROR: Groq configuration check failed.")
