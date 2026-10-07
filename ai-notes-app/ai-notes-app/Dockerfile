# Works on any Docker host: Fly.io, Railway, Render (Docker runtime), a VPS…
# Build context = the repo root (this folder).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY backend/requirements-prod.txt backend/requirements-prod.txt
RUN pip install -r backend/requirements-prod.txt

COPY backend backend
COPY frontend frontend

WORKDIR /app/backend

# Mount a persistent volume at /data — uploads (profile photos, attachments)
# live there. SECRET_KEY and DATABASE_URL must be supplied at runtime.
ENV PORT=8000 \
    UPLOAD_FOLDER=/data/uploads \
    SESSION_COOKIE_SECURE=1
EXPOSE 8000

CMD ["sh", "-c", "gunicorn --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:${PORT} wsgi:app"]
