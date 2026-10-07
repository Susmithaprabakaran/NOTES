# AI Notes

> **Publishing this online?** See [DEPLOY.md](DEPLOY.md) — production entry point
> (`backend/wsgi.py`), lean requirements, and ready-made config for Render,
> Docker, and Heroku-style hosts.

## Latest changes (search by name, profile avatar, alternate emails)

- **Search by note name** — the sidebar box now finds notes by their
  *title*: partial words ("mach" → "Machine Learning"), any capitalisation,
  any word order, and small typos. Typing shows a dropdown of matches
  (click one, or ↑/↓ + Enter, to open it); Enter with nothing highlighted
  shows every match on the Notes page, and on the Notes page the grid also
  filters live. "Search inside note text" runs the older keyword + semantic
  search over note bodies. API: `GET /api/search?q=…&mode=title` (the
  default, `mode=content`, is unchanged). Ranking lives in
  `backend/notes.py` (`title_match_score`).
  *Fixed along the way:* searching from the Dashboard used to update a
  hidden page, so nothing appeared to happen; and the Dashboard's "Total
  notes" no longer shows a leftover search's match count.
- **Profile opens from the round avatar only** — the "Profile" item is gone
  from the sidebar; the circle at the top right (now larger, and lit while
  you're on Profile) is the single way in. *Fixed:* on phones the circle
  used to be empty because a CSS rule hid the initials inside it.
- **Alternate emails** — on Profile → Edit Profile, add up to 3 backup email
  addresses, each with an optional label (Work, School, …). "Make primary"
  swaps one with your main address. Validation is server-side (valid
  address, no duplicates, can't equal the primary). API: `alternateEmails`
  on `GET/PATCH /api/profile`; stored in `users.alternate_emails`
  (added automatically on startup by `schema.sql`).
- **Startup fix** — `schema.sql` no longer fails on a PostgreSQL without
  the optional `pgvector` extension (nothing in the app needs it).
- 37 new backend tests (484 total): `tests/test_search_by_title.py`,
  `tests/test_alternate_emails.py`.

---

## Previous changes
## Latest changes (this pass)

A round of fixes and improvements made in response to feedback on the
app in its current state:

- **Branding cleanup** — removed the "Day 36/37" labels from the page
  title and topnav; the app just calls itself "AI Notes" now.
- **Flowchart fix** — the local (offline) AI provider's flowchart
  generator built decision nodes with a "No" branch that pointed back
  at the node itself. Both the backend and frontend correctly treat a
  self-referencing edge as meaningless and silently drop it, which
  meant every decision diamond quietly lost its "No" branch. It now
  loops back to a real, different node instead.
- **Smaller, atomic tasks** — task extraction (✨ AI assist → Extract
  tasks) now breaks a bundled task ("email Sarah the draft and book
  the conference room and confirm with the printer") into separate,
  individually-checkable tasks, and the underlying prompt asks the
  model to keep tasks atomic in the first place. Works with any
  provider, including the offline `local` one.
- **Suspicious activity detection** — failed login attempts are now
  logged and rate-limited (per username and per IP); repeated failures
  return a `429` instead of letting a brute-force attempt continue
  indefinitely, and a successful login right after a burst of failures
  surfaces a warning so the account owner knows to check their
  password. See `backend/security.py`.
- **Security headers** — `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, and `Permissions-Policy` are now set on every
  response, and `SESSION_COOKIE_SECURE` is configurable via
  `.env` (`SESSION_COOKIE_SECURE=1`) for HTTPS deployments.
- **Notifications arrive closer to "on time"** — the notification rule
  engine now re-runs automatically every few minutes (not just once at
  page load) and also on tab focus, and a genuinely new notification
  triggers a real OS-level desktop notification (with the browser's
  permission) so it's visible even if the tab isn't focused.
- **`check_groq.py` fix** — the Groq configuration checker script had
  a couple of broken multi-line string literals that made it fail to
  even run; fixed.
- Confirmed already working, no changes needed: notes search by name,
  the topnav avatar → Profile navigation, and "View" buttons on PDF/
  image/flowchart attachments (they were already wired up correctly).

Everything else below is the existing day-by-day feature log.

---

# Day 37 — Local AI provider (zero API key, zero network)

## What's new

A fifth `AI_PROVIDER` option — `local` — that makes every AI feature in
this app work immediately, for free, completely offline: no signup, no
API key, no internet connection, no PyTorch-sized download. Set
`AI_PROVIDER=local` in `backend/.env` and summaries, tags, entities,
concepts, task extraction, flowcharts, study packs, and the
notification digest all start working the moment you restart the
server — using real analysis of your note's own text, not a canned
response.

```
backend/
├── local_ai.py       new module: the whole local engine — sentence/
│                      word utilities, word-frequency-based extractive
│                      summarization, regex-based date/entity
│                      detection, rule-based task extraction, a small
│                      layered-sequence flowchart builder, and a
│                      cloze-quiz study-pack generator. Pure stdlib
│                      (`re` + `collections`) — no new dependency.
├── ai_client.py       AI_PROVIDER=local added alongside openai/gemini/
│                      groq/ollama; ai_status() reports it as always
│                      available (nothing to misconfigure — there's no
│                      external service); call_ai() routes to
│                      local_ai.run_local() instead of a network call
├── check_local.py     new — runs every local feature against a sample
│                      note and prints the results; no key, no network,
│                      unlike check_groq.py
├── .env.example       documents AI_PROVIDER=local
└── tests/test_local_ai.py   45 new tests (442 total backend tests) —
                        the only test file in this app that needs
                        neither TEST_DATABASE_URL nor a network call,
                        since local_ai.py is pure text analysis
```

## Why this exists

Every prior AI feature in this app (Day 11 onward) goes through one
function, `call_ai(prompt, system)`, and degrades gracefully to "AI
menu just doesn't appear" when nothing is configured. That's the right
default, but it means trying the app for the first time — or running
it somewhere without internet, or not wanting to hand out an API key —
meant seeing a mostly-empty shell of the product. `AI_PROVIDER=local`
closes that gap: it's the same "genuinely local, not a stub" pattern
as Day 9's self-hosted Whisper or Day 17's local sentence-transformers
embeddings, just applied to every text-generation feature at once
instead of one at a time.

## How the router works

Every caller in this app — `notes.py`, `tasks.py`, `study.py`,
`flowchart.py`, `graph.py`, `notifications.py`, `ai.py` — already
calls `call_ai(prompt, system=...)` with a **fixed, distinctive system
prompt** per feature (e.g. `"You extract action items from personal
notes..."`, `"You turn a personal note into a complete study pack..."`).
`local_ai.run_local()` sniffs that system prompt for a short substring
unique to each feature, recovers the underlying note text from the
prompt (every template in this app is `"<label>:\n\n{text}"`, so
splitting on the first blank line gets it back exactly), and runs the
matching local function. **Nothing in `notes.py`/`tasks.py`/`study.py`/
etc. needed to change** — they still just call `call_ai()` and get a
string back; they have no idea whether it came from a real LLM or from
local analysis.

## What the local engine actually does

None of this is a language model — it's classic, explainable text
analysis, and it's upfront about that trade-off:

- **Summarize** — extractive, not abstractive: scores each sentence by
  how many high-frequency, non-stopword terms it contains, and returns
  the top 1-3 sentences *from the note itself*, in their original
  order. Reads like a highlight reel, not fresh prose.
- **Tags / concepts** — tags are the most frequent non-stopword tokens;
  concepts favor repeated or multi-word Title-Case phrases (a cheap
  proxy for "named idea" without a real NER model).
- **Entities** — dates come from a weekday/month/relative-date word
  list plus numeric-date regexes; people/places/organizations come from
  capitalized-phrase heuristics (multi-word capitals lean "person",
  a phrase after "in/at/from/to" leans "place", a small suffix list
  like "Technologies"/"Inc"/"University" catches organizations). A
  small curated tech-word list (`python`, `scikit-learn`, `docker`, …)
  keeps common tech terms out of the people/places buckets, since
  those are what `local_concepts` is for instead.
- **Tasks** — a sentence is a task if it starts with a common
  imperative verb (`email`, `book`, `pay`, ...) or contains a task cue
  (`need to`, `remember to`, `don't forget to`, ...); a due phrase is
  pulled from weekday/relative-date wording in the same sentence and
  stripped out of the task text itself, same shape as the AI-provider
  version (`{"text": ..., "due": ...}`).
- **Flowchart** — one node per sentence (capped at 12), in order; a
  sentence mentioning "if"/"whether"/"either" becomes a `decision`
  node with a "Yes" edge forward and a "No" edge looping back to
  itself (a real branch, not a silently-linear guess); the first node
  is `start`, a terminal sentence with "finally"/"done"/"finish" is
  `end`.
- **Study pack** — summary reuses the extractive summarizer; key
  points are the top-scoring sentences; Q&A pairs a top keyword with
  the sentence that mentions it; MCQs are cloze questions (blank out a
  keyword, other frequent keywords from the same note are the
  distractors, deterministically "shuffled" by the keyword's own
  character sum so the answer isn't always option 0); definitions are
  pulled from literal `"X is a Y"` / `"X refers to Y"` sentence
  patterns; flashcards reuse definitions, falling back to key-point
  continuation cards when a note has none.
- **Notification digest / graph explain** — these two don't need any
  of the above: the caller already hands over fully-structured
  data (bullet points; a concept + its related concepts/notes, or a
  path-finder hop chain), so the local version is a plain formatting
  template, not text analysis.
- **Translation is the one feature this provider genuinely can't do.**
  There's no bundled translation model, and a fake one would be worse
  than an honest error — `AI_PROVIDER=local` + the translate endpoint
  returns the same kind of clear, tested `501` every other
  not-installed optional dependency in this app returns (Whisper,
  Tesseract, sentence-transformers), telling you to switch to openai/
  gemini/groq/ollama for that one feature.

**Known limitation, not a bug** (same spirit as Day 15's coreference
disclaimer): this is heuristic pattern-matching, not a trained model.
It won't disambiguate a name from a place as reliably as a real LLM,
MCQ distractors are simpler, and summaries are extractive rather than
freshly composed. It's meant as a genuinely-useful zero-setup default
and a fallback for offline/no-budget use, not a drop-in replacement
for GPT-4o/Gemini/Groq quality.

## Verifying this yourself

```bash
cd backend
pip install -r requirements.txt   # no new dependency for this feature
python check_local.py             # no key, no network — see it work directly
```

Then point the app at it:

```bash
# backend/.env
AI_PROVIDER=local
```

```bash
python run.py
```

Every AI-menu item, the auto-summary/tags/entities/concepts panels,
Extract tasks, the 🔀 Flowchart button, Study Mode, and the
notifications digest all work immediately — no other `.env` changes,
no restart-and-wait for a model download.

**This was tested against a real running server, not just the test
suite**: started Postgres + the Flask app with `AI_PROVIDER=local`,
registered a user, created a note naming a person/date/place/
organization plus a couple of to-dos and a short technical passage,
and hit every AI route over real HTTP — `auto-summarize`, `auto-tag`,
`extract-entities`, `extract-concepts`, `extract-tasks`,
`/flowcharts`, `/study/generate`, `/notifications/generate`, and
`/api/ai/assist` (summarize/polish/title actions) — confirming each
one returns well-formed, sensible output built from the note's actual
text, and that `/api/ai/translate` returns a clean `501` with an
actionable message instead of a crash.

Run the new unit tests (45 of them — pure Python, no database needed):

```bash
pytest tests/test_local_ai.py -v
```

Run everything (442 total: 397 from Day 36 + 45 new; this does need
`TEST_DATABASE_URL`, same as always, since most of the suite exercises
the full Flask+Postgres stack even though `test_local_ai.py` itself
doesn't):

```bash
export TEST_DATABASE_URL=postgresql://ainotes:ainotes_dev_pw@localhost:5432/ainotes_day8_test
pytest tests/ -v
```

## Manual QA checklist

- [ ] With `AI_PROVIDER` unset/blank: nothing changes from Day 36 —
      the AI-assist button and every auto-feature stay hidden
- [ ] Set `AI_PROVIDER=local`, restart the server: `GET /api/ai/status`
      reports `available: true, provider: "local"` with no API key set
      anywhere
- [ ] Write a note naming a person, a date, a place, and an
      organization ("Meeting with Sarah Chen next Tuesday at the
      downtown office for the Acme Technologies review") — summary,
      tags, and entity chips (person/date/place/org) all appear a
      couple seconds after you stop typing, same as with a real
      provider configured
- [ ] Write a note with a couple of obvious to-dos, click ✨ AI assist
      → Extract tasks — a checkbox list appears with sensible tasks
      (and a due date, if you mentioned one)
- [ ] Select a short numbered process and click 🔀 Flowchart — a
      diagram appears with a start/process/end shape and a real
      decision diamond if the text contains an "if"
- [ ] Generate a Study Mode pack from a note with a couple of
      definition-style sentences ("X is a Y") — the pack includes key
      points, Q&A, at least one MCQ, and flashcards, all traceable back
      to the note's own sentences
- [ ] `POST /api/ai/translate` with `AI_PROVIDER=local` returns a
      clean, readable `501` telling you to switch providers — not a
      crash, not silently-wrong output
- [ ] Switching `AI_PROVIDER` back to `openai`/`gemini`/`groq`/`ollama`
      and restarting immediately restores the real-model behavior —
      nothing about the local provider is sticky or cached
- [ ] Everything from Day 8-36 (login, CRUD, search, pin, voice input,
      the ✨ AI menu, auto-summary/tags/entities/concepts, attachments,
      the knowledge graph, rich text + formulas, the floating toolbar,
      AI-generated flowcharts, Study Mode, Profile & account settings,
      smart notifications) still works exactly as before

# Day 36 — Smart Notifications

## What's new

A notification bell in the topnav (next to the connection status) with
an unread badge and a dropdown feed. It surfaces three rule-based
signals — a task due soon, tasks that have sat open for a while, and a
high-priority note that's gone quiet — plus an optional one-line AI
digest on top. The rule engine is pure SQL and needs no AI model at
all; the digest is a phrasing upgrade that degrades to a plain template
when no provider is configured, never a 501.

```
backend/
├── schema.sql        new `notifications` table: type/title/message,
│                      optional note_id/task_id (ON DELETE SET NULL, so
│                      a notification outlives the note/task that
│                      triggered it), a dedupe_key with a unique index
│                      so re-running generation never double-inserts
│                      the same day's rule hit, read/ai_generated flags
├── notifications.py  new blueprint:
│                        POST /api/notifications/generate   run the
│                                                            rule engine
│                                                            (+ digest)
│                        GET  /api/notifications             list, with
│                                                            ?unread=1
│                        PATCH  /api/notifications/<id>       mark
│                                                            read/unread
│                        POST /api/notifications/read-all
│                        DELETE /api/notifications/<id>
├── app.py             registers the new blueprint
└── tests/test_notifications.py   22 new tests (397 total backend tests)

frontend/
├── index.html   bell button + dropdown panel in the topnav (Refresh /
│                Mark all read actions, a scrollable list, an empty
│                state)
├── app.js       loads/generates notifications on boot, renders the
│                feed, and wires up mark-read (click a row), dismiss
│                (✕ on hover), mark-all-read, and a manual Refresh
│                button — all optimistic-UI with a resync on failure
└── style.css    bell/badge/dropdown styling matching the existing
                 design tokens, including the light theme
```

## How it decides what to notify about, and when AI gets involved

Three SQL-only rules run on every `/generate` call (the frontend fires
one automatically each time the app boots):

- **Task due soon** — an open task whose free-text `due_text` contains
  "today", "tonight", "tomorrow", or "asap" (case-insensitive). Kept to
  keyword matching rather than date parsing, same reasoning as Day 16's
  `due_text` column: notes say things like "by next Tuesday" that don't
  reduce to a calendar date without guessing at timezone/context.
- **Tasks piling up** — one aggregated notification once there are any
  open tasks older than 7 days.
- **High-priority note going quiet** — a note marked `priority: high`
  that hasn't been updated in 5+ days.

Each candidate carries a `dedupe_key` scoped to today's date (e.g.
`task_stale:2026-09-10`), and the insert uses
`ON CONFLICT (user_id, dedupe_key) DO NOTHING` — so calling `/generate`
repeatedly only ever stores each rule hit once per day, no matter how
many times the app boots.

**The digest is optional and additive.** If the `aiUpdates` notification
preference (Day 35) is on, one more notification is added that
summarizes the same counts in a single line. When `AI_PROVIDER` is
configured (Day 11), that line is written by `call_ai()`; if no
provider is set, or the call fails/rejects, a plain f-string template
produces the exact same information instead — same notification, same
place in the feed, only the wording (and a small "AI" chip) differs.
Nothing in this feature ever fails a request because no API key is
set; the AI is always a nice-to-have on top of a fully working
rule-based feed.

## Verifying this yourself

```bash
cd backend
pip install -r requirements.txt
export TEST_DATABASE_URL=postgresql://ainotes:ainotes_dev_pw@localhost:5432/ainotes_day8_test
pytest tests/test_notifications.py -v
```

The 22 new tests cover: auth requirements, each rule firing (and *not*
firing when its condition isn't met), per-day dedupe on repeated
`/generate` calls, the `taskReminders` preference hiding task-shaped
notifications, the digest being skipped when `aiUpdates` is off,
AI-phrased vs. template-phrased digest content (with `call_ai` mocked,
same pattern as Day 11's `test_ai.py`), the digest falling back to the
template when a configured provider raises `AIUnavailable`, read/unread
listing and counts, mark-read, mark-all-read, delete, and that a
notification survives its source note being deleted.

Run everything (397 total: 375 from Day 35 + 22 new):

```bash
pytest tests/ -v
```

## Manual QA checklist

- [ ] With no `AI_PROVIDER` set, opening the app still populates the
      bell with rule-based notifications (task due soon / tasks piling
      up / note gone quiet) and a red unread badge
- [ ] A task with due text containing "today"/"tomorrow"/"tonight"/
      "asap" shows up as "Task due soon"; one with due text like "next
      quarter" doesn't
- [ ] A note marked High priority and left untouched for several days
      shows up as "High-priority note going quiet"; a Low/Medium
      priority note in the same state doesn't
- [ ] Clicking Refresh twice in a row doesn't duplicate any
      notification already shown
- [ ] Clicking a notification marks it read (the blue unread dot
      disappears, the badge count drops); "Mark all read" clears the
      whole badge at once
- [ ] Hovering a row reveals a Dismiss button that removes just that
      notification
- [ ] In Profile → notification preferences, turning "Task reminders"
      off and hitting Refresh stops task-shaped notifications from
      appearing (note-based ones still can)
- [ ] Turning "AI updates" on with no provider configured adds a
      "Today's overview" notification with plain-template wording and
      no "AI" chip
- [ ] Turning "AI updates" on *with* a working `AI_PROVIDER` /
      `*_API_KEY` configured produces the same notification with an
      "AI" chip and model-written wording instead
- [ ] The panel and badge look correct in both dark and light theme
      (Day 35's theme picker)
- [ ] Everything from Day 8-35 (login, CRUD, search, pin, voice input,
      the ✨ AI menu, auto-summary/tags/entities/concepts, attachments,
      the knowledge graph, rich text + formulas, the floating toolbar,
      AI-generated flowcharts, Study Mode, Profile & account settings)
      still works exactly as before

# Day 35 — Profile & account settings

## What's new

A dedicated **Profile** page (new sidebar tab, plus a small avatar button
in the topnav that jumps straight there) holding everything about *you*
that was previously nowhere in the app: a profile photo, contact and bio
fields, a password-change form, notification preferences, a light/dark/
system theme picker, and account-level actions including account
deletion.

```
backend/
├── schema.sql   8 new columns on `users` (full_name, email, phone, role,
│                bio, avatar_filename, theme_preference,
│                notification_prefs) — same "columns, not a side table"
│                pattern as every other one-per-user setting in this app,
│                plus a partial unique index so two accounts can't claim
│                the same email
├── profile.py   new blueprint:
│                  GET    /api/profile              fetch your profile
│                  PATCH  /api/profile               partial update (any
│                                                     subset of fields,
│                                                     including theme/
│                                                     notification prefs)
│                  POST   /api/profile/password       change password
│                  POST   /api/profile/avatar         upload a photo
│                  GET    /api/profile/avatar/<file>  serve it
│                  DELETE /api/profile/avatar         remove it
│                  DELETE /api/profile                delete the account
├── app.py       registers the new blueprint
└── tests/test_profile.py   30 new tests (375 total backend tests)

frontend/
├── index.html   new "Profile" sidebar nav + full page: avatar upload/
│                remove, a view/edit toggle for name/email/phone/role/
│                bio, a change-password form, notification toggles, a
│                theme picker, and an account-settings section with
│                logout + a delete-account confirmation modal; a small
│                circular avatar now sits in the topnav next to "Signed
│                in as…" too
├── app.js       profile load/render, the edit-form save flow, avatar
│                upload/remove, password change, notification-toggle
│                and theme-picker wiring (each one PATCHes immediately,
│                no separate "Save" step), delete-account flow, and
│                theme application (including live response to the OS
│                switching light/dark while "System" is selected)
└── style.css    profile card/avatar/toggle-switch/theme-picker styling,
                 plus a `[data-theme="light"]` CSS-variable override —
                 the whole app re-themes through the existing design
                 tokens, not a separate stylesheet
```

## Design choices worth knowing about

**Everything lives directly on `users`, not a side table.** Same
reasoning as Day 12's summary or Day 14's tags: a person has exactly one
profile, one current avatar, one theme preference, and one set of
notification toggles — a one-to-one relationship is just columns, not a
join.

**`PATCH /api/profile` is one endpoint for all of it.** Text fields,
theme preference, and notification prefs all go through the same
partial-update route — the frontend doesn't need to know these are
"different kinds" of settings, and each notification toggle or theme
choice can PATCH immediately on change without a separate page-level
"Save" button (only the name/email/phone/role/bio card has an explicit
Edit → Save flow, since those are the fields worth reviewing before
committing).

**Avatar upload reuses Day 21's validation, scoped down.** Extension
allow-list (PNG/JPG/GIF/WEBP only — no PDFs/audio here), a 5MB cap, and
a magic-byte sniff on the actual file bytes, exactly like note
attachments. The file is saved on disk under a fresh `uuid4` name in its
own `avatars/` subfolder — never the browser-supplied filename — and
re-uploading cleans up the previous file instead of leaving orphans.

**Theme preference re-themes the whole app through existing CSS
variables**, not a parallel stylesheet: `--bg`, `--surface`,
`--text`, `--border`, etc. are overridden under
`html[data-theme="light"]`, and since virtually every component in
`style.css` already reads colors through those variables (a deliberate
Day 3 decision), most of the UI re-themes for free. The saturated accent
colors (amber/indigo/rose/green) are intentionally left unchanged in
both themes — they're used as accents, not backgrounds, so they read
fine either way — and a handful of chip/badge colors hardcoded
elsewhere in the file keep their original values rather than being
individually re-themed. "System" resolves against
`prefers-color-scheme` at load time and again live if the OS setting
changes while "System" stays selected.

**Deleting your account is one `DELETE FROM users`.** Every other
table with user-owned data (notes, tasks, attachments, flowcharts,
study_materials) already has `ON DELETE CASCADE` on its `user_id`
foreign key from earlier days, so removing the user row removes
everything the account ever created — no manual cleanup pass needed.
The route still requires re-entering your password first, and the
frontend's confirmation modal spells out exactly what's being deleted
before that request ever fires.

**Session clearing on account deletion is real, not cosmetic.** This
app's sessions are signed cookies (see Day 8's `auth.py`), so deleting
the account calls `session.clear()`, which is what actually invalidates
the browser's session cookie on the response that follows — the
now-deleted user's old cookie stops working immediately, not just
whenever it happens to expire.

## Verifying this yourself

```bash
cd backend
pip install -r requirements.txt
export TEST_DATABASE_URL=postgresql://ainotes:ainotes_dev_pw@localhost:5432/ainotes_day8_test
pytest tests/test_profile.py -v
```

The 30 new tests cover: profile defaults on a brand-new account, partial
updates (each PATCH only touches the fields it was sent), email/phone
validation, case-insensitive email-uniqueness across accounts (including
that re-saving your own unchanged email doesn't false-positive), theme
preference validation, notification-prefs merging (an update to one
toggle doesn't clobber the others), password change (including that the
new password actually works on a fresh login and the old one no longer
does), avatar upload validation (extension allow-list, magic-byte sniff,
size cap, JPEG/WEBP acceptance, re-upload cleaning up the old file off
disk), avatar removal, and account deletion (wrong password rejected,
missing password rejected, and — on success — the session is really
cleared, the account is really gone from the database, and none of its
notes survive).

Run everything (375 total: 345 from Day 34 + 30 new):

```bash
pytest tests/ -v
```

**This was also tested end-to-end against a real running server**, not
just the test suite: registered an account, fetched the default profile,
PATCHed every text field plus theme and notification prefs, uploaded a
real (tiny, generated) PNG as an avatar and confirmed it round-trips
through `GET /api/profile/avatar/<file>`, changed the password and
confirmed the old one stops working while the new one logs in, and
deleted the account — confirming the session cookie is genuinely
invalidated (`/api/auth/me` returns 401 on the very next request) and
that the username is immediately available again for a fresh
registration.

## Manual QA checklist

- [ ] Opening Profile for a brand-new account shows the username, "Member
      since" date, empty name/email/phone/role/bio fields, and initials
      (derived from the username) in place of a photo
- [ ] Clicking "Edit Profile" reveals editable fields pre-filled with the
      current values; "Cancel" discards changes and returns to the
      read-only view without saving
- [ ] Saving valid changes updates the read-only view immediately and
      shows a brief "Saved" badge; an invalid email or phone shows an
      inline error instead of silently failing
- [ ] Uploading a photo (PNG/JPG/GIF/WEBP) replaces the initials with the
      image everywhere it appears (Profile page, topnav); uploading a
      disallowed file type or one over 5MB shows a clear error
- [ ] Uploading a second photo replaces the first one, not just visually
      — re-uploading the very first file afterward still works, proving
      the old one was actually deleted off disk
- [ ] "Remove photo" asks for confirmation, then reverts to initials
- [ ] Changing your password with the correct current password works;
      logging out and back in requires the new password, not the old one
- [ ] An incorrect current password, a too-short new password, or a
      mismatched confirmation are each rejected with a clear message
- [ ] Toggling a notification preference saves immediately (no separate
      Save button) and survives a page reload
- [ ] Picking Light, Dark, or System instantly re-themes the whole app
      and persists across a reload; System follows the OS's light/dark
      setting, including switching live if the OS setting changes while
      the page is open
- [ ] "Delete account" opens a confirmation modal explaining exactly
      what's deleted; a wrong password is rejected; the correct password
      deletes the account and returns you to the login screen, and the
      same username can be re-registered fresh afterward
- [ ] Everything from Day 8-34 (login, CRUD, search, pin, voice input,
      the ✨ AI menu, auto-summary/tags/entities/concepts, attachments,
      the knowledge graph, rich text + formulas, the floating toolbar,
      AI-generated flowcharts, Study Mode, the Tasks search/grouping)
      still works exactly as before

# Day 34 — Taming the Tasks view at scale (search + grouping)

## The problem

The screen recording that kicked this off shows exactly what's wrong:
open **Tasks** with a term's worth of course notes behind it (163 action
items in that recording) and the whole thing is one flat, ungrouped,
unsearchable scroll — every row looks the same, there's no way to jump
to "the tasks from that one note," and finding anything means scrolling
past a hundred rows you don't care about.

## What's new

```
backend/
└── tasks.py    GET /api/tasks gains an optional ?q= search param —
                ILIKE match against both the task text and its note's
                title, combined with the existing ?done= filter.
                Backward compatible: still returns a plain array, so
                every existing caller (including the sidebar badge)
                keeps working unmodified.
tests/test_tasks.py   7 new tests (345 total backend tests)

frontend/
├── index.html   Tasks view gets a search box, an Open/All/Done filter
│                (unchanged) alongside a new "By note / Flat list"
│                toggle, group expand-all/collapse-all controls, a
│                "Load more" row for flat mode, and a distinct "no
│                matching tasks" empty state
├── app.js       debounced search wired to the new ?q= param,
│                collapsible per-note grouping (new default view),
│                client-side batching in flat mode, and all the state
│                needed to remember which groups are collapsed/expanded
│                as you interact with the page
└── style.css    search box, view-toggle pills, collapsible group
                 cards with a rotating chevron, "show N more"/"load
                 more" affordances — matching the app's existing
                 palette, no new dependencies
```

## Two ways to make 163 rows scannable

**Group by note (new default).** Tasks are bucketed by the note they
came from into collapsible cards — "Standup Mon (2 open)", "Grocery run
(0 open · 3 done)" — sorted by whichever note you touched most
recently. A course's worth of notes turns into roughly as many
sections as you have notes, not one continuous list of everything at
once. Each group only renders its first 6 tasks by default, with a
"Show N more" link for the rare group that has more than that — so a
note with 40 tasks doesn't itself become the new wall of text.

**Flat list, batched.** If you'd rather see everything ordered
newest-first regardless of which note it came from (the old Day 16
behavior), the "Flat list" toggle gives you that back — but it now
renders 60 rows at a time with a "Load more" button instead of putting
every row in the DOM up front, which matters once the list is in the
hundreds.

**Search.** A search box above both modes narrows the list to tasks
whose text *or* note title match what you typed (`?q=` on the backend,
debounced 250ms on the frontend so it doesn't fire on every keystroke).
It composes with the Open/All/Done filter and whichever view mode
you're in — searching in grouped mode still groups just the matching
tasks; searching in flat mode still batches them.

## Why grouping and pagination are separate concerns

Grouping solves "I can't tell what belongs to what" — pure
information architecture. Batching solves "the browser is rendering
hundreds of DOM nodes it doesn't need to" — pure performance. Grouped
mode gets *both* for free (each group's own 6-row cap is a form of
batching), which is why it's the new default; flat mode keeps its own
independent batching so it's never worse than before, just no longer
unbounded.

## Verifying this yourself

```bash
cd backend
pip install -r requirements.txt
export TEST_DATABASE_URL=postgresql://ainotes:ainotes_dev_pw@localhost:5432/ainotes_day8_test
pytest tests/test_tasks.py -v
```

The 7 new tests cover: search matching task text, search matching a
note's title, case-insensitivity and whitespace trimming, combining
`q` with `done`, a query with no matches returning an empty array (not
an error), a blank/whitespace-only query returning everything
unfiltered, and that search results stay scoped per-user the same way
every other tasks endpoint already was.

Run everything (345 total: 338 from Day 33 + 7 new):

```bash
pytest tests/ -v
```

The frontend logic isn't covered by the Python suite (it's 100%
client-side, same as Day 31's floating toolbar) — verify manually with
the checklist below.

## Manual QA checklist

- [ ] Opening Tasks with a mix of notes shows collapsible sections by
      default, one per note, each labeled with its title and an
      open/done count
- [ ] Clicking a group's header collapses/expands it; the chevron
      rotates to match
- [ ] "Expand all" / "Collapse all" do exactly that across every group
- [ ] A note with more than 6 tasks shows a "Show N more" link inside
      its group instead of dumping every row at once
- [ ] Clicking a group's title (not the collapse chevron) opens that
      note in the editor, same as clicking a task's note chip used to
- [ ] Switching to "Flat list" shows a single newest-first list capped
      at 60 rows, with a "Load 60 more" button that reveals the rest
      in batches
- [ ] Typing in the search box narrows the list (in either view mode)
      to tasks whose text or note title match, without a full page
      reload
- [ ] Searching for something with no matches shows a distinct "No
      matching tasks" message (not the same empty state as "no tasks
      at all")
- [ ] Clearing the search (✕ button, or the inline "clear the search"
      link in the no-results state) restores the full list
- [ ] Search still composes correctly with the Open/All/Done filter —
      e.g. searching "buy" with "Open" selected only shows open buy-*
      tasks
- [ ] The sidebar's open-task badge count is unaffected by any of
      this — it always reflects every open task, not just what's
      currently searched/filtered/visible
- [ ] Everything from Day 8-33 (login, CRUD, search, pin, voice input,
      the ✨ AI menu, auto-summary/tags/entities/concepts, attachments,
      the knowledge graph, rich text + formulas, the floating toolbar,
      AI-generated flowcharts, Study Mode) still works exactly as
      before

# Day 33 — "Study Mode"

## What's new

A dedicated **Study** page (new sidebar tab, plus a "Study" shortcut
button on the editor toolbar) that turns any note into a complete
study pack with one click: a summary, key points, a Q&A list, a
graded multiple-choice quiz, flip-to-reveal flashcards, and a
glossary of definitions — one AI call generates all six at once.

```
backend/
├── schema.sql   new `study_materials` table: ONE row per note
│                (note_id UNIQUE), same reasoning as the summary/
│                tags/entities columns on `notes` itself — a note
│                has a single current study pack, and regenerating
│                replaces it (ON CONFLICT DO UPDATE) rather than
│                piling up old copies
├── study.py     GET    /api/notes/<id>/study             (fetch, or {"generated":false})
│                POST   /api/notes/<id>/study/generate     (generate/regenerate)
│                DELETE /api/notes/<id>/study
└── tests/test_study.py   22 new tests (338 total backend tests)

frontend/
├── index.html   new "Study" sidebar nav + full page: note picker,
│                generate/regenerate/delete controls, a stale-pack
│                badge, and six tabs (Summary / Key points / Q&A /
│                MCQs / Flashcards / Definitions); a "Study" button
│                on the editor toolbar jumps straight here for the
│                open note
├── app.js       note-picker wiring, generate/fetch/delete calls,
│                tab switching, a self-contained graded MCQ quiz
│                (click an option, see correct/incorrect + the AI's
│                explanation, running score, reset), and flip
│                animation for flashcards — no external quiz library
└── style.css    picker/tab/quiz/flashcard styling matching the
                 app's existing palette (amber for human content,
                 indigo accent, green/rose for quiz feedback)
```

## How it works

One prompt asks the AI for a single JSON object shaped like
`{"summary","keyPoints","qna","mcqs","flashcards","definitions"}`
instead of six separate calls — same latency and cost as *one* Day 11
AI-assist request, not six. The reply is parsed the same tolerant way
Day 15/16/32 parse structured AI replies (scan for the outermost
`{...}` since models sometimes wrap JSON in prose despite
instructions not to), and every field is independently validated and
capped: key points/Q&A/flashcards/definitions are each limited to a
sane maximum count and length, MCQs are dropped if they don't have
between 2 and 6 real options or a valid `answerIndex` pointing at one
of them, and duplicate definition terms are deduped. If every section
comes back empty (a note with nothing to work with), generation fails
with a clear "couldn't build a study pack from this note" message
instead of saving an empty pack.

**Regenerating overwrites, it doesn't duplicate** — `study_materials`
has a `UNIQUE` constraint on `note_id`, and the insert is an upsert
(`ON CONFLICT (note_id) DO UPDATE`), so there's always exactly one
current study pack per note, matching how summary/tags/entities work
elsewhere in the app rather than the one-to-many shape of attachments
or Day 32's flowcharts.

**Staleness is tracked the same way as Day 12's summary**: a
`source_hash` (sha256 of the note's text at generation time) is
compared against the note's current text on every fetch. Edit the
note after generating a pack and the page shows an "Out of date"
badge — the pack doesn't disappear or silently look current, it just
tells you it was built from an earlier version.

**The in-page MCQ quiz and flashcards are pure frontend state** — no
extra network round-trip per click. Picking an option locks that
question, colors it correct/incorrect, and shows the AI's explanation
plus a running score; "Reset quiz" just clears the local answer map
and re-renders. Flashcards flip with a CSS 3D transform on click, no
JS animation library.

## Verifying this yourself

- **New backend tests (22)**: auth required on all three routes, 404
  on a missing/someone-else's note, validation (empty/too-short note
  text), 501 when no AI provider is configured, 422 on an empty or
  unparseable AI reply, tolerance for a reply wrapped in prose/
  markdown fences, malformed MCQs dropped (bad `answerIndex`, too few
  options), list-length capping, duplicate-definition dedup,
  regenerate-overwrites-not-duplicates, the stale flag flipping after
  an edit, delete, and cross-user isolation.
- Backend tests need a real Postgres instance (`TEST_DATABASE_URL`,
  same as every other day) to run.

## Manual QA checklist

- [ ] Opening Study Mode with no note selected shows the "pick a
      note" empty state; selecting one with no pack yet shows
      "Generate study pack"
- [ ] Generating builds all six sections from a real note's content —
      the MCQs' wrong answers are plausible, not obviously silly
- [ ] Clicking through the MCQ quiz locks each question after
      answering, colors correct green / your wrong pick red, shows
      the explanation, and updates the running score; "Reset quiz"
      clears it and lets you retake it
- [ ] Clicking a flashcard flips it to show the back; clicking again
      flips it back
- [ ] Editing the note's text after generating a pack shows the "Out
      of date" badge on returning to Study Mode; regenerating clears it
- [ ] Regenerating a pack replaces the old one — no duplicate packs
      pile up for the same note
- [ ] Deleting a pack returns the page to "nothing generated yet"
- [ ] A note with almost no text shows a clear "needs more detail"
      error instead of a broken/empty pack
- [ ] Without an AI provider configured: the picker still works, but
      the same "no AI model connected" messaging as every other AI
      feature appears instead of a generate button
- [ ] The "Study" button on the editor toolbar jumps to Study Mode
      with that note pre-selected
- [ ] Everything from Day 8-32 (login, CRUD, search, pin, voice input,
      the ✨ AI menu, auto-summary/tags/entities/concepts, attachments,
      the knowledge graph, rich text + formulas, the floating toolbar,
      AI-generated flowcharts) still works exactly as before



New this drop: notes scan themselves for **who, when, and where**.
Same "no button, runs quietly in the background" pattern as Day
12/14 — a couple seconds after you stop editing, any people, dates,
and places mentioned in the note appear as three grouped chip rows
under the editor, and a quick 👤 preview shows up on note cards too.

## What's new

```
backend/
├── schema.sql   3 new columns on notes: entities (JSON object:
│                {"people":[],"dates":[],"places":[]}), entities_source_hash,
│                entities_generated_at — same migration pattern as every
│                prior day, safe on an existing database
├── notes.py     POST /api/notes/<id>/extract-entities
│                entities/entitiesGeneratedAt/entitiesStale added to every note
└── tests/test_notes_entities.py   14 new tests (92 total backend tests)

frontend/
├── index.html   new grouped entity panel under the editor (👤 People,
│                📅 Dates, 📍 Places — each group only shows if non-empty)
├── app.js       extraction trigger (shares the Day 12 debounce timer
│                with summary/tags), chip rendering, card preview
└── style.css    entity chip styling (color-coded per category)
```

## How it decides what counts, and when

The AI is asked for a single JSON object with three arrays —
`people`, `dates`, `places` — pulled directly from the note's own
wording (`"next Friday"` stays `"next Friday"`, it's not resolved to
an actual calendar date). Unlike tags, entity casing is preserved
("Sarah Chen", not "sarah chen") since these are proper nouns; dedup
is still case-insensitive so "Sarah" and "sarah" don't both show up.
Each category is capped at 8 entries so a long note can't produce an
overwhelming wall of chips.

**Scope limit worth knowing about, not a bug:** this doesn't do
coreference resolution. "Sarah Chen" and a later "Sarah" in the same
note are surfaced as two separate people, not merged into one — same
for a date mentioned two different ways. Good enough for skimming a
note at a glance; not a substitute for a real NER/coref pipeline.

Same cost-control pattern as Day 12/13/14: notes under ~10 words skip
the AI call entirely, and unchanged text never re-triggers a call
thanks to a stored content hash. Entity extraction now fires alongside
summarization and tagging off the same shared debounce timer.

## Verifying this yourself

- **92/92 tests pass**, including 14 new ones: the short-note skip,
  the unchanged-text cache skip, the 8-per-category cap, tolerance for
  AI replies wrapped in prose instead of bare JSON, case-preserving
  but case-insensitive dedup, an all-empty reply correctly *not* being
  flagged as "stale" (empty is a valid result, not a missing one),
  staleness after an edit, and cross-user isolation.
- **Tested the schema migration against a real pre-Day-15 database**
  (not a fresh one) — confirmed `entities`/`entities_source_hash`/
  `entities_generated_at` get added cleanly alongside the existing
  `tags` and `tasks` data from Days 13-14, entities correctly
  defaulting to empty arrays on existing rows.
- Ran a full live pass with a mocked AI response against the actual
  running server: created a note, extracted entities, and confirmed
  the exact JSON shape (`people`/`dates`/`places` each populated
  correctly) came back through the real HTTP response.
- Confirmed the served frontend actually contains the new elements
  and functions (`autoEntities`, `entitiesPeopleList`,
  `runAutoEntities`, `renderAutoEntities`, `ENTITY_GROUPS`) by
  fetching `index.html` and `app.js` straight from the running
  server, not just reading the source locally.

Run all 92 tests yourself:
```bash
cd backend
pytest tests/ -v
```

## Manual QA checklist

- [ ] With an AI provider configured (from Day 11): write a note
      naming a person, a date, and a place ("Meeting with Alex next
      Tuesday at the downtown office") — three labeled chip rows
      appear under the editor a couple seconds after you stop typing
- [ ] Go back to the notes list — that note's card shows a small 👤
      preview if any people were found
- [ ] A note with no people/dates/places mentioned shows no entity
      panel at all (empty groups don't render)
- [ ] Edit a tagged note's text — entity chips stay visible but you
      can confirm via the API (`entitiesStale: true`) that they're
      due for a refresh, same staleness behavior as summary/tags
- [ ] Without an AI provider configured: no entity panel or card
      preview appears anywhere — nothing else in the app is affected
- [ ] Everything from Day 8-14 (login, CRUD, search, pin, voice input,
      manual ✨ AI menu, auto-summary, task extraction, auto-tagging)
      still works exactly as before


New this drop: notes categorize **themselves**. Same "no button, runs
quietly in the background" pattern as Day 12's summarization — a
couple seconds after you stop editing a note, a handful of short tags
(`finance`, `travel`, `recipe`, etc.) appear under the editor, on the
note's card in the list, and roll up into a browsable tag list in the
sidebar you can click to filter your notes by category.

## What's new

```
backend/
├── schema.sql   3 new columns on notes: tags (JSON array), tags_source_hash,
│                tags_generated_at — same ALTER TABLE ... IF NOT EXISTS
│                pattern as every prior migration, safe on an existing DB
├── notes.py     POST /api/notes/<id>/auto-tag        (generate/refresh tags)
│                GET  /api/tags                        (aggregated tag list + counts)
│                GET  /api/notes?tag=x, /api/search?tag=x   (filter by tag)
│                tags/tagsGeneratedAt/tagsStale added to every note
└── tests/test_notes_tags.py   18 new tests (79 total backend tests)

frontend/
├── index.html   new tag chips panel under the editor, new "Tags"
│                section in the sidebar
├── app.js       auto-tag trigger (shares Day 12's debounce timer),
│                tag chip rendering (editor panel + note cards +
│                sidebar), click-to-filter wiring
└── style.css    tag chip + sidebar tag list styling
```

## How it decides what to tag, and when

The AI is asked for 1-5 short, reusable category words — general
enough to browse by later (`work`, `health`), not a restatement of
what the note says. Tags are normalized on the way in: lowercased,
stray punctuation stripped (`"#Work."` → `"work"`), deduped, and capped
at 5 per note so tagging can't spiral into a wall of chips.

Same cost-control pattern as Day 12/13: notes under ~12 words skip the
AI call entirely (and clear any leftover tags if edited down that
short), and unchanged text never re-triggers a call thanks to a stored
content hash. Auto-tagging and auto-summarizing now fire together off
the same debounce timer, since they're both "happens automatically,
no button" passes over the same freshly-saved text.

**Tag list is honest about staleness.** If you edit a note after it's
been tagged, those tags are marked stale (both in the editor panel and
excluded from the sidebar's aggregated counts) until they're
regenerated — so the sidebar never claims a note belongs to a category
based on text that's since changed.

## Verifying this yourself

- **79/79 tests pass**, including 18 new ones: normalization
  (case/punctuation/dedup), the 5-tag cap, tolerance for AI replies
  wrapped in prose instead of bare JSON, the short-note skip, the
  unchanged-text cache skip, tag aggregation across multiple notes,
  stale tags correctly excluded from `/api/tags`, filtering `/api/notes`
  by tag, and cross-user isolation (one user's tags never leak into
  another's `/api/tags` response).
- **Tested the schema migration against a real pre-Day-14 database**
  (not a fresh one) — confirmed `tags`/`tags_source_hash`/
  `tags_generated_at` get added cleanly alongside the existing `tasks`
  table from Day 13, with no data loss.
- Ran a full live pass with a mocked AI response against the actual
  running server: created a note, generated tags, hit `GET /api/tags`
  and confirmed the aggregated counts, then hit
  `GET /api/notes?tag=finance` and confirmed it returned only the
  matching note.
- Confirmed the served frontend actually contains the new elements and
  functions (`autoTags`, `sidebarTags`, `runAutoTag`,
  `renderSidebarTags`, `setTagFilter`) by fetching `index.html` and
  `app.js` straight from the running server, not just reading the
  source locally.

Run all 79 tests yourself:
```bash
cd backend
pytest tests/ -v
```

## Manual QA checklist

- [ ] With an AI provider configured (from Day 11): write a note with
      clear subject matter (e.g. a budget meeting recap) — a row of
      tag chips appears under the editor a couple seconds after you
      stop typing, no click required
- [ ] Go back to the notes list — that note's card shows the same tags
      as small chips
- [ ] Look at the sidebar — a new "Tags" section lists every tag
      you've generated so far, each with a count
- [ ] Click a tag chip (in the sidebar, on a card, or in the editor
      panel) — the notes list filters down to just notes with that tag,
      and the heading changes to `#tagname`
- [ ] Click "Clear tag filter" — the full notes list returns
- [ ] Edit a tagged note's text — the tag panel label switches to
      "(out of date)" until it regenerates, and that note's tags are
      excluded from the sidebar counts until then
- [ ] Without an AI provider configured: no tag panel, card chips, or
      sidebar Tags section appear anywhere — nothing else is affected
- [ ] Everything from Day 8-13 (login, CRUD, search, pin, voice input,
      manual ✨ AI menu, auto-summary, task extraction) still works
      exactly as before


New this drop: the app pulls **to-dos out of your normal notes** instead
of making you keep a separate task list. Write a note the way you
normally would (standup notes, a meeting recap, a brain dump), click
✨ AI assist → **Extract tasks**, and every actionable line — "email
Sarah the draft", "book the conference room" — gets pulled out into a
checkbox list, both under that note and in a new cross-note **Tasks**
view in the sidebar.

This is scoped as a **work-notes feature**: the extraction prompt is
tuned for the kind of note that actually generates to-dos (standups,
meeting notes, project updates) rather than diary-style journaling, and
the new Tasks view is the kind of daily "what do I still owe people"
list a work day starts with.

## What's new

```
backend/
├── schema.sql          new `tasks` table: id, user_id, note_id, text,
│                        done, created_at, updated_at — FKs cascade on
│                        delete, so deleting a note cleans up its tasks
├── tasks.py             new blueprint:
│                          POST   /api/notes/<id>/extract-tasks  (AI)
│                          GET    /api/notes/<id>/tasks
│                          POST   /api/tasks        (add one by hand)
│                          GET    /api/tasks[?done=0|1]  (all, across notes)
│                          PATCH  /api/tasks/<id>   (toggle done / edit text)
│                          DELETE /api/tasks/<id>
├── app.py                registers the new blueprint
└── tests/test_tasks.py   14 new tests

frontend/
├── index.html   sidebar Notes/Tasks nav + open-task badge, new Tasks
│                view, "Extract tasks" AI-menu item, per-note task panel
│                under the editor
├── app.js       extraction call, task panel rendering, full Tasks view,
│                checkbox toggle / delete / manual-add, badge count
└── style.css    nav, task row, and task panel styling
```

## How extraction works

`POST /api/notes/<id>/extract-tasks` sends the note's saved text to
whichever AI provider is configured (same `AI_PROVIDER` setup as Day
11/12 — nothing new to configure) with a prompt that asks specifically
for **things the author still needs to do**, not facts or ideas, and
asks for the reply as a bare JSON array of short imperative strings
(`["Email Sarah the budget draft", ...]`).

A few things make this robust rather than a thin wrapper around one
API call:

- **Tolerant parsing.** Models don't always follow "reply with ONLY
  json" perfectly — sometimes you get a ```` ```json ```` fence, sometimes
  a stray "Sure, here you go:" sentence in front. `_parse_task_list`
  finds the outermost `[...]`, parses just that, and falls back to an
  empty list (not a 500) if the response genuinely isn't parseable.
- **Dedup on repeat runs.** If you click "Extract tasks" twice on a
  note you haven't changed, the second run compares against tasks
  already stored for that note (case-insensitive) and adds nothing new
  — verified in `test_extract_tasks_dedupes_on_repeat_run`.
- **Empty is a valid answer.** A note with no action items returns
  `found: 0` and an empty task list, not an error — most notes won't
  have to-dos in them, and that's fine.
- **Manual tasks work independently of AI.** `POST /api/tasks` lets you
  add a task by hand (for the one the model missed), and toggling/
  deleting tasks never touches the AI at all — so the whole feature
  degrades gracefully to a plain checklist if no provider is configured.

## Verifying this yourself

- **14 new backend tests** covering: auth/404 guards, the empty-note
  400, the 501-without-provider path, a mocked successful extraction,
  markdown-fenced JSON tolerance, an empty-array response, dedup on a
  second run, manual add/toggle/delete, the cross-note "all my tasks"
  view with `done` filtering, and that tasks are scoped per-user (one
  person's tasks never leak into another's `/api/tasks` response).
- Unit-tested `_parse_task_list` directly against six inputs (clean
  JSON, fenced JSON, empty array, a stray sentence wrapped around the
  array, non-JSON garbage, and bullet-prefixed strings) to confirm the
  parsing tolerance actually holds before wiring it into the route.

Run the tests yourself:
```bash
cd backend
pytest tests/ -v
```

## Manual QA checklist

- [ ] Write a note with a few obvious to-dos mixed into normal prose,
      click ✨ AI assist → **Extract tasks** — a checkbox list appears
      under the editor a moment later with just the actionable lines
- [ ] Click a checkbox — it strikes through and the open-task badge in
      the sidebar decrements
- [ ] Click "Tasks" in the sidebar — every extracted/added task across
      every note shows up, each tagged with its note title; clicking
      the note title jumps back into that note
- [ ] Use the Open / All / Done filter in the Tasks view — each shows
      the right subset
- [ ] Click "+ Add" in a note's task panel and type a task by hand — it
      appears immediately, no AI call involved
- [ ] Click "Extract tasks" again on the same, unedited note — no
      duplicate tasks appear
- [ ] Delete a note that has tasks — its tasks disappear from the Tasks
      view too (cascade delete)
- [ ] Without an AI provider configured: "Extract tasks" shows a clear
      error in the status line instead of failing silently; manual add/
      toggle/delete still all work
- [ ] Everything from Day 8-12 (login, CRUD, search, pin, voice input,
      manual ✨ AI menu, auto-summary) still works exactly as before

---

# Day 12 — Automatic note summarization

New this drop: notes summarize **themselves**. No button — a couple
seconds after you stop typing a note of reasonable length, a summary
quietly appears underneath the editor, and shows up as a preview on
the note's card in the list view too.

This is different from the Day 11 ✨ menu's "Summarize" action, which
is manual (you click it, it replaces/appends text once). This one
runs automatically in the background every time a note changes.

## What's new

```
backend/
├── schema.sql   3 new columns on notes: summary, summary_source_hash,
│                summary_generated_at — added via ALTER TABLE ... ADD
│                COLUMN IF NOT EXISTS, so it's safe to run against an
│                existing Day 8-11 database (I tested this — see below)
├── notes.py     new POST /api/notes/<id>/auto-summarize route +
│                summary/summaryGeneratedAt/summaryStale on every note
└── tests/test_notes_summary.py   8 new tests (53 total backend tests)

frontend/
├── index.html   new "auto-summary" panel under the editor
├── app.js       debounced auto-summarize trigger (2.5s after autosave),
│                summary preview on note cards
└── style.css    summary panel + card preview styling
```

## How it decides when to actually call the AI

Calling an AI API on every keystroke would be slow and wasteful, so:

- **Notes under ~25 words are skipped entirely** — a 3-word note doesn't
  need an AI-written summary of itself. If a note is edited down below
  that threshold, any old summary is cleared rather than left stale.
- **Unchanged text never re-triggers a call.** Each note stores a hash
  of the text its current summary was generated from
  (`summary_source_hash`); if you reopen a note and nothing changed,
  hitting the auto-summarize endpoint again is a no-op — verified in
  `test_auto_summarize_skips_ai_call_when_text_unchanged`.
- **The frontend debounces separately from autosave** — text autosaves
  600ms after you stop typing (fast, for data safety), but the
  auto-summary trigger waits 2.5s (so a few quick pauses while typing
  don't each fire off an AI call).

This reuses the exact same `AI_PROVIDER`/`OPENAI_API_KEY`/`GEMINI_API_KEY`
setup from Day 11 — if you already have a key configured, this works
immediately, no extra setup. If you don't, the summary panel simply
never appears; everything else is unaffected.

## Verifying this yourself

- **53/53 tests pass**, including 8 new ones covering: short-note
  skipping, the "unchanged text → no AI call" cache behavior, the
  501-without-provider path, a note going stale after being edited,
  and a note shrinking below the threshold clearing its old summary.
- I specifically tested the **migration path**, not just a fresh
  database: I ran `init_db()` against a real Day 8-vintage database
  that had never seen the new columns, and confirmed `summary`,
  `summary_source_hash`, and `summary_generated_at` got added cleanly
  with no data loss — this matters because your existing database
  from earlier days needs to upgrade in place, not get recreated.
- Booted the real server and hit `/api/notes/<id>/auto-summarize`
  directly: confirmed a short note returns `skipped: "too_short"`,
  and a long note with no AI provider configured returns a clean
  `501` with the note data intact (not a crash).
- Fixed a real bug I caught during that manual testing: a brand-new,
  never-summarized note was incorrectly reporting `summaryStale: true`
  (there's nothing to be "stale" if a summary never existed). Caught
  it by actually inspecting the live JSON response, not just by
  reading the code — fixed and reverified before packaging this.

Run all 53 tests yourself:
```bash
cd backend
pytest tests/ -v
```

## Manual QA checklist

- [ ] With an AI provider configured (from Day 11): open a note, type
      a few sentences (25+ words), stop typing — a summary panel
      fades in underneath the editor a couple seconds later
      automatically, no click required
- [ ] Go back to the notes list — that note's card now shows the
      summary as a preview line, marked with a ✨
      # (Note: this preview persists after a page reload too, since
      # it's now stored with the note server-side)
- [ ] Edit the note's text — the summary panel label switches to
      "Summary (out of date)" until it's regenerated a couple seconds
      after you stop typing
- [ ] Shrink a note down to a couple words — the summary panel
      disappears (too short to bother)
- [ ] Without an AI provider configured: the summary panel and card
      previews just don't appear — nothing else in the app is affected
- [ ] Everything from Day 8-11 (login, CRUD, search, pin, voice input,
      manual ✨ AI menu) still works exactly as before

Two independent voice-input paths were added, either usable on its own:

1. **Live dictation** (mic icon) — the browser's built-in Web Speech API.
   Zero setup, zero cost, nothing leaves your machine. Works in Chrome
   and Edge today.
2. **Record & transcribe** (red-dot icon) — records your voice with
   `MediaRecorder` and sends it to a new Flask endpoint that runs it
   through **Whisper**, OpenAI's open-source speech model, hosted
   entirely on your own machine via `faster-whisper`. No API key, no
   per-request cost, no third-party account — because it never leaves
   your server.

## A note on "Whisper/Google Speech API"

Both of those, used the way most tutorials mean it (the *hosted*
OpenAI Whisper API, or Google Cloud Speech-to-Text), require **your
own account and a real API key from that provider** — I can't generate
a working key for a third-party paid service the way I could for our
own backend in Day 6. So this drop uses the actual open-source Whisper
model, self-hosted, which is genuinely the "Whisper" in that heading
without needing anyone's credit card. If you specifically want the
hosted OpenAI API or Google Cloud instead (e.g. for higher accuracy or
because you don't want the local model download), tell me and I'll wire
that in — it's a small change to `transcription.py`, you'd just need to
supply your own key in `.env`.

## What's new

```
backend/
├── voice.py                new blueprint: /api/voice/status, /api/voice/transcribe
├── transcription.py        lazy-loaded Whisper wrapper, degrades gracefully if not installed
├── requirements-voice.txt  optional — only needed for server-side transcription
└── tests/test_voice.py     8 new tests (36 total across the whole backend)

frontend/
├── index.html               two new toolbar buttons in the editor
├── app.js                    dictation + recording logic
└── style.css                 mic button + recording-pulse styles
```

## Setting it up

**Live dictation works with zero extra setup** — same `pip install -r
requirements.txt` and `python run.py` as Day 8, nothing new to install.
Open the app, click into a note, click the mic icon, allow microphone
access, and talk. Click it again to stop.

**Server-side Whisper is optional** (it's a genuinely heavy dependency —
PyTorch under the hood — so it's kept out of the core install):

```bash
cd backend
pip install -r requirements-voice.txt
```

The first time you use it, it downloads the "base" Whisper model
(~150MB) from Hugging Face — needs an internet connection once, then
it's cached locally and works offline after that. Restart `python
run.py` after installing so the new import is picked up.

The frontend checks `/api/voice/status` on load and only shows the
"Record & transcribe" button if the server reports Whisper is actually
installed — you won't see a button that doesn't work.

## How it behaves without the optional install

If you skip `requirements-voice.txt` entirely: live dictation still
works fully, and the server-side record button simply never appears
(status endpoint reports `available: false`). If you install it later,
it appears automatically on your next page load — no code changes
needed.

If you call `/api/voice/transcribe` directly without the dependency
installed, you get a real, tested `501` with a message telling you
exactly what to run — not a crash, not a raw stack trace.

## Testing this without a microphone

`backend/tests/test_voice.py` covers the full request lifecycle without
needing real audio or the actual model:

- Auth is required (401 without login)
- Missing/empty/unsupported-format audio is rejected with clear 400s
- **The graceful-degradation path is tested for real** — in the
  reference environment `faster-whisper` genuinely isn't installed, so
  this test exercises the actual code path, not a simulation
- The success path is tested by mocking `transcribe_audio_file` to
  confirm the route's upload handling, temp-file lifecycle, and
  response shape are correct independent of whether Whisper itself
  is present

Run all 36 tests (28 from Day 8 + 8 new):

```bash
cd backend
pytest tests/ -v
```

## Manual QA checklist

- [ ] Click the mic icon in a note editor → browser asks for mic
      permission → speak → words appear in the textarea as you talk,
      autosave kicks in on each finalized phrase
- [ ] Click the mic icon again → recording stops, status line clears
- [ ] (After installing `requirements-voice.txt`) the red-dot "record &
      transcribe" button appears → click it → speak → click again →
      "Transcribing…" appears briefly → recognized text is appended
- [ ] Without `requirements-voice.txt` installed, the red-dot button
      never appears at all — no dead button, no error on page load
- [ ] Using either mic while the OTHER one is active stops the first
      one automatically (they don't run simultaneously)
- [ ] Everything from Day 8 (login, CRUD, search, pin) still works —
      voice input is additive, nothing else changed

# Day 17 — Semantic search using embeddings

New this drop: search understands *meaning*, not just matching letters.
"What did we decide about pricing" can now find a note that says "we
landed on tiered pricing" even though almost no words overlap — while
still catching exact keyword/name matches the way search always did.
No paid embedding API: everything runs locally with
`sentence-transformers` (`all-MiniLM-L6-v2`, ~90MB, downloads once).

## What's new

```
backend/
├── embeddings.py          new module: lazy-loads the embedding model,
│                          embed_text/embed_texts, cosine_similarity,
│                          graceful "unavailable" fallback if the
│                          library isn't installed or can't download
├── schema.sql             3 new columns on notes: embedding (JSON-
│                          encoded vector, plain TEXT — works with or
│                          without the Postgres pgvector extension),
│                          embedding_source_hash, embedding_generated_at
├── notes.py                GET  /api/search           now hybrid-ranked
│                            GET  /api/search/compare   keyword vs. hybrid,
│                                                        side by side
│                            POST /api/notes/<id>/auto-embed
├── requirements.txt        + scikit-learn, numpy, sentence-transformers
├── backfill_embeddings.py  optional one-off script to embed every
│                           existing note up front
└── tests/test_search.py    22 new tests (114 total backend tests) —
                             uses a small fake embedding model so tests
                             don't need to download the real one

frontend/
├── app.js       search results now keep the backend's relevance order
│                instead of being re-sorted by pinned/date; match
│                badges (💡 Related / 🔤 Keyword / 🎯 Strong) on cards;
│                auto-embed wired into the existing debounce timer
└── style.css    match badge styling, color-coded per match type
```

## How hybrid ranking works

Every search combines two independent signals:

- **Semantic score** — cosine similarity between the query's embedding
  and each note's embedding. Catches paraphrases, synonyms, and
  conceptually-related notes that share few or no literal words.
- **Keyword score** — classic TF-IDF cosine similarity over your notes.
  Still wins for exact names, numbers, and specific jargon that an
  embedding model tends to blur together.

They're combined as `0.6 × semantic + 0.4 × keyword` (see
`SEMANTIC_WEIGHT`/`KEYWORD_WEIGHT` in `notes.py`). Each result is
labeled with *why* it matched:

- 🎯 **Strong match** — both signals agree
- 💡 **Related match** — found by meaning, not literal words
- 🔤 **Keyword match** — found by literal words, not concept

## What happens without the embedding model installed

`sentence-transformers` is a real dependency (PyTorch under the hood),
so — like Day 13's Whisper integration — it's designed to degrade
gracefully rather than being required:

- Search still works, using TF-IDF keyword ranking only (this is
  functionally similar to the pre-Day-17 `ILIKE`-based search, just
  ranked by relevance instead of just filtered)
- `POST /api/notes/<id>/auto-embed` returns a real, tested `501` with a
  clear reason instead of crashing
- `GET /api/search/compare` reports `"semanticSearchAvailable": false`
  so you can tell at a glance why results look keyword-only
- Once you `pip install sentence-transformers` and restart the server,
  semantic ranking turns on automatically — no code changes, and
  existing notes get embedded lazily (a search backfills up to 40
  missing embeddings inline, or run `backfill_embeddings.py` once to
  do them all up front)

## Verifying this yourself

```bash
cd backend
pip install -r requirements.txt   # now includes sentence-transformers
python run.py
```

The first search (or the first `auto-embed` call) after installing
will take a few seconds while the model downloads — after that it's
fast and fully offline.

Try it: create two notes — one that says something like *"We landed on
a tiered pricing structure for the new plan"* and one totally
unrelated (e.g. a grocery list). Search for `what did we decide about
cost` — the pricing note should show up labeled 💡 Related match, even
though the query and the note share almost no literal words. Compare
that against `/api/search/compare?q=what did we decide about cost` to
see the keyword-only ranking miss it entirely.

Run all 114 tests (92 from Day 15 + 22 new):

```bash
pytest tests/ -v
```

## Manual QA checklist

- [ ] Search for an exact word/phrase from a note → it appears, labeled
      🔤 or 🎯 depending on whether the concept also matches semantically
- [ ] Search for a paraphrase of a note's content (different words,
      same meaning) → the note still appears, labeled 💡 Related match
- [ ] Search results are ordered by relevance (best match first), not
      by pinned/most-recently-updated like the plain notes list
- [ ] Pin and tag filters still work combined with a search query
- [ ] Uninstall/rename `sentence-transformers` (or just don't install
      it) → search still works, results just lose the 💡/🎯 labels and
      fall back to keyword-only ranking — no errors, no crash
- [ ] `GET /api/search/compare?q=...` returns both ranked lists plus an
      `onlyInHybrid` count showing what semantic search added
- [ ] Everything from Day 15 (tags, entities, tasks, voice, auth) still
      works — semantic search is additive, nothing else changed

# Day 18 — Knowledge graph (linked notes)

New this drop: notes connect to each other automatically through the
ideas they share. Write a note about "Machine Learning, Python, and
Scikit-learn," then another about "Scikit-learn for classification,"
then a third about "evaluating classification models," and the graph
chains all three together — even though no single note mentions every
concept — because each pair of notes overlaps on at least one idea.

## What's new

```
backend/
├── schema.sql    3 new columns on notes: concepts (JSON array, plain
│                 TEXT — same portable pattern as everything else),
│                 concepts_source_hash, concepts_generated_at
├── notes.py       POST /api/notes/<id>/extract-concepts
│                  (background, hash-cached — same pattern as
│                  tags/entities; nothing stored in a separate table)
├── graph.py       new module/blueprint:
│                    GET  /api/graph                whole graph
│                    GET  /api/graph/path?from=&to=  BFS shortest path
│                    GET  /api/notes/<id>/related     ranked related notes
└── tests/         test_notes_concepts.py, test_graph.py — 30+ new
                   tests, including a replay of the exact "Machine
                   Learning → ... → Evaluation Metrics" chain

frontend/
├── index.html    new Graph nav + view (SVG canvas, path-finder form,
│                 legend); Concepts chip row + Related Notes panel in
│                 the editor
├── app.js        force-directed graph layout (plain JS physics, no
│                 charting library) + SVG renderer with drag support;
│                 concept extraction wired into the existing debounce
│                 chain; related-notes panel
└── style.css     styling for all of the above
```

## How the graph is built (no separate graph database)

Nothing about the graph is stored as its own table. Every note gets a
`concepts` list (2-8 short topic/technology/idea phrases, extracted the
same automatic way as tags and entities). The graph is *derived* from
those lists on every request:

- **note → concept** edge for each concept a note mentions
- **concept → concept** edge whenever two concepts are mentioned
  together in the same note (this is what creates the chain — two
  concepts don't need to ever appear in the *same* note to end up
  connected, as long as a path of shared notes links them)

Because it's derived rather than stored, the graph can never drift out
of sync with the notes themselves — edit or delete a note and the
graph reflects that on the next load, with nothing to clean up.

## Finding hidden connections

`GET /api/graph/path?from=Machine+Learning&to=Evaluation+Metrics` runs
a breadth-first search over the concept graph and returns the shortest
chain connecting two ideas, with each hop labeled by which note(s)
actually establish that link — so a "hidden connection" the graph
surfaces is always traceable back to real notes, not just asserted.
The graph view's path-finder form (top of the Graph tab) is a UI for
this same endpoint; click any concept node to drop it into the
from/to fields.

## Related notes

`GET /api/notes/<id>/related` ranks every other note by shared
concepts (weighted highest), shared tags (Day 14, weighted lower), and
falls back to Day 17's embedding similarity when two notes are related
in meaning but haven't converged on the same concept wording yet (e.g.
one note's concepts say "Neural Networks" and another's say "Deep
Learning"). This powers the "Related notes" panel in the editor.

## Verifying this yourself

```bash
cd backend
pip install -r requirements.txt
python run.py
```

Create the three notes from the spec example:

1. *"Machine Learning uses Python and Scikit-learn for model development."*
2. *"Scikit-learn provides tools for classification and regression."*
3. *"Classification models can be evaluated using accuracy, precision, and recall."*

Wait a couple of seconds after each (concept extraction runs on the
same debounce timer as tags/entities), then open the **Graph** tab —
all three notes and six concepts should appear, connected. Try the
path finder with `Machine Learning` → `Evaluation Metrics`: it should
return a chain through Scikit-learn and Classification, with the hop
labels naming the actual notes that connect them.

Run all tests (144 total: 114 from Day 17 + 30 new):

```bash
pytest tests/ -v
```

## Manual QA checklist

- [ ] Graph tab shows nothing but a friendly empty state on a brand
      new account (no notes yet, nothing broken)
- [ ] After writing 2-3 notes that share an idea, the Graph tab shows
      note nodes (indigo) and concept nodes (amber) connected by edges
- [ ] Dragging a node repositions it smoothly, without breaking its edges
- [ ] Clicking a note node opens that note in the editor
- [ ] Clicking a concept node fills the path-finder's from/to field
- [ ] The path finder correctly finds a multi-hop chain between two
      concepts that never appear in the same note, and names the
      connecting notes for each hop
- [ ] Searching for two totally unrelated concepts shows a clear "no
      connection found" message, not an error
- [ ] The editor's Related Notes panel shows other notes that share a
      concept or tag with the one you're viewing, and clicking one
      navigates to it
- [ ] Editing a note's text without re-extracting makes its old
      concepts "stale" — they drop out of the graph until concepts are
      regenerated (same rule as tags/entities/summary)
- [ ] Everything from Day 17 (semantic search, tasks, voice, auth)
      still works — the knowledge graph is additive, nothing else changed

# Day 19 — Displaying related notes in the UI (cross-reference feature)

Day 18 added the plumbing for related notes (`GET
/api/notes/<id>/related`, built on shared concepts/tags + Day 17
embeddings) but only surfaced it as a plain list. Day 19 finishes the
actual UI flow the feature is supposed to deliver:

```
User opens a note
       ↓
System analyzes note relationships   ← loadRelatedNotes() fires on open
       ↓
Find related notes                   ← GET /api/notes/<id>/related
       ↓
Calculate relevance/similarity       ← shared concepts + tags + embedding
       ↓                                similarity → strong/moderate/light
Display related notes in UI          ← panel always visible: loading state,
       ↓                                results, or an honest empty state
User clicks a related note
       ↓
Open connected note                  ← openNote(), already worked, still does
```

## What changed

```
backend/
└── graph.py    each result in /api/notes/<id>/related now also
                 includes "relatedness": "strong" | "moderate" | "light"
                 — a human-readable label derived from the same score
                 that was already there, so relevance is something the
                 UI can actually show next to each note instead of a
                 number nobody would know how to read

frontend/
├── app.js       loadRelatedNotes() now shows the panel immediately
│                with a loading message instead of staying hidden
│                until the request resolves; renderRelatedNotes() shows
│                a friendly empty state instead of just hiding when
│                there's nothing to show yet, and renders a 🎯/🔗/💡
│                badge per note based on "relatedness"
└── style.css    styles for the loading/empty states and the badges
```

## Why a label instead of showing the raw score

The backend score mixes three different kinds of signal (shared
concept count, shared tag count, and 0-1 embedding similarity) into
one number — useful for sorting, meaningless as a percentage someone
would glance at. Rather than fake a precise-looking "73% match" out of
that, related notes get a plain three-tier label:

- 🎯 **Strong match** — shares a specific concept (or several tags)
- 🔗 **Related** — shares at least one tag, or a lighter concept signal
- 💡 **Loosely related** — no shared concept/tag, but similar in
  meaning per Day 17's embeddings (only shown above a similarity floor
  — see `MIN_SEMANTIC_ONLY_SIMILARITY` in `graph.py`)

## Verifying this yourself

```bash
cd backend
pip install -r requirements.txt
python run.py
```

Open any note that has at least one other note sharing a concept or
tag — the Related Notes panel should show a brief "Finding related
notes…" message and then the result within a second, each row labeled
with its match strength. Open a brand new, empty note — the panel
should show the empty-state message rather than disappearing. Click
any related note row — it opens that note in the editor.

Run all tests (147 total: 144 from Day 18 + 3 new for the relatedness
label thresholds):

```bash
pytest tests/ -v
```

## Manual QA checklist

- [ ] Opening a note with related notes shows a brief loading message,
      then the results — never a silent blank panel
- [ ] Opening a note with NO related notes shows the empty-state
      message, not a hidden/missing panel
- [ ] Each related note shows a 🎯 / 🔗 / 💡 badge, and hovering it
      shows a text label (Strong match / Related / Loosely related)
- [ ] Clicking a related note opens it in the editor
- [ ] Switching notes quickly doesn't show stale related-notes results
      from the previously open note (race condition guard in
      `loadRelatedNotes`)
- [ ] Everything from Day 18 (concepts, knowledge graph, path finder)
      still works — this is a UI/labeling refinement on top of it,
      nothing structural changed

# Day 21 — File attachments (PDF, images)

```
User creates/opens a note
        v
Click "Attach File"
        v
Select PDF / Image
        v
Frontend uploads file          -> multipart/form-data POST
        v
Flask backend receives file    -> request.files["file"]
        v
Validate file                  -> extension + size + magic-byte sniff
        v
Save file                      -> disk, server-generated filename
        v
Store file info in PostgreSQL  -> attachments table
        v
Display attachment in the note -> thumbnail (image) or file chip (PDF)
```

## What's new

```
backend/
├── schema.sql          new `attachments` table (user_id, note_id,
│                        filename, original_name, mime_type, kind,
│                        size_bytes) — ON DELETE CASCADE from both
│                        users and notes, so deleting a note or account
│                        cleans up its attachment rows automatically
├── attachments.py       new module/blueprint:
│                          GET    /api/notes/<id>/attachments   list
│                          POST   /api/notes/<id>/attachments   upload
│                          GET    /api/attachments/<id>/file    serve
│                          DELETE /api/attachments/<id>         remove
├── app.py               registers the new blueprint; adds a
│                        MAX_CONTENT_LENGTH cap and a JSON 413 handler
└── tests/
    └── test_attachments.py   34 new tests — validation, magic-byte
                               sniffing, ownership scoping, cascade
                               delete, disk cleanup

frontend/
├── index.html    "Attach" button + hidden file input in the editor
│                 toolbar; an Attachments panel under the text area; a
│                 lightbox modal for full-size image preview
├── app.js        upload flow (client-side pre-check -> FormData POST
│                 -> render), attachment cards (image thumbnail /
│                 PDF chip), delete-with-confirm, lightbox
└── style.css     styling for all of the above, plus a global fix for
                  a [hidden]-related CSS bug (see below)
```

## Validating uploads (not just trusting the filename)

Every upload goes through three checks, in order:

1. **Extension allow-list** — only `.pdf .png .jpg .jpeg .gif .webp`;
   everything else is rejected outright, no exceptions list to bypass
2. **Size** — rejected over 15MB (`MAX_UPLOAD_BYTES`), with a
   `MAX_CONTENT_LENGTH` cap at the Flask level as a second line of
   defense that rejects an oversized request before the body even
   finishes uploading
3. **Magic-byte sniff** — the first bytes of the actual file content
   are checked against the real signature for that format (`%PDF-` for
   PDF, the PNG header, etc.), so a file renamed to `photo.png` that
   isn't actually a PNG gets rejected instead of silently accepted

The filename the browser sent is used **only as display text**. The
file is always saved on disk under a fresh server-generated name
(`uuid4().hex + extension`), which is what actually rules out path
traversal — sanitizing a user-supplied filename is trying to clean up
untrusted input; never using it as a path in the first place removes
the entire problem.

## Verifying this yourself

```bash
cd backend
pip install -r requirements.txt
python run.py
```

Open a note, click the 📎 attach button in the toolbar, pick a PDF or
an image. Images should appear as a small thumbnail (click to preview
full-size); PDFs should appear as a file chip that opens in a new tab
using your browser's built-in PDF viewer. Try renaming a `.txt` file to
`.png` and uploading it — it should be rejected with a clear error
instead of silently accepted.

Run all tests (181 total: 147 from Day 19 + 34 new):

```bash
pytest tests/ -v
```

## An unrelated bug this day's work turned up

While wiring up the attachments panel and the image lightbox, both new
`hidden`-toggled elements independently hit the exact same CSS bug the
Day 18 README already documented for `#view-graph`: a component's own
`display:` rule (author CSS) silently overrides the browser's built-in
`[hidden]{display:none}` (user-agent CSS) regardless of selector
specificity, because author-origin rules always win over user-agent
ones for equal importance. Patching it locally per element (like Day
18 did for `#view-graph` with `:not([hidden])`) turned out to be easy
to forget and re-break — which is exactly what happened again with the
Day 21 lightbox. This drop replaces every local patch with a single
global rule near the top of `style.css`:

```css
[hidden]{ display:none !important; }
```

Author + `!important` beats every normal-importance author rule
(including ID selectors), so this now protects every current *and
future* `hidden`-toggled element in the app permanently, instead of
relying on remembering to special-case each new one.

## Manual QA checklist

- [ ] Uploading a valid PDF, PNG, JPG, GIF, and WEBP each succeed and
      appear in the attachments panel
- [ ] Uploading a disallowed file type (e.g. `.txt`, `.exe`) is
      rejected with a clear error, no attachment created
- [ ] Uploading a file renamed to a fake extension (e.g. a `.exe`
      renamed to `.png`) is rejected by the magic-byte check
- [ ] Uploading a file over 15MB is rejected with a clear error
- [ ] Clicking an image thumbnail opens a full-size preview (lightbox);
      Escape or clicking outside the image closes it
- [ ] Clicking a PDF chip opens the file in a new tab
- [ ] Removing an attachment asks for confirmation, then removes it
      from both the list and disk (re-upload the same file afterward —
      it should work, proving the old file was actually deleted)
- [ ] Attachments are scoped per note and per account — another
      account can't list, view, or delete someone else's attachments
- [ ] Deleting a note also removes its attachments (cascade)
- [ ] Every other `hidden`-toggled panel in the app (auto-summary,
      tags, entities, concepts, related notes, task panel, error
      banner, graph view) still hides/shows correctly after the
      global `[hidden]` CSS fix
- [ ] Everything from Day 19 (related notes, search, tasks, voice,
      auth) still works — file attachments are additive, nothing else
      changed

# Day 22 — OCR for handwritten notes

```
User uploads handwritten image
        v
Image validation             -> reuses Day 21's extension + size +
                                 magic-byte checks unchanged
        v
Image preprocessing          -> grayscale, upscale, autocontrast,
                                 denoise, binarize
        v
OCR                           -> Tesseract via pytesseract
        v
Extract text from image
        v
Save text with the note      -> "Insert into note" button; appends
                                 to the note's text, then saves
        v
AI Processing
   |         |         |
Summary    Tags      Tasks     -> Day 12/14/18's existing auto-
                                   summarize/tag/concepts pipeline,
                                   plus an immediate Day 13 task-
                                   extraction pass — unmodified,
                                   just fed by the newly-inserted text
```

## What's new

```
backend/
├── schema.sql          4 new columns on attachments: ocr_text,
│                        ocr_status ('none'|'pending'|'done'|
│                        'unavailable'|'error'), ocr_error,
│                        ocr_generated_at — same ALTER TABLE ...
│                        IF NOT EXISTS pattern as every prior migration
├── ocr.py               new module: lazy Tesseract availability check
│                        (mirrors Day 9's transcription.py), image
│                        preprocessing, extract_text()
├── attachments.py       GET  /api/ocr/status             (availability check)
│                        POST /api/attachments/<id>/ocr    (run OCR, store result)
│                        serialize_attachment() gains ocrStatus/ocrText/
│                        ocrError/ocrGeneratedAt
├── requirements.txt     added Pillow (core — image preprocessing)
└── requirements-ocr.txt new — optional pytesseract + Tesseract system
                         binary install instructions (see below)

frontend/
├── index.html           new "Extracted text" panel under Attachments;
│                         logo swapped in for the placeholder brand mark
│                         (topnav, login, signup)
├── app.js                automatic OCR trigger right after an image
│                         upload finishes (no button — same background
│                         pattern as Day 12's auto-summary); preview
│                         rendering with Insert/Copy/Retry; boot-time
│                         GET /api/ocr/status check so the app doesn't
│                         waste a request per image when Tesseract
│                         isn't installed
├── style.css             .ocr-result* styles, logo sizing
└── assets/               logo.png, logo-128.png, favicon.png
```

## Installing Tesseract (the actual OCR engine)

`pytesseract` is a thin Python wrapper — it does **not** bundle the OCR
engine itself. Install both:

```bash
cd backend
pip install -r requirements-ocr.txt      # Python wrapper

# then the system binary, one of:
brew install tesseract                    # macOS
sudo apt-get install tesseract-ocr        # Ubuntu / Debian
# Windows: https://github.com/UB-Mannheim/tesseract/wiki

tesseract --version                       # confirm it's on PATH
```

Without the system binary, `/api/ocr/status` reports `available: false`
with an actionable message, the frontend never even attempts an OCR
call, and every other Day 21 attachment feature (upload, thumbnails,
PDF chips, delete, lightbox) works exactly as before.

## Why the extracted text isn't auto-inserted into the note

Every other automatic pass in this app (summary, tags, entities,
concepts) enriches a note that already has real content. OCR is
different: it's proposing entirely new content pulled from a photo, and
a phone photo of a whiteboard sometimes catches things the person
didn't mean to capture (someone else's handwriting at the edge of the
frame, a stray note below the line they cared about). So OCR itself
runs automatically — no button — but landing that text in the note
body is a single explicit "Insert into note" click. Once inserted, it's
just note text, and the *existing* Day 12/13/14/15/18 AI passes pick it
up with no special-casing for where it came from.

## Preprocessing, and why each step is there

- **Grayscale** — color is noise for text recognition; Tesseract works
  on luminance either way, so this also speeds up every later step.
- **Upscale small images** (below 1000px on the long edge) — thin
  pencil/pen strokes on a low-resolution phone photo often fall below
  what Tesseract can resolve as distinct character shapes.
- **Autocontrast** — stretches faint pencil marks and washed-out photo
  lighting to the full black/white range instead of a narrow gray band.
- **Median filter (denoise)** — removes paper texture and JPEG
  compression artifacts that autocontrast would otherwise sharpen into
  false "strokes."
- **Binarize** (threshold at 150/255) — converts to pure black-on-white,
  which is what Tesseract's default recognizer is tuned for.

None of this is handwriting-specific magic — it's the standard
cleanup pass for "photo of text taken in imperfect lighting," which
covers handwritten notes, whiteboards, and sticky notes about equally
well. Genuinely messy cursive will still OCR worse than print —
preprocessing narrows that gap, it doesn't close it.

## Verifying this yourself

```bash
cd backend
pip install -r requirements.txt -r requirements-ocr.txt
# install the Tesseract system binary — see above
python run.py
```

Open a note, click 📎, attach a photo of some text (even a screenshot
of printed text works for testing — Tesseract doesn't care that it
wasn't handwritten). A few seconds later an "Extracted text" panel
appears under the attachment with the OCR result. Click "Insert into
note" — the text lands in the editor, the note saves, and a "Summary"/
"Tags" panel fills in a couple seconds later (if an AI provider is
configured — Day 11) plus a toast when task extraction finishes.

Run all tests (204 total: 191 from Day 21 + 13 new):

```bash
pytest tests/ -v
```

The 13 new tests cover: the `/api/ocr/status` availability check,
auth/ownership scoping on the OCR route, rejecting OCR on PDF
attachments, the attachment shape starting with `ocrStatus: "none"`,
mocked-engine success (including the "found nothing legible" case,
which is `done` with empty text — not an error), the unavailable-engine
and processing-failure error paths, and two real end-to-end passes
against whatever Tesseract is actually installed in the environment
(mirroring Day 9's `test_transcribe_succeeds_when_whisper_available`
pattern) — one confirming genuine text extraction from a real generated
image, one confirming a corrupt-but-magic-byte-valid file is rejected
with a clear error instead of crashing the server.

**This was tested against a real running server, not just the test
suite**: registered a user, created a note, uploaded a generated image
containing the text "Pick up dry cleaning / Email the client by Friday
/ Book flight to Chicago," ran OCR, and confirmed the exact same three
lines came back through the real HTTP response — then inserted that
text into the note and confirmed it persisted via `GET
/api/notes/<id>`.

## Manual QA checklist

- [ ] With Tesseract installed: attach a photo of some text to a note —
      a "Reading handwriting…" status appears within a second, then an
      "Extracted text" preview with the recognized text
- [ ] Click "Insert into note" — the text is appended to the editor,
      the note saves, and (if Day 11's AI is configured) a summary and
      tags appear a couple seconds later plus a toast for task
      extraction
- [ ] Click "Copy" — the extracted text is on the clipboard
- [ ] A photo with no legible text shows "No legible text found" with
      a "Try again" button, not an error
- [ ] Without Tesseract installed: `/api/ocr/status` reports
      `available: false`; attaching an image shows "OCR isn't set up
      on this server" immediately, with no failed network request in
      the browser console
- [ ] Attaching a PDF never shows an OCR panel at all
- [ ] Re-opening a note whose image was never OCR'd (e.g. attached
      right before closing the app) picks up and runs OCR
      automatically on next open
- [ ] OCR results are scoped per attachment and per account — another
      account can't trigger or see OCR results for someone else's
      attachment
- [ ] Everything from Day 21 (upload, thumbnails, PDF chips, delete,
      lightbox, the global `[hidden]` fix) still works exactly as
      before — OCR is additive

# Day 29 — Ollama as an AI provider, and a knowledge-graph overhaul

## What's new: Ollama provider

`AI_PROVIDER=ollama` is now a fourth option alongside `openai`, `gemini`,
and `groq` (Day 11). It works two ways, chosen automatically by whether
`OLLAMA_API_KEY` is set:

- **Set `OLLAMA_API_KEY`** → talks to ollama.com's hosted cloud models
  at `https://ollama.com/v1/chat/completions`, same OpenAI-compatible
  shape the other providers use, key sent as a Bearer token. Get a key
  at <https://ollama.com/settings/keys>.
- **Leave it blank** → talks to a local `ollama serve` at
  `http://localhost:11434` instead — no key needed, since a local
  server has no auth of its own.

`OLLAMA_MODEL` (default `gpt-oss:120b`) and `OLLAMA_HOST` (to point at
something other than the two defaults above — e.g. a self-hosted
Ollama on your network) are both overridable in `.env`. Everything else
in the app — `/api/ai/status`, the AI-assist menu, summaries, tags,
translation, task extraction, and the new graph "explain" feature below
— goes through the same `call_ai()`/`ai_status()` functions as every
other provider, so none of it needed to change to support this.

## What's new: knowledge graph overhaul

The Day 18 graph rendered every note/concept relationship at once with
no way to declutter it, which turns into an unreadable "hairball" of
overlapping labels and crossing lines once you have more than a couple
dozen notes. This day adds real display controls instead:

- **Direct relationships only** (on by default) — hides the *inferred*
  concept↔concept edges (two ideas that happen to co-occur in the same
  notes) and shows only the *direct* note→concept edges. Turning it off
  reveals an **edge-strength slider** to filter out weak/low-confidence
  inferred connections (a single shared note) while keeping strong ones
  (many shared notes).
- **Hide isolated nodes** — once the above filters remove all of a
  node's edges, hide it rather than leave a disconnected dot.
- **Show labels** — toggle all labels off entirely, or on with
  automatic **collision avoidance**: overlapping labels are nudged
  downward in small steps against every other already-placed label's
  actual bounding box until they clear it.
- **Group by topic** — colors concepts (and the notes that mention
  them) by cluster, computed via union-find over the co-occurs graph,
  largest cluster first. A small extra attraction force in the layout
  also pulls same-cluster nodes into loose neighborhoods.
- **Layout: Force / Radial / Grid** — the existing physics simulation,
  a "most-connected-in-the-center" concentric-ring layout, or a plain
  deterministic grid — three different ways to look at the same data
  depending on whether you want organic clustering or a scannable
  overview.
- **Click a node → highlights its direct connections** (dims
  everything else) and opens a side panel with the node's connections,
  an "Open note →" button for notes, "Set as path start/end" for
  concepts (feeding the existing Day 18 path-finder), and an AI
  explain button (see below). Double-click a note node to jump straight
  to it. Click empty canvas, or the same node again, to clear the
  selection.
- **Pan (drag empty canvas) + zoom (scroll wheel, or the +/−/reset
  buttons)**, plus a **minimap** in the corner showing every node's
  position and the current viewport — click anywhere on it to jump
  there.
- **Node sizing** now reflects each node's *current* (post-filter)
  degree rather than a fixed count, so sizes stay meaningful as you
  change the filters.
- **AI-powered insights**: a "✨ Ask AI about this" button on any
  selected concept, and a "✨ Explain this connection" button on any
  path-finder result, both hit a new `POST /api/graph/explain` endpoint
  that builds context entirely from your own notes/concepts (never
  free text) and asks whichever AI provider is configured for a short
  plain-language explanation.
- **Better empty states**: a friendlier "no connections yet" state with
  a "Write a note" button when you have no notes/concepts at all, and a
  *separate* "nothing matches the current filters" state (with a Reset
  filters button) for when your graph has data but the current display
  controls are hiding all of it — previously both cases looked like an
  identical blank canvas.

## Verifying this yourself

Ollama provider (13 new unit tests in `test_ai_client.py`, mocking
`requests.post` so nothing needs a real Ollama server):

```bash
pytest tests/test_ai_client.py -v
```

Graph AI endpoint (12 new tests in `test_graph.py`):

```bash
pytest tests/test_graph.py -v
```

Run everything (268 total):

```bash
pytest tests/ -v
```

The graph *frontend* logic (filtering, clustering, the three layouts,
label collision avoidance, click-to-select, pan/zoom, and the AI-button
wiring) was exercised directly against real sample data — first as pure
functions, then end-to-end through an actual DOM (via jsdom) simulating
clicks, checkbox toggles, and wheel events — confirming e.g. that
turning off "Direct relationships only" reveals exactly the co-occurs
edges, raising the strength slider drops exactly the weak ones,
selecting a node dims exactly its non-neighbors, and "Group by topic"
puts co-occurring concepts in the same cluster.

## Manual QA checklist

- [ ] `AI_PROVIDER=ollama` with `OLLAMA_API_KEY` set: AI features
      (summary, tags, translate, assist, graph explain) work against
      ollama.com
- [ ] `AI_PROVIDER=ollama` with `OLLAMA_API_KEY` blank, and `ollama
      serve` running locally: same features work against localhost
- [ ] Wrong/revoked `OLLAMA_API_KEY` shows a clear "rejected the API
      key" error, not a generic failure
- [ ] Graph: default view shows only note→concept edges; toggling
      "Direct relationships only" off reveals concept↔concept edges
      and the strength slider
- [ ] Dragging the strength slider hides/reveals the weaker inferred
      connections live
- [ ] "Hide isolated nodes" removes whatever the current filters leave
      disconnected
- [ ] Turning off "Show labels" clears all labels; turning it back on
      restores them without them piling on top of each other
- [ ] "Group by topic" visibly colors related concepts (and their
      notes) the same color
- [ ] Switching Layout between Force / Radial / Grid re-arranges the
      same nodes without losing any
- [ ] Clicking a node dims unrelated nodes/edges and opens the side
      panel; clicking it again (or empty canvas) clears the selection
- [ ] Double-clicking a note node opens that note
- [ ] Scrolling zooms in/out around the cursor; dragging empty canvas
      pans; the minimap's rectangle tracks the current view and
      clicking it jumps there
- [ ] "✨ Ask AI about this" on a concept, and "✨ Explain this
      connection" on a path-finder result, both return a short
      explanation (or a clear error if no AI provider is configured)
- [ ] A brand-new account with zero notes shows the "Write a note"
      empty state; an account with notes but an over-restrictive filter
      shows the separate "nothing matches the current filters" state
      with a working Reset button

# Day 30 — rich text formatting + math formulas

## What's new

The editor is a `contenteditable` rich text surface now instead of a
plain `<textarea>`, with a formatting toolbar above it:

- **Font family** — Inter, Arial, Times New Roman, Georgia, Courier
  New, Verdana
- **Font size** — Small / Medium / Large / Heading
- **Bold / Italic / Underline**
- **Text color** and **highlight color** (native color pickers)
- **Bullet and numbered lists**
- **Alignment** — left / center / right
- **Clear formatting** — strips inline styles and headings back to
  plain paragraphs
- **Math formulas** — a "∑ Formula" button opens a small dialog where
  you type LaTeX (e.g. `\frac{a}{b}`, `E=mc^2`) with a live-rendered
  preview (via [KaTeX](https://katex.org/), loaded from cdnjs);
  clicking Insert drops a rendered, non-editable formula into the note
  at the cursor. Double-click any formula to reopen it for editing.

## How it's stored (and why nothing else broke)

`notes.text` — the plain-text field every single piece of AI
functionality (summaries, tags, entities, concept extraction,
embeddings/search, task extraction, the Day 29 knowledge graph) has
always read — **is completely unchanged**. A new `content_html` column
holds the actual rich HTML purely as an additive rendering layer.
`text` is derived automatically from the live editor by walking its DOM
(so `<b>`, colors, fonts, etc. simply disappear, block elements become
line breaks, and a math formula serializes to its LaTeX source wrapped
in `$...$`) — nothing needed to change in `ai_client.py`, `notes.py`'s
AI routes, `embeddings.py`, or `graph.py` for this feature.

`content_html` is nullable: notes created before Day 30, or created
via the API directly without it, simply render `text` as plain lines
instead — there's no migration step and no note becomes unreadable.

Formulas themselves are stored efficiently: only each formula's raw
LaTeX (in a `data-latex` attribute) is saved, not KaTeX's rendered
HTML/MathML output — that gets regenerated from the LaTeX source alone
every time a note loads.

## Security: server-side HTML sanitization

`content_html` arrives from the client as plain JSON, so it's sanitized
server-side before being stored (and before it's ever handed back to a
browser to render via `innerHTML`) — the same principle as sanitizing
any other user-controlled HTML, regardless of how trustworthy the
client-side editor that produced it is supposed to be. `backend/html_sanitize.py`
is a small allow-list sanitizer (stdlib `html.parser`, no new
dependency) that:

- keeps only the tags the editor can actually produce (`b`, `i`, `u`,
  `s`, `span`, `font`, `div`, `p`, `ul`/`ol`/`li`, `h1`-`h3`,
  `blockquote`, `br`)
- keeps only a small allow-list of CSS properties in `style` attributes
  (`color`, `background-color`, `font-family`, `font-size`,
  `text-align`, `font-weight`, `font-style`, `text-decoration`) and
  blocks `url()`/`expression()`/`javascript:` in any of their values
- strips `<script>`/`<style>` tags *and* their content entirely
- drops every attribute not explicitly allowed (so `onclick=`,
  `onerror=`, etc. never survive)
- keeps a formula span's `data-latex` (raw LaTeX text, rendered
  client-side by KaTeX — never executed as HTML/JS)

## Verifying this yourself

Sanitizer + API round-trip (26 new tests in `test_content_html.py`):

```bash
pytest tests/test_content_html.py -v
```

Run everything (294 total):

```bash
pytest tests/ -v
```

The editor/formula frontend logic doesn't have a Python test — it was
verified with a real headless DOM (jsdom) loading the *actual* KaTeX
library (not a mock), exercising: plain-text ⇄ rich-HTML conversion in
both directions, list/heading serialization, a formula's full
render → save (stripped back to just `data-latex`) → reload → re-render
round trip, inserting a formula at a real cursor position via manual
Range manipulation, double-click-to-edit updating the existing node
rather than duplicating it, live preview error states for malformed
LaTeX, every toolbar button calling the right `execCommand`, and that
every toolbar action preserves the editor's text selection instead of
losing it when focus moves to the button/dropdown. 53 checks, all
passing.

## Manual QA checklist

- [ ] Bold / Italic / Underline apply to selected text and toggle back
      off when clicked again
- [ ] Changing font family/size actually changes the selected text's
      appearance; "Heading" turns the current line into a heading
- [ ] Text color and highlight color pickers apply to the current
      selection
- [ ] Bullet and numbered lists toggle on/off correctly
- [ ] Left/center/right alignment buttons work
- [ ] Clear formatting strips bold/italic/color/heading back to plain
      text, without deleting the text itself
- [ ] Clicking ∑ Formula, typing `\frac{a}{b}`, and clicking Insert
      drops a rendered fraction into the note at the cursor
- [ ] Double-clicking that formula reopens it pre-filled, and saving
      changes updates it in place (not a duplicate)
- [ ] Typing invalid LaTeX in the formula dialog shows a friendly
      "keep typing" message instead of a raw error or a crash
- [ ] Reloading the page (or reopening the note from the sidebar)
      preserves all formatting and re-renders every formula correctly
- [ ] A note created before Day 30 (plain `text`, no `content_html`)
      still opens and displays correctly
- [ ] Summaries, tags, task extraction, translation, and the knowledge
      graph all still work normally on a note that contains rich
      formatting and formulas — the AI pipeline should be completely
      unaffected
- [ ] Pasting rich text copied from another app (Word, Google Docs, a
      webpage) comes in as plain text, not with foreign styling

# Day 31 — floating, selection-triggered formatting toolbar

## What changed

The Day 30 toolbar sat permanently docked above the editor, taking up
space whether or not you were formatting anything. It's now a
**floating toolbar** that appears right above your cursor or selection
and disappears when you're not actively positioned in the note —
exactly the "select text → see formatting options" behavior of editors
like Google Docs or Notion.

All the same controls are still there — nothing was removed:
Font family (Inter/Arial/Times New Roman/Georgia/Courier New/Verdana),
Font size (Small/Medium/Large/Heading), Bold/Italic/Underline, text
color, highlight color, bullet/numbered lists, alignment, Clear
formatting, and the ∑ Formula (KaTeX) button — just repositioned.

## Behavior

- Click into the note or select some text → the toolbar appears
  positioned right above it (or below, if there isn't room above —
  e.g. selecting the very first line).
- It follows you: selecting different text, moving the cursor with the
  keyboard, or typing all reposition it live.
- Click anywhere else — the title field, the sidebar, another note —
  and it disappears. Using the toolbar's own controls (including the
  native color pickers, which need a real click to open) never
  triggers this, since those clicks land on the toolbar itself.
- Scrolling hides it (simplest correct behavior — the selection may no
  longer even be visible); resizing the window repositions it if it's
  currently showing.
- Switching notes, creating a new note, or navigating to any other
  view (Dashboard, Tasks, Graph) always hides it — it never gets stuck
  floating over the wrong screen.
- It shows for a plain cursor placement too, not only an actual text
  selection — otherwise there'd be no way to reach the Formula button
  to insert a formula on an empty line, or change the font before
  typing new text.

## Verifying this yourself

The floating toolbar's positioning/visibility logic doesn't touch the
backend at all — Day 31 is 100% frontend. It was verified with the same
real-DOM (jsdom) approach as Day 30, extended with new checks (23,
all passing) covering: showing on selection *and* on a plain caret,
flipping below when there's no room above, clamping horizontally on
both edges so it can never run off-screen, hiding when the selection
leaves the editor or disappears entirely, hiding on scroll,
repositioning (but only when visible) on resize, and the outside-click
rule specifically distinguishing "clicked a toolbar control" (stays
open) from "clicked somewhere unrelated" (closes). The full Day 30
regression suite (53 checks — real KaTeX rendering, formula
insert/edit round-trips, every toolbar button's execCommand call) was
also re-run against the updated code to confirm nothing broke from
converting the toolbar to float.

The backend's full suite (294 tests) was re-run too, confirming this
change is isolated to the frontend as expected.

## Manual QA checklist

- [ ] Opening a note shows no toolbar until you click into the text or
      select something
- [ ] Selecting a word shows the toolbar right above it, vertically
      centered on the selection
- [ ] Selecting the very first line (no room above) flips the toolbar
      to appear below the selection instead
- [ ] Selecting text near the left/right edge of the screen keeps the
      toolbar fully on-screen rather than clipping off the edge
- [ ] Clicking the text-color or highlight-color swatch opens the
      native color picker without the toolbar disappearing first
- [ ] Clicking the title field, the sidebar, or switching to another
      note closes the toolbar
- [ ] Scrolling the page closes the toolbar
- [ ] Placing just a cursor (no selection) still shows the toolbar, so
      Insert Formula and font changes work on an empty line
- [ ] Switching to Dashboard/Tasks/Graph while the toolbar is open
      doesn't leave it stuck floating on screen
- [ ] Every formatting action (bold, lists, alignment, colors, clear
      formatting, formula insert/edit) still works exactly as it did
      in Day 30 — only the toolbar's position/visibility changed




# Day 32 — "Generate Flowchart with AI"

## What's new

Select some text in a note — a set of instructions, a process, a
decision with branches — and click the new **🔀 Flowchart** button
that appears on the Day 31 floating toolbar. The AI reads just that
selection and turns it into a small step-by-step diagram: boxes for
steps, a diamond for a yes/no decision, arrows connecting them, and
Yes/No labels on the branches. It opens immediately in a viewer, and
is saved to the note so it's there next time you open it.

```
backend/
├── schema.sql      new `flowcharts` table: one row per generated
│                   diagram (a note can have several — one per
│                   selection someone's diagrammed), not columns on
│                   `notes` like summary/tags/entities/concepts are,
│                   since regenerating from a different passage
│                   shouldn't overwrite an earlier diagram
├── flowchart.py    POST /api/notes/<id>/flowcharts   (generate + save)
│                   GET  /api/notes/<id>/flowcharts   (list for a note)
│                   DELETE /api/flowcharts/<id>
└── tests/test_flowchart.py   24 new tests

frontend/
├── index.html      🔀 Flowchart button on the floating toolbar, a new
│                   "Flowcharts" panel under the editor (one card per
│                   saved diagram), and a viewer modal
├── app.js          selection-length gating for the toolbar button,
│                   generate/list/delete wiring, a small layered-DAG
│                   layout engine, and an SVG renderer — all frontend,
│                   no charting library
└── style.css       toolbar button disabled state, flowchart panel/
                    card styling, viewer modal, and SVG node/edge
                    styling matching the app's existing palette
```

## How it works

The backend only produces graph *structure* — it asks the AI for
`{"nodes":[{"id","label","type"}], "edges":[{"from","to","label"}]}`
describing the selection (`type` is one of `start`/`end`/`process`/
`decision`/`io`), parses that reply the same tolerant way Day 15/16
parse tag and entity replies (scanning for the outermost `{...}`
since models sometimes wrap JSON in prose despite instructions not
to), and validates it: unknown node types fall back to `process`,
edges pointing at a node that didn't make it into the list are
dropped, everything's capped at 12 nodes / 24 edges so one selection
can't produce an unreadably huge diagram. If the AI can't find any
kind of sequence in the selected text, generation fails with a clear
"select a passage that describes steps or a process" message rather
than inventing a flowchart that isn't there.

*Laying the graph out on a canvas* — deciding which row each box goes
in and where — is entirely a frontend concern, same division as Day
18's knowledge graph (backend produces edges, frontend does the force
layout). Here it's a much simpler layered layout: each node's row is
its longest-path distance from a starting node (computed via Kahn's
algorithm), nodes within a row get one barycenter pass to reduce edge
crossings, and a node with no incoming edges at all (a pure cycle,
which does happen — "keep retrying until it works" loops back on
itself) still gets *some* row rather than being silently dropped.
Edges that loop backward or sideways (retry loops, decision branches
that land in the same row) are routed out and around the right side of
the diagram instead of cutting straight through the middle.

**The 🔀 Flowchart button is gated on the selection itself**, not just
on being inside the editor — unlike the ∑ Formula button (which works
fine on a bare cursor), a flowchart needs actual text to diagram. It
stays visible but disabled, with an explanatory tooltip, until at
least 6 words are actually selected — rather than appearing/
disappearing, which would make it harder to discover in the first
place.

## Verifying this yourself

- **New backend tests (24)**: auth required on all three routes, 404
  on a missing/someone-else's note, validation (empty/too-short/
  too-long selections), 501 when no AI provider is configured, 422
  when the AI can't find a process in the text, tolerance for a reply
  wrapped in prose/markdown fences, dangling edges (pointing at a
  dropped node) correctly stripped, unknown node types falling back to
  `process`, the 12-node cap, delete, and cross-user isolation on both
  list and delete.
- The layered layout and SVG renderer were exercised directly with
  Node (outside the browser) against a straight-line flowchart, a
  branching decision, and a genuine cycle (a "retry" loop back to an
  earlier step) — confirmed all three produce valid SVG without
  hanging on the cyclic case.
- Backend tests need a real Postgres instance (`TEST_DATABASE_URL`,
  same as every other day) to actually run — write them against that
  before merging if you're picking this up in an environment without
  one already configured.

## Manual QA checklist

- [ ] Selecting fewer than ~6 words leaves 🔀 Flowchart greyed out;
      selecting a real passage (a recipe, a set of steps, "if X then Y
      else Z") enables it
- [ ] Clicking it opens a viewer with a diagram matching the selected
      text — steps as boxes, a real decision as a diamond with
      Yes/No-labeled branches
- [ ] The diagram is also saved: closing and reopening the note shows
      it as a card in the new "Flowcharts" panel
- [ ] Generating a second flowchart from a different selection adds a
      second card — it doesn't overwrite the first
- [ ] "Download SVG" on the viewer saves a valid, openable .svg file
- [ ] Deleting a flowchart (from its card or from inside the viewer)
      removes it immediately and for good
- [ ] Selecting text that isn't a process at all (a single sentence of
      description, a name and a date) shows a friendly "couldn't find
      a clear sequence of steps" message instead of a fake diagram
- [ ] Without an AI provider configured: the button is still visible
      but generating shows the same "no AI model connected" error as
      every other AI feature — nothing else in the app is affected
- [ ] Everything from Day 8-31 (login, CRUD, search, pin, voice input,
      the ✨ AI menu, auto-summary/tags/entities/concepts, attachments,
      the knowledge graph, rich text + formulas, the floating toolbar)
      still works exactly as before


## Quick Fix: Groq Study Pack

The project is configured to use Groq through `backend/.env`. The current default model is `openai/gpt-oss-120b`. Groq has deprecated `llama-3.3-70b-versatile` for free/developer-tier usage, so this project no longer uses it as the default.

1. Copy `backend/.env.example` to `backend/.env`.
2. Fill in `DATABASE_URL` and `SECRET_KEY`.
3. Create a Groq API key at https://console.groq.com/keys.
4. Set `GROQ_API_KEY=gsk_...` in `backend/.env`. Never commit the real key.
5. Keep `GROQ_MODEL=openai/gpt-oss-120b`.
6. From `backend`, activate the virtual environment and run `python check_groq.py`.
7. If it reports `Groq configuration looks good`, run `python run.py`.

The API key checker never prints the full key.
