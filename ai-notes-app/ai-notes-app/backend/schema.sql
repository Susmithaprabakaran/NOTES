-- AI Notes — Day 8 schema (+ Day 12 auto-summary columns)
-- Run automatically on startup by db.py (init_db), or manually:
--   psql -U ainotes -d ainotes_day8 -f schema.sql

CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notes (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT NOT NULL DEFAULT '',
    text       TEXT NOT NULL DEFAULT '',
    pinned     BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Day 12: automatic summarization. summary_source_hash lets us tell
-- whether the stored summary still matches the current text, so we
-- never re-summarize (and never re-bill an AI call for) unchanged notes.
ALTER TABLE notes ADD COLUMN IF NOT EXISTS summary TEXT;
ALTER TABLE notes ADD COLUMN IF NOT EXISTS summary_source_hash TEXT;
ALTER TABLE notes ADD COLUMN IF NOT EXISTS summary_generated_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_notes_user_id ON notes(user_id);
CREATE INDEX IF NOT EXISTS idx_notes_user_pinned ON notes(user_id, pinned);

-- Day 13: extracted action items ("tasks"). Each task belongs to the note
-- it was pulled from (or manually added to) and to the note's owner, so
-- both a per-note task panel and a cross-note "all my tasks" view are
-- simple queries away.
--
-- Day 16: due_text holds an optional short due-date phrase pulled from
-- the task's own wording ("Tomorrow", "Friday", "Aug 18") — deliberately
-- free text rather than a parsed DATE, since notes say things like "by
-- next Tuesday" or "sometime this week" that don't cleanly become a
-- calendar date without guessing the note's timezone/context.
CREATE TABLE IF NOT EXISTS tasks (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    note_id    INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    text       TEXT NOT NULL,
    done       BOOLEAN NOT NULL DEFAULT false,
    due_text   TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_note_id ON tasks(note_id);
CREATE INDEX IF NOT EXISTS idx_tasks_user_done ON tasks(user_id, done);

-- Day 14: automatic tags/categories. tags is a JSON array of short
-- lowercase strings (e.g. ["work", "travel", "finance"]). Same
-- source-hash-caching pattern as Day 12's summary columns.
ALTER TABLE notes ADD COLUMN IF NOT EXISTS tags TEXT NOT NULL DEFAULT '[]';
ALTER TABLE notes ADD COLUMN IF NOT EXISTS tags_source_hash TEXT;
ALTER TABLE notes ADD COLUMN IF NOT EXISTS tags_generated_at TIMESTAMPTZ;

-- Day 15: automatic entity recognition. entities is a JSON object of
-- the shape {"people": [...], "dates": [...], "places": [...],
-- "organizations": [...]} — one column rather than four, since the
-- lists are always generated and refreshed together in a single AI call.
ALTER TABLE notes ADD COLUMN IF NOT EXISTS entities TEXT NOT NULL DEFAULT '{"people":[],"dates":[],"places":[],"organizations":[]}';
ALTER TABLE notes ADD COLUMN IF NOT EXISTS entities_source_hash TEXT;
ALTER TABLE notes ADD COLUMN IF NOT EXISTS entities_generated_at TIMESTAMPTZ;

-- Day 16: due-date phrase per task (see note on the tasks table above).
-- Kept here too as a plain ALTER, matching the pattern for every other
-- column added after the initial CREATE TABLE, so this script stays
-- safely re-runnable against a database that already has a tasks table
-- from before Day 16.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS due_text TEXT;

-- Day 17: semantic search embeddings.
--
-- `embedding` stores a 384-dim sentence-transformers vector (model:
-- all-MiniLM-L6-v2) as a JSON array of floats in a plain TEXT column —
-- deliberately NOT a pgvector `vector` column, so this schema applies
-- cleanly on any Postgres install, including ones without the pgvector
-- extension. Same source-hash-caching pattern as summary/tags/entities:
-- embedding_source_hash lets the backend skip re-embedding text that
-- hasn't changed.
--
-- If your Postgres DOES have the pgvector extension available, the
-- line below opts in to it — the backend detects this automatically at
-- query time (see backend/embeddings.py: pgvector_available) and uses
-- a fast in-database `<=>` cosine-distance query instead of pulling
-- every embedding into Python. If the extension isn't installed this
-- CREATE EXTENSION statement will just fail and can be skipped (psql
-- continues past errors by default) — semantic search still works via
-- the pure-Python fallback either way.
-- Wrapped in a DO block so a Postgres WITHOUT pgvector doesn't abort the
-- whole schema (init_db runs this file as one transaction — a bare failing
-- CREATE EXTENSION would roll back every table after it, and the app would
-- refuse to start).
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS vector;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pgvector not available (%) - continuing without it', SQLERRM;
END
$$;

ALTER TABLE notes ADD COLUMN IF NOT EXISTS embedding TEXT;
ALTER TABLE notes ADD COLUMN IF NOT EXISTS embedding_source_hash TEXT;
ALTER TABLE notes ADD COLUMN IF NOT EXISTS embedding_generated_at TIMESTAMPTZ;

-- Day 18: knowledge graph. `concepts` is a JSON array of short strings
-- naming the specific ideas/technologies/topics a note discusses (e.g.
-- ["Machine Learning", "Python", "Scikit-learn"]) — finer-grained than
-- Day 14's tags (broad categories like "work") and disjoint from Day
-- 15's entities (people/dates/places/organizations). These are the
-- graph's nodes: two notes that mention the same concept get an edge
-- between them, and two concepts mentioned together in one note get an
-- edge between each other, which is what lets the graph chain
-- "Machine Learning → Python → Scikit-learn → Classification →
-- Evaluation Metrics" across three separate notes that only ever
-- overlap pairwise. Same source-hash-caching pattern as every other
-- AI-derived column.
ALTER TABLE notes ADD COLUMN IF NOT EXISTS concepts TEXT NOT NULL DEFAULT '[]';
ALTER TABLE notes ADD COLUMN IF NOT EXISTS concepts_source_hash TEXT;
ALTER TABLE notes ADD COLUMN IF NOT EXISTS concepts_generated_at TIMESTAMPTZ;

-- Day 21: file attachments (PDF, images). The file itself lives on disk
-- (see UPLOAD_FOLDER in attachments.py) under a server-generated name —
-- never the name the browser sent — so this row is what maps a safe
-- on-disk filename back to what the user actually uploaded, and who's
-- allowed to see it.
CREATE TABLE IF NOT EXISTS attachments (
    id            SERIAL PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    note_id       INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    filename      TEXT NOT NULL,              -- server-generated on-disk name
    original_name TEXT NOT NULL,              -- what the user uploaded (display/download name)
    mime_type     TEXT NOT NULL,
    kind          TEXT NOT NULL,              -- 'pdf' | 'image' — which UI treatment to use
    size_bytes    INTEGER NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_attachments_note_id ON attachments(note_id);
CREATE INDEX IF NOT EXISTS idx_attachments_user_id ON attachments(user_id);

-- Day 22: OCR for handwritten notes. Extracted text lives on the
-- attachment row it came from (an attachment can be re-OCR'd; the note's
-- own `text` column is only ever touched when the person explicitly
-- clicks "Insert into note" in the frontend — this table just tracks
-- what OCR found and whether it's been run at all).
--   ocr_status: 'none' | 'pending' | 'done' | 'unavailable' | 'error'
--     - 'none'        no OCR attempt yet (e.g. non-image attachments stay here forever)
--     - 'pending'     upload finished, OCR is running (set by the frontend optimistically;
--                     the backend call is synchronous so this is transient)
--     - 'done'        ocr_text holds the result (possibly an empty string — legitimately
--                     no legible text found, not a failure)
--     - 'unavailable' Tesseract isn't installed on the server — see ocr.py
--     - 'error'       the image itself couldn't be processed (corrupt file, etc)
ALTER TABLE attachments ADD COLUMN IF NOT EXISTS ocr_text TEXT;
ALTER TABLE attachments ADD COLUMN IF NOT EXISTS ocr_status TEXT NOT NULL DEFAULT 'none';
ALTER TABLE attachments ADD COLUMN IF NOT EXISTS ocr_error TEXT;
ALTER TABLE attachments ADD COLUMN IF NOT EXISTS ocr_generated_at TIMESTAMPTZ;

-- Day 26: manual priority, for the new priority filter. Deliberately a
-- plain TEXT column validated in the app layer (notes.py: PRIORITY_VALUES)
-- rather than a DB CHECK constraint or ENUM type — same pattern already
-- used for attachments.ocr_status above — so this ALTER stays a simple,
-- safely re-runnable "ADD COLUMN IF NOT EXISTS" like everything else in
-- this file, with no ALTER-constraint edge cases to worry about.
-- Values: 'none' | 'low' | 'medium' | 'high'
ALTER TABLE notes ADD COLUMN IF NOT EXISTS priority TEXT NOT NULL DEFAULT 'none';
CREATE INDEX IF NOT EXISTS idx_notes_user_priority ON notes(user_id, priority);

-- Day 27: audio notes. Audio attachments reuse the existing attachments
-- table/pipeline entirely (same upload, storage, delete, download code —
-- see attachments.py's ALLOWED_EXTENSIONS) rather than a separate table;
-- these two extra columns are audio-only metadata (duration for the
-- player) plus a transcript_* column group mirroring ocr_status/ocr_text/
-- ocr_error/ocr_generated_at above, one column set per content-extraction
-- pipeline (OCR for images, Whisper transcription for audio) rather than
-- overloading the ocr_* columns for something that isn't OCR.
ALTER TABLE attachments ADD COLUMN IF NOT EXISTS duration_seconds NUMERIC;
ALTER TABLE attachments ADD COLUMN IF NOT EXISTS transcript_text TEXT;
ALTER TABLE attachments ADD COLUMN IF NOT EXISTS transcript_status TEXT NOT NULL DEFAULT 'none';
ALTER TABLE attachments ADD COLUMN IF NOT EXISTS transcript_error TEXT;
ALTER TABLE attachments ADD COLUMN IF NOT EXISTS transcript_generated_at TIMESTAMPTZ;

-- Day 30: rich text formatting + math formulas in the editor.
--
-- `text` (above) stays exactly what it always was — the plain-text
-- extraction of the note, which is all the entire AI pipeline
-- (summaries, tags, entities, concepts, embeddings, search, task
-- extraction) has ever read. None of that code changes for this
-- feature. `content_html` is purely an additive rendering layer: the
-- actual HTML (bold/italic/underline, fonts, colors, lists, alignment,
-- and rendered <span class="math-formula" data-latex="..."> elements
-- for KaTeX formulas) the rich editor displays and edits. It's
-- nullable — notes created before Day 30, or via the API directly,
-- have no HTML and the frontend falls back to rendering `text` as
-- plain lines, so nothing existing breaks.
ALTER TABLE notes ADD COLUMN IF NOT EXISTS content_html TEXT;

-- Day 32: AI-generated flowcharts. Select some text in a note, click
-- "Generate Flowchart with AI", and the AI turns that selection into a
-- small directed graph (nodes + edges) rendered as an SVG diagram.
--
-- This is its own table, not columns on `notes` (unlike summary/tags/
-- entities/concepts, which are one-per-note and overwrite on
-- regeneration): a note can have several flowcharts, one per selection
-- someone's turned into a diagram, and generating a new one from a
-- different passage shouldn't blow away an earlier one. Same
-- one-to-many shape as `attachments` above.
CREATE TABLE IF NOT EXISTS flowcharts (
    id            SERIAL PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    note_id       INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    source_text   TEXT NOT NULL,             -- the selection this was generated from
    title         TEXT NOT NULL DEFAULT '',  -- short label for the panel list (derived from source_text)
    nodes         TEXT NOT NULL,             -- JSON array: [{id, label, type}], type one of
                                              -- start/end/process/decision/io
    edges         TEXT NOT NULL,             -- JSON array: [{from, to, label}]
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_flowcharts_note_id ON flowcharts(note_id);
CREATE INDEX IF NOT EXISTS idx_flowcharts_user_id ON flowcharts(user_id);

-- Day 33: "Study Mode". One AI call turns a whole note into a study
-- pack (summary, key points, Q&A, MCQs, flashcards, definitions),
-- shown on its own page instead of buried in the note editor.
--
-- One row PER NOTE (note_id UNIQUE), not one-to-many like flowcharts —
-- same reasoning as the summary/tags/entities columns on `notes`
-- itself: there's a single "current" study pack for a note, and
-- regenerating replaces it (via ON CONFLICT (note_id) DO UPDATE)
-- rather than piling up old copies every time someone clicks
-- "Regenerate". source_hash mirrors summary_source_hash: it lets the
-- frontend flag a pack as stale once the note's text has moved on
-- from what the pack was generated from, without needing a separate
-- AI call just to check.
CREATE TABLE IF NOT EXISTS study_materials (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    note_id      INTEGER NOT NULL UNIQUE REFERENCES notes(id) ON DELETE CASCADE,
    summary      TEXT NOT NULL DEFAULT '',
    key_points   TEXT NOT NULL DEFAULT '[]',  -- JSON array of strings
    qna          TEXT NOT NULL DEFAULT '[]',  -- JSON array of {question, answer}
    mcqs         TEXT NOT NULL DEFAULT '[]',  -- JSON array of {question, options, answerIndex, explanation}
    flashcards   TEXT NOT NULL DEFAULT '[]',  -- JSON array of {front, back}
    definitions  TEXT NOT NULL DEFAULT '[]',  -- JSON array of {term, definition}
    source_hash  TEXT NOT NULL,               -- sha256 of the note's text when this pack was generated
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_study_materials_user_id ON study_materials(user_id);

-- Day 35: user profile + account settings.
--
-- Everything here lives directly on `users` (one row per account, not a
-- one-to-many table) since a person has exactly one profile, one current
-- avatar, one theme preference, and one set of notification settings —
-- same "columns, not a side table" reasoning as Day 12's summary or Day
-- 14's tags. `email`/`phone`/`role`/`bio` are all optional (nullable or
-- empty-string default); only `username` (already NOT NULL UNIQUE from
-- Day 8) is required to have an account at all.
ALTER TABLE users ADD COLUMN IF NOT EXISTS full_name TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS bio TEXT NOT NULL DEFAULT '';

-- avatar_filename mirrors attachments.filename from Day 21: a
-- server-generated on-disk name (uuid4 + validated extension), never the
-- name a browser sent, served back through GET /api/profile/avatar/<file>.
ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_filename TEXT;

-- 'dark' | 'light' | 'system' — validated in the app layer (profile.py:
-- VALID_THEMES), same plain-TEXT-not-ENUM pattern as attachments.ocr_status.
ALTER TABLE users ADD COLUMN IF NOT EXISTS theme_preference TEXT NOT NULL DEFAULT 'dark';

-- JSON object of boolean toggles, e.g. {"emailDigest":true,
-- "taskReminders":true,"aiUpdates":false} — same portable
-- plain-TEXT-JSON pattern as notes.tags/notes.entities rather than a
-- separate table, since the whole object is always read/written together.
ALTER TABLE users ADD COLUMN IF NOT EXISTS notification_prefs TEXT NOT NULL DEFAULT '{"emailDigest":true,"taskReminders":true,"aiUpdates":false}';

-- Alternate (backup) email addresses. A JSON array of {"email","label"}
-- objects, e.g. [{"email":"me@work.com","label":"Work"}] — same plain-TEXT-
-- JSON pattern as notification_prefs / notes.tags, since the whole list is
-- always read and written together and is capped at a handful of entries
-- (profile.py: MAX_ALTERNATE_EMAILS). The primary `email` above keeps its
-- unique index; these are extra contact/recovery addresses on the same
-- account, so they deliberately don't.
ALTER TABLE users ADD COLUMN IF NOT EXISTS alternate_emails TEXT NOT NULL DEFAULT '[]';

-- Day 36: Smart Notifications. A small rule engine (due-soon tasks, tasks
-- going stale, high-priority notes going quiet) runs on every /generate
-- call and needs no AI model at all — that's the baseline that always
-- works. When AI_PROVIDER *is* configured and the user's aiUpdates
-- preference is on, one extra "digest" notification is added that asks
-- the model to phrase a one-line summary of the same underlying facts;
-- with no model configured the digest still gets created, just from a
-- plain template instead of a call_ai() result (ai_generated = false).
-- Either way the feature fully works — the AI is a phrasing upgrade,
-- never a requirement.
--
-- note_id/task_id are nullable + ON DELETE SET NULL (not CASCADE): if the
-- note or task that triggered a notification is later deleted, the
-- notification itself is still worth keeping in the feed ("Finished the
-- thesis outline note" shouldn't vanish just because the note got
-- archived) — it just loses its deep link.
--
-- dedupe_key is what stops /generate from spamming duplicate rows every
-- time it's called (on every app boot, per Day 36's frontend): it encodes
-- the rule + subject + a day-bucket, e.g. "task_stale:2026-09-10", so at
-- most one notification per rule/subject/day is ever stored. The unique
-- index below is what makes `ON CONFLICT (user_id, dedupe_key) DO
-- NOTHING` in notifications.py an atomic dedupe instead of a
-- check-then-insert race.
CREATE TABLE IF NOT EXISTS notifications (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type         TEXT NOT NULL,
    title        TEXT NOT NULL,
    message      TEXT NOT NULL,
    note_id      INTEGER REFERENCES notes(id) ON DELETE SET NULL,
    task_id      INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    dedupe_key   TEXT NOT NULL,
    read         BOOLEAN NOT NULL DEFAULT false,
    ai_generated BOOLEAN NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_user_dedupe ON notifications(user_id, dedupe_key);
CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_user_read ON notifications(user_id, read);
CREATE INDEX IF NOT EXISTS idx_notifications_user_created ON notifications(user_id, created_at DESC);

-- Optional, but if two accounts DO both set an email, they can't be the
-- same one. A partial index (WHERE email IS NOT NULL) rather than a plain
-- UNIQUE constraint, since email itself isn't required and Postgres would
-- otherwise need every NULL treated as distinct anyway (which it already
-- does) — the partial form just documents that intent explicitly and
-- keeps the index smaller.
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_lower ON users (lower(email)) WHERE email IS NOT NULL;

-- Security hardening: suspicious-activity detection.
--
-- login_attempts logs every login attempt (success or failure) with the
-- username tried and the caller's IP. auth.py rate-limits new attempts
-- once too many failures pile up for either the username or the IP
-- within a short window (a classic brute-force guard), and
-- security.py's suspicious-activity scan reads this table to raise a
-- notification when a pattern looks like credential stuffing / brute
-- forcing rather than a person mistyping their own password a couple
-- of times.
CREATE TABLE IF NOT EXISTS login_attempts (
    id         SERIAL PRIMARY KEY,
    username   TEXT NOT NULL,
    ip_address TEXT NOT NULL,
    success    BOOLEAN NOT NULL,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_login_attempts_username_time ON login_attempts(username, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_time ON login_attempts(ip_address, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_login_attempts_user_time ON login_attempts(user_id, created_at DESC);
