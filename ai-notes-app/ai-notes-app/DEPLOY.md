# Deploying AI Notes

AI Notes is one Python (Flask) service that serves both the API and the
frontend, plus a PostgreSQL database and a folder for uploads. Any host that
gives you those three things works.

> The steps below were written against the setup that was tested locally
> (gunicorn + a clean environment built from `requirements-prod.txt` +
> PostgreSQL). The host-specific files (`render.yaml`, `Dockerfile`,
> `Procfile`) have **not** been run on those hosts — if a host's dashboard
> has changed, follow its docs for the equivalent setting.

## What you need to set (any host)

| Variable | Value |
|---|---|
| `DATABASE_URL` | Your PostgreSQL connection URL (the host's database add-on gives you this). |
| `SECRET_KEY` | A long random string. Generate: `python3 -c "import secrets; print(secrets.token_hex(32))"`. Keep it secret and **don't change it later** or everyone is logged out. |
| `SESSION_COOKIE_SECURE` | `1` (your site must be served over HTTPS — hosts do this for you). |
| `UPLOAD_FOLDER` | A folder on a **persistent disk/volume**, e.g. `/data/uploads`. Without one, profile photos and attachments disappear on every redeploy. |
| `AI_PROVIDER` | `local` (no key needed), or `openai` / `gemini` / `groq` / `ollama` plus that provider's key — see `backend/.env.example`. |

`backend/wsgi.py` refuses to start in HTTPS mode if `SECRET_KEY` is missing,
so a forgotten key fails loudly instead of running insecurely. Database
tables are created/updated automatically on every start.

## Option A — Render (uses `render.yaml`)

1. Push the project to GitHub (`backend/.env` is git-ignored — keep it that way).
2. Render dashboard → **New → Blueprint** → pick the repo. It creates the web
   service and the database and connects them.
3. A persistent disk needs a paid web-service plan on Render. If you don't
   need uploads to survive redeploys you can remove the `disk:` block and the
   `UPLOAD_FOLDER` line in `render.yaml`.
4. Deploy, then open `https://<your-service>.onrender.com/api/health`.

## Option B — Docker (Fly.io, Railway, a VPS, anywhere)

```bash
docker build -t ai-notes .
docker run -p 8000:8000 \
  -e DATABASE_URL=postgresql://user:pass@host:5432/dbname \
  -e SECRET_KEY=<your-random-key> \
  -e AI_PROVIDER=local \
  -v ai-notes-data:/data \
  ai-notes
```

`-v ai-notes-data:/data` is the persistent uploads volume. Put a reverse
proxy with HTTPS (Caddy, nginx, or the host's built-in one) in front; the
image sets `SESSION_COOKIE_SECURE=1`, so **logging in only works over HTTPS**.
On Railway/Fly, create a Postgres, set the variables above, and attach a
volume mounted at `/data`.

## Option C — Heroku-style hosts (uses `Procfile`)

The root `requirements.txt` and `Procfile` are set up for buildpack hosts.
Add a Postgres add-on, set the variables above, deploy. These hosts usually
have no persistent disk, so uploads won't survive restarts — use Option A or
B if you need them to.

## Option D — your own Linux server

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-prod.txt
export DATABASE_URL=... SECRET_KEY=... SESSION_COOKIE_SECURE=1 UPLOAD_FOLDER=/var/lib/ai-notes/uploads
gunicorn --workers 1 --threads 4 --timeout 120 --bind 127.0.0.1:8000 wsgi:app
```

Run it under systemd and put nginx/Caddy (with HTTPS) in front.

## After deploying — quick check

1. `GET /api/health` returns `{"status":"ok"}`.
2. Register an account, create a note, search for it **by name**.
3. Profile (round avatar, top right): upload a photo, add an alternate email.
4. Trigger a redeploy and confirm the photo is still there (proves the disk works).

## Notes

- **Lean vs full install.** `requirements-prod.txt` leaves out
  `sentence-transformers` (it pulls in PyTorch — gigabytes). Without it,
  "Search inside note text" uses keyword (TF-IDF) ranking; search by note
  *name* is unaffected. Use `requirements.txt` on a bigger server to get
  semantic search back.
- **One worker on purpose.** `--workers 1 --threads 4` keeps memory low and
  avoids several processes racing to set up the database on first boot. Raise
  it once the app is running if you need more throughput.
- **Open sign-up.** Anyone who can reach the site can create an account.
- **Back up your database** (your host's Postgres backups, or `pg_dump`) —
  and the uploads folder if it matters to you.
- **Optional extras** — server-side voice transcription and OCR need the
  extra requirement files and, for OCR, the Tesseract system package; they
  aren't installed by the files above. See the main README.
- **Never** deploy with `python run.py` — that's the debug development server.
