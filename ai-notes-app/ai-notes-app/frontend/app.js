// ============================================================
// AI Notes — Day 8 — frontend
// Same CRUD/search/pin/autosave behavior as Day 7, but auth is
// now a real per-user login: every request relies on the
// browser automatically sending the session cookie (same-origin,
// so no special headers needed), and a 401 anywhere sends the
// user back to the login page instead of just showing an error.
// ============================================================

const API_BASE = window.APP_CONFIG.API_BASE_URL;
const AUTOSAVE_DELAY = 600; // ms of inactivity before we save
const AUTO_SUMMARY_DELAY = 2500; // ms of inactivity before we auto-summarize (Day 12) — longer than
                                  // autosave so we're not calling the AI on every short pause

const state = {
  notes: [],
  filter: "all",
  query: "",
  searchMode: "title", // "title" (match note names — the sidebar search box) | "content" (hybrid keyword+semantic over the note text)
  editingId: null,
  saveTimer: null,
  summaryTimer: null,
  online: false,
  nav: "dashboard", // "dashboard" | "notes" | "tasks" | "graph" — which sidebar section is active
  noteTasks: [], // tasks belonging to the note currently open in the editor
  noteAttachments: [], // attachments belonging to the note currently open in the editor
  pendingUploads: [], // Day 25: [{id, file, progress, status: 'uploading'|'error', errorMsg}] — in-flight uploads shown alongside real attachments
  allTasks: [], // tasks loaded for the full Tasks view
  taskFilter: "open", // "open" | "all" | "done" — filter for the Tasks view
  // Day 34 — keeping the Tasks view usable once it has a couple hundred
  // rows in it: a server-side search, a "group by note" mode (the
  // default — a course's worth of notes reads far better as ~30
  // collapsible sections than one 200-row scroll), and client-side
  // batching so neither mode ever renders more DOM than it has to.
  taskSearch: "", // current search query, already applied server-side to state.allTasks
  taskSearchTimer: null, // debounce handle for the search input
  taskViewMode: "grouped", // "grouped" | "flat"
  collapsedTaskGroups: new Set(), // noteIds collapsed in "grouped" mode
  expandedTaskGroups: new Set(), // noteIds showing all their tasks instead of the first batch
  taskGroupPageSize: 6, // tasks shown per group before "show N more"
  flatTasksVisible: 60, // how many rows are rendered in "flat" mode; grows via "Load more"
  flatTasksPageSize: 60,
  tagFilter: null, // active tag filter for the notes grid, or null
  priorityFilter: "", // Day 26: "" | "high" | "medium" | "low" — active priority filter
  dateRangeFilter: "", // Day 26: "" | "today" | "week" | "month" — active date filter
  allTags: [], // [{tag, count}] — for the sidebar tag list (Day 14)
  graphData: null, // last-loaded {nodes, edges} from GET /api/graph (Day 18)
  graphPath: null, // active path-finder result, or null
  // Day 29 — graph display controls + interaction state
  graphFilters: {
    layout: "force", // "force" | "radial" | "grid"
    directOnly: true, // hide "co-occurs" (inferred) edges, show only note--mentions-->concept edges
    hideIsolated: false, // hide nodes with 0 edges after the above filtering
    showLabels: true, // collision-avoided labels are shown under nodes
    groupByTopic: false, // color concept clusters (connected components of the co-occurs graph) instead of by type
    minStrength: 1, // co-occurs edges below this weight are hidden (only matters when directOnly is off)
  },
  graphSelectedNodeId: null, // id of the clicked/selected node, or null
  graphView: { x: 0, y: 0, k: 1 }, // current pan (x,y) + zoom (k) transform for the canvas viewport
  graphLayoutCache: null, // {positions, adjacency, degree, clusters} from the last renderGraph() call — reused by click-selection/minimap so they don't need to recompute layout
  ocrAvailable: null, // null = not checked yet, true/false once /api/ocr/status responds (Day 22)
  ocrReason: null,
  ocrPendingIds: new Set(), // attachment ids currently being OCR'd (request in flight)
  transcriptAvailable: null, // Day 27: null = not checked yet, true/false once /api/voice/status responds
  transcriptReason: null,
  transcriptPendingIds: new Set(), // attachment ids currently being transcribed (request in flight)
  aiAvailable: null, // Day 28: null = not checked yet, true/false once /api/ai/status responds
  translateLanguages: [], // Day 28: [{code, name}], fetched once from /api/ai/languages
  translations: {}, // Day 28: translateKey(attachmentId, langCode, customName) -> { status: 'pending'|'done'|'error', text, error }
  translateSelection: {}, // Day 28: attachmentId -> last-picked language code, so the dropdown remembers your choice
  translateCustomLangName: {}, // Day 28 (fix): attachmentId -> last-typed custom language name, so it survives re-renders
  noteFlowcharts: [], // Day 32: AI-generated flowcharts belonging to the note currently open in the editor
  flowchartGenerating: false, // true while a "Generate Flowchart" request is in flight (guards against double-clicks)
  openFlowchartId: null, // id of the flowchart currently shown in flowchartOverlay, or null
  // Day 33 — Study Mode
  studySelectedNoteId: null, // id of the note the Study page is currently showing, or null
  studyData: null, // last-loaded study pack for the selected note ({generated:false} or the full pack), or null before anything's loaded
  studyGenerating: false, // true while a generate/regenerate request is in flight
  studyTab: "summary", // "summary" | "keypoints" | "qna" | "mcqs" | "flashcards" | "definitions"
  studyQuizAnswers: {}, // mcq index -> the option index the user picked, for the in-page graded quiz
  // Day 35 — Profile & account settings
  profile: null, // last-loaded profile object from GET /api/profile, or null before first load
  profileEditing: false, // whether the "Profile details" card is showing the edit form or the read-only view
  profileSaving: false, // true while a PATCH /api/profile request is in flight
  // Day 36 — Smart Notifications
  notifications: [], // last-loaded list from GET/POST /api/notifications*
  notifUnreadCount: 0,
  notifPanelOpen: false,
  notifRefreshing: false, // true while a /generate request is in flight (disables the Refresh button)
};

const el = (id) => document.getElementById(id);
const views = {
  dashboard: el("view-dashboard"),
  notes: el("view-notes"),
  editor: el("view-editor"),
  tasks: el("view-tasks"),
  graph: el("view-graph"),
  study: el("view-study"),
  profile: el("view-profile"),
};

function showView(name) {
  Object.values(views).forEach((v) => (v.hidden = true));
  views[name].hidden = false;
  // Profile has no sidebar entry — the round avatar button is its only door,
  // so it doubles as the "you're on Profile" indicator.
  el("topnavAvatarBtn").classList.toggle("active", name === "profile");
  // Day 31: the formatting toolbar now floats (position: fixed) rather
  // than sitting docked in the editor's document flow, so it has to be
  // explicitly hidden on navigation — otherwise it would stay visible,
  // stuck mid-screen, over whatever view you just switched to.
  if (name !== "editor" && typeof hideFloatingToolbar === "function") hideFloatingToolbar();
}

// ============================================================
// API client
// ============================================================
async function api(path, opts = {}) {
  // Day 21: file uploads pass a FormData body — fetch sets the correct
  // multipart/form-data Content-Type (with the required boundary
  // string) automatically for those, but only if we don't also set our
  // own Content-Type header. Forcing application/json onto a FormData
  // body would break the upload silently (Flask would fail to parse
  // request.files at all), so skip the default header in that case.
  const isFormData = typeof FormData !== "undefined" && opts.body instanceof FormData;
  const res = await fetch(API_BASE + path, {
    ...opts,
    credentials: "same-origin",
    headers: {
      ...(isFormData ? {} : { "Content-Type": "application/json" }),
      ...(opts.headers || {}),
    },
  });

  if (res.status === 401) {
    window.location.href = "login.html";
    throw new Error("Not logged in");
  }

  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = await res.json();
      if (body.error) message = body.error;
    } catch (_) {}
    throw new Error(message);
  }
  if (res.status === 204) return null;
  return res.json();
}

async function checkAuthAndHealth() {
  try {
    const me = await api("/api/auth/me");
    setConnStatus(true);
    showUserBadge(me.username);
    return true;
  } catch (e) {
    setConnStatus(false);
    return false;
  }
}

function setConnStatus(online) {
  state.online = online;
  const badge = el("connStatus");
  badge.classList.toggle("online", online);
  badge.classList.toggle("offline", !online);
  el("connLabel").textContent = online ? "Backend connected" : "Backend unreachable";
}

function showUserBadge(username) {
  el("userBadge").hidden = false;
  el("userName").textContent = username;
}

// Day 35 — small circular avatar in the topnav (image if one's been
// uploaded, otherwise initials derived from the profile). Shared by the
// topnav badge and the Profile page's own larger avatar.
function initialsFor(profile) {
  const source = (profile && profile.fullName) || (profile && profile.username) || "?";
  const parts = source.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function updateTopnavAvatar(profile) {
  const img = el("topnavAvatarImg");
  const initials = el("topnavAvatarInitials");
  if (profile && profile.avatarUrl) {
    img.src = profile.avatarUrl;
    img.hidden = false;
    initials.hidden = true;
  } else {
    img.hidden = true;
    initials.hidden = false;
    initials.textContent = initialsFor(profile);
  }
}

async function logout() {
  try {
    await fetch(API_BASE + "/api/auth/logout", { method: "POST", credentials: "same-origin" });
  } catch (e) {
    /* even if this fails, still send them to login */
  }
  window.location.href = "login.html";
}

// ============================================================
// Error banner
// ============================================================
function showError(message, retryFn) {
  const banner = el("errorBanner");
  el("errorText").textContent = message;
  banner.hidden = false;
  el("btnRetry").onclick = async () => {
    banner.hidden = true;
    if (retryFn) await retryFn();
  };
}
function clearError() {
  el("errorBanner").hidden = true;
}

// ============================================================
// Helpers
// ============================================================
function escapeHtml(str) {
  const d = document.createElement("div");
  d.textContent = str || "";
  return d.innerHTML;
}

function wordCount(text) {
  const trimmed = (text || "").trim();
  return trimmed ? trimmed.split(/\s+/).length : 0;
}

function timeAgo(iso) {
  if (!iso) return "";
  const diff = (Date.now() - new Date(iso)) / 1000;
  if (diff < 5) return "just now";
  if (diff < 60) return Math.floor(diff) + "s ago";
  if (diff < 3600) return Math.floor(diff / 60) + "m ago";
  if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
  if (diff < 604800) return Math.floor(diff / 86400) + "d ago";
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function fullTimestamp(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, {
    month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit",
  });
}

function highlight(text, query) {
  if (!query) return escapeHtml(text);
  const escaped = escapeHtml(text);
  const safeQuery = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return escaped.replace(new RegExp(`(${safeQuery})`, "ig"), "<mark>$1</mark>");
}

// ============================================================
// Loading notes from the API
// ============================================================
// Search-as-you-type fires overlapping requests; a slow, older response must
// never overwrite the newer one, so only the most recent call may set state.
let loadNotesSeq = 0;
async function loadNotes() {
  const mySeq = ++loadNotesSeq;
  try {
    const params = [];
    if (state.filter === "pinned") params.push("pinned=1");
    if (state.tagFilter) params.push(`tag=${encodeURIComponent(state.tagFilter)}`);
    if (state.priorityFilter) params.push(`priority=${encodeURIComponent(state.priorityFilter)}`);
    if (state.dateRangeFilter) params.push(`dateRange=${encodeURIComponent(state.dateRangeFilter)}`);
    const qs = params.length ? `?${params.join("&")}` : "";
    const path = state.query
      ? `/api/search?q=${encodeURIComponent(state.query)}&mode=${state.searchMode}${params.length ? `&${params.join("&")}` : ""}`
      : `/api/notes${qs}`;
    const result = await api(path);
    if (mySeq !== loadNotesSeq) return; // superseded by a newer load
    state.notes = result;
    clearError();
    setConnStatus(true);
  } catch (e) {
    if (e.message === "Not logged in") return; // already redirecting
    if (mySeq !== loadNotesSeq) return;
    setConnStatus(false);
    showError(`Couldn't reach the backend (${e.message}). Is the server running on ${window.location.origin}?`, loadAndRender);
    state.notes = [];
  }
}

async function loadAndRender() {
  await loadNotes();
  renderNotesGrid();
}

// ============================================================
// Rendering: notes grid
// ============================================================
function renderNotesGrid() {
  // Day 17: when a search query is active, the backend has already
  // ranked state.notes by hybrid relevance score — re-sorting by
  // pinned/updatedAt here would throw that ranking away and make
  // "smart search" look identical to browsing. Only apply the
  // pinned-first/most-recent ordering when there's no query to rank by.
  const list = state.query
    ? state.notes.slice()
    : state.notes.slice().sort((a, b) => {
        if (!!b.pinned - !!a.pinned !== 0) return !!b.pinned - !!a.pinned;
        return new Date(b.updatedAt) - new Date(a.updatedAt);
      });

  const grid = el("notesGrid");
  const empty = el("emptyState");
  const heading = el("notesHeading");

  heading.textContent = state.tagFilter
    ? `#${state.tagFilter}`
    : state.filter === "pinned"
    ? "Pinned notes"
    : state.query
    ? state.searchMode === "title"
      ? `Notes named “${state.query}”`
      : "Search results (inside note text)"
    : anyFilterActive()
    ? "Filtered notes"
    : "All notes";
  el("notesCount").textContent = list.length
    ? `${list.length} note${list.length > 1 ? "s" : ""}`
    : "";

  // Only offered when a *name* search came up empty: one click re-runs the
  // same words against the full text of every note instead.
  el("btnSearchContentInstead").hidden = !(state.query && state.searchMode === "title" && !list.length);

  if (!list.length) {
    grid.innerHTML = "";
    empty.hidden = false;
    empty.querySelector(".empty-body").textContent = state.query
      ? state.searchMode === "title"
        ? `No note has “${state.query}” in its name.`
        : "No notes matched that search."
      : state.tagFilter
      ? "No notes with this tag (yet)."
      : state.filter === "pinned"
      ? "Pin a note to see it here."
      : anyFilterActive()
      ? "No notes match these filters. Try clearing one."
      : 'Click "New note" to capture your first thought.';
    renderStats();
    return;
  }
  empty.hidden = true;

  // A name search matched on titles only, so only the title gets the yellow
  // highlight; a text search highlights the summary/preview too.
  const bodyQuery = state.searchMode === "content" ? state.query : "";
  grid.innerHTML = list
    .map((n) => {
      const preview = (n.text || "").trim() || "Empty note";
      const summaryHtml =
        n.summary && !n.summaryStale
          ? `<p class="nc-summary"><span class="nc-summary-icon">✨</span>${highlight(n.summary, bodyQuery)}</p>`
          : "";
      const tagsHtml =
        n.tags && n.tags.length && !n.tagsStale
          ? `<div class="nc-tags">${n.tags.map((t) => `<span class="tag-chip" data-tag="${escapeHtml(t)}">${escapeHtml(t)}</span>`).join("")}</div>`
          : "";
      const ent = n.entities && !n.entitiesStale ? n.entities : null;
      const previewChips = ent
        ? [
            ...(ent.people || []).map((v) => ({ cat: "people", icon: "👤", v })),
            ...(ent.organizations || []).map((v) => ({ cat: "organizations", icon: "🏢", v })),
          ].slice(0, 3)
        : [];
      const entitiesHtml = previewChips.length
        ? `<div class="nc-entities">${previewChips
            .map((c) => `<span class="entity-chip ${c.cat}">${c.icon} ${escapeHtml(c.v)}</span>`)
            .join("")}</div>`
        : "";
      // Day 17: only present when this card came back from a search —
      // shows why it matched (found by meaning vs. found by literal
      // words vs. both), which is the whole point of demoing hybrid
      // search rather than a plain keyword search.
      const matchLabels = { semantic: "💡 Related match", keyword: "🔤 Keyword match", both: "🎯 Strong match", title: "🏷️ Name match" };
      const matchBadgeHtml =
        state.query && n.searchMatch
          ? `<span class="nc-match-badge nc-match-${n.searchMatch}">${matchLabels[n.searchMatch] || ""}</span>`
          : "";
      const priorityLabels = { high: "🔴 High", medium: "🟡 Medium", low: "🔵 Low" };
      const priorityBadgeHtml =
        n.priority && n.priority !== "none"
          ? `<span class="nc-priority-badge nc-priority-${n.priority}">${priorityLabels[n.priority]}</span>`
          : "";
      return `
      <div class="note-card priority-${n.priority || "none"} ${n.pinned ? "pinned" : ""}" data-id="${n.id}">
        <button class="nc-del" data-id="${n.id}" title="Delete">✕</button>
        <div class="nc-top">
          <h3>${highlight(n.title || "Untitled note", state.query)}</h3>
          ${n.pinned ? `<span class="nc-pin" title="Pinned">📌</span>` : ""}
        </div>
        ${matchBadgeHtml}
        ${priorityBadgeHtml}
        ${summaryHtml}
        ${tagsHtml}
        ${entitiesHtml}
        <p>${highlight(preview.slice(0, 160), bodyQuery)}</p>
        <div class="nc-foot">
          <span>${n.wordCount ?? wordCount(n.text)} words</span>
          <span>${timeAgo(n.updatedAt)}</span>
        </div>
      </div>`;
    })
    .join("");

  grid.querySelectorAll(".note-card").forEach((card) => {
    card.addEventListener("click", (e) => {
      if (e.target.closest(".nc-del")) return;
      if (e.target.closest(".tag-chip")) return;
      openNote(card.dataset.id);
    });
  });
  grid.querySelectorAll(".nc-del").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteNote(btn.dataset.id, { fromGrid: true });
    });
  });
  grid.querySelectorAll(".nc-tags .tag-chip").forEach((chip) => {
    chip.addEventListener("click", (e) => {
      e.stopPropagation();
      setTagFilter(chip.dataset.tag);
    });
  });

  renderStats();
}

function renderStats() {
  const total = state.notes.length;
  const pinned = state.notes.filter((n) => n.pinned).length;
  const words = state.notes.reduce((sum, n) => sum + (n.wordCount ?? wordCount(n.text)), 0);
  el("sidebarStats").innerHTML = `
    <div><b>${total}</b> note${total === 1 ? "" : "s"} ${state.query || state.filter === "pinned" || state.tagFilter ? "shown" : "total"}</div>
    <div><b>${pinned}</b> pinned</div>
    <div><b>${words}</b> words written</div>
  `;
}

// ============================================================
// Day 14 — sidebar tag browsing
// The tag list comes from GET /api/tags (aggregated server-side, not
// derived from the notes currently loaded in the grid) so it stays
// accurate even while a filter is narrowing what's on screen.
// ============================================================
async function loadTags() {
  try {
    state.allTags = await api("/api/tags");
    renderSidebarTags();
  } catch (e) {
    // Not logged in yet, or no AI provider configured — just show no tags.
    state.allTags = [];
  }
}

function renderSidebarTags() {
  const wrap = el("sidebarTags");
  const list = el("sidebarTagsList");

  if (!state.allTags.length) {
    wrap.hidden = true;
    return;
  }
  wrap.hidden = false;

  const chips = state.allTags
    .map(
      (t) => `
      <span class="tag-chip ${state.tagFilter === t.tag ? "active" : ""}" data-tag="${escapeHtml(t.tag)}">
        ${escapeHtml(t.tag)} <span class="tag-chip-count">${t.count}</span>
      </span>`
    )
    .join("");
  const clearBtn = state.tagFilter ? `<button class="tag-chip-clear" id="btnClearTagFilter">Clear tag filter</button>` : "";

  list.innerHTML = chips + clearBtn;

  list.querySelectorAll(".tag-chip").forEach((chip) => {
    chip.addEventListener("click", () => setTagFilter(chip.dataset.tag));
  });
  const clear = el("btnClearTagFilter");
  if (clear) clear.addEventListener("click", () => setTagFilter(null));
}

function setTagFilter(tag) {
  state.tagFilter = state.tagFilter === tag ? null : tag;
  renderSidebarTags();
  updateClearFiltersButton();
  loadAndRender();
}

// ============================================================
// Day 26 — priority & date filters
// ============================================================
function anyFilterActive() {
  return !!(state.tagFilter || state.priorityFilter || state.dateRangeFilter || state.query || state.filter === "pinned");
}

function updateClearFiltersButton() {
  el("btnClearFilters").hidden = !anyFilterActive();
}

document.querySelectorAll("#priorityFilterRow .filter-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#priorityFilterRow .filter-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.priorityFilter = btn.dataset.priority || "";
    updateClearFiltersButton();
    loadAndRender();
  });
});

el("dateFilterSelect").addEventListener("change", (e) => {
  state.dateRangeFilter = e.target.value;
  updateClearFiltersButton();
  loadAndRender();
});

el("btnClearFilters").addEventListener("click", () => {
  state.tagFilter = null;
  state.priorityFilter = "";
  state.dateRangeFilter = "";
  state.query = "";
  state.searchMode = "title";
  state.filter = "all";
  el("searchInput").value = "";
  syncSearchUi();
  el("dateFilterSelect").value = "";
  document.querySelectorAll("#priorityFilterRow .filter-btn").forEach((b) => b.classList.toggle("active", !b.dataset.priority));
  document.querySelectorAll("#notesFilterRow .filter-btn").forEach((b) => b.classList.toggle("active", b.dataset.filter === "all"));
  renderSidebarTags();
  updateClearFiltersButton();
  loadAndRender();
});

// ============================================================
// Day 30 — rich text editor helpers
//
// The editor is a contenteditable <div> now, not a <textarea>. These
// wrap it so the rest of the app can keep thinking in terms of "the
// current plain text" — word count, autosave's `text` field, every AI
// endpoint (summarize/tag/entities/concepts/embeddings/search/task
// extraction) — none of which changed for Day 30. `text` stays a
// faithful plain-text extraction; `contentHtml` is the new, purely
// additive rich rendering layer saved alongside it.
// ============================================================
function getEditorEl() {
  return el("editorText");
}

function escapeHtmlForEditor(s) {
  return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

// Seeds the rich editor from a plain-text string (one <div> per line) —
// used both to load a pre-Day-30 note that has no contentHtml yet, and
// to fully replace the editor's content with plain AI output (e.g. the
// "Polish grammar" assist action, which always returns plain text).
function plainTextToEditorHtml(text) {
  return (text || "")
    .split("\n")
    .map((line) => `<div>${escapeHtmlForEditor(line) || "<br>"}</div>`)
    .join("");
}

function setEditorPlainText(text) {
  const editor = getEditorEl();
  // Left genuinely empty (not a lone empty <div>) so the :empty
  // placeholder CSS rule actually applies for a blank note.
  editor.innerHTML = text ? plainTextToEditorHtml(text) : "";
}

// The inverse: walks the editor's live DOM to build a plain-text
// version. Block-level elements each become a line; math formulas
// serialize back to their LaTeX source wrapped in $...$ (from each
// formula's data-latex attribute) rather than KaTeX's rendered
// HTML+MathML markup, so a note containing formulas still gives the
// entire AI pipeline something sensible to read instead of garbled,
// duplicated text pulled out of KaTeX's internal DOM structure.
const EDITOR_BLOCK_TAGS = ["DIV", "P", "LI", "H1", "H2", "H3", "BLOCKQUOTE"];
function getEditorPlainText() {
  const clone = getEditorEl().cloneNode(true);
  clone.querySelectorAll(".math-formula").forEach((node) => {
    node.replaceWith(document.createTextNode(`$${node.dataset.latex || ""}$`));
  });
  clone.querySelectorAll("br").forEach((node) => node.replaceWith(document.createTextNode("\n")));
  clone.querySelectorAll(EDITOR_BLOCK_TAGS.join(",")).forEach((node) => {
    node.appendChild(document.createTextNode("\n"));
  });
  const text = (clone.textContent || "").replace(/\n{3,}/g, "\n\n");
  return text.replace(/^\n+|\n+$/g, "");
}

// getEditorHtml() is what actually gets saved to contentHtml. Math
// formulas are stripped back down to an empty <span data-latex="…">
// before serializing (rather than storing KaTeX's rendered internals),
// since that rendered markup uses tags (<math>, <annotation>, …) well
// outside the backend's sanitizer allow-list, and storing it would be
// redundant anyway — renderAllFormulasIn() re-renders every formula
// from its data-latex source alone on load, so nothing is lost.
function getEditorHtml() {
  const clone = getEditorEl().cloneNode(true);
  clone.querySelectorAll(".math-formula").forEach((node) => {
    node.innerHTML = "";
  });
  return clone.innerHTML;
}

function setEditorHtml(html) {
  getEditorEl().innerHTML = html || "";
  renderAllFormulasIn(getEditorEl());
}

// Appends a new paragraph of plain text at the end of the editor —
// used by every "insert into note" action (OCR text, translations,
// audio transcripts), replacing the old string-concatenation-on-.value
// approach a <textarea> allowed.
function appendPlainTextToEditor(text) {
  const div = document.createElement("div");
  div.textContent = text;
  getEditorEl().appendChild(div);
}

// ============================================================
// Day 30 — math formula support (KaTeX)
// Formulas live in the editor as non-editable
// <span class="math-formula" data-latex="…"> nodes — data-latex is the
// single source of truth; KaTeX just renders it into that span's
// content. Click the ∑ Formula button to insert one at the cursor,
// double-click an existing formula to edit it.
// ============================================================
function renderAllFormulasIn(root) {
  if (typeof katex === "undefined") return;
  root.querySelectorAll(".math-formula[data-latex]").forEach((node) => {
    try {
      katex.render(node.dataset.latex, node, { throwOnError: false, displayMode: false });
    } catch (e) {
      node.textContent = node.dataset.latex;
    }
  });
}

let savedEditorRange = null;
let formulaEditingNode = null;

// Only overwrites the saved range while the selection is actually
// inside the editor — called on every keyup/mouseup there, and
// defensively on mousedown of any toolbar control (which fires before
// that control steals focus away from the editor).
function saveEditorSelectionRange() {
  const sel = window.getSelection();
  if (!sel || !sel.rangeCount) return;
  const range = sel.getRangeAt(0);
  if (getEditorEl().contains(range.commonAncestorContainer)) {
    savedEditorRange = range.cloneRange();
  }
}

function restoreEditorSelectionRange() {
  const editor = getEditorEl();
  editor.focus();
  if (savedEditorRange) {
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(savedEditorRange);
  }
}

function execFormatCommand(command, value = null) {
  restoreEditorSelectionRange();
  document.execCommand(command, false, value);
  saveEditorSelectionRange();
  scheduleAutosave();
  updateToolbarActiveStates();
}

function updateToolbarActiveStates() {
  document.querySelectorAll("#formatToolbar .fmt-btn[data-cmd], #formatToolbar .fmt-btn[data-align]").forEach((btn) => {
    const command = btn.dataset.cmd || btn.dataset.align;
    let active = false;
    try {
      active = document.queryCommandState(command);
    } catch (e) {
      /* some commands don't support queryCommandState in every browser — just skip highlighting */
    }
    btn.classList.toggle("active", active);
  });
}

// ============================================================
// Day 31 — floating toolbar positioning
// The toolbar (see style.css: position: fixed) tracks wherever the
// caret or selection currently is inside the editor, appearing right
// above it — rather than sitting permanently docked above the note
// taking up space whether you're formatting anything or not.
// ============================================================
function positionFloatingToolbar() {
  const toolbar = el("formatToolbar");
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0) {
    hideFloatingToolbar();
    return;
  }
  const range = sel.getRangeAt(0);
  if (!getEditorEl().contains(range.commonAncestorContainer)) {
    hideFloatingToolbar();
    return;
  }

  toolbar.hidden = false; // show first so its real size is measurable below

  let rect = range.getBoundingClientRect();
  if (rect.width === 0 && rect.height === 0) {
    // A collapsed caret at the very start/end of an empty line can
    // report an all-zero rect in some browsers — fall back to the
    // editor's own box so the toolbar still lands somewhere sensible
    // instead of pinned to the top-left corner of the viewport.
    rect = getEditorEl().getBoundingClientRect();
  }

  const toolbarRect = toolbar.getBoundingClientRect();
  let top = rect.top - toolbarRect.height - 10;
  if (top < 8) top = rect.bottom + 10; // flip below the selection if there's no room above
  let left = rect.left + rect.width / 2 - toolbarRect.width / 2;
  const maxLeft = window.innerWidth - toolbarRect.width - 8;
  left = Math.max(8, Math.min(left, maxLeft));

  toolbar.style.top = `${top}px`;
  toolbar.style.left = `${left}px`;
}

function hideFloatingToolbar() {
  el("formatToolbar").hidden = true;
}

// ============================================================
// Day 32 — "Generate Flowchart with AI" button state
// Unlike every other control on the floating toolbar, this one needs
// an actual (non-collapsed) text selection with something to diagram —
// a bare caret has no text to turn into a flowchart. The button stays
// visible but disabled otherwise, with a tooltip explaining why,
// rather than appearing/disappearing (which would make it harder to
// discover in the first place).
const FLOWCHART_MIN_SELECTION_WORDS = 6;

function updateFlowchartButtonState() {
  const btn = el("fmtFlowchartBtn");
  if (!btn) return;
  const sel = window.getSelection();
  const text = sel && !sel.isCollapsed ? sel.toString().trim() : "";
  const enough = text && text.split(/\s+/).length >= FLOWCHART_MIN_SELECTION_WORDS;
  btn.disabled = !enough;
  btn.title = enough
    ? "Generate a flowchart from the selected text"
    : `Select at least ${FLOWCHART_MIN_SELECTION_WORDS} words describing steps or a process first`;
}

// Clicking inside the toolbar itself never reaches this listener as an
// "outside" click (mousedown on its own buttons is already prevented
// from bubbling as a focus-stealing event — see the toolbar wiring
// below — and its color inputs/selects are still within `.contains()`
// here), so this only fires for genuinely-elsewhere clicks: the title
// field, sidebar, another note, etc.
document.addEventListener("mousedown", (e) => {
  const toolbar = el("formatToolbar");
  if (toolbar.hidden) return;
  if (toolbar.contains(e.target) || getEditorEl().contains(e.target)) return;
  hideFloatingToolbar();
});
window.addEventListener("scroll", () => hideFloatingToolbar(), true);
window.addEventListener("resize", () => {
  if (!el("formatToolbar").hidden) positionFloatingToolbar();
});

function openFormulaModal(editingNode) {
  formulaEditingNode = editingNode || null;
  const input = el("formulaInput");
  input.value = editingNode ? editingNode.dataset.latex || "" : "";
  renderFormulaPreview();
  el("formulaOverlay").hidden = false;
  input.focus();
}

function closeFormulaModal() {
  el("formulaOverlay").hidden = true;
  formulaEditingNode = null;
}

function renderFormulaPreview() {
  const preview = el("formulaPreview");
  const latex = el("formulaInput").value.trim();
  preview.classList.remove("formula-preview-error");
  if (!latex) {
    preview.textContent = "";
    return;
  }
  if (typeof katex === "undefined") {
    preview.textContent = latex;
    return;
  }
  try {
    preview.innerHTML = katex.renderToString(latex, { throwOnError: true, displayMode: false });
  } catch (e) {
    preview.textContent = "Can't parse this yet — keep typing.";
    preview.classList.add("formula-preview-error");
  }
}

el("formulaInput").addEventListener("input", renderFormulaPreview);
el("formulaCancelBtn").addEventListener("click", closeFormulaModal);
el("formulaOverlay").addEventListener("click", (e) => {
  if (e.target === el("formulaOverlay")) closeFormulaModal();
});

el("formulaInsertBtn").addEventListener("click", () => {
  const latex = el("formulaInput").value.trim();
  if (!latex) {
    closeFormulaModal();
    return;
  }

  if (formulaEditingNode) {
    formulaEditingNode.dataset.latex = latex;
    renderAllFormulasIn(getEditorEl());
  } else {
    const span = document.createElement("span");
    span.className = "math-formula";
    span.contentEditable = "false";
    span.dataset.latex = latex;

    restoreEditorSelectionRange();
    const sel = window.getSelection();
    if (sel.rangeCount && getEditorEl().contains(sel.getRangeAt(0).commonAncestorContainer)) {
      const range = sel.getRangeAt(0);
      range.deleteContents();
      range.insertNode(span);
      range.setStartAfter(span);
      range.setEndAfter(span);
      sel.removeAllRanges();
      sel.addRange(range);
      savedEditorRange = range.cloneRange();
    } else {
      getEditorEl().appendChild(span);
    }
    renderAllFormulasIn(getEditorEl());
  }
  closeFormulaModal();
  scheduleAutosave();
});

el("fmtFormulaBtn").addEventListener("mousedown", (e) => {
  e.preventDefault();
  saveEditorSelectionRange();
});
el("fmtFormulaBtn").addEventListener("click", () => openFormulaModal(null));

// Day 32: same "preventDefault on mousedown so the button click doesn't
// steal/collapse the editor's selection first" pattern as every other
// toolbar control above.
el("fmtFlowchartBtn").addEventListener("mousedown", (e) => {
  e.preventDefault();
  saveEditorSelectionRange();
});
el("fmtFlowchartBtn").addEventListener("click", () => {
  if (el("fmtFlowchartBtn").disabled) return;
  const sel = window.getSelection();
  const text = sel ? sel.toString().trim() : "";
  generateFlowchart(text);
});

// Double-click an existing formula to edit it, instead of it being just
// an inert rendered image with no way back to the source.
getEditorEl().addEventListener("dblclick", (e) => {
  const node = e.target.closest(".math-formula");
  if (node) openFormulaModal(node);
});

// ============================================================
// Day 30 — formatting toolbar wiring
// Every control does `mousedown -> preventDefault()/save the current
// selection` *before* `click/change -> restore that selection, then
// run the actual execCommand` — clicking a toolbar button or opening a
// <select> would otherwise steal focus from the contenteditable editor
// first, collapsing/losing whatever text was selected to format.
// ============================================================
document.querySelectorAll("#formatToolbar .fmt-btn[data-cmd], #formatToolbar .fmt-btn[data-align]").forEach((btn) => {
  btn.addEventListener("mousedown", (e) => e.preventDefault());
  btn.addEventListener("click", () => execFormatCommand(btn.dataset.cmd || btn.dataset.align));
});

el("fmtClearBtn").addEventListener("mousedown", (e) => e.preventDefault());
el("fmtClearBtn").addEventListener("click", () => {
  restoreEditorSelectionRange();
  document.execCommand("removeFormat");
  document.execCommand("formatBlock", false, "DIV"); // also drop any heading block, not just inline styles
  saveEditorSelectionRange();
  scheduleAutosave();
  updateToolbarActiveStates();
});

el("fmtFontFamily").addEventListener("mousedown", saveEditorSelectionRange);
el("fmtFontFamily").addEventListener("change", () => {
  execFormatCommand("fontName", el("fmtFontFamily").value);
});

// Small/Medium/Large map to the legacy execCommand('fontSize') 1-7
// scale (values 2/3/5), restyled to our own exact pixel sizes via the
// font[size="N"] CSS rule above rather than trusting the browser's
// built-in — not very controllable — size scale. Heading is a
// block-level format instead (<h2>), so it goes through formatBlock.
const FONT_SIZE_MAP = { small: "2", medium: "3", large: "5" };
el("fmtFontSize").addEventListener("mousedown", saveEditorSelectionRange);
el("fmtFontSize").addEventListener("change", () => {
  const val = el("fmtFontSize").value;
  restoreEditorSelectionRange();
  if (val === "heading") {
    document.execCommand("formatBlock", false, "H2");
  } else {
    document.execCommand("formatBlock", false, "DIV");
    document.execCommand("fontSize", false, FONT_SIZE_MAP[val] || "3");
  }
  saveEditorSelectionRange();
  scheduleAutosave();
  el("fmtFontSize").selectedIndex = 0; // this is an action, not a persistent "current size" indicator
});

el("fmtTextColor").addEventListener("mousedown", saveEditorSelectionRange);
el("fmtTextColor").addEventListener("input", () => {
  execFormatCommand("foreColor", el("fmtTextColor").value);
});

el("fmtHighlightColor").addEventListener("mousedown", saveEditorSelectionRange);
el("fmtHighlightColor").addEventListener("input", () => {
  restoreEditorSelectionRange();
  // hiliteColor is what Firefox/most browsers use for this; backColor
  // is Chrome's older equivalent for the rare case hiliteColor no-ops.
  const applied = document.execCommand("hiliteColor", false, el("fmtHighlightColor").value);
  if (!applied) document.execCommand("backColor", false, el("fmtHighlightColor").value);
  saveEditorSelectionRange();
  scheduleAutosave();
});

getEditorEl().addEventListener("keyup", () => {
  saveEditorSelectionRange();
  updateToolbarActiveStates();
  positionFloatingToolbar();
  updateFlowchartButtonState();
});
getEditorEl().addEventListener("mouseup", () => {
  saveEditorSelectionRange();
  updateToolbarActiveStates();
  positionFloatingToolbar();
  updateFlowchartButtonState();
});
// Tabbing into the editor (no mouseup/keyup of its own) still needs to
// bring the toolbar up.
getEditorEl().addEventListener("focus", () => {
  saveEditorSelectionRange();
  updateToolbarActiveStates();
  positionFloatingToolbar();
  updateFlowchartButtonState();
});

// Paste as plain text, always. Rich content pasted from another app
// (Word, Google Docs, a webpage) brings along arbitrary markup/classes
// our sanitizer allow-list doesn't know about — simplest, safest, and
// most predictable behavior is to strip it on the way in rather than
// try to losslessly translate every possible source's HTML.
getEditorEl().addEventListener("paste", (e) => {
  e.preventDefault();
  const text = (e.clipboardData || window.clipboardData).getData("text/plain");
  document.execCommand("insertText", false, text);
});

// ============================================================
// Editor
// ============================================================
async function openNewNote() {
  try {
    const note = await api("/api/notes", {
      method: "POST",
      body: JSON.stringify({ title: "", text: "" }),
    });
    state.notes.unshift(note);
    clearError();
    openNote(String(note.id), { isNew: true });
  } catch (e) {
    if (e.message === "Not logged in") return;
    showError(`Couldn't create a new note (${e.message}).`);
  }
}

function openNote(id, opts = {}) {
  const note = state.notes.find((n) => String(n.id) === String(id));
  if (!note) return;
  clearTimeout(state.summaryTimer);
  hideFloatingToolbar(); // Day 31: don't carry a stale position/selection over from whatever was open before
  state.editingId = note.id;
  state.nav = "notes";
  document.querySelectorAll(".sidebar-nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.nav === "notes"));
  closeMobileSidebar();
  el("titleInput").value = note.title;
  if (note.contentHtml) {
    setEditorHtml(note.contentHtml);
  } else {
    setEditorPlainText(note.text || "");
  }
  updatePinButton(note.pinned);
  updatePrioritySelect(note.priority || "none");
  updateEditorMeta(note);
  setAutosaveHint(opts.isNew ? "New note" : `Saved ${timeAgo(note.updatedAt)}`, "saved");
  renderAutoSummary(note);
  renderAutoTags(note);
  renderAutoEntities(note);
  renderAutoConcepts(note);
  loadRelatedNotes(note.id);
  state.noteTasks = [];
  renderTaskPanel();
  if (!opts.isNew) loadNoteTasks(note.id);
  loadAttachments(note.id);
  state.pendingUploads = [];
  state.noteFlowcharts = [];
  renderFlowchartList();
  loadFlowcharts(note.id);
  showView("editor");
  el(opts.isNew ? "titleInput" : "editorText").focus();
}

function updatePinButton(pinned) {
  el("btnPin").classList.toggle("active", !!pinned);
}

// Day 26 — the <select>'s own dataset.priority drives its color styling
// (see .priority-select[data-priority="..."] in style.css), since a
// plain <select> can't be styled per-option-selected any other way.
function updatePrioritySelect(priority) {
  const sel = el("prioritySelect");
  sel.value = priority;
  sel.dataset.priority = priority;
}

el("prioritySelect").addEventListener("change", async (e) => {
  const note = currentNote();
  if (!note) return;
  const priority = e.target.value;
  const previous = note.priority;
  note.priority = priority; // optimistic
  updatePrioritySelect(priority);
  try {
    const updated = await api(`/api/notes/${note.id}/priority`, {
      method: "PATCH",
      body: JSON.stringify({ priority }),
    });
    Object.assign(note, updated);
    showToast("Priority updated", "success");
  } catch (err) {
    if (err.message === "Not logged in") return;
    note.priority = previous;
    updatePrioritySelect(previous);
    showToast(`Couldn't update priority (${err.message}).`, "error");
  }
});

function updateEditorMeta(note) {
  el("editorMeta").textContent =
    `${wordCount(note.text)} word${wordCount(note.text) === 1 ? "" : "s"} · ` +
    `created ${fullTimestamp(note.createdAt)}`;
}

function setAutosaveHint(text, mode) {
  const hint = el("autosaveHint");
  hint.textContent = text;
  hint.classList.remove("saving", "saved");
  if (mode) hint.classList.add(mode);
}

function currentNote() {
  return state.notes.find((n) => n.id === state.editingId);
}

function scheduleAutosave() {
  setAutosaveHint("Saving…", "saving");
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(saveCurrentNote, AUTOSAVE_DELAY);
}

async function saveCurrentNote() {
  const note = currentNote();
  if (!note) return;
  const title = el("titleInput").value;
  const text = getEditorPlainText();
  const contentHtml = getEditorHtml();
  try {
    const updated = await api(`/api/notes/${note.id}`, {
      method: "PUT",
      body: JSON.stringify({ title, text, contentHtml }),
    });
    Object.assign(note, updated);
    updateEditorMeta(note);
    setAutosaveHint(`Saved ${timeAgo(note.updatedAt)}`, "saved");
    clearError();
    scheduleAutoSummary();
  } catch (e) {
    if (e.message === "Not logged in") return;
    setAutosaveHint("Save failed — retrying…", "saving");
    showError(`Couldn't save your note (${e.message}). Your text is still here — retry once the backend is reachable.`, saveCurrentNote);
  }
}

// ============================================================
// Day 12 — automatic summarization
// Runs on its own, longer debounce than autosave (a note has to
// actually be saved before there's anything to summarize). No button —
// the summary panel just fills in a couple seconds after you stop typing.
// (Day 14 reuses this same debounce timer to also trigger auto-tagging,
// since both are "no button, just happens" background AI passes over
// the same freshly-saved text — no reason to run them on separate timers.)
// ============================================================
function scheduleAutoSummary() {
  clearTimeout(state.summaryTimer);
  state.summaryTimer = setTimeout(() => {
    runAutoSummary();
    runAutoTag();
    runAutoEntities();
    runAutoEmbed();
    runAutoConcepts();
  }, AUTO_SUMMARY_DELAY);
}

function renderAutoSummary(note, { generating = false } = {}) {
  const panel = el("autoSummary");
  const label = el("autoSummaryLabel");
  const text = el("autoSummaryText");

  if (generating) {
    panel.hidden = false;
    panel.classList.add("generating");
    panel.classList.remove("stale");
    label.textContent = "Summarizing…";
    text.textContent = note.summary || "Reading your note…";
    return;
  }
  panel.classList.remove("generating");

  if (!note.summary) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  panel.classList.toggle("stale", !!note.summaryStale);
  label.textContent = note.summaryStale ? "Summary (out of date)" : "Summary";
  text.textContent = note.summary;
}

async function runAutoSummary() {
  const note = currentNote();
  if (!note) return;
  // Only bother the user with a "Summarizing…" state if there's nothing
  // to show yet — if we already have a summary, update it silently.
  if (!note.summary) renderAutoSummary(note, { generating: true });

  try {
    const res = await api(`/api/notes/${note.id}/auto-summarize`, { method: "POST" });
    Object.assign(note, res.note);
    if (state.editingId === note.id) renderAutoSummary(note);
  } catch (e) {
    if (e.message === "Not logged in") return;
    // Silent by design — this runs automatically and shouldn't interrupt
    // typing with an error banner. If nothing is configured (Day 11 setup
    // not done), the panel just stays hidden.
    if (state.editingId === note.id) renderAutoSummary(note);
  }
}

// ============================================================
// Day 14 — automatic tagging and categorization
// Same "no button" background pattern as Day 12's summary (in fact it
// shares the same debounce timer — see scheduleAutoSummary above).
// Tag chips appear under the editor and on note cards; the sidebar's
// tag list refreshes after a successful run so newly-created tags
// show up there without needing a manual reload.
// ============================================================
function renderAutoTags(note) {
  const panel = el("autoTags");
  const label = el("autoTagsLabel");
  const list = el("autoTagsList");

  if (!note.tags || note.tags.length === 0) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  panel.classList.toggle("stale", !!note.tagsStale);
  label.textContent = note.tagsStale ? "Tags (out of date)" : "Tags";
  list.innerHTML = note.tags
    .map((t) => `<span class="tag-chip" data-tag="${escapeHtml(t)}">${escapeHtml(t)}</span>`)
    .join("");

  list.querySelectorAll(".tag-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      switchNav("notes");
      setTagFilter(chip.dataset.tag);
    });
  });
}

async function runAutoTag() {
  const note = currentNote();
  if (!note) return;

  try {
    const res = await api(`/api/notes/${note.id}/auto-tag`, { method: "POST" });
    const hadNewTags = JSON.stringify(note.tags) !== JSON.stringify(res.note.tags);
    Object.assign(note, res.note);
    if (state.editingId === note.id) renderAutoTags(note);
    if (hadNewTags) loadTags(); // refresh the sidebar tag list/counts
  } catch (e) {
    if (e.message === "Not logged in") return;
    // Silent by design, same reasoning as runAutoSummary — including
    // when no AI provider is configured (501): the tags panel just
    // stays hidden rather than showing an error banner.
    if (state.editingId === note.id) renderAutoTags(note);
  }
}

// ============================================================
// Day 15 — automatic entity recognition (people, dates, places,
// organizations)
// Same "no button" background pattern as Day 12/14 — shares the same
// debounce timer (see scheduleAutoSummary above). Renders four
// grouped chip rows; groups with nothing found just stay hidden
// rather than showing an empty "People" label with no chips under it.
// ============================================================
const ENTITY_GROUPS = [
  { key: "people", icon: "👤", groupId: "entitiesPeopleGroup", listId: "entitiesPeopleList" },
  { key: "organizations", icon: "🏢", groupId: "entitiesOrganizationsGroup", listId: "entitiesOrganizationsList" },
  { key: "places", icon: "📍", groupId: "entitiesPlacesGroup", listId: "entitiesPlacesList" },
  { key: "dates", icon: "📅", groupId: "entitiesDatesGroup", listId: "entitiesDatesList" },
];
const EMPTY_ENTITIES = { people: [], dates: [], places: [], organizations: [] };

function renderAutoEntities(note) {
  const panel = el("autoEntities");
  const entities = note.entities || EMPTY_ENTITIES;
  const hasAny = ENTITY_GROUPS.some((g) => entities[g.key] && entities[g.key].length);

  if (!hasAny) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;

  ENTITY_GROUPS.forEach(({ key, icon, groupId, listId }) => {
    const group = el(groupId);
    const values = entities[key] || [];
    if (!values.length) {
      group.hidden = true;
      return;
    }
    group.hidden = false;
    el(listId).innerHTML = values
      .map((v) => `<span class="entity-chip ${key}">${icon} ${escapeHtml(v)}</span>`)
      .join("");
  });
}

async function runAutoEntities() {
  const note = currentNote();
  if (!note) return;

  try {
    const res = await api(`/api/notes/${note.id}/extract-entities`, { method: "POST" });
    Object.assign(note, res.note);
    if (state.editingId === note.id) renderAutoEntities(note);
  } catch (e) {
    if (e.message === "Not logged in") return;
    // Silent by design, same reasoning as runAutoSummary/runAutoTag.
    if (state.editingId === note.id) renderAutoEntities(note);
  }
}

// ============================================================
// Day 17 — automatic embedding generation for semantic search
// Same "no button" background pattern as the others above — shares the
// same debounce timer. Nothing to render here: an embedding isn't
// user-visible content, it's just infrastructure that keeps
// /api/search fast by not needing to backfill this note inline the
// next time someone searches. Failures are silent by design — if the
// embedding model isn't installed, search just falls back to keyword
// ranking (see notes.py), so there's nothing to surface as an error.
// ============================================================
async function runAutoEmbed() {
  const note = currentNote();
  if (!note) return;
  try {
    const res = await api(`/api/notes/${note.id}/auto-embed`, { method: "POST" });
    Object.assign(note, res.note);
  } catch (e) {
    if (e.message === "Not logged in") return;
    // Silent by design — see comment above.
  }
}

// ============================================================
// Day 18 — concept extraction + knowledge graph
//
// Concepts share the same "no button" debounced background pattern as
// tags/entities/embeddings. Unlike embeddings, concepts ARE rendered
// (a chip row, like tags) since they're the labels people will
// recognize on the graph. Once concepts land, we also refresh the
// "related notes" panel — the two features are meant to feel like one
// continuous idea: concepts are what get extracted, the graph/related
// panel is what you DO with them.
// ============================================================
async function runAutoConcepts() {
  const note = currentNote();
  if (!note) return;
  try {
    const res = await api(`/api/notes/${note.id}/extract-concepts`, { method: "POST" });
    Object.assign(note, res.note);
    if (state.editingId === note.id) {
      renderAutoConcepts(note);
      loadRelatedNotes(note.id);
    }
  } catch (e) {
    if (e.message === "Not logged in") return;
    if (state.editingId === note.id) renderAutoConcepts(note);
  }
}

function renderAutoConcepts(note) {
  const panel = el("autoConcepts");
  const concepts = note.concepts && !note.conceptsStale ? note.concepts : [];
  if (!concepts.length) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  el("autoConceptsList").innerHTML = concepts
    .map(
      (c) =>
        `<span class="concept-chip" data-concept="${escapeHtml(c)}" title="Find this concept's connections">${escapeHtml(c)}</span>`
    )
    .join("");
}

// Clicking a concept chip in the editor jumps straight to the graph
// view with that concept pre-filled as the path-finder's starting
// point — a quick way to go from "what is this note about" to
// "what else connects to that idea".
el("autoConceptsList").addEventListener("click", (e) => {
  const chip = e.target.closest(".concept-chip");
  if (!chip) return;
  switchNav("graph");
  el("graphPathFrom").value = chip.dataset.concept;
  el("graphPathTo").focus();
});

async function loadRelatedNotes(noteId) {
  const panel = el("relatedNotes");
  // Day 19: show the panel immediately with a loading state rather than
  // staying hidden until the request resolves — "the system is
  // analyzing relationships" should be a visible step, not something
  // that only shows up once it already has an answer.
  panel.hidden = false;
  el("relatedNotesList").innerHTML = `<p class="related-notes-loading">Finding related notes…</p>`;
  try {
    const related = await api(`/api/notes/${noteId}/related`);
    if (state.editingId !== noteId) return; // user navigated away while this was in flight
    renderRelatedNotes(related);
  } catch (e) {
    if (e.message === "Not logged in") return;
    el("relatedNotesList").innerHTML = `<p class="related-notes-loading">Couldn't load related notes.</p>`;
  }
}

const RELATEDNESS_BADGE = {
  strong: { icon: "🎯", label: "Strong match" },
  moderate: { icon: "🔗", label: "Related" },
  light: { icon: "💡", label: "Loosely related" },
};

function renderRelatedNotes(related) {
  const panel = el("relatedNotes");
  panel.hidden = false;

  if (!related || !related.length) {
    // Day 19: an explicit, friendly empty state instead of hiding the
    // panel — makes clear the cross-reference feature ran and simply
    // didn't find a strong match yet, rather than looking broken/absent.
    el("relatedNotesList").innerHTML = `
      <p class="related-notes-empty">
        No related notes yet. As this note (and others) pick up tags and
        concepts, connections will start showing up here.
      </p>`;
    return;
  }

  el("relatedNotesList").innerHTML = related
    .map((r) => {
      const sharedBits = [
        ...r.sharedConcepts.slice(0, 3).map((c) => `🧠 ${escapeHtml(c)}`),
        ...r.sharedTags.slice(0, 2).map((t) => `#${escapeHtml(t)}`),
      ];
      const sharedHtml = sharedBits.length
        ? `<div class="related-note-shared">${sharedBits.map((b) => `<span>${b}</span>`).join("")}</div>`
        : `<div class="related-note-shared"><span>💡 Related in meaning</span></div>`;
      const badge = RELATEDNESS_BADGE[r.relatedness] || RELATEDNESS_BADGE.light;
      return `
        <button class="related-note-row" data-id="${r.note.id}">
          <div class="related-note-top">
            <span class="related-note-title">${escapeHtml(r.note.title || "Untitled note")}</span>
            <span class="related-note-badge related-note-badge-${r.relatedness}" title="${badge.label}">${badge.icon}</span>
          </div>
          ${sharedHtml}
        </button>`;
    })
    .join("");
}

el("relatedNotesList").addEventListener("click", (e) => {
  const row = e.target.closest(".related-note-row");
  if (!row) return;
  openNote(row.dataset.id);
});

// ============================================================
// Day 21 — file attachments (PDF, images)
//
//   Click "Attach File" -> pick a file -> upload -> validate -> save ->
//   store in DB -> show it in the note. Client-side checks here are
//   just a fast first pass for a good error message; the backend
//   re-validates everything (extension, size, actual file content via
//   magic bytes) since client-side checks are trivial to bypass.
// ============================================================
const ALLOWED_ATTACHMENT_EXT = ["pdf", "png", "jpg", "jpeg", "gif", "webp", "webm", "ogg", "wav", "mp3", "m4a"];
const AUDIO_ATTACHMENT_EXT = ["webm", "ogg", "wav", "mp3", "m4a"];
const MAX_ATTACHMENT_MB = 25;

el("btnAttach").addEventListener("click", () => el("attachInput").click());
el("btnAttachInline").addEventListener("click", () => el("attachInput").click());

el("attachInput").addEventListener("change", async (e) => {
  const files = Array.from(e.target.files);
  e.target.value = ""; // allow re-selecting the same file(s) later
  handleIncomingFiles(files);
});

// ---------- Day 25: drag-and-drop ----------
const dropzone = el("attachmentsDropzone");
let dragCounter = 0; // tracks nested dragenter/dragleave pairs so the
                      // highlight doesn't flicker off over child elements

function isFileDrag(e) {
  return e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files");
}

["dragenter", "dragover", "dragleave", "drop"].forEach((evt) => {
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    e.stopPropagation();
  });
});
dropzone.addEventListener("dragenter", (e) => {
  if (!isFileDrag(e)) return;
  dragCounter++;
  dropzone.classList.add("dragover");
});
dropzone.addEventListener("dragleave", () => {
  dragCounter = Math.max(0, dragCounter - 1);
  if (dragCounter === 0) dropzone.classList.remove("dragover");
});
dropzone.addEventListener("drop", (e) => {
  dragCounter = 0;
  dropzone.classList.remove("dragover");
  if (!isFileDrag(e)) return;
  const files = Array.from(e.dataTransfer.files || []);
  handleIncomingFiles(files);
});

// Also accept a drop anywhere over the note editor, not just the small
// dropzone box — the whole editor lights up as a fallback target so
// people don't have to aim precisely.
const editorView = el("view-editor");
["dragenter", "dragover"].forEach((evt) => {
  editorView.addEventListener(evt, (e) => {
    if (!isFileDrag(e)) return;
    e.preventDefault();
  });
});
editorView.addEventListener("drop", (e) => {
  if (!isFileDrag(e)) return;
  if (e.target.closest("#attachmentsDropzone")) return; // already handled above
  e.preventDefault();
  const files = Array.from(e.dataTransfer.files || []);
  handleIncomingFiles(files);
});

function handleIncomingFiles(files) {
  if (!currentNote() || !files.length) return;
  for (const file of files) {
    const ext = file.name.includes(".") ? file.name.split(".").pop().toLowerCase() : "";
    if (!ALLOWED_ATTACHMENT_EXT.includes(ext)) {
      showToast(`"${file.name}" isn't a supported format (PDF, JPG, PNG, GIF, WEBP, or audio only).`, "error");
      continue;
    }
    if (file.size > MAX_ATTACHMENT_MB * 1024 * 1024) {
      showToast(`"${file.name}" is too large — max ${MAX_ATTACHMENT_MB}MB.`, "error");
      continue;
    }
    uploadAttachment(file);
  }
}

function uploadAttachment(file, extraFields = {}) {
  const note = currentNote();
  if (!note) return;

  const pendingId = `pending-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  state.pendingUploads.push({ id: pendingId, file, progress: 0, status: "uploading", errorMsg: "" });
  renderAttachments();

  const btn = el("btnAttach");
  btn.classList.add("working");

  const formData = new FormData();
  formData.append("file", file);
  Object.entries(extraFields).forEach(([key, value]) => formData.append(key, value));

  const xhr = new XMLHttpRequest();
  xhr.open("POST", API_BASE + `/api/notes/${note.id}/attachments`);
  xhr.withCredentials = true;

  xhr.upload.addEventListener("progress", (e) => {
    if (!e.lengthComputable) return;
    const p = state.pendingUploads.find((u) => u.id === pendingId);
    if (!p) return;
    p.progress = Math.round((e.loaded / e.total) * 100);
    updatePendingProgress(pendingId, p.progress);
  });

  xhr.addEventListener("load", () => {
    const stillPending = state.pendingUploads.some((u) => u.id === pendingId);
    if (!stillPending) return; // note was switched away from mid-upload

    if (xhr.status === 401) {
      window.location.href = "login.html";
      return;
    }

    let body = {};
    try { body = JSON.parse(xhr.responseText); } catch (_) {}

    if (xhr.status >= 200 && xhr.status < 300) {
      state.pendingUploads = state.pendingUploads.filter((u) => u.id !== pendingId);
      if (state.editingId === note.id) {
        state.noteAttachments.push(body);
        renderAttachments();
        showToast(`"${file.name}" attached`, "success");
        // Day 22: images get an automatic "read the handwriting" pass the
        // moment they land, same no-button background pattern as auto-
        // summary/tags — no reason to make someone click a second button
        // right after they just attached a file.
        if (body.kind === "image") maybeRunOcr(body.id);
        // Day 27: same idea for audio — transcribe automatically right
        // after upload, rather than making someone hunt for a button.
        if (body.kind === "audio") maybeRunTranscription(body.id);
      }
    } else {
      const msg = body.error || xhr.statusText || "Upload failed";
      const p = state.pendingUploads.find((u) => u.id === pendingId);
      if (p) { p.status = "error"; p.errorMsg = msg; }
      renderAttachments();
      showToast(`Couldn't attach "${file.name}" (${msg}).`, "error");
    }
    if (!state.pendingUploads.some((u) => u.status === "uploading")) {
      btn.classList.remove("working");
    }
  });

  xhr.addEventListener("error", () => {
    const p = state.pendingUploads.find((u) => u.id === pendingId);
    if (p) { p.status = "error"; p.errorMsg = "Network error"; }
    renderAttachments();
    showToast(`Couldn't attach "${file.name}" (network error).`, "error");
    if (!state.pendingUploads.some((u) => u.status === "uploading")) btn.classList.remove("working");
  });

  xhr.send(formData);
}

function updatePendingProgress(pendingId, pct) {
  const row = document.querySelector(`.attachment-card-pending[data-pending-id="${pendingId}"]`);
  if (!row) return;
  const bar = row.querySelector(".attachment-progress-bar");
  const label = row.querySelector(".attachment-progress-pct");
  if (bar) bar.style.width = `${pct}%`;
  if (label) label.textContent = `${pct}%`;
}

function dismissFailedUpload(pendingId) {
  state.pendingUploads = state.pendingUploads.filter((u) => u.id !== pendingId);
  renderAttachments();
}

async function loadAttachments(noteId) {
  try {
    const attachments = await api(`/api/notes/${noteId}/attachments`);
    if (state.editingId !== noteId) return; // navigated away while this was in flight
    state.noteAttachments = attachments;
    renderAttachments();
    // Day 22: pick up any image attachments that were uploaded before OCR
    // ran (e.g. the app was reopened mid-upload, or OCR wasn't set up
    // yet at the time) — 'none' means genuinely never attempted.
    attachments
      .filter((a) => a.kind === "image" && a.ocrStatus === "none")
      .forEach((a) => maybeRunOcr(a.id));
    // Day 27: same catch-up pass for audio transcription.
    attachments
      .filter((a) => a.kind === "audio" && a.transcriptStatus === "none")
      .forEach((a) => maybeRunTranscription(a.id));
  } catch (e) {
    if (e.message === "Not logged in") return;
    state.noteAttachments = [];
    renderAttachments();
  }
}

function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function fileKindLabel(nameOrExt) {
  const ext = (nameOrExt.includes(".") ? nameOrExt.split(".").pop() : nameOrExt).toLowerCase();
  if (ext === "pdf") return "PDF";
  if (["jpg", "jpeg"].includes(ext)) return "JPG";
  return ext.toUpperCase();
}

function formatDuration(seconds) {
  if (seconds == null || isNaN(seconds)) return "";
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function renderAttachments() {
  // Day 25: the panel (dropzone + format hint) stays visible whenever a
  // note is open, whether or not it has attachments yet — that's what
  // makes drag-and-drop discoverable instead of only appearing after
  // the first file lands.
  const list = el("attachmentsList");

  const pendingHtml = state.pendingUploads
    .map((p) => {
      const kindLabel = fileKindLabel(p.file.name);
      if (p.status === "error") {
        return `
          <div class="attachment-card attachment-card-pending attachment-card-error" data-pending-id="${p.id}">
            <div class="attachment-pending-icon">⚠️</div>
            <div class="attachment-meta">
              <span class="attachment-name" title="${escapeHtml(p.file.name)}">${escapeHtml(p.file.name)}</span>
              <span class="attachment-size">${formatFileSize(p.file.size)} · ${kindLabel}</span>
            </div>
            <div class="attachment-upload-error">${escapeHtml(p.errorMsg || "Upload failed")}</div>
            <button class="attachment-dismiss" data-dismiss="${p.id}" title="Dismiss">Dismiss</button>
          </div>`;
      }
      return `
        <div class="attachment-card attachment-card-pending" data-pending-id="${p.id}">
          <div class="attachment-pending-icon">${p.file.type.startsWith("image/") ? "🖼️" : p.file.type.startsWith("audio/") ? "🎙️" : "📄"}</div>
          <div class="attachment-meta">
            <span class="attachment-name" title="${escapeHtml(p.file.name)}">${escapeHtml(p.file.name)}</span>
            <span class="attachment-size">${formatFileSize(p.file.size)} · ${kindLabel}</span>
          </div>
          <div class="attachment-progress-track"><div class="attachment-progress-bar" style="width:${p.progress}%"></div></div>
          <span class="attachment-progress-pct">${p.progress}%</span>
        </div>`;
    })
    .join("");

  if (!state.noteAttachments.length && !state.pendingUploads.length) {
    list.innerHTML = "";
    el("ocrResults").innerHTML = "";
    el("transcriptResults").innerHTML = "";
    return;
  }

  const attachedHtml = state.noteAttachments
    .map((a) => {
      const kindLabel = fileKindLabel(a.originalName);
      const actions = `
        <div class="attachment-actions">
          <button class="att-action" data-act="preview" data-id="${a.id}" title="Preview">
            <svg viewBox="0 0 24 24" width="14" height="14"><path fill="none" stroke="currentColor" stroke-width="1.8" d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7Z"/><circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>
          </button>
          <a class="att-action" href="${a.url}" download="${escapeHtml(a.originalName)}" data-id="${a.id}" title="Download">
            <svg viewBox="0 0 24 24" width="14" height="14"><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M12 4v11m0 0-4-4m4 4 4-4M5 19h14"/></svg>
          </a>
          <button class="att-action att-action-danger" data-act="delete" data-id="${a.id}" title="Delete">
            <svg viewBox="0 0 24 24" width="14" height="14"><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" d="M4 7h16M9 7V4h6v3m-8 0 1 13h8l1-13"/></svg>
          </button>
        </div>`;
      if (a.kind === "image") {
        return `
          <div class="attachment-card attachment-card-image" data-id="${a.id}">
            <img class="attachment-thumb" src="${a.url}" alt="${escapeHtml(a.originalName)}" loading="lazy" data-id="${a.id}" />
            <div class="attachment-meta">
              <span class="attachment-name" title="${escapeHtml(a.originalName)}">${escapeHtml(a.originalName)}</span>
              <span class="attachment-size">${formatFileSize(a.sizeBytes)} · ${kindLabel}</span>
            </div>
            ${actions}
          </div>`;
      }
      if (a.kind === "audio") {
        const transcribeAction =
          a.transcriptStatus === "none"
            ? `<button class="att-action" data-act="transcribe" data-id="${a.id}" title="Transcribe">
                 <svg viewBox="0 0 24 24" width="14" height="14"><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M4 6h16M4 12h10M4 18h7"/></svg>
               </button>`
            : "";
        return `
          <div class="attachment-card attachment-card-audio" data-id="${a.id}">
            <div class="attachment-audio-icon">🎙️</div>
            <div class="attachment-audio-body">
              <div class="attachment-meta">
                <span class="attachment-name" title="${escapeHtml(a.originalName)}">${escapeHtml(a.originalName)}</span>
                <span class="attachment-size">${formatFileSize(a.sizeBytes)}${a.durationSeconds ? ` · ${formatDuration(a.durationSeconds)}` : ""} · ${kindLabel}</span>
              </div>
              <audio class="attachment-audio-player" controls preload="none" src="${a.url}"></audio>
              <div class="attachment-speed-row">
                <span class="attachment-speed-label">Speed</span>
                <select class="attachment-speed-select" data-id="${a.id}" title="Playback speed — slow it down to follow along with the transcript">
                  <option value="0.5">0.5×</option>
                  <option value="0.75">0.75×</option>
                  <option value="1" selected>1×</option>
                  <option value="1.25">1.25×</option>
                  <option value="1.5">1.5×</option>
                  <option value="2">2×</option>
                </select>
              </div>
            </div>
            <div class="attachment-actions attachment-actions-audio">
              ${transcribeAction}
              <a class="att-action" href="${a.url}" download="${escapeHtml(a.originalName)}" data-id="${a.id}" title="Download">
                <svg viewBox="0 0 24 24" width="14" height="14"><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M12 4v11m0 0-4-4m4 4 4-4M5 19h14"/></svg>
              </a>
              <button class="att-action att-action-danger" data-act="delete" data-id="${a.id}" title="Delete">
                <svg viewBox="0 0 24 24" width="14" height="14"><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" d="M4 7h16M9 7V4h6v3m-8 0 1 13h8l1-13"/></svg>
              </button>
            </div>
          </div>`;
      }
      return `
        <div class="attachment-card attachment-card-file" data-id="${a.id}">
          <div class="attachment-file-icon">📄</div>
          <div class="attachment-meta">
            <span class="attachment-name" title="${escapeHtml(a.originalName)}">${escapeHtml(a.originalName)}</span>
            <span class="attachment-size">${formatFileSize(a.sizeBytes)} · ${kindLabel}</span>
          </div>
          ${actions}
        </div>`;
    })
    .join("");

  // Pending uploads show first so the thing you just did is where you're looking.
  list.innerHTML = pendingHtml + attachedHtml;
  renderOcrResults();
  renderTranscriptResults();
}

el("attachmentsList").addEventListener("click", async (e) => {
  const dismissBtn = e.target.closest(".attachment-dismiss");
  if (dismissBtn) {
    dismissFailedUpload(dismissBtn.dataset.dismiss);
    return;
  }
  const img = e.target.closest(".attachment-thumb");
  if (img) {
    openLightbox(img.src, img.alt);
    return;
  }
  const previewBtn = e.target.closest('[data-act="preview"]');
  if (previewBtn) {
    const a = state.noteAttachments.find((x) => String(x.id) === String(previewBtn.dataset.id));
    if (!a) return;
    if (a.kind === "image") openLightbox(a.url, a.originalName);
    else window.open(a.url, "_blank", "noopener");
    return;
  }
  const delBtn = e.target.closest('[data-act="delete"]');
  if (delBtn) {
    e.preventDefault();
    await deleteAttachment(delBtn.dataset.id);
    return;
  }
  const transcribeBtn = e.target.closest('[data-act="transcribe"]');
  if (transcribeBtn) {
    e.preventDefault();
    maybeRunTranscription(transcribeBtn.dataset.id);
  }
  // "download" action is a plain <a download> — no JS needed, browser handles it.
});

// Day 28 — playback speed. Slowing this down is the practical fix for
// "the transcribed text goes by too fast to follow along with the
// recording" — the transcript text itself doesn't animate or auto-scroll,
// so the actual lever is how fast the audio plays while you read it.
el("attachmentsList").addEventListener("change", (e) => {
  const speedSelect = e.target.closest(".attachment-speed-select");
  if (!speedSelect) return;
  const card = speedSelect.closest(".attachment-card-audio");
  const player = card?.querySelector(".attachment-audio-player");
  if (player) player.playbackRate = parseFloat(speedSelect.value);
});

async function deleteAttachment(id) {
  const ok = await confirmDialog("Remove this attachment? This can't be undone.", { title: "Remove attachment?" });
  if (!ok) return;
  try {
    await api(`/api/attachments/${id}`, { method: "DELETE" });
    state.noteAttachments = state.noteAttachments.filter((a) => String(a.id) !== String(id));
    renderAttachments();
    showToast("Attachment removed", "success");
  } catch (e) {
    if (e.message === "Not logged in") return;
    showError(`Couldn't remove that attachment (${e.message}).`);
    showToast(`Couldn't remove that attachment (${e.message}).`, "error");
  }
}

// ============================================================
// Day 22 — OCR for handwritten notes
//
//   Attach an image (Day 21 flow above)
//           v
//   Automatic OCR pass runs in the background, no button needed —
//   same "happens quietly a moment after the trigger" feel as
//   Day 12's auto-summary, just triggered by an upload instead of a
//   typing pause
//           v
//   Extracted text shows up as a preview under the attachment, with
//   an explicit "Insert into note" action (never auto-inserted — the
//   person decides whether a given photo's text belongs in this note)
//           v
//   Clicking Insert appends the text, saves, and immediately fans out
//   into the note's EXISTING AI pipeline: auto-summarize / auto-tag /
//   auto-entities / auto-concepts (Day 12/14/15/18's debounce) plus an
//   immediate task-extraction pass (Day 13) — nothing here duplicates
//   that logic, it just feeds it.
// ============================================================
async function checkOcrAvailable() {
  try {
    const res = await api("/api/ocr/status");
    state.ocrAvailable = !!res.available;
    state.ocrReason = res.reason || null;
  } catch (e) {
    // Not logged in yet, or a transient failure — leave it unknown
    // rather than assuming unavailable; the first real OCR attempt
    // will surface the true state either way.
    state.ocrAvailable = null;
  }
}

function maybeRunOcr(attachmentId) {
  // Known-unavailable: skip the network round trip entirely and just
  // show the "not installed" message straight away (attachments.js's
  // response would tell us the same thing, but we already know).
  if (state.ocrAvailable === false) {
    const a = state.noteAttachments.find((x) => String(x.id) === String(attachmentId));
    if (a) {
      a.ocrStatus = "unavailable";
      a.ocrError = state.ocrReason || "OCR isn't set up on this server.";
      renderAttachments();
    }
    return;
  }
  runOcr(attachmentId);
}

async function runOcr(attachmentId) {
  state.ocrPendingIds.add(attachmentId);
  renderAttachments();
  try {
    const res = await api(`/api/attachments/${attachmentId}/ocr`, { method: "POST" });
    applyAttachmentUpdate(res.attachment);
  } catch (e) {
    if (e.message === "Not logged in") return;
    // The API route returns the updated attachment (status 'unavailable'
    // or 'error') in its body even on a non-2xx response, but api()'s
    // error path only surfaces e.message, not the body — so re-fetch
    // this one attachment's real state from the list rather than
    // guessing. Cheap: it's already state.noteAttachments' own data
    // source, just re-synced.
    if (state.editingId != null) loadAttachments(state.editingId);
  } finally {
    state.ocrPendingIds.delete(attachmentId);
    renderAttachments();
  }
}

function applyAttachmentUpdate(updated) {
  const idx = state.noteAttachments.findIndex((a) => String(a.id) === String(updated.id));
  if (idx !== -1) state.noteAttachments[idx] = updated;
  renderAttachments();
}

function renderOcrResults() {
  const wrap = el("ocrResults");
  const images = state.noteAttachments.filter((a) => a.kind === "image");

  const relevant = images.filter(
    (a) => a.ocrStatus !== "none" || state.ocrPendingIds.has(a.id)
  );

  if (!relevant.length) {
    wrap.innerHTML = "";
    return;
  }

  wrap.innerHTML = relevant
    .map((a) => {
      const pending = state.ocrPendingIds.has(a.id);
      const name = escapeHtml(a.originalName);

      if (pending) {
        return `
          <div class="ocr-result ocr-pending" data-id="${a.id}">
            <div class="ocr-result-head">
              <span class="ocr-result-icon">📝</span>
              <span class="ocr-result-name">${name}</span>
              <span class="ocr-result-status">Reading handwriting…</span>
            </div>
          </div>`;
      }

      if (a.ocrStatus === "unavailable") {
        return `
          <div class="ocr-result ocr-unavailable" data-id="${a.id}">
            <div class="ocr-result-head">
              <span class="ocr-result-icon">📝</span>
              <span class="ocr-result-name">${name}</span>
            </div>
            <p class="ocr-result-note">OCR isn't set up on this server — see the README's Day 22 section to enable it.</p>
          </div>`;
      }

      if (a.ocrStatus === "error") {
        return `
          <div class="ocr-result ocr-error" data-id="${a.id}">
            <div class="ocr-result-head">
              <span class="ocr-result-icon">📝</span>
              <span class="ocr-result-name">${name}</span>
              <span class="ocr-result-status">Couldn't read this image</span>
            </div>
            <p class="ocr-result-note">${escapeHtml(a.ocrError || "Unknown error.")}</p>
            <div class="ocr-result-actions">
              <button class="ocr-btn ocr-retry-btn" data-id="${a.id}">Try again</button>
            </div>
          </div>`;
      }

      // status === 'done'
      if (!a.ocrText) {
        return `
          <div class="ocr-result ocr-empty" data-id="${a.id}">
            <div class="ocr-result-head">
              <span class="ocr-result-icon">📝</span>
              <span class="ocr-result-name">${name}</span>
              <span class="ocr-result-status">No legible text found</span>
            </div>
            <div class="ocr-result-actions">
              <button class="ocr-btn ocr-retry-btn" data-id="${a.id}">Try again</button>
            </div>
          </div>`;
      }

      return `
        <div class="ocr-result ocr-done" data-id="${a.id}">
          <div class="ocr-result-head">
            <span class="ocr-result-icon">📝</span>
            <span class="ocr-result-name">${name}</span>
            <span class="ocr-result-status">Extracted text</span>
          </div>
          <p class="ocr-result-text">${escapeHtml(a.ocrText)}</p>
          <div class="ocr-result-actions">
            <button class="ocr-btn ocr-insert-btn" data-id="${a.id}">Insert into note</button>
            <button class="ocr-btn ocr-copy-btn" data-id="${a.id}">Copy</button>
            <button class="ocr-btn ocr-retry-btn" data-id="${a.id}" title="Re-run OCR">↻</button>
          </div>
        </div>`;
    })
    .join("");
}

el("ocrResults").addEventListener("click", async (e) => {
  const insertBtn = e.target.closest(".ocr-insert-btn");
  const copyBtn = e.target.closest(".ocr-copy-btn");
  const retryBtn = e.target.closest(".ocr-retry-btn");

  if (insertBtn) await insertOcrText(insertBtn.dataset.id);
  else if (copyBtn) await copyOcrText(copyBtn.dataset.id);
  else if (retryBtn) runOcr(isNaN(Number(retryBtn.dataset.id)) ? retryBtn.dataset.id : Number(retryBtn.dataset.id));
});

async function insertOcrText(attachmentId) {
  const attachment = state.noteAttachments.find((a) => String(a.id) === String(attachmentId));
  const note = currentNote();
  if (!attachment || !attachment.ocrText || !note) return;

  appendPlainTextToEditor(attachment.ocrText);
  updateEditorMeta({ ...note, text: getEditorPlainText() });

  clearTimeout(state.saveTimer);
  setAutosaveHint("Saving…", "saving");
  await saveCurrentNote(); // persists text, then queues the existing auto-summarize/tag/entities/concepts pass
  showToast("Inserted into note — AI is processing it now.");
  extractTasks(); // immediate pass, same Day 13 endpoint the ✨ AI menu's "Extract tasks" button uses
}

async function copyOcrText(attachmentId) {
  const attachment = state.noteAttachments.find((a) => String(a.id) === String(attachmentId));
  if (!attachment || !attachment.ocrText) return;
  try {
    await navigator.clipboard.writeText(attachment.ocrText);
    showToast("Copied to clipboard.");
  } catch (e) {
    showToast("Couldn't copy — your browser blocked clipboard access.", "error");
  }
}

// ============================================================
// Day 27 — audio notes: transcription
//
//   Attach or record an audio file (Day 27 upload flow above)
//           v
//   Automatic transcription pass runs in the background, same
//   no-button pattern as OCR (Day 22) above — triggered by the upload
//   itself rather than a click
//           v
//   Transcript shows up as a preview under the recording, with an
//   explicit "Insert into note" action (never auto-inserted — same
//   reasoning as OCR: the person decides whether a given recording's
//   words belong in the note text)
//           v
//   Clicking Insert appends the text, saves, and feeds the note's
//   existing AI pipeline exactly like OCR's insert does — nothing here
//   duplicates that logic, it just feeds it
// ============================================================
function maybeRunTranscription(attachmentId) {
  if (state.transcriptAvailable === false) {
    const a = state.noteAttachments.find((x) => String(x.id) === String(attachmentId));
    if (a) {
      a.transcriptStatus = "unavailable";
      a.transcriptError = state.transcriptReason || "Server-side transcription isn't set up on this server.";
      renderAttachments();
    }
    return;
  }
  runTranscription(attachmentId);
}

async function runTranscription(attachmentId) {
  state.transcriptPendingIds.add(attachmentId);
  renderAttachments();
  try {
    const res = await api(`/api/attachments/${attachmentId}/transcribe`, { method: "POST" });
    applyAttachmentUpdate(res.attachment);
  } catch (e) {
    if (e.message === "Not logged in") return;
    // Same reasoning as runOcr's catch: the route returns the updated
    // attachment even on a non-2xx response, but api()'s error path only
    // surfaces e.message — so re-sync from the note's attachment list.
    if (state.editingId != null) loadAttachments(state.editingId);
  } finally {
    state.transcriptPendingIds.delete(attachmentId);
    renderAttachments();
  }
}

function renderTranscriptResults() {
  const wrap = el("transcriptResults");
  const audios = state.noteAttachments.filter((a) => a.kind === "audio");

  const relevant = audios.filter(
    (a) => a.transcriptStatus !== "none" || state.transcriptPendingIds.has(a.id)
  );

  if (!relevant.length) {
    wrap.innerHTML = "";
    return;
  }

  wrap.innerHTML = relevant
    .map((a) => {
      const pending = state.transcriptPendingIds.has(a.id);
      const name = escapeHtml(a.originalName);

      if (pending) {
        return `
          <div class="ocr-result ocr-pending" data-id="${a.id}">
            <div class="ocr-result-head">
              <span class="ocr-result-icon">🎙️</span>
              <span class="ocr-result-name">${name}</span>
              <span class="ocr-result-status">Transcribing…</span>
            </div>
          </div>`;
      }

      if (a.transcriptStatus === "unavailable") {
        return `
          <div class="ocr-result ocr-unavailable" data-id="${a.id}">
            <div class="ocr-result-head">
              <span class="ocr-result-icon">🎙️</span>
              <span class="ocr-result-name">${name}</span>
            </div>
            <p class="ocr-result-note">${escapeHtml(a.transcriptError || "Server-side transcription isn't set up on this server.")} You can still play the recording above.</p>
          </div>`;
      }

      if (a.transcriptStatus === "error") {
        return `
          <div class="ocr-result ocr-error" data-id="${a.id}">
            <div class="ocr-result-head">
              <span class="ocr-result-icon">🎙️</span>
              <span class="ocr-result-name">${name}</span>
              <span class="ocr-result-status">Couldn't transcribe this recording</span>
            </div>
            <p class="ocr-result-note">${escapeHtml(a.transcriptError || "Unknown error.")}</p>
            <div class="ocr-result-actions">
              <button class="ocr-btn transcript-retry-btn" data-id="${a.id}">Try again</button>
            </div>
          </div>`;
      }

      // status === 'done'
      if (!a.transcriptText) {
        return `
          <div class="ocr-result ocr-empty" data-id="${a.id}">
            <div class="ocr-result-head">
              <span class="ocr-result-icon">🎙️</span>
              <span class="ocr-result-name">${name}</span>
              <span class="ocr-result-status">No speech detected</span>
            </div>
            <div class="ocr-result-actions">
              <button class="ocr-btn transcript-retry-btn" data-id="${a.id}">Try again</button>
            </div>
          </div>`;
      }

      return `
        <div class="ocr-result ocr-done" data-id="${a.id}">
          <div class="ocr-result-head">
            <span class="ocr-result-icon">🎙️</span>
            <span class="ocr-result-name">${name}</span>
            <span class="ocr-result-status">Transcript</span>
          </div>
          <p class="ocr-result-text">${escapeHtml(a.transcriptText)}</p>
          <div class="ocr-result-actions">
            <button class="ocr-btn transcript-insert-btn" data-id="${a.id}">Insert into note</button>
            <button class="ocr-btn transcript-copy-btn" data-id="${a.id}">Copy</button>
            <button class="ocr-btn transcript-retry-btn" data-id="${a.id}" title="Re-run transcription">↻</button>
          </div>
          ${renderTranslateBlock(a)}
        </div>`;
    })
    .join("");
}

// Day 28 — translate a transcript into one of a fixed set of languages.
// Only renders once /api/ai/languages has loaded (i.e. an AI provider is
// actually connected) — no dropdown offering something that'll just 501.
//
// Day 28 (fix): the dropdown used to be a hard ceiling — only the 10
// preset languages were reachable. "custom" is now one more option in
// that same dropdown ("Other language…"); picking it reveals a text
// field so any language can be typed in, and that name flows through
// to the backend's own "custom" handling (see ai.py) instead of the
// frontend inventing its own unvalidated path.
const CUSTOM_LANGUAGE_CODE = "custom";

// Builds the cache key a translation result is stored/looked-up under.
// Preset/  "all" translations only need the language code; "custom"
// translations are keyed by the actual typed name too, so translating
// into "French" and then "Filipino" don't overwrite each other.
function translateKey(attachmentId, langCode, customName) {
  if (langCode === CUSTOM_LANGUAGE_CODE) {
    return `${attachmentId}:${CUSTOM_LANGUAGE_CODE}:${(customName || "").trim().toLowerCase()}`;
  }
  return `${attachmentId}:${langCode}`;
}

function renderTranslateBlock(attachment) {
  if (!state.translateLanguages.length) return "";

  const selected = state.translateSelection[attachment.id] || state.translateLanguages[0].code;
  const customName = state.translateCustomLangName[attachment.id] || "";
  const key = translateKey(attachment.id, selected, customName);
  const entry = state.translations[key];
  const isCustom = selected === CUSTOM_LANGUAGE_CODE;
  const customNameTrimmed = customName.trim();

  const options = state.translateLanguages
    .map((l) => `<option value="${l.code}" ${l.code === selected ? "selected" : ""}>${escapeHtml(l.name)}</option>`)
    .join("");

  const customInputHtml = isCustom
    ? `<input type="text" class="translate-custom-input" data-id="${attachment.id}"
        placeholder="e.g. French, Japanese, Swahili…" maxlength="40"
        value="${escapeHtml(customName)}" />`
    : "";

  let resultHtml = "";
  if (entry?.status === "pending") {
    resultHtml =
      selected === "all"
        ? `<p class="translate-result translate-pending"><span class="spinner"></span> Translating into all 10 languages…</p>`
        : `<p class="translate-result translate-pending"><span class="spinner"></span> Translating…</p>`;
  } else if (entry?.status === "error") {
    resultHtml = `<p class="translate-result translate-error">${escapeHtml(entry.error)}</p>`;
  } else if (entry?.status === "done" && selected === "all") {
    // Day 28 — "All Languages": the same sentence translated into every
    // supported language in one pass, each shown as its own row with its
    // own copy/insert (so you can drop just the Hindi line into the
    // note, say, without pulling all ten).
    resultHtml = `
      <div class="translate-all-head">
        <button class="ocr-btn translate-copy-all-btn" data-id="${attachment.id}">Copy all</button>
      </div>` + entry.results
      .map((r) => {
        if (r.error) {
          return `
            <div class="translate-all-row">
              <span class="translate-all-lang">${escapeHtml(r.name)}</span>
              <p class="translate-result translate-error">${escapeHtml(r.error)}</p>
            </div>`;
        }
        return `
          <div class="translate-all-row">
            <span class="translate-all-lang">${escapeHtml(r.name)}</span>
            <p class="translate-result translate-done">${escapeHtml(r.text)}</p>
            <div class="ocr-result-actions">
              <button class="ocr-btn translate-copy-btn" data-id="${attachment.id}" data-lang="${r.code}">Copy</button>
              <button class="ocr-btn translate-insert-btn" data-id="${attachment.id}" data-lang="${r.code}">Insert into note</button>
            </div>
          </div>`;
      })
      .join("");
  } else if (entry?.status === "done") {
    resultHtml = `
      <p class="translate-result translate-done">${escapeHtml(entry.text)}</p>
      <div class="ocr-result-actions">
        <button class="ocr-btn translate-copy-btn" data-id="${attachment.id}" data-lang="${selected}" data-langname="${escapeHtml(customName)}">Copy</button>
        <button class="ocr-btn translate-insert-btn" data-id="${attachment.id}" data-lang="${selected}" data-langname="${escapeHtml(customName)}">Insert into note</button>
      </div>`;
  }

  // Disable Translate on the custom path until a name is actually typed,
  // so there's nothing to send that the backend would just 400 on.
  const goDisabled = isCustom && !customNameTrimmed ? "disabled" : "";

  return `
    <div class="translate-block">
      <div class="translate-controls">
        <span class="translate-label">🌐 Translate to</span>
        <select class="translate-lang-select" data-id="${attachment.id}">${options}</select>
        ${customInputHtml}
        <button class="ocr-btn translate-go-btn" data-id="${attachment.id}" data-lang="${selected}" ${goDisabled}>Translate</button>
      </div>
      ${resultHtml}
    </div>`;
}

el("transcriptResults").addEventListener("click", async (e) => {
  const insertBtn = e.target.closest(".transcript-insert-btn");
  const copyBtn = e.target.closest(".transcript-copy-btn");
  const retryBtn = e.target.closest(".transcript-retry-btn");
  const translateGoBtn = e.target.closest(".translate-go-btn");
  const translateCopyBtn = e.target.closest(".translate-copy-btn");
  const translateInsertBtn = e.target.closest(".translate-insert-btn");
  const translateCopyAllBtn = e.target.closest(".translate-copy-all-btn");

  if (insertBtn) await insertTranscriptText(insertBtn.dataset.id);
  else if (copyBtn) await copyTranscriptText(copyBtn.dataset.id);
  else if (retryBtn) runTranscription(isNaN(Number(retryBtn.dataset.id)) ? retryBtn.dataset.id : Number(retryBtn.dataset.id));
  else if (translateGoBtn) {
    if (translateGoBtn.disabled) return;
    const customName =
      translateGoBtn.dataset.lang === CUSTOM_LANGUAGE_CODE ? state.translateCustomLangName[translateGoBtn.dataset.id] : undefined;
    await runTranslation(translateGoBtn.dataset.id, translateGoBtn.dataset.lang, customName);
  } else if (translateCopyBtn) await copyTranslatedText(translateCopyBtn.dataset.id, translateCopyBtn.dataset.lang, translateCopyBtn.dataset.langname);
  else if (translateInsertBtn) await insertTranslatedText(translateInsertBtn.dataset.id, translateInsertBtn.dataset.lang, translateInsertBtn.dataset.langname);
  else if (translateCopyAllBtn) await copyAllTranslatedText(translateCopyAllBtn.dataset.id);
});

el("transcriptResults").addEventListener("change", (e) => {
  const select = e.target.closest(".translate-lang-select");
  if (!select) return;
  state.translateSelection[select.dataset.id] = select.value;
  renderTranscriptResults();
});

// Day 28 (fix): the custom-language text field updates state on every
// keystroke but deliberately does NOT trigger a re-render — re-rendering
// the whole transcript panel on every keystroke would rebuild the input
// and steal focus/cursor position out from under the person typing.
el("transcriptResults").addEventListener("input", (e) => {
  const input = e.target.closest(".translate-custom-input");
  if (!input) return;
  state.translateCustomLangName[input.dataset.id] = input.value;
  // Still keep the Translate button's disabled state in sync without a
  // full re-render.
  const goBtn = input.closest(".translate-controls")?.querySelector(".translate-go-btn");
  if (goBtn && goBtn.dataset.lang === CUSTOM_LANGUAGE_CODE) {
    goBtn.disabled = !input.value.trim();
  }
});

async function runTranslation(attachmentId, langCode, customName) {
  const attachment = state.noteAttachments.find((a) => String(a.id) === String(attachmentId));
  if (!attachment || !attachment.transcriptText) return;
  if (langCode === CUSTOM_LANGUAGE_CODE && !(customName || "").trim()) {
    showToast("Type a language to translate into first.", "error");
    return;
  }

  const key = translateKey(attachmentId, langCode, customName);
  state.translations[key] = { status: "pending" };
  renderTranscriptResults();

  try {
    const res = await api("/api/ai/translate", {
      method: "POST",
      body: JSON.stringify({
        text: attachment.transcriptText,
        targetLanguage: langCode,
        ...(langCode === CUSTOM_LANGUAGE_CODE ? { targetLanguageName: customName.trim() } : {}),
      }),
    });
    // "all" comes back as {results: [{code, name, text, error}, ...]} —
    // one call_ai() per language on the backend, collected into a single
    // response — vs. a single-language {result: "..."}.
    state.translations[key] = langCode === "all" ? { status: "done", results: res.results } : { status: "done", text: res.result };
  } catch (e) {
    if (e.message === "Not logged in") return;
    state.translations[key] = { status: "error", error: `Translation failed (${e.message}).` };
    showToast(`Translation failed (${e.message}).`, "error");
  }
  renderTranscriptResults();
}

// Looks up translated text for a specific language, whether it was
// translated on its own or as part of an "All Languages" batch — the
// per-row Copy/Insert buttons inside an "all" result always carry the
// individual language's code, not "all", so this covers both origins.
// A "custom" lookup additionally needs the typed language name, since
// that's part of the cache key (see translateKey).
function getTranslatedText(attachmentId, langCode, customName) {
  const single = state.translations[translateKey(attachmentId, langCode, customName)];
  if (single?.status === "done" && single.text) return single.text;

  if (langCode !== CUSTOM_LANGUAGE_CODE) {
    const all = state.translations[`${attachmentId}:all`];
    if (all?.status === "done") {
      const match = all.results?.find((r) => r.code === langCode);
      if (match && match.text) return match.text;
    }
  }
  return null;
}

async function copyTranslatedText(attachmentId, langCode, customName) {
  const text = getTranslatedText(attachmentId, langCode, customName);
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    showToast("Copied to clipboard.");
  } catch (e) {
    showToast("Couldn't copy — your browser blocked clipboard access.", "error");
  }
}

async function copyAllTranslatedText(attachmentId) {
  const entry = state.translations[`${attachmentId}:all`];
  if (!entry || entry.status !== "done") return;
  const combined = entry.results
    .filter((r) => r.text)
    .map((r) => `${r.name}:\n${r.text}`)
    .join("\n\n");
  if (!combined) return;
  try {
    await navigator.clipboard.writeText(combined);
    showToast("Copied all translations to clipboard.");
  } catch (e) {
    showToast("Couldn't copy — your browser blocked clipboard access.", "error");
  }
}

async function insertTranslatedText(attachmentId, langCode, customName) {
  const text = getTranslatedText(attachmentId, langCode, customName);
  const note = currentNote();
  if (!text || !note) return;

  appendPlainTextToEditor(text);
  updateEditorMeta({ ...note, text: getEditorPlainText() });

  clearTimeout(state.saveTimer);
  setAutosaveHint("Saving…", "saving");
  await saveCurrentNote();
  showToast("Inserted into note — AI is processing it now.");
  extractTasks();
}

async function insertTranscriptText(attachmentId) {
  const attachment = state.noteAttachments.find((a) => String(a.id) === String(attachmentId));
  const note = currentNote();
  if (!attachment || !attachment.transcriptText || !note) return;

  appendPlainTextToEditor(attachment.transcriptText);
  updateEditorMeta({ ...note, text: getEditorPlainText() });

  clearTimeout(state.saveTimer);
  setAutosaveHint("Saving…", "saving");
  await saveCurrentNote(); // persists text, then queues the existing auto-summarize/tag/entities/concepts pass
  showToast("Inserted into note — AI is processing it now.");
  extractTasks(); // immediate pass, same Day 13 endpoint the ✨ AI menu's "Extract tasks" button uses
}

async function copyTranscriptText(attachmentId) {
  const attachment = state.noteAttachments.find((a) => String(a.id) === String(attachmentId));
  if (!attachment || !attachment.transcriptText) return;
  try {
    await navigator.clipboard.writeText(attachment.transcriptText);
    showToast("Copied to clipboard.");
  } catch (e) {
    showToast("Couldn't copy — your browser blocked clipboard access.", "error");
  }
}

// ============================================================
// Day 27 — audio notes: recording an attachment (as opposed to
// btnRecordServer above, which transcribes-and-discards; this button
// keeps the actual recording as a saved, playable attachment)
// ============================================================
const audioNoteRecorder = { recorder: null, chunks: [], recording: false, startedAt: 0, timerHandle: null };

el("btnAudioNote").addEventListener("click", () => {
  if (!currentNote()) return;
  if (audioNoteRecorder.recording) {
    stopAudioNoteRecording();
  } else {
    // Don't run three microphone consumers at once.
    if (voice.dictating) voice.recognition.stop();
    if (voice.recording) stopServerRecording();
    startAudioNoteRecording();
  }
});

async function startAudioNoteRecording() {
  const btn = el("btnAudioNote");
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    showToast("Your browser doesn't support audio recording.", "error");
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const recorder = new MediaRecorder(stream);
    audioNoteRecorder.chunks = [];

    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) audioNoteRecorder.chunks.push(e.data);
    };

    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      clearInterval(audioNoteRecorder.timerHandle);
      const durationSeconds = (Date.now() - audioNoteRecorder.startedAt) / 1000;
      const blob = new Blob(audioNoteRecorder.chunks, { type: "audio/webm" });
      if (blob.size === 0) {
        setVoiceStatus("");
        return;
      }
      const filename = `Audio note ${new Date().toLocaleString()}.webm`;
      const file = new File([blob], filename, { type: "audio/webm" });
      uploadAttachment(file, { duration: durationSeconds.toFixed(1) });
      setVoiceStatus("");
    };

    recorder.start();
    audioNoteRecorder.recorder = recorder;
    audioNoteRecorder.recording = true;
    audioNoteRecorder.startedAt = Date.now();
    btn.classList.add("recording");
    updateAudioNoteTimer();
    audioNoteRecorder.timerHandle = setInterval(updateAudioNoteTimer, 500);
  } catch (e) {
    setVoiceStatus(`Couldn't access the microphone (${e.message}).`, "error");
  }
}

function updateAudioNoteTimer() {
  const elapsed = (Date.now() - audioNoteRecorder.startedAt) / 1000;
  setVoiceStatus(`🎙️ Recording audio note — ${formatDuration(elapsed)} — click again to stop and save`, "active");
}

function stopAudioNoteRecording() {
  if (audioNoteRecorder.recorder && audioNoteRecorder.recording) {
    audioNoteRecorder.recorder.stop();
    audioNoteRecorder.recording = false;
    el("btnAudioNote").classList.remove("recording");
    setVoiceStatus("Saving audio note…", "working");
  }
}

function openLightbox(src, alt) {
  el("lightboxImg").src = src;
  el("lightboxImg").alt = alt || "";
  el("lightbox").hidden = false;
}
function closeLightbox() {
  el("lightbox").hidden = true;
  el("lightboxImg").src = "";
}
el("lightboxClose").addEventListener("click", closeLightbox);
el("lightbox").addEventListener("click", (e) => {
  if (e.target.id === "lightbox") closeLightbox();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !el("lightbox").hidden) closeLightbox();
});

// ============================================================
// Day 13 — action item extraction
//
// Two surfaces share the same `tasks` rows:
//   - the task panel inside the editor, scoped to the note that's open
//   - the standalone Tasks view (sidebar nav), spanning every note
// Both just render `{ id, noteId, text, done }` rows and PATCH/DELETE
// against /api/tasks/<id>; extraction itself is a separate POST that
// only the editor's AI menu triggers.
// ============================================================
function taskRowHtml(task, { showNote = false } = {}) {
  const noteBit = showNote
    ? `<span class="task-row-note" data-note-id="${task.noteId}">${escapeHtml(task.noteTitle || "Untitled note")}</span>`
    : "";
  const dueBit = task.due
    ? `<span class="task-row-due">📅 ${escapeHtml(task.due)}</span>`
    : "";
  return `
    <div class="task-row ${task.done ? "done" : ""}" data-task-id="${task.id}">
      <button class="task-check ${task.done ? "checked" : ""}" data-task-id="${task.id}" title="${task.done ? "Mark not done" : "Mark done"}"></button>
      <div class="task-row-main">
        <span class="task-row-text">${escapeHtml(task.text)}</span>
        ${dueBit}
      </div>
      ${noteBit}
      <button class="task-row-del" data-task-id="${task.id}" title="Delete">✕</button>
    </div>`;
}

function wireTaskRowButtons(container, { onToggle, onDelete, onNoteClick } = {}) {
  container.querySelectorAll(".task-check").forEach((btn) => {
    btn.addEventListener("click", () => onToggle && onToggle(btn.dataset.taskId));
  });
  container.querySelectorAll(".task-row-del").forEach((btn) => {
    btn.addEventListener("click", () => onDelete && onDelete(btn.dataset.taskId));
  });
  container.querySelectorAll(".task-row-note").forEach((el) => {
    el.addEventListener("click", () => onNoteClick && onNoteClick(el.dataset.noteId));
  });
}

// ---------- per-note task panel (inside the editor) ----------
async function loadNoteTasks(noteId) {
  try {
    state.noteTasks = await api(`/api/notes/${noteId}/tasks`);
  } catch (e) {
    state.noteTasks = [];
  }
  renderTaskPanel();
}

function renderTaskPanel() {
  const panel = el("taskPanel");
  const list = el("taskPanelList");
  // Kept visible even with zero tasks (rather than hidden like the
  // auto-summary panel) so "+ Add" is always reachable — a task panel
  // with nothing extracted yet is still a place to jot one down by hand.
  panel.hidden = false;
  if (!state.noteTasks.length) {
    list.innerHTML = `<p class="task-panel-empty">No action items yet. Click ✨ AI assist → "Extract tasks", or add one by hand.</p>`;
    return;
  }
  list.innerHTML = state.noteTasks
    .slice()
    .sort((a, b) => a.done - b.done || new Date(a.createdAt) - new Date(b.createdAt))
    .map((t) => taskRowHtml(t))
    .join("");
  wireTaskRowButtons(list, {
    onToggle: (id) => toggleTask(id, "panel"),
    onDelete: (id) => deleteTask(id, "panel"),
  });
}

async function toggleTask(taskId, source) {
  const pool = source === "panel" ? state.noteTasks : state.allTasks;
  const task = pool.find((t) => String(t.id) === String(taskId));
  if (!task) return;
  try {
    const updated = await api(`/api/tasks/${taskId}`, {
      method: "PATCH",
      body: JSON.stringify({ done: !task.done }),
    });
    Object.assign(task, updated);
    if (source === "panel") renderTaskPanel();
    else renderTasksView();
    updateTasksBadge();
  } catch (e) {
    if (e.message === "Not logged in") return;
    showError(`Couldn't update that task (${e.message}).`);
  }
}

async function deleteTask(taskId, source) {
  try {
    await api(`/api/tasks/${taskId}`, { method: "DELETE" });
    state.noteTasks = state.noteTasks.filter((t) => String(t.id) !== String(taskId));
    state.allTasks = state.allTasks.filter((t) => String(t.id) !== String(taskId));
    if (source === "panel") renderTaskPanel();
    else renderTasksView();
    updateTasksBadge();
  } catch (e) {
    if (e.message === "Not logged in") return;
    showError(`Couldn't delete that task (${e.message}).`);
  }
}

async function addManualTask() {
  const note = currentNote();
  if (!note) return;
  const text = (prompt("Add a task:") || "").trim();
  if (!text) return;
  const due = (prompt("Due date (optional — leave blank for none):") || "").trim() || null;
  try {
    const task = await api("/api/tasks", {
      method: "POST",
      body: JSON.stringify({ noteId: note.id, text, due }),
    });
    state.noteTasks.push(task);
    renderTaskPanel();
    updateTasksBadge();
  } catch (e) {
    if (e.message === "Not logged in") return;
    showError(`Couldn't add that task (${e.message}).`);
  }
}

async function extractTasks() {
  const note = currentNote();
  if (!note) return;
  const text = getEditorPlainText().trim();

  try {
    // Extraction reads from what's saved, so make sure the latest text
    // is persisted first (in case it hasn't autosaved yet).
    await saveCurrentNote();
    const res = await api(`/api/notes/${note.id}/extract-tasks`, { method: "POST" });
    state.noteTasks = res.tasks;
    renderTaskPanel();
    updateTasksBadge();
    setVoiceStatus("");
    showToast(res.added > 0 ? `Added ${res.added} task${res.added > 1 ? "s" : ""}` : "No new action items found", "success");
  } catch (e) {
    if (e.message === "Not logged in") return;
    setVoiceStatus(`Task extraction failed (${e.message}).`, "ai-error");
    showToast(`Task extraction failed: ${e.message}`, "error");
  } finally {
    aiBtn.classList.remove("working");
    setButtonLoading(aiBtn, false);
  }
}

// ---------- full-page Tasks view (sidebar nav) ----------
async function loadTasksView() {
  try {
    const params = new URLSearchParams();
    if (state.taskFilter !== "all") params.set("done", state.taskFilter === "done" ? "1" : "0");
    if (state.taskSearch.trim()) params.set("q", state.taskSearch.trim());
    const qs = params.toString();
    state.allTasks = await api(`/api/tasks${qs ? `?${qs}` : ""}`);
    clearError();
  } catch (e) {
    if (e.message === "Not logged in") return;
    state.allTasks = [];
    showError(`Couldn't load tasks (${e.message}).`, loadTasksView);
  }
  // A fresh search/filter always starts from the top of the (new) result
  // set — otherwise "Load more"'s progress from a previous, larger list
  // would carry over and make the first render look like it's missing rows.
  state.flatTasksVisible = state.flatTasksPageSize;
  renderTasksView();
}

function renderTasksView() {
  const list = el("tasksList");
  const empty = el("tasksEmptyState");
  const noResults = el("tasksNoSearchResults");
  const groupControls = el("tasksGroupControls");
  const loadMoreRow = el("tasksLoadMoreRow");
  const total = state.allTasks.length;
  const searching = !!state.taskSearch.trim();

  el("tasksCount").textContent = total ? `${total} task${total > 1 ? "s" : ""}` : "";
  el("tasksSearchClear").hidden = !searching;

  if (!total) {
    list.innerHTML = "";
    groupControls.hidden = true;
    loadMoreRow.hidden = true;
    if (searching) {
      empty.hidden = true;
      noResults.hidden = false;
      el("tasksNoResultsQuery").textContent = state.taskSearch.trim();
    } else {
      noResults.hidden = true;
      empty.hidden = false;
      empty.querySelector(".empty-body").textContent =
        state.taskFilter === "done"
          ? "Nothing marked done yet."
          : 'Open a note, click ✨ AI assist, then "Extract tasks" to pull them out automatically.';
    }
    return;
  }
  empty.hidden = true;
  noResults.hidden = true;

  if (state.taskViewMode === "grouped") {
    loadMoreRow.hidden = true;
    groupControls.hidden = false;
    renderGroupedTasks(list);
  } else {
    groupControls.hidden = true;
    renderFlatTasks(list, loadMoreRow);
  }
}

function renderFlatTasks(list, loadMoreRow) {
  const visible = state.allTasks.slice(0, state.flatTasksVisible);
  list.innerHTML = visible.map((t) => taskRowHtml(t, { showNote: true })).join("");
  wireTaskRowButtons(list, {
    onToggle: (id) => toggleTask(id, "view"),
    onDelete: (id) => deleteTask(id, "view"),
    onNoteClick: (noteId) => {
      switchNav("notes");
      openNote(noteId);
    },
  });

  const remaining = state.allTasks.length - visible.length;
  if (remaining > 0) {
    loadMoreRow.hidden = false;
    el("btnLoadMoreTasks").textContent = `Load ${Math.min(remaining, state.flatTasksPageSize)} more`;
    el("tasksLoadMoreHint").textContent = `Showing ${visible.length} of ${state.allTasks.length}`;
  } else {
    loadMoreRow.hidden = true;
  }
}

function renderGroupedTasks(list) {
  // Group the already-filtered flat array by note, without losing the
  // server's ordering within each group (open tasks first, newest first).
  const groups = new Map(); // noteId -> { noteId, noteTitle, tasks: [] }
  for (const t of state.allTasks) {
    const key = String(t.noteId);
    if (!groups.has(key)) groups.set(key, { noteId: t.noteId, noteTitle: t.noteTitle || "Untitled note", tasks: [] });
    groups.get(key).tasks.push(t);
  }
  // Notes with the most recently-touched task bubble to the top, so
  // whatever you were just working on is the group you land on.
  const sections = [...groups.values()].sort((a, b) => {
    const aLatest = Math.max(...a.tasks.map((t) => new Date(t.updatedAt).getTime()));
    const bLatest = Math.max(...b.tasks.map((t) => new Date(t.updatedAt).getTime()));
    return bLatest - aLatest;
  });

  list.innerHTML = sections.map((section) => taskGroupHtml(section)).join("");

  list.querySelectorAll(".task-group-header").forEach((header) => {
    header.addEventListener("click", () => {
      const noteId = header.dataset.noteId;
      if (state.collapsedTaskGroups.has(noteId)) state.collapsedTaskGroups.delete(noteId);
      else state.collapsedTaskGroups.add(noteId);
      renderTasksView();
    });
  });
  list.querySelectorAll(".task-group-show-more").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      state.expandedTaskGroups.add(btn.dataset.noteId);
      renderTasksView();
    });
  });
  wireTaskRowButtons(list, {
    onToggle: (id) => toggleTask(id, "view"),
    onDelete: (id) => deleteTask(id, "view"),
  });
  list.querySelectorAll(".task-group-title").forEach((titleEl) => {
    titleEl.addEventListener("click", (e) => {
      e.stopPropagation();
      switchNav("notes");
      openNote(titleEl.dataset.noteId);
    });
  });
}

function taskGroupHtml(section) {
  const key = String(section.noteId);
  const collapsed = state.collapsedTaskGroups.has(key);
  const expanded = state.expandedTaskGroups.has(key);
  const openCount = section.tasks.filter((t) => !t.done).length;
  const countLabel = openCount === 0 ? `${section.tasks.length} done` : `${openCount} open`;
  const visibleTasks = expanded ? section.tasks : section.tasks.slice(0, state.taskGroupPageSize);
  const remaining = section.tasks.length - visibleTasks.length;

  const rows = visibleTasks.map((t) => taskRowHtml(t, { showNote: false })).join("");
  const showMoreBtn =
    remaining > 0
      ? `<button class="task-group-show-more" data-note-id="${key}">Show ${remaining} more</button>`
      : "";

  return `
    <div class="task-group ${collapsed ? "collapsed" : ""}" data-note-id="${key}">
      <button class="task-group-header" data-note-id="${key}">
        <span class="task-group-chevron">▾</span>
        <span class="task-group-title" data-note-id="${key}" title="Open this note">${escapeHtml(section.noteTitle)}</span>
        <span class="task-group-count ${openCount === 0 ? "all-done" : ""}">${countLabel}</span>
      </button>
      <div class="task-group-body">
        ${rows}
        ${showMoreBtn}
      </div>
    </div>`;
}

async function updateTasksBadge() {
  try {
    const open = await api("/api/tasks?done=0");
    const badge = el("tasksBadge");
    if (open.length) {
      badge.hidden = false;
      badge.textContent = open.length;
    } else {
      badge.hidden = true;
    }
  } catch (e) {
    /* not logged in yet, or offline — leave the badge as-is */
  }
}

// ---------- sidebar nav: Dashboard vs Notes vs Tasks vs Graph ----------
function switchNav(name) {
  state.nav = name;
  document.querySelectorAll(".sidebar-nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.nav === name));
  el("notesFilterRow").hidden = name !== "notes";
  el("sidebarTags").hidden = name !== "notes" || !state.allTags.length;
  if (name === "tasks") {
    showView("tasks");
    loadTasksView();
  } else if (name === "graph") {
    showView("graph");
    loadGraphView();
  } else if (name === "study") {
    showView("study");
    loadStudyView();
  } else if (name === "profile") {
    showView("profile");
    loadProfileView();
  } else if (name === "dashboard") {
    showView("dashboard");
    // The Dashboard counts state.notes, so a leftover search would make
    // "Total notes" show the number of *matches*. Drop the search (the
    // other filters are notes-page-only concerns) and reload first.
    (async () => {
      if (state.query) await clearSearch();
      loadDashboard();
    })();
  } else {
    showView("notes");
  }
  closeMobileSidebar();
}

el("navDashboard").addEventListener("click", () => switchNav("dashboard"));
el("navNotes").addEventListener("click", () => switchNav("notes"));
el("navTasks").addEventListener("click", () => switchNav("tasks"));
el("navGraph").addEventListener("click", () => switchNav("graph"));
el("navStudy").addEventListener("click", () => switchNav("study"));
el("qaStudy").addEventListener("click", () => switchNav("study"));
// No sidebar entry for Profile — the round avatar button (top right) is the
// only way in.
el("topnavAvatarBtn").addEventListener("click", () => switchNav("profile"));

// ---------- Day 23 — dashboard ----------
async function loadDashboard() {
  el("statNotes").textContent = state.notes.length;

  // Tasks: reuse whatever's already loaded if we have it, otherwise fetch
  // fresh — the dashboard is often the very first thing rendered on boot,
  // before the Tasks view has ever loaded state.allTasks.
  try {
    const openTasks = await api("/api/tasks?done=0");
    el("statTasks").textContent = openTasks.length;
  } catch (e) {
    el("statTasks").textContent = "–";
  }

  el("statTags").textContent = state.allTags.length;

  try {
    const res = await api("/api/attachments/count");
    el("statAttachments").textContent = res.count;
  } catch (e) {
    el("statAttachments").textContent = "–";
  }

  const total = state.notes.length;
  el("dashboardSubhead").textContent = total ? `${total} note${total === 1 ? "" : "s"} total` : "";

  const recent = state.notes
    .slice()
    .sort((a, b) => new Date(b.updatedAt) - new Date(a.updatedAt))
    .slice(0, 5);

  const list = el("dashboardRecent");
  const empty = el("dashboardEmptyState");
  if (!recent.length) {
    list.innerHTML = "";
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  list.innerHTML = recent
    .map(
      (n) => `
      <div class="dashboard-recent-row" data-id="${n.id}">
        ${n.pinned ? `<span class="dashboard-recent-pin">📌</span>` : ""}
        <span class="dashboard-recent-title">${escapeHtml(n.title || "Untitled note")}</span>
        <span class="dashboard-recent-meta">${timeAgo(n.updatedAt)}</span>
      </div>`
    )
    .join("");
  list.querySelectorAll(".dashboard-recent-row").forEach((row) => {
    row.addEventListener("click", () => openNote(row.dataset.id));
  });
}

el("qaNewNote").addEventListener("click", openNewNote);
el("qaTasks").addEventListener("click", () => switchNav("tasks"));
el("qaGraph").addEventListener("click", () => switchNav("graph"));
el("qaPinned").addEventListener("click", () => {
  switchNav("notes");
  state.filter = "pinned";
  document.querySelectorAll("#notesFilterRow .filter-btn").forEach((b) => b.classList.toggle("active", b.dataset.filter === "pinned"));
  updateClearFiltersButton();
  loadAndRender();
});

// ---------- Day 23 — mobile drawer sidebar ----------
function openMobileSidebar() {
  document.querySelector(".sidebar").classList.add("open");
  el("sidebarOverlay").hidden = false;
}
function closeMobileSidebar() {
  document.querySelector(".sidebar").classList.remove("open");
  el("sidebarOverlay").hidden = true;
}
el("btnMenuToggle").addEventListener("click", () => {
  const sidebar = document.querySelector(".sidebar");
  if (sidebar.classList.contains("open")) closeMobileSidebar();
  else openMobileSidebar();
});
el("sidebarOverlay").addEventListener("click", closeMobileSidebar);

document.querySelectorAll("#view-tasks .filter-btn[data-taskfilter]").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#view-tasks .filter-btn[data-taskfilter]").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.taskFilter = btn.dataset.taskfilter;
    loadTasksView();
  });
});

el("btnAddTask").addEventListener("click", addManualTask);

// ---------- Day 34: Tasks view search, grouping, and pagination controls ----------
function setTasksSearch(value) {
  state.taskSearch = value;
  el("tasksSearchInput").value = value;
  el("tasksSearchClear").hidden = !value.trim();
  loadTasksView();
}

el("tasksSearchInput").addEventListener("input", (e) => {
  const value = e.target.value;
  el("tasksSearchClear").hidden = !value.trim();
  clearTimeout(state.taskSearchTimer);
  // Debounced like Day 12's auto-summary trigger, but much shorter —
  // this hits a plain indexed-ish ILIKE, not an AI call, so it can
  // afford to feel closer to instant while still not firing on every
  // single keystroke of a fast typist.
  state.taskSearchTimer = setTimeout(() => {
    state.taskSearch = value;
    loadTasksView();
  }, 250);
});
el("tasksSearchClear").addEventListener("click", () => setTasksSearch(""));
el("btnClearTasksSearchInline").addEventListener("click", () => setTasksSearch(""));

document.querySelectorAll(".view-toggle-btn[data-taskview]").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.dataset.taskview === state.taskViewMode) return;
    document.querySelectorAll(".view-toggle-btn[data-taskview]").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.taskViewMode = btn.dataset.taskview;
    state.flatTasksVisible = state.flatTasksPageSize;
    renderTasksView();
  });
});

el("btnExpandAllGroups").addEventListener("click", () => {
  state.collapsedTaskGroups.clear();
  renderTasksView();
});
el("btnCollapseAllGroups").addEventListener("click", () => {
  const noteIds = new Set(state.allTasks.map((t) => String(t.noteId)));
  state.collapsedTaskGroups = noteIds;
  renderTasksView();
});
el("btnLoadMoreTasks").addEventListener("click", () => {
  state.flatTasksVisible += state.flatTasksPageSize;
  renderTasksView();
});

async function deleteNote(id, opts = {}) {
  const note = state.notes.find((n) => String(n.id) === String(id));
  if (!note) return;
  const label = note.title || "this note";
  const ok = await confirmDialog(`Delete "${label}"? This can't be undone.`, { title: "Delete note?" });
  if (!ok) return;
  try {
    await api(`/api/notes/${note.id}`, { method: "DELETE" });
    state.notes = state.notes.filter((n) => n.id !== note.id);
    clearError();
    showToast("Note deleted", "success");
    if (!opts.fromGrid) showView("notes");
    renderNotesGrid();
  } catch (e) {
    if (e.message === "Not logged in") return;
    showError(`Couldn't delete the note (${e.message}).`);
    showToast(`Couldn't delete the note (${e.message}).`, "error");
  }
}

async function togglePin() {
  const note = currentNote();
  if (!note) return;
  try {
    const updated = await api(`/api/notes/${note.id}/pin`, { method: "PATCH" });
    Object.assign(note, updated);
    updatePinButton(note.pinned);
    clearError();
    showToast(note.pinned ? "Pinned" : "Unpinned", "success");
  } catch (e) {
    if (e.message === "Not logged in") return;
    showError(`Couldn't update pin status (${e.message}).`);
    showToast(`Couldn't update pin status (${e.message}).`, "error");
  }
}

function showToast(msg, type = "success") {
  const t = el("toast");
  t.textContent = msg;
  t.className = "toast " + type;
  t.hidden = false;
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => (t.hidden = true), 2200);
}

// ============================================================
// Day 24 — confirmation modal (replaces native confirm() for
// destructive actions so it matches the rest of the app's design
// and can name exactly what's being deleted)
// ============================================================
function confirmDialog(message, { title = "Delete this?", confirmLabel = "Delete" } = {}) {
  return new Promise((resolve) => {
    const overlay = el("confirmOverlay");
    el("confirmTitle").textContent = title;
    el("confirmMessage").textContent = message;
    const okBtn = el("confirmOkBtn");
    okBtn.textContent = confirmLabel;
    overlay.hidden = false;

    const cleanup = (result) => {
      overlay.hidden = true;
      okBtn.removeEventListener("click", onOk);
      cancelBtn.removeEventListener("click", onCancel);
      overlay.removeEventListener("click", onOverlay);
      document.removeEventListener("keydown", onKey);
      resolve(result);
    };
    const cancelBtn = el("confirmCancelBtn");
    const onOk = () => cleanup(true);
    const onCancel = () => cleanup(false);
    const onOverlay = (e) => { if (e.target === overlay) cleanup(false); };
    const onKey = (e) => { if (e.key === "Escape") cleanup(false); };

    okBtn.addEventListener("click", onOk);
    cancelBtn.addEventListener("click", onCancel);
    overlay.addEventListener("click", onOverlay);
    document.addEventListener("keydown", onKey);
    okBtn.focus();
  });
}

// Day 24 — swap a button's contents for a spinner while an async action
// runs, then restore it. Used for anything that hits the AI backend so
// "processing" is visible on the control itself, not just in a status
// line elsewhere on the page.
function setButtonLoading(btn, loading, loadingLabel) {
  if (loading) {
    if (btn.dataset.restoreHtml === undefined) btn.dataset.restoreHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span>${loadingLabel ? `<span class="btn-label">${escapeHtml(loadingLabel)}</span>` : ""}`;
  } else {
    if (btn.dataset.restoreHtml !== undefined) {
      btn.innerHTML = btn.dataset.restoreHtml;
      delete btn.dataset.restoreHtml;
    }
    btn.disabled = false;
  }
}

// ============================================================
// Search — by note NAME
//
//  • Type in the sidebar box: a dropdown lists notes whose *title* matches
//    (partial words, any capitalisation, any word order, small typos).
//  • Click a result, or ↑/↓ + Enter, to open that note straight away.
//  • Enter with nothing highlighted (or "See all results") shows every match
//    on the Notes page. While you're already on the Notes page, the grid
//    filters live as you type.
//  • "Search inside note text" runs the older hybrid keyword+semantic search
//    over the note bodies, for when the name isn't what you remember.
//
// The dropdown's results are kept in `searchUi`, NOT in state.notes, so just
// typing never disturbs the Dashboard counts or a note that's open in the
// editor. state.notes is only replaced when the results page is showing.
// ============================================================
const SEARCH_DEBOUNCE_MS = 200;
const SUGGEST_MAX = 6;
const searchUi = { timer: null, seq: 0, results: [], query: "", activeIndex: -1 };

function isEditorOpen() {
  return !views.editor.hidden;
}

// Shows/hides the ✕ button and keeps the "clear all filters" button honest.
function syncSearchUi() {
  el("searchClear").hidden = !el("searchInput").value;
  if (!el("searchInput").value) closeSuggest();
  updateClearFiltersButton();
}

function closeSuggest() {
  el("searchSuggest").hidden = true;
  el("searchInput").setAttribute("aria-expanded", "false");
  searchUi.activeIndex = -1;
}

function suggestItems() {
  return Array.from(el("searchSuggest").querySelectorAll(".suggest-item"));
}

function setActiveSuggest(index) {
  const items = suggestItems();
  if (!items.length) return;
  searchUi.activeIndex = (index + items.length) % items.length;
  items.forEach((it, i) => it.classList.toggle("active", i === searchUi.activeIndex));
  items[searchUi.activeIndex].scrollIntoView({ block: "nearest" });
}

function renderSuggest() {
  const box = el("searchSuggest");
  const q = searchUi.query;
  if (!q) {
    closeSuggest();
    return;
  }
  const shown = searchUi.results.slice(0, SUGGEST_MAX);
  const more = searchUi.results.length - shown.length;
  let html = "";

  if (shown.length) {
    html += `<div class="suggest-label">Notes named…</div>`;
    html += shown
      .map(
        (n, i) => `
      <button type="button" class="suggest-item" role="option" data-kind="note" data-id="${n.id}" data-index="${i}">
        <span class="suggest-item-icon">${n.pinned ? "📌" : "📝"}</span>
        <span class="suggest-item-title">${highlight(n.title || "Untitled note", q)}</span>
        <span class="suggest-item-meta">${timeAgo(n.updatedAt)}</span>
      </button>`
      )
      .join("");
    html += `<div class="suggest-divider"></div>`;
    html += `<button type="button" class="suggest-item suggest-action" data-kind="all">
        <span class="suggest-item-icon">↵</span>
        <span class="suggest-item-title">See all ${searchUi.results.length} result${searchUi.results.length === 1 ? "" : "s"}${more > 0 ? ` (${more} more)` : ""}</span>
      </button>`;
  } else {
    html += `<div class="suggest-empty">No note has “${escapeHtml(q)}” in its name.</div>
      <div class="suggest-divider"></div>`;
  }
  html += `<button type="button" class="suggest-item suggest-action" data-kind="content">
      <span class="suggest-item-icon">🔎</span>
      <span class="suggest-item-title">Search inside note text for “${escapeHtml(q)}”</span>
    </button>`;

  box.innerHTML = html;
  box.hidden = false;
  el("searchInput").setAttribute("aria-expanded", "true");
  searchUi.activeIndex = -1;
}

// Runs the name search for whatever's in the box right now.
async function runSearchSuggestions() {
  const q = el("searchInput").value.trim();
  const seq = ++searchUi.seq;
  if (!q) {
    searchUi.results = [];
    searchUi.query = "";
    closeSuggest();
    if (state.query) await clearSearch();
    return;
  }
  let results;
  try {
    results = await api(`/api/search?mode=title&q=${encodeURIComponent(q)}`);
  } catch (e) {
    if (e.message === "Not logged in") return;
    results = [];
  }
  if (seq !== searchUi.seq) return; // the user kept typing — a newer request owns the UI now
  searchUi.results = results;
  searchUi.query = q;
  renderSuggest();

  // Already looking at the Notes page? Filter the grid live too — through
  // the normal loader, so the sidebar's Pinned / Priority / Date / Tag
  // filters still apply to it (the dropdown above deliberately ignores them:
  // it's for finding *any* note by name).
  if (state.nav === "notes" && !isEditorOpen()) {
    state.query = q;
    state.searchMode = "title";
    updateClearFiltersButton();
    await loadAndRender();
  }
}

// Show every match on the Notes page.
async function commitSearch(mode = "title") {
  const q = el("searchInput").value.trim();
  clearTimeout(searchUi.timer);
  searchUi.seq++; // drop any suggestion request still in flight
  closeSuggest();
  if (!q) {
    await clearSearch();
    return;
  }
  await leaveEditorIfOpen();
  state.query = q;
  state.searchMode = mode;
  switchNav("notes"); // also closes the mobile drawer so the results are actually visible
  updateClearFiltersButton();
  await loadAndRender();
}

// state.notes is about to be replaced by search results. Save what's being
// typed in the editor first and detach it, so the open note can't end up
// "missing" (and silently un-saveable) because it isn't among the matches.
async function leaveEditorIfOpen() {
  if (!isEditorOpen()) return;
  if (voice.dictating && voice.recognition) voice.recognition.stop();
  if (voice.recording) stopServerRecording();
  clearTimeout(state.saveTimer);
  clearTimeout(state.summaryTimer);
  await saveCurrentNote();
  state.editingId = null;
}

async function clearSearch() {
  clearTimeout(searchUi.timer);
  searchUi.seq++;
  searchUi.results = [];
  searchUi.query = "";
  el("searchInput").value = "";
  state.query = "";
  state.searchMode = "title";
  syncSearchUi();
  await loadAndRender();
}

async function openNoteFromSearch(id) {
  const note = searchUi.results.find((n) => String(n.id) === String(id));
  if (!note) return;
  clearTimeout(searchUi.timer);
  searchUi.seq++;
  closeSuggest();
  await leaveEditorIfOpen();
  // openNote() looks the note up in state.notes, which may not contain it
  // (e.g. we're on the Dashboard, or a different search is showing).
  if (!state.notes.some((n) => String(n.id) === String(id))) state.notes.push(note);
  // Opened from the dropdown alone (no results page behind it): empty the box
  // so it doesn't claim a search is active when "Back" shows every note.
  if (!state.query) {
    el("searchInput").value = "";
    searchUi.results = [];
    searchUi.query = "";
    syncSearchUi();
  }
  openNote(id);
}

el("searchInput").addEventListener("input", () => {
  syncSearchUi();
  clearTimeout(searchUi.timer);
  searchUi.timer = setTimeout(runSearchSuggestions, SEARCH_DEBOUNCE_MS);
});

el("searchInput").addEventListener("focus", () => {
  // Coming back to a box that still has text: bring the results back.
  if (el("searchInput").value.trim() && searchUi.query) renderSuggest();
});

el("searchInput").addEventListener("keydown", (e) => {
  const open = !el("searchSuggest").hidden;
  if (e.key === "ArrowDown" && open) {
    e.preventDefault();
    setActiveSuggest(searchUi.activeIndex + 1);
  } else if (e.key === "ArrowUp" && open) {
    e.preventDefault();
    setActiveSuggest(searchUi.activeIndex - 1);
  } else if (e.key === "Enter") {
    e.preventDefault();
    const active = open && searchUi.activeIndex >= 0 ? suggestItems()[searchUi.activeIndex] : null;
    if (active) active.click();
    else commitSearch("title");
  } else if (e.key === "Escape") {
    if (open) closeSuggest();
    else if (el("searchInput").value) clearSearch();
  }
});

el("searchSuggest").addEventListener("click", (e) => {
  const item = e.target.closest(".suggest-item");
  if (!item) return;
  if (item.dataset.kind === "note") openNoteFromSearch(item.dataset.id);
  else if (item.dataset.kind === "content") commitSearch("content");
  else commitSearch("title");
});

// Keep focus in the box when clicking inside the dropdown, so it doesn't
// blur/close before the click registers.
el("searchSuggest").addEventListener("mousedown", (e) => e.preventDefault());

el("searchClear").addEventListener("click", () => {
  clearSearch();
  el("searchInput").focus();
});

document.addEventListener("click", (e) => {
  if (!el("searchWrap").contains(e.target)) closeSuggest();
});

el("btnSearchContentInstead").addEventListener("click", () => commitSearch("content"));

// ============================================================
// Filter tabs
// ============================================================
document.querySelectorAll("#notesFilterRow .filter-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#notesFilterRow .filter-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.filter = btn.dataset.filter;
    updateClearFiltersButton();
    loadAndRender();
  });
});

// ============================================================
// Live word count + auto-save while typing
// ============================================================
el("editorText").addEventListener("input", () => {
  const note = currentNote();
  if (note) {
    const wc = wordCount(getEditorPlainText());
    el("editorMeta").textContent = `${wc} word${wc === 1 ? "" : "s"} · created ${fullTimestamp(note.createdAt)}`;
  }
  scheduleAutosave();
  // Day 31: typing (new lines, formula insertion shifting the caret,
  // etc.) can move the caret without a keyup/mouseup of its own firing
  // first — keep the floating toolbar following it either way.
  if (!el("formatToolbar").hidden) positionFloatingToolbar();
});
el("titleInput").addEventListener("input", scheduleAutosave);

// ============================================================
// Nav / button wiring
// ============================================================
el("btnNewNote").addEventListener("click", openNewNote);

el("btnBack").addEventListener("click", async () => {
  if (voice.dictating) voice.recognition.stop();
  if (voice.recording) stopServerRecording();
  clearTimeout(state.summaryTimer);
  await saveCurrentNote();
  await loadAndRender();
  showView("notes");
});

el("btnPin").addEventListener("click", togglePin);
el("btnDeleteNote").addEventListener("click", () => deleteNote(state.editingId));
el("btnLogout").addEventListener("click", logout);

// ============================================================
// Voice input (Day 9)
// Two independent paths — either can be used on its own:
//  1. Live dictation: the browser's built-in SpeechRecognition.
//     Zero setup, works instantly in Chrome/Edge, nothing sent
//     to our backend at all.
//  2. Record & transcribe: records audio with MediaRecorder and
//     sends it to POST /api/voice/transcribe, which runs it
//     through a local (self-hosted) Whisper model on the Flask
//     server. Only shown if the backend reports it's installed.
// ============================================================
const voice = {
  recognition: null,
  dictating: false,
  recorder: null,
  recordedChunks: [],
  recording: false,
};

function setVoiceStatus(text, mode) {
  const box = el("voiceStatus");
  if (!text) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.textContent = text;
  box.classList.remove("active", "working", "error");
  if (mode) box.classList.add(mode);
}

// ---------- 1. Live browser dictation ----------
function setupDictation() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const btn = el("btnDictate");

  if (!SpeechRecognition) {
    btn.disabled = true;
    btn.title = "Live dictation needs Chrome or Edge";
    return;
  }

  const recognition = new SpeechRecognition();
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.lang = navigator.language || "en-US";

  let baseText = "";

  recognition.onstart = () => {
    voice.dictating = true;
    btn.classList.add("recording");
    // Day 30: dictation flattens whatever's in the editor to plain text
    // at this exact moment (any rich formatting from here down gets
    // rebuilt as plain lines as speech comes in) — this isn't a
    // regression from the old plain-textarea behavior, which worked the
    // exact same way; dictation has always meant "the editor's content
    // becomes exactly this spoken text" for as long as it's active.
    baseText = getEditorPlainText();
    setVoiceStatus("Listening… click the mic again to stop", "active");
  };

  recognition.onresult = (event) => {
    let finalTranscript = "";
    let interimTranscript = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const chunk = event.results[i][0].transcript;
      if (event.results[i].isFinal) finalTranscript += chunk;
      else interimTranscript += chunk;
    }
    if (finalTranscript) {
      baseText = (baseText + " " + finalTranscript).trim();
    }
    setEditorPlainText((baseText + " " + interimTranscript).trim());
    if (finalTranscript) scheduleAutosave();
  };

  recognition.onerror = (event) => {
    setVoiceStatus(`Dictation error: ${event.error}`, "error");
  };

  recognition.onend = () => {
    voice.dictating = false;
    btn.classList.remove("recording");
    setVoiceStatus("");
    scheduleAutosave();
  };

  voice.recognition = recognition;

  btn.addEventListener("click", () => {
    if (voice.dictating) {
      recognition.stop();
    } else {
      if (voice.recording) stopServerRecording(); // don't run both at once
      try {
        recognition.start();
      } catch (e) {
        // start() throws if already started; safe to ignore
      }
    }
  });
}

// ---------- 2. Server-side (Whisper) recording ----------
async function checkServerTranscriptionAvailable() {
  try {
    const res = await api("/api/voice/status");
    state.transcriptAvailable = !!res.available;
    state.transcriptReason = res.reason || null;
    if (res.available) {
      el("btnRecordServer").hidden = false;
    }
  } catch (e) {
    // Not available / not logged in yet — leave the button hidden, and
    // leave transcriptAvailable at null (unknown) rather than assuming
    // false — the first real transcribe attempt will find out for real.
  }
}

function setupServerRecording() {
  const btn = el("btnRecordServer");
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    btn.hidden = true;
    return;
  }

  btn.addEventListener("click", () => {
    if (voice.recording) {
      stopServerRecording();
    } else {
      if (voice.dictating) voice.recognition.stop(); // don't run both at once
      startServerRecording();
    }
  });
}

async function startServerRecording() {
  const btn = el("btnRecordServer");
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const recorder = new MediaRecorder(stream);
    voice.recordedChunks = [];

    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) voice.recordedChunks.push(e.data);
    };

    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(voice.recordedChunks, { type: "audio/webm" });
      await sendRecordingForTranscription(blob);
    };

    recorder.start();
    voice.recorder = recorder;
    voice.recording = true;
    btn.classList.add("recording");
    setVoiceStatus("Recording… click again to stop and transcribe", "active");
  } catch (e) {
    setVoiceStatus(`Couldn't access the microphone (${e.message}).`, "error");
  }
}

function stopServerRecording() {
  if (voice.recorder && voice.recording) {
    voice.recorder.stop();
    voice.recording = false;
    el("btnRecordServer").classList.remove("recording");
  }
}

async function sendRecordingForTranscription(blob) {
  setVoiceStatus("Transcribing…", "working");
  try {
    const formData = new FormData();
    formData.append("audio", blob, "recording.webm");

    const res = await fetch(API_BASE + "/api/voice/transcribe", {
      method: "POST",
      credentials: "same-origin",
      body: formData,
    });
    const body = await res.json().catch(() => ({}));

    if (res.status === 401) {
      window.location.href = "login.html";
      return;
    }
    if (!res.ok) {
      setVoiceStatus(body.error || "Transcription failed.", "error");
      return;
    }

    const transcribed = (body.text || "").trim();
    if (transcribed) {
      appendPlainTextToEditor(transcribed);
      scheduleAutosave();
    }
    setVoiceStatus("");
  } catch (e) {
    setVoiceStatus(`Couldn't reach the server (${e.message}).`, "error");
  }
}

setupDictation();
setupServerRecording();

// ============================================================
// AI assist (Day 11)
// Checks /api/ai/status on load; the button only appears if the
// backend reports a model is actually connected (AI_PROVIDER +
// matching API key set in .env). Clicking it opens a small menu —
// Summarize / Polish grammar / Suggest title — each of which sends
// the current note text to POST /api/ai/assist and applies the
// result to the editor.
// ============================================================
async function checkAiAvailable() {
  try {
    const res = await api("/api/ai/status");
    state.aiAvailable = !!res.available;
    if (res.available) {
      el("btnAi").hidden = false;
      el("btnOpenStudy").hidden = false;
      loadTranslateLanguages();
    }
  } catch (e) {
    // Not available / not logged in yet — leave the button hidden.
  }
}

// Day 28 — fetched once and cached; the translate dropdown on every
// audio attachment's transcript reads from this same list rather than
// each one re-fetching it.
async function loadTranslateLanguages() {
  if (state.translateLanguages.length) return;
  try {
    const res = await api("/api/ai/languages");
    state.translateLanguages = res.languages;
    if (state.noteAttachments.some((a) => a.kind === "audio")) renderTranscriptResults();
  } catch (e) {
    // Leave it empty — the translate control just won't render.
  }
}

function toggleAiMenu(force) {
  const menu = el("aiMenu");
  menu.hidden = force === undefined ? !menu.hidden : !force;
}

el("btnAi").addEventListener("click", (e) => {
  e.stopPropagation();
  toggleAiMenu();
});
document.addEventListener("click", () => toggleAiMenu(false));

el("aiMenu").querySelectorAll("button[data-action]").forEach((btn) => {
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleAiMenu(false);
    if (btn.dataset.action === "tasks") {
      extractTasks();
    } else {
      runAiAssist(btn.dataset.action);
    }
  });
});

async function runAiAssist(action) {
  const note = currentNote();
  if (!note) return;
  const text = getEditorPlainText().trim();
  setVoiceStatus(labels[action] || "Thinking…", "working");

  try {
    const res = await api("/api/ai/assist", {
      method: "POST",
      body: JSON.stringify({ text, action }),
    });

    if (action === "title") {
      el("titleInput").value = res.result;
    } else if (action === "polish") {
      setEditorPlainText(res.result);
    } else {
      // summarize: append rather than overwrite, so the original text stays
      // intact — appended to whatever's currently in the editor (not the
      // possibly-now-stale `text` snapshot from before the request) in
      // case more was typed while this was in flight.
      appendPlainTextToEditor("— Summary —");
      appendPlainTextToEditor(res.result);
    }
    scheduleAutosave();
    setVoiceStatus("");
    showToast("Done", "success");
  } catch (e) {
    if (e.message === "Not logged in") return;
    setVoiceStatus(`AI assist failed (${e.message}).`, "ai-error");
    showToast(`AI assist failed: ${e.message}`, "error");
  } finally {
    aiBtn.classList.remove("working");
    setButtonLoading(aiBtn, false);
  }
}

el("btnOpenStudy").addEventListener("click", () => {
  const note = currentNote();
  if (!note) return;
  switchNav("study");
  el("studyNoteSelect").value = String(note.id);
  state.studySelectedNoteId = String(note.id);
  fetchStudyForSelectedNote();
});

// ============================================================
// Day 33 — Study Mode
//
//   Pick a note (from the dropdown here, or via the editor's "Study"
//   button) -> click "Generate study pack"
//           |
//   POST /api/notes/<id>/study/generate — ONE AI call, asked for a
//   single JSON object covering all six sections at once
//           |
//   Backend validates/caps every field (see study.py) and stores ONE
//   row per note, so reopening this page or regenerating never leaves
//   stale duplicates lying around
//           |
//   Rendered here as six tabs: a plain summary, a bullet list of key
//   points, a Q&A list, a graded multiple-choice quiz, flip-to-reveal
//   flashcards, and a glossary of definitions.
// ============================================================
function renderStudyNoteOptions() {
  const select = el("studyNoteSelect");
  const previousValue = select.value;
  const sorted = state.notes
    .slice()
    .sort((a, b) => new Date(b.updatedAt) - new Date(a.updatedAt));

  select.innerHTML =
    `<option value="">Select a note…</option>` +
    sorted.map((n) => `<option value="${n.id}">${escapeHtml(n.title || "Untitled note")}</option>`).join("");

  // Keep whatever was selected (by id, since sort order can change) if
  // it's still a valid note; otherwise fall back to whatever's tracked
  // in state (e.g. set by the editor's "Study" button before this
  // dropdown was rebuilt).
  const wanted = state.studySelectedNoteId || previousValue;
  if (wanted && sorted.some((n) => String(n.id) === String(wanted))) {
    select.value = String(wanted);
    state.studySelectedNoteId = String(wanted);
  } else {
    select.value = "";
    state.studySelectedNoteId = null;
  }
}

function loadStudyView() {
  renderStudyNoteOptions();
  el("studyAiUnavailable").hidden = state.aiAvailable !== false;
  fetchStudyForSelectedNote();
}

el("studyNoteSelect").addEventListener("change", (e) => {
  state.studySelectedNoteId = e.target.value || null;
  fetchStudyForSelectedNote();
});

function updateStudyControls() {
  const hasNote = !!state.studySelectedNoteId;
  const generated = !!(state.studyData && state.studyData.generated);
  el("studyGenerateBtn").hidden = generated;
  el("studyGenerateBtn").disabled = !hasNote || state.studyGenerating;
  el("studyRegenerateBtn").hidden = !generated;
  el("studyRegenerateBtn").disabled = state.studyGenerating;
  el("studyDeleteBtn").hidden = !generated;
  el("studyStaleBadge").hidden = !(generated && state.studyData.stale);
}

async function fetchStudyForSelectedNote() {
  const noteId = state.studySelectedNoteId;
  el("studyLoading").hidden = true;
  el("studyBody").hidden = true;
  el("studyEmptyState").hidden = !!noteId;
  el("studyNotGeneratedState").hidden = true;

  if (!noteId) {
    state.studyData = null;
    updateStudyControls();
    return;
  }

  el("studyLoading").hidden = false;
  try {
    const res = await api(`/api/notes/${noteId}/study`);
    state.studyData = res;
    el("studyLoading").hidden = true;
    if (res.generated) {
      renderStudyBody();
    } else {
      el("studyNotGeneratedState").hidden = false;
    }
  } catch (e) {
    if (e.message === "Not logged in") return;
    el("studyLoading").hidden = true;
    state.studyData = null;
    showToast(`Couldn't load study material (${e.message}).`, "error");
  }
  updateStudyControls();
}

async function generateStudy() {
  const noteId = state.studySelectedNoteId;
  if (!noteId || state.studyGenerating) return;
  state.studyGenerating = true;
  updateStudyControls();
  el("studyBody").hidden = true;
  el("studyNotGeneratedState").hidden = true;
  el("studyLoading").hidden = false;

  try {
    const res = await api(`/api/notes/${noteId}/study/generate`, { method: "POST" });
    state.studyData = res;
    state.studyQuizAnswers = {};
    el("studyLoading").hidden = true;
    renderStudyBody();
    showToast("Study pack ready", "success");
  } catch (e) {
    if (e.message === "Not logged in") return;
    el("studyLoading").hidden = true;
    if (!(state.studyData && state.studyData.generated)) {
      el("studyNotGeneratedState").hidden = false;
    } else {
      renderStudyBody();
    }
    showToast(`Couldn't generate a study pack (${e.message}).`, "error");
  } finally {
    state.studyGenerating = false;
    updateStudyControls();
  }
}

el("studyGenerateBtn").addEventListener("click", generateStudy);
el("studyRegenerateBtn").addEventListener("click", generateStudy);

el("studyDeleteBtn").addEventListener("click", async () => {
  const noteId = state.studySelectedNoteId;
  if (!noteId) return;
  const ok = await confirmDialog("Delete this study pack? You can always regenerate it later.", {
    title: "Delete study pack?",
  });
  if (!ok) return;
  try {
    await api(`/api/notes/${noteId}/study`, { method: "DELETE" });
    state.studyData = { generated: false };
    state.studyQuizAnswers = {};
    el("studyBody").hidden = true;
    el("studyNotGeneratedState").hidden = false;
    showToast("Study pack deleted", "success");
  } catch (e) {
    if (e.message === "Not logged in") return;
    showToast(`Couldn't delete the study pack (${e.message}).`, "error");
  }
  updateStudyControls();
});

function switchStudyTab(tab) {
  state.studyTab = tab;
  document.querySelectorAll(".study-tab").forEach((btn) => btn.classList.toggle("active", btn.dataset.studytab === tab));
  ["summary", "keypoints", "qna", "mcqs", "flashcards", "definitions"].forEach((name) => {
    el(`studyPanel-${name}`).hidden = name !== tab;
  });
}

document.querySelectorAll(".study-tab").forEach((btn) => {
  btn.addEventListener("click", () => switchStudyTab(btn.dataset.studytab));
});

function renderStudyBody() {
  const data = state.studyData;
  if (!data || !data.generated) return;

  el("studyEmptyState").hidden = true;
  el("studyNotGeneratedState").hidden = true;
  el("studyBody").hidden = false;
  switchStudyTab(state.studyTab || "summary");

  el("studySummaryText").textContent = data.summary || "No summary generated.";

  const points = el("studyKeyPoints");
  points.innerHTML = data.keyPoints.length
    ? data.keyPoints.map((p) => `<li>${escapeHtml(p)}</li>`).join("")
    : `<li class="study-empty-row">No key points generated.</li>`;

  const qnaList = el("studyQnaList");
  qnaList.innerHTML = data.qna.length
    ? data.qna
        .map(
          (item, i) => `
      <details class="study-qna-item">
        <summary>${i + 1}. ${escapeHtml(item.question)}</summary>
        <p>${escapeHtml(item.answer)}</p>
      </details>`
        )
        .join("")
    : `<p class="study-empty-row">No questions generated.</p>`;

  renderMcqs();
  renderFlashcards();

  const defs = el("studyDefinitions");
  defs.innerHTML = data.definitions.length
    ? data.definitions
        .map((d) => `<dt>${escapeHtml(d.term)}</dt><dd>${escapeHtml(d.definition)}</dd>`)
        .join("")
    : `<p class="study-empty-row">No definitions generated.</p>`;
}

function renderMcqs() {
  const data = state.studyData;
  const list = el("studyMcqList");
  if (!data.mcqs.length) {
    list.innerHTML = `<p class="study-empty-row">No multiple-choice questions generated.</p>`;
    el("studyQuizScore").textContent = "";
    return;
  }

  list.innerHTML = data.mcqs
    .map((q, qi) => {
      const picked = state.studyQuizAnswers[qi];
      const answered = picked !== undefined;
      const options = q.options
        .map((opt, oi) => {
          let cls = "study-mcq-option";
          if (answered) {
            if (oi === q.answerIndex) cls += " correct";
            else if (oi === picked) cls += " incorrect";
          }
          return `<button type="button" class="${cls}" data-qi="${qi}" data-oi="${oi}" ${answered ? "disabled" : ""}>${escapeHtml(opt)}</button>`;
        })
        .join("");
      const explanation =
        answered && q.explanation
          ? `<p class="study-mcq-explanation">${picked === q.answerIndex ? "✅" : "❌"} ${escapeHtml(q.explanation)}</p>`
          : "";
      return `
      <div class="study-mcq-card">
        <p class="study-mcq-question">${qi + 1}. ${escapeHtml(q.question)}</p>
        <div class="study-mcq-options">${options}</div>
        ${explanation}
      </div>`;
    })
    .join("");

  list.querySelectorAll(".study-mcq-option").forEach((btn) => {
    btn.addEventListener("click", () => {
      const qi = Number(btn.dataset.qi);
      const oi = Number(btn.dataset.oi);
      if (state.studyQuizAnswers[qi] !== undefined) return;
      state.studyQuizAnswers[qi] = oi;
      renderMcqs();
    });
  });

  const answeredCount = Object.keys(state.studyQuizAnswers).length;
  const correctCount = data.mcqs.filter((q, qi) => state.studyQuizAnswers[qi] === q.answerIndex).length;
  el("studyQuizScore").textContent = answeredCount
    ? `${correctCount} / ${answeredCount} correct so far`
    : `${data.mcqs.length} question${data.mcqs.length === 1 ? "" : "s"}`;
}

el("studyQuizResetBtn").addEventListener("click", () => {
  state.studyQuizAnswers = {};
  renderMcqs();
});

function renderFlashcards() {
  const data = state.studyData;
  const grid = el("studyFlashcardGrid");
  if (!data.flashcards.length) {
    grid.innerHTML = `<p class="study-empty-row">No flashcards generated.</p>`;
    return;
  }
  grid.innerHTML = data.flashcards
    .map(
      (card, i) => `
    <div class="study-flashcard" data-index="${i}">
      <div class="study-flashcard-inner">
        <div class="study-flashcard-face study-flashcard-front">${escapeHtml(card.front)}</div>
        <div class="study-flashcard-face study-flashcard-back">${escapeHtml(card.back)}</div>
      </div>
    </div>`
    )
    .join("");
  grid.querySelectorAll(".study-flashcard").forEach((cardEl) => {
    cardEl.addEventListener("click", () => cardEl.classList.toggle("flipped"));
  });
}

// ============================================================
// Day 32 — "Generate Flowchart with AI"
//
//   Select text in the note -> click 🔀 Flowchart (floating toolbar,
//   only enabled once a real selection of a few words exists)
//           |
//   POST /api/notes/<id>/flowcharts { text: <selection> }
//           |
//   Backend returns { nodes, edges } (validated/capped — see flowchart.py)
//           |
//   computeFlowchartLayout() lays the DAG out in ranked rows (longest-
//   path layering), renderFlowchartSvg() draws it, and the result opens
//   immediately in flowchartOverlay — it's also saved, so it reappears
//   as a card in the Flowcharts panel every time the note is reopened.
// ============================================================

async function loadFlowcharts(noteId) {
  try {
    const flowcharts = await api(`/api/notes/${noteId}/flowcharts`);
    if (state.editingId !== noteId) return; // navigated away while this was in flight
    state.noteFlowcharts = flowcharts;
    renderFlowchartList();
  } catch (e) {
    if (e.message === "Not logged in") return;
    state.noteFlowcharts = [];
    renderFlowchartList();
  }
}

function renderFlowchartList() {
  const panel = el("flowchartPanel");
  const list = el("flowchartList");
  if (!state.noteFlowcharts.length) {
    panel.hidden = true;
    list.innerHTML = "";
    return;
  }
  panel.hidden = false;
  list.innerHTML = state.noteFlowcharts
    .map(
      (f) => `
      <div class="flowchart-card" data-id="${f.id}">
        <div class="flowchart-card-title" title="${escapeHtml(f.sourceText)}">${escapeHtml(f.title)}</div>
        <div class="flowchart-card-meta">${f.nodes.length} step${f.nodes.length === 1 ? "" : "s"} · ${timeAgo(f.createdAt)}</div>
        <div class="flowchart-card-actions">
          <button type="button" class="flowchart-card-view" data-act="view" data-id="${f.id}">View</button>
          <button type="button" class="flowchart-card-delete" data-act="delete" data-id="${f.id}" title="Delete">✕</button>
        </div>
      </div>`
    )
    .join("");
}

el("flowchartList").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const id = btn.dataset.id;
  if (btn.dataset.act === "view") openFlowchartOverlay(id);
  else if (btn.dataset.act === "delete") deleteFlowchart(id);
});

async function generateFlowchart(text) {
  const note = currentNote();
  if (!note || state.flowchartGenerating) return;
  if (!text || text.split(/\s+/).length < FLOWCHART_MIN_SELECTION_WORDS) {
    showToast(`Select at least ${FLOWCHART_MIN_SELECTION_WORDS} words describing steps or a process.`, "error");
    return;
  }

  state.flowchartGenerating = true;
  const btn = el("fmtFlowchartBtn");
  const originalLabel = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Generating…";
  setVoiceStatus("Generating flowchart…", "working");

  try {
    const flowchart = await api(`/api/notes/${note.id}/flowcharts`, {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    state.noteFlowcharts.push(flowchart);
    renderFlowchartList();
    setVoiceStatus("");
    showToast("Flowchart generated", "success");
    openFlowchartOverlay(flowchart.id);
  } catch (e) {
    if (e.message === "Not logged in") return;
    setVoiceStatus("");
    showToast(`Couldn't generate a flowchart: ${e.message}`, "error");
  } finally {
    state.flowchartGenerating = false;
    btn.textContent = originalLabel;
    updateFlowchartButtonState();
  }
}

async function deleteFlowchart(id) {
  const confirmed = await confirmDialog(
    "Delete this flowchart? This can't be undone. The note's text itself is unaffected.",
    { title: "Delete flowchart?" }
  );
  if (!confirmed) return;
  try {
    await api(`/api/flowcharts/${id}`, { method: "DELETE" });
    state.noteFlowcharts = state.noteFlowcharts.filter((f) => String(f.id) !== String(id));
    renderFlowchartList();
    if (String(state.openFlowchartId) === String(id)) closeFlowchartOverlay();
    showToast("Flowchart deleted", "success");
  } catch (e) {
    if (e.message === "Not logged in") return;
    showToast(`Couldn't delete flowchart: ${e.message}`, "error");
  }
}

function openFlowchartOverlay(id) {
  const flowchart = state.noteFlowcharts.find((f) => String(f.id) === String(id));
  if (!flowchart) return;
  state.openFlowchartId = flowchart.id;
  el("flowchartModalTitle").textContent = flowchart.title || "Flowchart";
  el("flowchartModalSource").textContent = `From: "${flowchart.sourceText}"`;
  el("flowchartCanvas").innerHTML = renderFlowchartSvg(flowchart.nodes, flowchart.edges);
  el("flowchartOverlay").hidden = false;
}

function closeFlowchartOverlay() {
  el("flowchartOverlay").hidden = true;
  state.openFlowchartId = null;
}

el("flowchartCloseBtn").addEventListener("click", closeFlowchartOverlay);
el("flowchartOverlay").addEventListener("click", (e) => {
  if (e.target === el("flowchartOverlay")) closeFlowchartOverlay();
});
el("flowchartDeleteBtn").addEventListener("click", () => {
  if (state.openFlowchartId != null) deleteFlowchart(state.openFlowchartId);
});
el("flowchartDownloadBtn").addEventListener("click", () => {
  const svg = el("flowchartCanvas").querySelector("svg");
  if (!svg) return;
  const flowchart = state.noteFlowcharts.find((f) => String(f.id) === String(state.openFlowchartId));
  const serialized = new XMLSerializer().serializeToString(svg);
  const blob = new Blob([serialized], { type: "image/svg+xml" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${(flowchart && flowchart.title ? flowchart.title : "flowchart").replace(/[^\w\- ]/g, "").trim() || "flowchart"}.svg`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
});

// ------------------------------------------------------------
// Layered DAG layout — assigns every node a rank (its longest-path
// distance from a "root" with no incoming edges, via Kahn's algorithm)
// and a slot within that rank's row, then converts ranks/slots into
// pixel positions. This is deliberately simple (no crossing-count
// optimization beyond one barycenter pass) — flowcharts from a single
// selection of note text are small (<=12 nodes), so a basic layered
// layout reads clearly without needing a full Sugiyama pipeline.
// ------------------------------------------------------------
const FC_NODE_W = 180;
const FC_NODE_H = 56;
const FC_H_GAP = 40;
const FC_V_GAP = 64;
const FC_PAD = 30;

function computeFlowchartLayout(nodes, edges) {
  const ids = nodes.map((n) => n.id);
  const outgoing = {};
  const indeg = {};
  ids.forEach((id) => {
    outgoing[id] = [];
    indeg[id] = 0;
  });
  edges.forEach((e) => {
    outgoing[e.from].push(e.to);
    indeg[e.to] = (indeg[e.to] || 0) + 1;
  });

  const rank = {};
  const queue = ids.filter((id) => indeg[id] === 0);
  // No source at all (e.g. every node has an incoming edge because the
  // whole thing is one cycle) — start ranking from the first node
  // rather than leaving everything unranked.
  if (!queue.length && ids.length) queue.push(ids[0]);
  queue.forEach((id) => (rank[id] = 0));

  const remaining = { ...indeg };
  let i = 0;
  while (i < queue.length) {
    const u = queue[i++];
    outgoing[u].forEach((v) => {
      rank[v] = Math.max(rank[v] || 0, (rank[u] || 0) + 1);
      remaining[v] -= 1;
      if (remaining[v] <= 0 && !queue.includes(v)) queue.push(v);
    });
  }
  // Any node a cycle kept out of the queue entirely still needs a rank
  // so it renders somewhere, rather than being silently dropped.
  let maxAssigned = Math.max(0, ...Object.values(rank));
  ids.forEach((id) => {
    if (!(id in rank)) rank[id] = ++maxAssigned;
  });

  // Group into rows, preserving discovery order as the initial slot order.
  const rows = [];
  ids.forEach((id) => {
    const r = rank[id];
    if (!rows[r]) rows[r] = [];
    rows[r].push(id);
  });

  // One barycenter pass: reorder each row by the average slot-index of
  // its predecessors in the row above, so edges tend to point "mostly
  // downward" instead of crossing every which way.
  const slotIndex = {};
  rows[0] && rows[0].forEach((id, idx) => (slotIndex[id] = idx));
  for (let r = 1; r < rows.length; r++) {
    if (!rows[r]) continue;
    const incomingFrom = {};
    edges.forEach((e) => {
      if (rank[e.to] === r && e.from in slotIndex) {
        (incomingFrom[e.to] = incomingFrom[e.to] || []).push(slotIndex[e.from]);
      }
    });
    rows[r].sort((a, b) => {
      const avg = (arr) => (arr && arr.length ? arr.reduce((s, x) => s + x, 0) / arr.length : 999);
      return avg(incomingFrom[a]) - avg(incomingFrom[b]);
    });
    rows[r].forEach((id, idx) => (slotIndex[id] = idx));
  }

  const maxRowWidth = Math.max(...rows.map((row) => row.length * (FC_NODE_W + FC_H_GAP) - FC_H_GAP));
  const positions = {};
  rows.forEach((row, r) => {
    const rowWidth = row.length * (FC_NODE_W + FC_H_GAP) - FC_H_GAP;
    const xOffset = (maxRowWidth - rowWidth) / 2;
    row.forEach((id, idx) => {
      positions[id] = {
        x: FC_PAD + xOffset + idx * (FC_NODE_W + FC_H_GAP),
        y: FC_PAD + r * (FC_NODE_H + FC_V_GAP),
        w: FC_NODE_W,
        h: FC_NODE_H,
        rank: r,
      };
    });
  });

  return {
    positions,
    width: maxRowWidth + FC_PAD * 2,
    height: rows.length * (FC_NODE_H + FC_V_GAP) - FC_V_GAP + FC_PAD * 2,
  };
}

function fcWrapLabel(label, maxCharsPerLine = 20) {
  const words = (label || "").split(/\s+/);
  const lines = [];
  let current = "";
  words.forEach((w) => {
    if ((current + " " + w).trim().length > maxCharsPerLine && current) {
      lines.push(current);
      current = w;
    } else {
      current = (current + " " + w).trim();
    }
  });
  if (current) lines.push(current);
  return lines.slice(0, 3); // a box only has so much room
}

function fcNodeShape(pos, type) {
  const { x, y, w, h } = pos;
  if (type === "decision") {
    const points = [
      [x + w / 2, y],
      [x + w, y + h / 2],
      [x + w / 2, y + h],
      [x, y + h / 2],
    ]
      .map((p) => p.join(","))
      .join(" ");
    return `<polygon class="fc-node fc-node-decision" points="${points}" />`;
  }
  if (type === "io") {
    const skew = 16;
    const points = [
      [x + skew, y],
      [x + w, y],
      [x + w - skew, y + h],
      [x, y + h],
    ]
      .map((p) => p.join(","))
      .join(" ");
    return `<polygon class="fc-node fc-node-io" points="${points}" />`;
  }
  if (type === "start" || type === "end") {
    return `<rect class="fc-node fc-node-${type}" x="${x}" y="${y}" width="${w}" height="${h}" rx="${h / 2}" />`;
  }
  return `<rect class="fc-node fc-node-process" x="${x}" y="${y}" width="${w}" height="${h}" rx="8" />`;
}

function renderFlowchartSvg(nodes, edges) {
  const layout = computeFlowchartLayout(nodes, edges);
  const { positions, width, height } = layout;

  const nodesSvg = nodes
    .map((n) => {
      const pos = positions[n.id];
      if (!pos) return "";
      const lines = fcWrapLabel(n.label);
      const lineHeight = 14;
      const startY = pos.y + pos.h / 2 - ((lines.length - 1) * lineHeight) / 2;
      const textSvg = lines
        .map(
          (line, idx) =>
            `<tspan x="${pos.x + pos.w / 2}" y="${startY + idx * lineHeight}">${escapeHtml(line)}</tspan>`
        )
        .join("");
      return `<g class="fc-node-group">
        ${fcNodeShape(pos, n.type)}
        <text class="fc-node-label" text-anchor="middle" dominant-baseline="middle">${textSvg}</text>
      </g>`;
    })
    .join("");

  const edgesSvg = edges
    .map((e) => {
      const from = positions[e.from];
      const to = positions[e.to];
      if (!from || !to) return "";
      let path, labelX, labelY;
      if (to.rank > from.rank) {
        // Forward edge: bottom-center of source curving to top-center of target.
        const x1 = from.x + from.w / 2;
        const y1 = from.y + from.h;
        const x2 = to.x + to.w / 2;
        const y2 = to.y;
        const midY = (y1 + y2) / 2;
        path = `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`;
        labelX = (x1 + x2) / 2;
        labelY = midY;
      } else {
        // Same-rank or backward edge (e.g. a "retry"/loop-back step) —
        // route it out the right side and back in on the right, so it
        // reads as a distinct loop rather than crossing straight
        // through the middle of the diagram.
        const x1 = from.x + from.w;
        const y1 = from.y + from.h / 2;
        const x2 = to.x + to.w;
        const y2 = to.y + to.h / 2;
        const bow = 50;
        path = `M ${x1} ${y1} C ${x1 + bow} ${y1}, ${x2 + bow} ${y2}, ${x2} ${y2}`;
        labelX = Math.max(x1, x2) + bow - 6;
        labelY = (y1 + y2) / 2;
      }
      const labelSvg = e.label
        ? `<rect class="fc-edge-label-bg" x="${labelX - e.label.length * 3.4 - 4}" y="${labelY - 9}" width="${e.label.length * 6.8 + 8}" height="16" rx="4" />
           <text class="fc-edge-label" x="${labelX}" y="${labelY + 3}" text-anchor="middle">${escapeHtml(e.label)}</text>`
        : "";
      return `<path class="fc-edge" d="${path}" marker-end="url(#fcArrow)" />${labelSvg}`;
    })
    .join("");

  return `
    <svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <marker id="fcArrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" class="fc-arrowhead" />
        </marker>
      </defs>
      ${edgesSvg}
      ${nodesSvg}
    </svg>`;
}

window.addEventListener("beforeunload", () => {
  if (state.editingId) saveCurrentNote();
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden && state.editingId) saveCurrentNote();
});

// ============================================================
// Day 18 — knowledge graph view: force-directed layout + rendering
// Overhauled Day 29 — the raw note/concept graph tends toward a dense
// hairball once you have a few dozen notes, so this adds: relationship
// filtering (direct-only + an edge-strength threshold), a layout
// picker, topic clustering, click-to-highlight-connections, label
// collision avoidance, pan/zoom + a minimap, and an AI "explain this"
// feature — all still plain SVG, no charting library, consistent with
// the rest of this project being framework-free HTML/CSS/JS.
// ============================================================
const GRAPH_ITERATIONS = 250;
const GRAPH_REPULSION = 2600;
const GRAPH_SPRING_LENGTH = 90;
const GRAPH_SPRING_STRENGTH = 0.02;
const GRAPH_CENTER_STRENGTH = 0.02;
const GRAPH_DAMPING = 0.85;
const GRAPH_CLUSTER_STRENGTH = 0.012; // Day 29: gentle extra pull between same-cluster nodes when "Group by topic" is on
const GRAPH_MIN_RADIUS = 7;
const GRAPH_MAX_RADIUS = 26;
const GRAPH_ZOOM_MIN = 0.35;
const GRAPH_ZOOM_MAX = 3;
const GRAPH_CLUSTER_COLORS = [
  "#f59e0b", "#6366f1", "#22c55e", "#ec4899", "#06b6d4",
  "#eab308", "#a855f7", "#f97316", "#14b8a6", "#ef4444",
];
const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
  return node;
}

function conceptKeyClient(label) {
  return (label || "").trim().toLowerCase().replace(/\s+/g, " ");
}

// ---------- Day 29: filtering, degree ----------

// Applies the current display controls to the raw {nodes, edges} graph:
// "direct only" drops inferred concept<->concept edges entirely, the
// strength slider (only relevant when those edges are shown) drops the
// weak ones, and "hide isolated" removes whatever nodes end up with no
// edges left after that. Degree is computed on the *filtered* edge set
// so node sizing/isolation reflect what's actually visible, not the
// unfiltered totals.
function filterGraphData(data, filters) {
  const edges = data.edges.filter((e) => {
    if (e.type === "mentions") return true; // always direct — never filtered out
    if (filters.directOnly) return false;
    return (e.weight || 1) >= filters.minStrength;
  });

  const degree = {};
  edges.forEach((e) => {
    degree[e.source] = (degree[e.source] || 0) + 1;
    degree[e.target] = (degree[e.target] || 0) + 1;
  });

  const nodes = filters.hideIsolated ? data.nodes.filter((n) => degree[n.id]) : data.nodes;
  return { nodes, edges, degree };
}

function maxCoOccursWeight(edges) {
  let max = 1;
  edges.forEach((e) => {
    if (e.type === "co-occurs" && (e.weight || 1) > max) max = e.weight;
  });
  return max;
}

// ---------- Day 29: topic clustering ----------
//
// Union-find over the *unfiltered* co-occurs graph — clustering is a
// statement about which topics tend to travel together across your
// notes, so it should stay meaningful even while "Direct relationships
// only" is hiding those edges from the drawing itself. Returns a map
// of every node id -> a small integer cluster index (stable sort by
// cluster size, largest first, so cluster 0 is always the biggest
// topic and colors don't reshuffle across re-renders).
function computeClusters(data) {
  const parent = {};
  const find = (x) => {
    parent[x] = parent[x] || x;
    while (parent[x] !== x) {
      parent[x] = parent[parent[x]]; // path compression
      x = parent[x];
    }
    return x;
  };
  const union = (a, b) => {
    const ra = find(a), rb = find(b);
    if (ra !== rb) parent[ra] = rb;
  };

  const conceptIds = data.nodes.filter((n) => n.type === "concept").map((n) => n.id);
  conceptIds.forEach((id) => find(id));
  data.edges.forEach((e) => {
    if (e.type === "co-occurs") union(e.source, e.target);
  });

  const groupSizes = {};
  conceptIds.forEach((id) => {
    const root = find(id);
    groupSizes[root] = (groupSizes[root] || 0) + 1;
  });
  const rootsBySize = Object.keys(groupSizes).sort((a, b) => groupSizes[b] - groupSizes[a]);
  const rootIndex = {};
  rootsBySize.forEach((root, i) => (rootIndex[root] = i));

  const clusterOf = {};
  conceptIds.forEach((id) => {
    clusterOf[id] = rootIndex[find(id)];
  });

  // A note's cluster is whichever cluster most of its mentioned
  // concepts belong to — ties broken by first-seen concept, which is
  // stable since note_concepts (backend) already returns sorted keys.
  const mentionsBySource = {};
  data.edges.forEach((e) => {
    if (e.type === "mentions") (mentionsBySource[e.source] ||= []).push(e.target);
  });
  data.nodes
    .filter((n) => n.type === "note")
    .forEach((n) => {
      const concepts = mentionsBySource[n.id] || [];
      if (!concepts.length) return;
      const counts = {};
      concepts.forEach((c) => {
        const cl = clusterOf[c];
        if (cl === undefined) return;
        counts[cl] = (counts[cl] || 0) + 1;
      });
      const best = Object.keys(counts).sort((a, b) => counts[b] - counts[a])[0];
      if (best !== undefined) clusterOf[n.id] = Number(best);
    });

  return clusterOf;
}

function clusterColor(clusterIndex) {
  return GRAPH_CLUSTER_COLORS[clusterIndex % GRAPH_CLUSTER_COLORS.length];
}

// ---------- Day 29: layouts ----------
//
// Three interchangeable position-computers, all with the same
// signature (nodes, edges/degree, width, height, extra) -> {id: {x,y}}.
// Only "force" needs edges/iterating; radial and grid are single-pass
// and deterministic — useful specifically *because* force layouts turn
// messy once relationships pile up, when sometimes you just want to
// scan every node without a physics simulation's arrangement fighting
// you.

function computeForceLayout(nodes, edges, width, height, clusters) {
  const positions = {};
  nodes.forEach((n, i) => {
    // Golden-angle spiral start (not Math.random()) so the same graph
    // looks roughly the same across reloads instead of reshuffling
    // every time, while still avoiding exact node overlaps.
    const angle = i * 2.399963;
    const radius = 40 + 6 * Math.sqrt(i);
    positions[n.id] = {
      x: width / 2 + radius * Math.cos(angle),
      y: height / 2 + radius * Math.sin(angle),
      vx: 0,
      vy: 0,
    };
  });

  const useClusters = clusters && Object.keys(clusters).length;

  for (let iter = 0; iter < GRAPH_ITERATIONS; iter++) {
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = positions[nodes[i].id];
        const b = positions[nodes[j].id];
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let distSq = dx * dx + dy * dy;
        if (distSq < 0.01) {
          dx = Math.random() - 0.5;
          dy = Math.random() - 0.5;
          distSq = 0.01;
        }
        const dist = Math.sqrt(distSq);
        const force = GRAPH_REPULSION / distSq;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        a.vx += fx;
        a.vy += fy;
        b.vx -= fx;
        b.vy -= fy;

        // Day 29: a weak extra pull between nodes sharing a topic
        // cluster — small enough not to fight the layout's overall
        // shape, but enough to nudge same-topic nodes into loose
        // neighborhoods when "Group by topic" is on.
        if (useClusters) {
          const ca = clusters[nodes[i].id];
          const cb = clusters[nodes[j].id];
          if (ca !== undefined && ca === cb) {
            const pull = dist * GRAPH_CLUSTER_STRENGTH;
            const pfx = (dx / dist) * pull;
            const pfy = (dy / dist) * pull;
            a.vx -= pfx;
            a.vy -= pfy;
            b.vx += pfx;
            b.vy += pfy;
          }
        }
      }
    }
    edges.forEach((e) => {
      const a = positions[e.source];
      const b = positions[e.target];
      if (!a || !b) return;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const displacement = dist - GRAPH_SPRING_LENGTH;
      const force = displacement * GRAPH_SPRING_STRENGTH;
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;
      a.vx += fx;
      a.vy += fy;
      b.vx -= fx;
      b.vy -= fy;
    });
    nodes.forEach((n) => {
      const p = positions[n.id];
      p.vx += (width / 2 - p.x) * GRAPH_CENTER_STRENGTH;
      p.vy += (height / 2 - p.y) * GRAPH_CENTER_STRENGTH;
      p.vx *= GRAPH_DAMPING;
      p.vy *= GRAPH_DAMPING;
      p.x += p.vx;
      p.y += p.vy;
      p.x = Math.max(30, Math.min(width - 30, p.x));
      p.y = Math.max(30, Math.min(height - 30, p.y));
    });
  }
  return positions;
}

// Most-connected nodes ring the center, progressively less-connected
// nodes further out — a deliberately calmer alternative to the force
// layout's physics-driven scatter, good for scanning "what matters
// most" at a glance.
function computeRadialLayout(nodes, degree, width, height) {
  const positions = {};
  const cx = width / 2;
  const cy = height / 2;
  const sorted = [...nodes].sort((a, b) => (degree[b.id] || 0) - (degree[a.id] || 0));
  const maxRingRadius = Math.max(60, Math.min(width, height) / 2 - 40);

  const ringCapacities = [1, 6, 12, 20, 30, 42];
  let idx = 0;
  let ring = 0;
  while (idx < sorted.length) {
    const capacity = ringCapacities[Math.min(ring, ringCapacities.length - 1)];
    const ringNodes = sorted.slice(idx, idx + capacity);
    const ringRadius = ring === 0 ? 0 : Math.min(maxRingRadius, (maxRingRadius / (ringCapacities.length - 1)) * ring);
    ringNodes.forEach((n, i) => {
      const angle = (i / Math.max(1, ringNodes.length)) * Math.PI * 2 + ring * 0.4;
      positions[n.id] = {
        x: cx + ringRadius * Math.cos(angle),
        y: cy + ringRadius * Math.sin(angle),
        vx: 0,
        vy: 0,
      };
    });
    idx += capacity;
    ring += 1;
  }
  return positions;
}

// Deterministic row-major grid, grouped by type then sorted by degree
// — the "just let me see everything laid out plainly" option.
function computeGridLayout(nodes, degree, width, height) {
  const positions = {};
  const sorted = [...nodes].sort((a, b) => {
    if (a.type !== b.type) return a.type === "note" ? -1 : 1;
    return (degree[b.id] || 0) - (degree[a.id] || 0);
  });
  const cols = Math.max(1, Math.ceil(Math.sqrt((sorted.length * width) / Math.max(height, 1))));
  const cellW = width / (cols + 1);
  const rows = Math.ceil(sorted.length / cols);
  const cellH = height / (rows + 1);
  sorted.forEach((n, i) => {
    const col = i % cols;
    const row = Math.floor(i / cols);
    positions[n.id] = { x: cellW * (col + 1), y: cellH * (row + 1), vx: 0, vy: 0 };
  });
  return positions;
}

function computeGraphLayout(layout, nodes, edges, degree, width, height, clusters) {
  if (layout === "radial") return computeRadialLayout(nodes, degree, width, height);
  if (layout === "grid") return computeGridLayout(nodes, degree, width, height);
  return computeForceLayout(nodes, edges, width, height, clusters);
}

// ---------- Day 29: label collision avoidance ----------
//
// Greedy vertical-nudge pass: labels default to sitting just under
// their node, but in a dense area that stacks dozens of labels on top
// of each other (exactly the mess in the Day 28 screenshot). This
// keeps each label's *anchor* under its own node but nudges the ones
// that would overlap an already-placed label straight down in small
// steps until they clear it, checked against every previously-placed
// label's actual bounding box (not just same-row neighbors), which is
// cheap enough at this graph's scale (a personal notes app, not
// thousands of nodes).
function computeLabelOffsets(nodes, positions, radiusOf) {
  const offsets = {};
  const placed = [];
  const order = [...nodes].sort((a, b) => positions[a.id].y - positions[b.id].y);

  order.forEach((n) => {
    const p = positions[n.id];
    const r = radiusOf(n);
    const text = n.label.length > 22 ? n.label.slice(0, 21) + "…" : n.label;
    const boxW = Math.max(24, text.length * 5.6);
    const boxH = 13;
    let y = p.y + r + 14;
    let tries = 0;
    while (tries < 6) {
      const x1 = p.x - boxW / 2, x2 = p.x + boxW / 2;
      const y1 = y - boxH, y2 = y;
      const overlaps = placed.some((b) => x1 < b.x2 && x2 > b.x1 && y1 < b.y2 && y2 > b.y1);
      if (!overlaps) break;
      y += boxH + 1;
      tries += 1;
    }
    placed.push({ x1: p.x - boxW / 2, x2: p.x + boxW / 2, y1: y - boxH, y2: y });
    offsets[n.id] = y - p.y;
  });
  return offsets;
}

// ---------- graph view controls: wiring (attached once) ----------

function graphControlsFromDom() {
  return {
    layout: el("graphLayoutSelect").value,
    directOnly: el("graphDirectOnly").checked,
    hideIsolated: el("graphHideIsolated").checked,
    showLabels: el("graphShowLabels").checked,
    groupByTopic: el("graphGroupByTopic").checked,
    minStrength: Number(el("graphStrengthSlider").value) || 1,
  };
}

function applyGraphControlsToDom(filters) {
  el("graphLayoutSelect").value = filters.layout;
  el("graphDirectOnly").checked = filters.directOnly;
  el("graphHideIsolated").checked = filters.hideIsolated;
  el("graphShowLabels").checked = filters.showLabels;
  el("graphGroupByTopic").checked = filters.groupByTopic;
  el("graphStrengthSlider").value = filters.minStrength;
  el("graphStrengthValue").textContent = filters.minStrength;
  el("graphStrengthControl").hidden = filters.directOnly;
}

function onGraphControlsChanged() {
  state.graphFilters = graphControlsFromDom();
  el("graphStrengthValue").textContent = state.graphFilters.minStrength;
  el("graphStrengthControl").hidden = state.graphFilters.directOnly;
  clearGraphSelection();
  if (state.graphData) renderGraph(state.graphData, state.graphPath);
}

["graphLayoutSelect", "graphDirectOnly", "graphHideIsolated", "graphShowLabels", "graphGroupByTopic"].forEach((id) => {
  el(id).addEventListener("change", onGraphControlsChanged);
});
el("graphStrengthSlider").addEventListener("input", () => {
  el("graphStrengthValue").textContent = el("graphStrengthSlider").value;
});
el("graphStrengthSlider").addEventListener("change", onGraphControlsChanged);

el("graphResetFilters").addEventListener("click", () => {
  state.graphFilters = {
    layout: "force",
    directOnly: true,
    hideIsolated: false,
    showLabels: true,
    groupByTopic: false,
    minStrength: 1,
  };
  applyGraphControlsToDom(state.graphFilters);
  clearGraphSelection();
  if (state.graphData) renderGraph(state.graphData, state.graphPath);
});

el("graphEmptyNewNote").addEventListener("click", openNewNote);

// ---------- load + render ----------

async function loadGraphView() {
  const empty = el("graphEmptyState");
  const filteredEmpty = el("graphFilteredEmptyState");
  const canvas = el("graphCanvas");
  try {
    const data = await api("/api/graph");
    state.graphData = data;
    const noteCount = data.nodes.filter((n) => n.type === "note").length;
    const conceptCount = data.nodes.filter((n) => n.type === "concept").length;
    el("graphCount").textContent = data.nodes.length ? `${noteCount} notes · ${conceptCount} concepts` : "";

    const maxWeight = maxCoOccursWeight(data.edges);
    el("graphStrengthSlider").max = Math.max(2, maxWeight);
    if (state.graphFilters.minStrength > maxWeight) state.graphFilters.minStrength = 1;
    applyGraphControlsToDom(state.graphFilters);

    if (!data.nodes.length) {
      empty.hidden = false;
      filteredEmpty.hidden = true;
      canvas.innerHTML = "";
      el("graphMinimap").hidden = true;
      el("graphZoomControls").hidden = true;
      return;
    }
    empty.hidden = true;
    renderGraph(data, state.graphPath);
  } catch (e) {
    if (e.message === "Not logged in") return;
    showError(`Couldn't load the knowledge graph (${e.message}).`, loadGraphView);
  }
}

function radiusForNode(n, degree, isConcept) {
  const d = degree[n.id] || (isConcept ? n.noteCount : n.conceptCount) || 1;
  return Math.max(GRAPH_MIN_RADIUS, Math.min(GRAPH_MAX_RADIUS, 7 + Math.sqrt(d) * 5));
}

function renderGraph(data, highlightPath = null) {
  const canvas = el("graphCanvas");
  const wrap = canvas.parentElement;
  const width = wrap.clientWidth || 800;
  const height = Math.max(420, wrap.clientHeight || 480);
  canvas.setAttribute("viewBox", `0 0 ${width} ${height}`);
  canvas.innerHTML = "";

  const filters = state.graphFilters;
  const { nodes, edges, degree } = filterGraphData(data, filters);

  const filteredEmpty = el("graphFilteredEmptyState");
  if (!nodes.length) {
    filteredEmpty.hidden = false;
    el("graphMinimap").hidden = true;
    el("graphZoomControls").hidden = true;
    state.graphLayoutCache = null;
    return;
  }
  filteredEmpty.hidden = true;
  el("graphZoomControls").hidden = false;

  const clusters = filters.groupByTopic ? computeClusters(data) : null;
  const positions = computeGraphLayout(filters.layout, nodes, edges, degree, width, height, clusters);
  const labelOffsets = filters.showLabels
    ? computeLabelOffsets(nodes, positions, (n) => radiusForNode(n, degree, n.type === "concept"))
    : {};

  const highlightNodeIds = new Set();
  const highlightEdgeKeys = new Set();
  if (highlightPath && highlightPath.length) {
    for (let i = 0; i < highlightPath.length; i++) {
      highlightNodeIds.add(`concept:${highlightPath[i]}`);
      if (i < highlightPath.length - 1) {
        highlightEdgeKeys.add(`concept:${highlightPath[i]}|concept:${highlightPath[i + 1]}`);
        highlightEdgeKeys.add(`concept:${highlightPath[i + 1]}|concept:${highlightPath[i]}`);
      }
    }
  }

  // Day 29: pan/zoom lives on one wrapping <g> so wheel/drag only ever
  // need to touch a single transform attribute, never re-run layout.
  const viewport = svgEl("g", { class: "graph-viewport" });
  const edgesLayer = svgEl("g", { class: "graph-edges" });
  const nodesLayer = svgEl("g", { class: "graph-nodes" });
  viewport.appendChild(edgesLayer);
  viewport.appendChild(nodesLayer);
  canvas.appendChild(viewport);

  const adjacency = {}; // nodeId -> Set of connected nodeIds (within the currently-filtered graph)
  const nodesById = {};
  nodes.forEach((n) => (nodesById[n.id] = n));

  const linesByNode = {};
  const lineByEdgeKey = {};
  edges.forEach((e) => {
    const a = positions[e.source];
    const b = positions[e.target];
    if (!a || !b) return;
    const key = `${e.source}|${e.target}`;
    const isHighlighted = highlightEdgeKeys.has(key);
    const line = svgEl("line", {
      x1: a.x,
      y1: a.y,
      x2: b.x,
      y2: b.y,
      class: `graph-edge graph-edge-${e.type}${isHighlighted ? " graph-edge-highlight" : ""}`,
    });
    edgesLayer.appendChild(line);
    (linesByNode[e.source] ||= []).push({ line, end: "1" });
    (linesByNode[e.target] ||= []).push({ line, end: "2" });
    lineByEdgeKey[key] = line;
    lineByEdgeKey[`${e.target}|${e.source}`] = line;
    (adjacency[e.source] ||= new Set()).add(e.target);
    (adjacency[e.target] ||= new Set()).add(e.source);
  });

  let draggingId = null;
  const nodeGroups = {};

  nodes.forEach((n) => {
    const p = positions[n.id];
    const isConcept = n.type === "concept";
    const radius = radiusForNode(n, degree, isConcept);
    const isHighlighted = highlightNodeIds.has(n.id);
    const cluster = clusters ? clusters[n.id] : undefined;

    const classes = [`graph-node`, `graph-node-${n.type}`];
    if (isHighlighted) classes.push("graph-node-highlight");
    if (n.type === "note" && n.pinned) classes.push("graph-node-pinned");
    if (cluster !== undefined) classes.push("graph-node-clustered");

    const g = svgEl("g", {
      class: classes.join(" "),
      transform: `translate(${p.x},${p.y})`,
      "data-id": n.id,
    });
    if (cluster !== undefined) {
      const color = clusterColor(cluster);
      g.style.setProperty("--cluster-color", color);
      g.style.setProperty("--cluster-color-dim", color + "33");
    }
    const circle = svgEl("circle", { r: radius });
    g.appendChild(circle);
    if (filters.showLabels) {
      const label = svgEl("text", { x: 0, y: labelOffsets[n.id] ?? radius + 14, "text-anchor": "middle" });
      label.textContent = n.label.length > 22 ? n.label.slice(0, 21) + "…" : n.label;
      g.appendChild(label);
    }
    nodesLayer.appendChild(g);
    nodeGroups[n.id] = g;

    let downX = 0, downY = 0, moved = false;

    g.addEventListener("pointerdown", (ev) => {
      ev.stopPropagation(); // don't also trigger the background pan handler
      draggingId = n.id;
      downX = ev.clientX;
      downY = ev.clientY;
      moved = false;
      g.setPointerCapture(ev.pointerId);
    });

    g.addEventListener("pointermove", (ev) => {
      if (draggingId !== n.id) return;
      const rect = canvas.getBoundingClientRect();
      const scaleX = width / rect.width / state.graphView.k;
      const scaleY = height / rect.height / state.graphView.k;
      if (Math.abs(ev.clientX - downX) > 3 || Math.abs(ev.clientY - downY) > 3) moved = true;
      p.x += (ev.movementX || 0) * scaleX;
      p.y += (ev.movementY || 0) * scaleY;
      g.setAttribute("transform", `translate(${p.x},${p.y})`);
      (linesByNode[n.id] || []).forEach(({ line, end }) => {
        line.setAttribute(`x${end}`, p.x);
        line.setAttribute(`y${end}`, p.y);
      });
    });

    g.addEventListener("pointerup", () => {
      draggingId = null;
      if (moved) return;
      toggleGraphSelection(n.id);
    });

    g.addEventListener("dblclick", () => {
      if (n.type === "note") openNote(n.noteId);
    });

    g.addEventListener("mouseenter", () => showGraphTooltip(n, g));
    g.addEventListener("mouseleave", () => showGraphTooltip(null));
  });

  // Clicking/dragging empty canvas (not a node) pans, and clears the
  // current selection the same way clicking any empty area should.
  canvas.addEventListener("pointerdown", (ev) => {
    if (ev.target === canvas || ev.target === viewport) startGraphPan(ev);
  });

  state.graphLayoutCache = { positions, adjacency, degree, nodesById, nodeGroups, lineByEdgeKey, width, height, filters };

  updateViewportTransform();
  renderMinimap();
  if (state.graphSelectedNodeId && nodesById[state.graphSelectedNodeId]) {
    applySelectionHighlight(state.graphSelectedNodeId);
  } else {
    state.graphSelectedNodeId = null;
    el("graphSelectionPanel").hidden = true;
  }
}

function showGraphTooltip(node, g) {
  const tip = el("graphTooltip");
  if (!node) {
    tip.hidden = true;
    return;
  }
  tip.hidden = false;
  tip.textContent =
    node.type === "note"
      ? `📝 ${node.label}`
      : `🧠 ${node.label} · ${node.noteCount} note${node.noteCount === 1 ? "" : "s"}`;
  const rect = g.getBoundingClientRect();
  const wrapRect = el("graphCanvas").parentElement.getBoundingClientRect();
  tip.style.left = `${rect.left - wrapRect.left + rect.width / 2}px`;
  tip.style.top = `${rect.top - wrapRect.top - 10}px`;
}

// ---------- Day 29: pan + zoom ----------

function updateViewportTransform() {
  const viewport = el("graphCanvas").querySelector(".graph-viewport");
  if (!viewport) return;
  const { x, y, k } = state.graphView;
  viewport.setAttribute("transform", `translate(${x},${y}) scale(${k})`);
  renderMinimapViewportRect();
}

function zoomGraphBy(factor, cx, cy) {
  const cache = state.graphLayoutCache;
  if (!cache) return;
  const view = state.graphView;
  const newK = Math.max(GRAPH_ZOOM_MIN, Math.min(GRAPH_ZOOM_MAX, view.k * factor));
  const centerX = cx ?? cache.width / 2;
  const centerY = cy ?? cache.height / 2;
  // Keep the point under the cursor (or the canvas center, for the
  // +/- buttons) fixed on screen while the scale changes.
  view.x = centerX - ((centerX - view.x) / view.k) * newK;
  view.y = centerY - ((centerY - view.y) / view.k) * newK;
  view.k = newK;
  updateViewportTransform();
}

el("graphCanvas").addEventListener(
  "wheel",
  (ev) => {
    if (!state.graphLayoutCache) return;
    ev.preventDefault();
    const rect = el("graphCanvas").getBoundingClientRect();
    const scaleX = state.graphLayoutCache.width / rect.width;
    const scaleY = state.graphLayoutCache.height / rect.height;
    const cx = (ev.clientX - rect.left) * scaleX;
    const cy = (ev.clientY - rect.top) * scaleY;
    zoomGraphBy(ev.deltaY < 0 ? 1.1 : 1 / 1.1, cx, cy);
  },
  { passive: false }
);

el("graphZoomIn").addEventListener("click", () => zoomGraphBy(1.25));
el("graphZoomOut").addEventListener("click", () => zoomGraphBy(1 / 1.25));
el("graphZoomReset").addEventListener("click", () => {
  state.graphView = { x: 0, y: 0, k: 1 };
  updateViewportTransform();
});

function startGraphPan(startEvent) {
  clearGraphSelection();
  const canvas = el("graphCanvas");
  canvas.classList.add("graph-panning");
  const rect = canvas.getBoundingClientRect();
  const scaleX = (state.graphLayoutCache?.width || 800) / rect.width;
  const scaleY = (state.graphLayoutCache?.height || 480) / rect.height;

  const onMove = (ev) => {
    state.graphView.x += (ev.movementX || 0) * scaleX;
    state.graphView.y += (ev.movementY || 0) * scaleY;
    updateViewportTransform();
  };
  const onUp = () => {
    canvas.classList.remove("graph-panning");
    canvas.removeEventListener("pointermove", onMove);
    canvas.removeEventListener("pointerup", onUp);
    canvas.removeEventListener("pointerleave", onUp);
  };
  canvas.addEventListener("pointermove", onMove);
  canvas.addEventListener("pointerup", onUp);
  canvas.addEventListener("pointerleave", onUp);
}

// ---------- Day 29: minimap ----------

function renderMinimap() {
  const svg = el("graphMinimap");
  const cache = state.graphLayoutCache;
  if (!cache) {
    svg.hidden = true;
    return;
  }
  svg.hidden = false;
  svg.innerHTML = "";

  const xs = Object.values(cache.positions).map((p) => p.x);
  const ys = Object.values(cache.positions).map((p) => p.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const spanX = Math.max(1, maxX - minX);
  const spanY = Math.max(1, maxY - minY);
  const pad = 8;
  const mmW = 160 - pad * 2;
  const mmH = 110 - pad * 2;
  const scale = Math.min(mmW / spanX, mmH / spanY);

  const toMini = (x, y) => ({
    x: pad + (x - minX) * scale + (mmW - spanX * scale) / 2,
    y: pad + (y - minY) * scale + (mmH - spanY * scale) / 2,
  });

  Object.entries(cache.nodesById).forEach(([id, n]) => {
    const p = cache.positions[id];
    if (!p) return;
    const mp = toMini(p.x, p.y);
    svg.appendChild(
      svgEl("circle", {
        cx: mp.x,
        cy: mp.y,
        r: 1.6,
        class: `graph-minimap-node graph-minimap-node-${n.type}`,
      })
    );
  });

  svg.dataset.minX = minX;
  svg.dataset.minY = minY;
  svg.dataset.scale = scale;
  svg.dataset.offsetX = (mmW - spanX * scale) / 2;
  svg.dataset.offsetY = (mmH - spanY * scale) / 2;

  renderMinimapViewportRect();
}

function renderMinimapViewportRect() {
  const svg = el("graphMinimap");
  const cache = state.graphLayoutCache;
  if (!cache || svg.hidden || svg.dataset.scale === undefined) return;

  svg.querySelectorAll(".graph-minimap-viewport").forEach((r) => r.remove());

  const { x, y, k } = state.graphView;
  const minX = Number(svg.dataset.minX);
  const minY = Number(svg.dataset.minY);
  const scale = Number(svg.dataset.scale);
  const offX = Number(svg.dataset.offsetX);
  const offY = Number(svg.dataset.offsetY);

  // Visible graph-space rect = inverse of the current pan/zoom transform.
  const visX1 = -x / k, visY1 = -y / k;
  const visX2 = (cache.width - x) / k, visY2 = (cache.height - y) / k;

  const toMiniX = (gx) => 8 + (gx - minX) * scale + offX;
  const toMiniY = (gy) => 8 + (gy - minY) * scale + offY;

  const rx1 = Math.max(0, toMiniX(visX1));
  const ry1 = Math.max(0, toMiniY(visY1));
  const rx2 = Math.min(160, toMiniX(visX2));
  const ry2 = Math.min(110, toMiniY(visY2));

  svg.appendChild(
    svgEl("rect", {
      x: rx1,
      y: ry1,
      width: Math.max(2, rx2 - rx1),
      height: Math.max(2, ry2 - ry1),
      class: "graph-minimap-viewport",
    })
  );
}

el("graphMinimap").addEventListener("click", (ev) => {
  const svg = el("graphMinimap");
  const cache = state.graphLayoutCache;
  if (!cache || svg.dataset.scale === undefined) return;
  const rect = svg.getBoundingClientRect();
  const mx = ((ev.clientX - rect.left) / rect.width) * 160;
  const my = ((ev.clientY - rect.top) / rect.height) * 110;
  const minX = Number(svg.dataset.minX);
  const minY = Number(svg.dataset.minY);
  const scale = Number(svg.dataset.scale);
  const offX = Number(svg.dataset.offsetX);
  const offY = Number(svg.dataset.offsetY);
  const graphX = (mx - 8 - offX) / scale + minX;
  const graphY = (my - 8 - offY) / scale + minY;

  const { k } = state.graphView;
  state.graphView.x = cache.width / 2 - graphX * k;
  state.graphView.y = cache.height / 2 - graphY * k;
  updateViewportTransform();
});

// ---------- Day 29: click-to-select + highlight connections ----------

function clearGraphSelection() {
  state.graphSelectedNodeId = null;
  el("graphSelectionPanel").hidden = true;
  const cache = state.graphLayoutCache;
  if (!cache) return;
  Object.values(cache.nodeGroups).forEach((g) => g.classList.remove("graph-node-dimmed", "graph-node-selected"));
  el("graphCanvas").querySelectorAll(".graph-edge").forEach((line) => line.classList.remove("graph-edge-dimmed"));
}

function toggleGraphSelection(nodeId) {
  if (state.graphSelectedNodeId === nodeId) {
    clearGraphSelection();
    return;
  }
  state.graphSelectedNodeId = nodeId;
  applySelectionHighlight(nodeId);
}

// Fades everything except the selected node, its direct neighbors, and
// the edges between them — without re-running layout, since positions
// (including any manual drag) shouldn't reset just from clicking a node.
function applySelectionHighlight(nodeId) {
  const cache = state.graphLayoutCache;
  if (!cache || !cache.nodesById[nodeId]) return;

  const connected = cache.adjacency[nodeId] || new Set();
  Object.entries(cache.nodeGroups).forEach(([id, g]) => {
    const isRelated = id === nodeId || connected.has(id);
    g.classList.toggle("graph-node-dimmed", !isRelated);
    g.classList.toggle("graph-node-selected", id === nodeId);
  });

  // Every undirected edge is registered under both "a|b" and "b|a" in
  // lineByEdgeKey, pointing at the same <line> element — so collecting
  // into a Set first (rather than toggling per key) means each line's
  // class only gets touched once regardless of how many keys hit it.
  const touching = new Set();
  Object.keys(cache.lineByEdgeKey).forEach((key) => {
    const [a, b] = key.split("|");
    if (a === nodeId || b === nodeId) touching.add(cache.lineByEdgeKey[key]);
  });
  el("graphCanvas")
    .querySelectorAll(".graph-edge")
    .forEach((line) => line.classList.toggle("graph-edge-dimmed", !touching.has(line)));

  renderSelectionPanel(cache.nodesById[nodeId], connected, cache.nodesById);
}

function renderSelectionPanel(node, connectedIds, nodesById) {
  const panel = el("graphSelectionPanel");
  panel.hidden = false;
  el("graphSelectionTitle").textContent = (node.type === "note" ? "📝 " : "🧠 ") + node.label;
  el("graphSelectionMeta").textContent = `${node.type === "note" ? "Note" : "Concept"} · ${connectedIds.size} connection${connectedIds.size === 1 ? "" : "s"}`;

  const actions = el("graphSelectionActions");
  actions.innerHTML = "";
  const aiBox = el("graphSelectionAi");
  aiBox.hidden = true;
  aiBox.innerHTML = "";

  if (node.type === "note") {
    const openBtn = document.createElement("button");
    openBtn.textContent = "Open note →";
    openBtn.addEventListener("click", () => openNote(node.noteId));
    actions.appendChild(openBtn);
  } else {
    const fromBtn = document.createElement("button");
    fromBtn.textContent = "Set as path start";
    fromBtn.addEventListener("click", () => {
      el("graphPathFrom").value = node.label;
    });
    actions.appendChild(fromBtn);

    const toBtn = document.createElement("button");
    toBtn.textContent = "Set as path end";
    toBtn.addEventListener("click", () => {
      el("graphPathTo").value = node.label;
    });
    actions.appendChild(toBtn);

    const aiBtn = document.createElement("button");
    aiBtn.textContent = "✨ Ask AI about this";
    aiBtn.addEventListener("click", () => explainConcept(node.label, aiBtn, aiBox));
    actions.appendChild(aiBtn);
  }

  const list = document.createElement("div");
  list.className = "graph-selection-connections";
  const ids = [...connectedIds].slice(0, 10);
  list.innerHTML = ids
    .map((id) => {
      const n = nodesById[id];
      if (!n) return "";
      return `<div class="conn-row"><span>${n.type === "note" ? "📝" : "🧠"} ${escapeHtml(n.label)}</span></div>`;
    })
    .join("");
  if (connectedIds.size > 10) {
    const more = document.createElement("div");
    more.className = "conn-row";
    more.textContent = `+ ${connectedIds.size - 10} more`;
    list.appendChild(more);
  }
  actions.appendChild(list);
}

el("graphSelectionClose").addEventListener("click", clearGraphSelection);

// ---------- Day 29: AI-powered graph insights ----------

async function explainConcept(label, triggerBtn, targetBox) {
  triggerBtn.disabled = true;
  targetBox.hidden = false;
  targetBox.innerHTML = `<span class="spinner"></span> Asking AI…`;
  try {
    const res = await api("/api/graph/explain", { method: "POST", body: JSON.stringify({ concept: label }) });
    targetBox.textContent = res.result;
  } catch (e) {
    if (e.message === "Not logged in") return;
    targetBox.textContent = `Couldn't get an AI explanation (${e.message}).`;
  } finally {
    triggerBtn.disabled = false;
  }
}

async function explainPath(pathLabels, container) {
  container.innerHTML = `<span class="spinner"></span> Asking AI…`;
  container.hidden = false;
  try {
    const res = await api("/api/graph/explain", { method: "POST", body: JSON.stringify({ path: pathLabels }) });
    container.textContent = res.result;
  } catch (e) {
    if (e.message === "Not logged in") return;
    container.textContent = `Couldn't get an AI explanation (${e.message}).`;
  }
}

// ---------- path finder ----------

el("graphPathForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const from = el("graphPathFrom").value.trim();
  const to = el("graphPathTo").value.trim();
  if (!from || !to) return;
  const resultEl = el("graphPathResult");
  resultEl.hidden = false;
  resultEl.className = "graph-path-result";
  resultEl.textContent = "Searching…";
  try {
    const res = await api(`/api/graph/path?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`);
    if (!res.path) {
      resultEl.classList.add("graph-path-none");
      resultEl.textContent = res.message || res.error || "No connection found.";
      el("graphPathClear").hidden = false;
      state.graphPath = null;
      if (state.graphData) renderGraph(state.graphData);
      return;
    }
    const pathLabels = res.path.map((step) => step.concept);
    const chain = pathLabels.join("  →  ");
    resultEl.classList.add("graph-path-found");
    let html = `<div class="graph-path-chain">${escapeHtml(chain)}</div>`;
    if (res.hops.length) {
      html += `<div class="graph-path-hops">${res.hops
        .map(
          (h) =>
            `<div class="graph-path-hop"><b>${escapeHtml(h.from)} → ${escapeHtml(h.to)}</b> via ${h.connectingNotes
              .map((n) => escapeHtml(n.title))
              .join(", ")}</div>`
        )
        .join("")}</div>`;
    }
    if (pathLabels.length > 1) {
      html += `<button type="button" class="graph-path-explain-btn" id="graphPathExplainBtn">✨ Explain this connection</button>`;
      html += `<div class="graph-path-explain-result" id="graphPathExplainResult" hidden></div>`;
    }
    resultEl.innerHTML = html;
    el("graphPathClear").hidden = false;
    const explainBtn = el("graphPathExplainBtn");
    if (explainBtn) {
      explainBtn.addEventListener("click", () => {
        explainBtn.disabled = true;
        explainPath(pathLabels, el("graphPathExplainResult"));
      });
    }

    const pathKeys = res.path.map((step) => conceptKeyClient(step.concept));
    state.graphPath = pathKeys;
    if (state.graphData) renderGraph(state.graphData, pathKeys);
  } catch (err) {
    if (err.message === "Not logged in") return;
    resultEl.classList.add("graph-path-none");
    resultEl.textContent = `Couldn't search for a connection (${err.message}).`;
  }
});

el("graphPathClear").addEventListener("click", () => {
  el("graphPathFrom").value = "";
  el("graphPathTo").value = "";
  el("graphPathResult").hidden = true;
  el("graphPathClear").hidden = true;
  state.graphPath = null;
  if (state.graphData) renderGraph(state.graphData);
});

window.addEventListener("resize", () => {
  if (state.nav === "graph" && state.graphData) renderGraph(state.graphData, state.graphPath);
});

// ============================================================
// Day 35 — Profile & account settings
// ============================================================

// Applies a resolved dark/light theme to <html data-theme="…">. "system"
// isn't a real value here — it resolves to whichever the OS reports via
// prefers-color-scheme at the moment this runs, and again live if the OS
// setting changes while "System" stays selected (see the matchMedia
// listener below).
const systemThemeMql = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;

function applyTheme(preference) {
  const resolved =
    preference === "system" ? (systemThemeMql && systemThemeMql.matches ? "light" : "dark") : preference;
  document.documentElement.dataset.theme = resolved === "light" ? "light" : "dark";
}

if (systemThemeMql) {
  systemThemeMql.addEventListener("change", () => {
    if (state.profile && state.profile.themePreference === "system") applyTheme("system");
  });
}

// Fetched once at boot (so the topnav avatar and the correct theme are
// ready immediately) and re-fetched/re-rendered whenever the Profile
// page itself is opened.
async function initProfile() {
  try {
    const profile = await api("/api/profile");
    state.profile = profile;
    updateTopnavAvatar(profile);
    applyTheme(profile.themePreference);
  } catch (e) {
    /* not logged in yet, or offline — Profile page will retry its own load */
  }
}

function renderProfileAvatar(profile) {
  const img = el("profileAvatarImg");
  const initials = el("profileAvatarInitials");
  const removeBtn = el("profileAvatarRemoveBtn");
  if (profile.avatarUrl) {
    img.src = profile.avatarUrl;
    img.hidden = false;
    initials.hidden = true;
    removeBtn.hidden = false;
  } else {
    img.hidden = true;
    initials.hidden = false;
    initials.textContent = initialsFor(profile);
    removeBtn.hidden = true;
  }
}

// ---------- alternate emails ----------
const MAX_ALT_EMAILS = 3;

// Read-only list on the Profile card: label chip + address, and a small
// "Make primary" link that swaps it with the current primary email.
function renderAltEmailsView(profile) {
  const box = el("viewAltEmails");
  const list = profile.alternateEmails || [];
  if (!list.length) {
    box.textContent = "—";
    return;
  }
  box.innerHTML = `<div class="alt-email-view-list">${list
    .map(
      (a, i) => `
      <div class="alt-email-view-item">
        ${a.label ? `<span class="alt-email-chip">${escapeHtml(a.label)}</span>` : ""}
        <span class="alt-email-address-text">${escapeHtml(a.email)}</span>
        <button type="button" class="alt-email-make-primary" data-index="${i}">Make primary</button>
      </div>`
    )
    .join("")}</div>`;
}

function updateAltEmailControls() {
  const count = el("altEmailList").querySelectorAll(".alt-email-row").length;
  el("altEmailCount").textContent = `${count} / ${MAX_ALT_EMAILS}`;
  el("altEmailAddBtn").disabled = count >= MAX_ALT_EMAILS;
}

function addAltEmailRow(entry = {}, { focus = false } = {}) {
  const list = el("altEmailList");
  if (list.querySelectorAll(".alt-email-row").length >= MAX_ALT_EMAILS) return;
  const row = document.createElement("div");
  row.className = "alt-email-row";
  row.innerHTML = `
    <input type="text" class="alt-email-label" list="altEmailLabels" maxlength="30" placeholder="Label (e.g. Work)" aria-label="Label" />
    <input type="email" class="alt-email-address" maxlength="255" placeholder="backup@example.com" aria-label="Alternate email address" />
    <button type="button" class="alt-email-remove" title="Remove" aria-label="Remove this alternate email">✕</button>`;
  row.querySelector(".alt-email-label").value = entry.label || "";
  row.querySelector(".alt-email-address").value = entry.email || "";
  list.appendChild(row);
  updateAltEmailControls();
  if (focus) row.querySelector(".alt-email-address").focus();
}

function renderAltEmailRows(list) {
  el("altEmailList").innerHTML = "";
  list.forEach((entry) => addAltEmailRow(entry));
  updateAltEmailControls();
}

// Rows left completely empty are simply skipped by the server.
function collectAltEmails() {
  return Array.from(el("altEmailList").querySelectorAll(".alt-email-row")).map((row) => ({
    label: row.querySelector(".alt-email-label").value.trim(),
    email: row.querySelector(".alt-email-address").value.trim(),
  }));
}

el("altEmailAddBtn").addEventListener("click", () => addAltEmailRow({}, { focus: true }));
el("altEmailList").addEventListener("click", (e) => {
  const btn = e.target.closest(".alt-email-remove");
  if (!btn) return;
  btn.closest(".alt-email-row").remove();
  updateAltEmailControls();
});

el("viewAltEmails").addEventListener("click", async (e) => {
  const btn = e.target.closest(".alt-email-make-primary");
  if (!btn || !state.profile) return;
  const alternates = state.profile.alternateEmails || [];
  const chosen = alternates[Number(btn.dataset.index)];
  if (!chosen) return;
  const oldPrimary = state.profile.email;
  const ok = await confirmDialog(
    oldPrimary
      ? `Make ${chosen.email} your primary email? ${oldPrimary} will move to your alternate emails.`
      : `Make ${chosen.email} your primary email?`,
    { title: "Change primary email", confirmLabel: "Make primary" }
  );
  if (!ok) return;
  // Swap: the chosen address becomes primary, the old primary (if any) takes
  // its place in the alternates list.
  const remaining = alternates.filter((_, i) => i !== Number(btn.dataset.index));
  if (oldPrimary) remaining.push({ email: oldPrimary, label: "Former primary" });
  try {
    const updated = await api("/api/profile", {
      method: "PATCH",
      body: JSON.stringify({ email: chosen.email, alternateEmails: remaining }),
    });
    renderProfile(updated);
    showToast("Primary email changed.");
  } catch (err) {
    if (err.message === "Not logged in") return;
    showToast(`Couldn't change your primary email (${err.message}).`, "error");
  }
});

function renderProfileViewFields(profile) {
  el("viewFullName").textContent = profile.fullName || "—";
  el("viewEmail").textContent = profile.email || "—";
  renderAltEmailsView(profile);
  el("viewPhone").textContent = profile.phone || "—";
  el("viewRole").textContent = profile.role || "—";
  el("viewBio").textContent = profile.bio || "—";
}

function fillProfileEditForm(profile) {
  el("editFullName").value = profile.fullName || "";
  el("editEmail").value = profile.email || "";
  renderAltEmailRows(profile.alternateEmails || []);
  el("editPhone").value = profile.phone || "";
  el("editRole").value = profile.role || "";
  el("editBio").value = profile.bio || "";
  el("editBioCount").textContent = `${(profile.bio || "").length} / 500`;
}

function renderProfile(profile) {
  state.profile = profile;

  renderProfileAvatar(profile);
  el("profileDisplayName").textContent = profile.fullName || profile.username;
  el("profileUsername").textContent = profile.username;
  el("accountUsername").textContent = profile.username;

  const roleDisplay = el("profileRoleDisplay");
  if (profile.role) {
    roleDisplay.textContent = profile.role;
    roleDisplay.hidden = false;
  } else {
    roleDisplay.hidden = true;
  }

  const memberSince = profile.createdAt
    ? new Date(profile.createdAt).toLocaleDateString(undefined, { month: "long", year: "numeric" })
    : "—";
  el("profileMemberSince").textContent = memberSince;
  el("accountMemberSince").textContent = memberSince;

  renderProfileViewFields(profile);
  fillProfileEditForm(profile);

  el("prefEmailDigest").checked = !!profile.notificationPrefs.emailDigest;
  el("prefTaskReminders").checked = !!profile.notificationPrefs.taskReminders;
  el("prefAiUpdates").checked = !!profile.notificationPrefs.aiUpdates;

  document.querySelectorAll(".theme-option").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.themeChoice === profile.themePreference);
  });

  updateTopnavAvatar(profile);
}

async function loadProfileView() {
  el("profileLoading").hidden = false;
  el("profileBody").hidden = true;
  try {
    const profile = await api("/api/profile");
    renderProfile(profile);
    setProfileEditing(false);
    el("profileLoading").hidden = true;
    el("profileBody").hidden = false;
  } catch (e) {
    if (e.message === "Not logged in") return;
    el("profileLoading").hidden = true;
    showError(`Couldn't load your profile (${e.message}).`, loadProfileView);
  }
}

function setProfileEditing(editing) {
  state.profileEditing = editing;
  el("profileViewFields").hidden = editing;
  el("profileEditForm").hidden = !editing;
  el("profileEditBtn").hidden = editing;
  el("profileFormError").hidden = true;
  if (editing && state.profile) fillProfileEditForm(state.profile);
}

function showProfileSavedBadge() {
  const badge = el("profileSavedBadge");
  badge.hidden = false;
  clearTimeout(showProfileSavedBadge._timer);
  showProfileSavedBadge._timer = setTimeout(() => (badge.hidden = true), 1800);
}

el("profileEditBtn").addEventListener("click", () => setProfileEditing(true));
el("profileCancelBtn").addEventListener("click", () => setProfileEditing(false));

el("editBio").addEventListener("input", (e) => {
  el("editBioCount").textContent = `${e.target.value.length} / 500`;
});

el("profileEditForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (state.profileSaving) return;
  state.profileSaving = true;
  const errBox = el("profileFormError");
  errBox.hidden = true;
  const saveBtn = el("profileSaveBtn");
  const originalLabel = saveBtn.textContent;
  saveBtn.disabled = true;
  saveBtn.textContent = "Saving…";
  try {
    const updated = await api("/api/profile", {
      method: "PATCH",
      body: JSON.stringify({
        fullName: el("editFullName").value.trim(),
        email: el("editEmail").value.trim(),
        alternateEmails: collectAltEmails(),
        phone: el("editPhone").value.trim(),
        role: el("editRole").value.trim(),
        bio: el("editBio").value.trim(),
      }),
    });
    renderProfile(updated);
    setProfileEditing(false);
    showProfileSavedBadge();
  } catch (err) {
    if (err.message === "Not logged in") return;
    errBox.textContent = err.message;
    errBox.hidden = false;
  } finally {
    state.profileSaving = false;
    saveBtn.disabled = false;
    saveBtn.textContent = originalLabel;
  }
});

// ---------- avatar upload / remove ----------
el("profileAvatarInput").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = ""; // allow re-selecting the same file later
  if (!file) return;

  if (file.size > 5 * 1024 * 1024) {
    showToast("Image must be under 5MB.", "error");
    return;
  }

  const form = new FormData();
  form.append("file", file);
  try {
    const res = await api("/api/profile/avatar", { method: "POST", body: form });
    const updated = { ...state.profile, avatarUrl: res.avatarUrl };
    renderProfile(updated);
    showToast("Profile photo updated.");
  } catch (err) {
    if (err.message === "Not logged in") return;
    showToast(`Couldn't upload that photo (${err.message}).`, "error");
  }
});

el("profileAvatarRemoveBtn").addEventListener("click", async () => {
  const ok = await confirmDialog("Remove your profile photo?", {
    title: "Remove photo",
    confirmLabel: "Remove",
  });
  if (!ok) return;
  try {
    await api("/api/profile/avatar", { method: "DELETE" });
    const updated = { ...state.profile, avatarUrl: null };
    renderProfile(updated);
    showToast("Profile photo removed.");
  } catch (err) {
    if (err.message === "Not logged in") return;
    showToast(`Couldn't remove the photo (${err.message}).`, "error");
  }
});

// ---------- change password ----------
el("passwordForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errBox = el("passwordFormError");
  const successBox = el("passwordFormSuccess");
  errBox.hidden = true;
  successBox.hidden = true;

  const currentPassword = el("currentPassword").value;
  const newPassword = el("newPassword").value;
  const confirmPassword = el("confirmPassword").value;

  if (newPassword !== confirmPassword) {
    errBox.textContent = "New password and confirmation don't match.";
    errBox.hidden = false;
    return;
  }

  const saveBtn = el("passwordSaveBtn");
  saveBtn.disabled = true;
  const originalLabel = saveBtn.textContent;
  saveBtn.textContent = "Updating…";
  try {
    await api("/api/profile/password", {
      method: "POST",
      body: JSON.stringify({ currentPassword, newPassword }),
    });
    el("passwordForm").reset();
    successBox.hidden = false;
  } catch (err) {
    if (err.message === "Not logged in") return;
    errBox.textContent = err.message;
    errBox.hidden = false;
  } finally {
    saveBtn.disabled = false;
    saveBtn.textContent = originalLabel;
  }
});

// ---------- notification preferences ----------
document.querySelectorAll("#profileBody input[data-pref]").forEach((input) => {
  input.addEventListener("change", async () => {
    const key = input.dataset.pref;
    const prevValue = !input.checked; // what it was before this toggle
    try {
      const updated = await api("/api/profile", {
        method: "PATCH",
        body: JSON.stringify({ notificationPrefs: { [key]: input.checked } }),
      });
      state.profile = updated;
      showToast("Preferences saved.");
    } catch (err) {
      if (err.message === "Not logged in") return;
      input.checked = prevValue; // revert on failure
      showToast(`Couldn't save that preference (${err.message}).`, "error");
    }
  });
});

// ---------- theme preference ----------
document.querySelectorAll(".theme-option").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const choice = btn.dataset.themeChoice;
    document.querySelectorAll(".theme-option").forEach((b) => b.classList.toggle("active", b === btn));
    applyTheme(choice);
    try {
      const updated = await api("/api/profile", {
        method: "PATCH",
        body: JSON.stringify({ themePreference: choice }),
      });
      state.profile = updated;
    } catch (err) {
      if (err.message === "Not logged in") return;
      showToast(`Couldn't save theme preference (${err.message}).`, "error");
    }
  });
});

// ---------- account settings: logout / delete account ----------
el("profileLogoutBtn").addEventListener("click", logout);

el("profileDeleteAccountBtn").addEventListener("click", () => {
  el("deleteAccountPassword").value = "";
  el("deleteAccountError").hidden = true;
  el("deleteAccountOverlay").hidden = false;
  el("deleteAccountPassword").focus();
});

el("deleteAccountCancelBtn").addEventListener("click", () => {
  el("deleteAccountOverlay").hidden = true;
});

el("deleteAccountOverlay").addEventListener("click", (e) => {
  if (e.target === el("deleteAccountOverlay")) el("deleteAccountOverlay").hidden = true;
});

el("deleteAccountConfirmBtn").addEventListener("click", async () => {
  const password = el("deleteAccountPassword").value;
  const errBox = el("deleteAccountError");
  errBox.hidden = true;
  if (!password) {
    errBox.textContent = "Enter your password to confirm.";
    errBox.hidden = false;
    return;
  }
  const confirmBtn = el("deleteAccountConfirmBtn");
  confirmBtn.disabled = true;
  const originalLabel = confirmBtn.textContent;
  confirmBtn.textContent = "Deleting…";
  try {
    await api("/api/profile", { method: "DELETE", body: JSON.stringify({ password }) });
    window.location.href = "login.html";
  } catch (err) {
    errBox.textContent = err.message;
    errBox.hidden = false;
  } finally {
    confirmBtn.disabled = false;
    confirmBtn.textContent = originalLabel;
  }
});

// ============================================================
// Day 36 — Smart Notifications
//
// GET /api/notifications loads whatever's already stored; POST
// /api/notifications/generate runs the rule engine (+ AI digest, when
// configured and enabled) and returns the updated list. Both return the
// same shape, so one render function (renderNotifications) covers
// either. The backend already dedupes per rule/subject/day, so calling
// generate() on every boot never spams duplicates.
// ============================================================
const NOTIF_ICONS = {
  task_due: "⏰",
  task_stale: "🗂️",
  note_stale: "📝",
  digest: "✨",
};

// Real "on time" delivery: a rule (task due soon, etc.) is only useful
// if it reaches you close to when it actually fires, not just whenever
// you next happen to open the notif panel. Two additive pieces make
// that true without any server push infrastructure:
//   1. generateNotifications() is re-run automatically on an interval
//      (see the boot section below), not just once at page load.
//   2. Any notification that's brand new since the last check also
//      fires a real OS-level desktop notification (Web Notification
//      API) — so it's visible even if this tab isn't the one focused —
//      as long as the browser has been granted permission.
// Both degrade silently: on a browser/OS that blocks Notification, or
// while the tab is closed, the in-app bell (unchanged) is still there
// next time the app is opened.
const NOTIF_POLL_INTERVAL_MS = 3 * 60 * 1000; // 3 minutes
let notifSeenIds = new Set();

function maybeDesktopNotify(newOnes) {
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  newOnes.slice(0, 4).forEach((n) => {
    try {
      const dn = new Notification(n.title, { body: n.message, tag: `ai-notes-${n.id}`, icon: "assets/logo-128.png" });
      dn.onclick = () => {
        window.focus();
        dn.close();
      };
    } catch (e) {
      // Some browsers throw if Notification is constructed from a
      // background/service-worker-less context — never let that break
      // the in-app feed.
    }
  });
}

function applyNotifResult(body) {
  const incoming = body.notifications || [];
  const newOnes = incoming.filter((n) => !n.read && !notifSeenIds.has(String(n.id)));
  notifSeenIds = new Set(incoming.map((n) => String(n.id)));

  state.notifications = incoming;
  state.notifUnreadCount = body.unreadCount || 0;
  renderNotifBadge();
  renderNotifications();

  if (newOnes.length) maybeDesktopNotify(newOnes);
}

function requestDesktopNotifyPermission() {
  if (typeof Notification === "undefined") return;
  if (Notification.permission === "default") {
    // Ask once, quietly — never re-prompt every boot if they dismissed it.
    Notification.requestPermission().catch(() => {});
  }
}

function renderNotifBadge() {
  const badge = el("notifCount");
  if (state.notifUnreadCount > 0) {
    badge.textContent = state.notifUnreadCount > 99 ? "99+" : String(state.notifUnreadCount);
    badge.hidden = false;
  } else {
    badge.hidden = true;
  }
}

function renderNotifications() {
  const list = el("notifList");
  if (!state.notifications.length) {
    list.innerHTML = `<div class="notif-empty">You're all caught up — no notifications yet.</div>`;
    return;
  }

  list.innerHTML = state.notifications
    .map(
      (n) => `
      <div class="notif-row ${n.read ? "" : "unread"}" data-id="${n.id}">
        <div class="notif-row-icon ${n.type}">${NOTIF_ICONS[n.type] || "🔔"}</div>
        <div class="notif-row-body">
          <div class="notif-row-title">
            ${escapeHtml(n.title)}
            ${n.aiGenerated ? `<span class="notif-ai-chip">AI</span>` : ""}
          </div>
          <div class="notif-row-message">${escapeHtml(n.message)}</div>
          <div class="notif-row-meta">
            <span class="notif-row-time">${timeAgo(n.createdAt)}</span>
            <button class="notif-row-dismiss" data-dismiss="${n.id}" title="Dismiss">Dismiss</button>
          </div>
        </div>
      </div>`
    )
    .join("");

  list.querySelectorAll(".notif-row").forEach((row) => {
    row.addEventListener("click", (e) => {
      if (e.target.closest("[data-dismiss]")) return;
      markNotificationRead(row.dataset.id);
    });
  });
  list.querySelectorAll("[data-dismiss]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      dismissNotification(btn.dataset.dismiss);
    });
  });
}

async function loadNotifications() {
  try {
    const body = await api("/api/notifications");
    applyNotifResult(body);
  } catch (e) {
    // Quiet failure — the bell just won't show a count until next load.
  }
}

async function generateNotifications() {
  if (state.notifRefreshing) return;
  state.notifRefreshing = true;
  el("btnNotifRefresh").disabled = true;
  el("btnNotifRefresh").textContent = "Checking…";
  try {
    const body = await api("/api/notifications/generate", { method: "POST" });
    applyNotifResult(body);
  } catch (e) {
    // Leave whatever was already loaded in place.
  } finally {
    state.notifRefreshing = false;
    el("btnNotifRefresh").disabled = false;
    el("btnNotifRefresh").textContent = "Refresh";
  }
}

async function markNotificationRead(id) {
  const notif = state.notifications.find((n) => String(n.id) === String(id));
  if (!notif || notif.read) return;
  notif.read = true; // optimistic
  state.notifUnreadCount = Math.max(0, state.notifUnreadCount - 1);
  renderNotifBadge();
  renderNotifications();
  try {
    await api(`/api/notifications/${id}`, { method: "PATCH", body: JSON.stringify({ read: true }) });
  } catch (e) {
    loadNotifications(); // resync on failure
  }
}

async function dismissNotification(id) {
  const wasUnread = state.notifications.some((n) => String(n.id) === String(id) && !n.read);
  state.notifications = state.notifications.filter((n) => String(n.id) !== String(id));
  if (wasUnread) state.notifUnreadCount = Math.max(0, state.notifUnreadCount - 1);
  renderNotifBadge();
  renderNotifications();
  try {
    await api(`/api/notifications/${id}`, { method: "DELETE" });
  } catch (e) {
    loadNotifications(); // resync on failure
  }
}

async function markAllNotificationsRead() {
  state.notifications.forEach((n) => (n.read = true));
  state.notifUnreadCount = 0;
  renderNotifBadge();
  renderNotifications();
  try {
    await api("/api/notifications/read-all", { method: "POST" });
  } catch (e) {
    loadNotifications(); // resync on failure
  }
}

function toggleNotifPanel(force) {
  state.notifPanelOpen = force === undefined ? !state.notifPanelOpen : force;
  el("notifPanel").hidden = !state.notifPanelOpen;
}

el("btnNotif").addEventListener("click", (e) => {
  e.stopPropagation();
  toggleNotifPanel();
});
document.addEventListener("click", () => toggleNotifPanel(false));
el("notifPanel").addEventListener("click", (e) => e.stopPropagation());
el("btnNotifRefresh").addEventListener("click", generateNotifications);
el("btnNotifReadAll").addEventListener("click", markAllNotificationsRead);

// ============================================================
// Boot — gate the whole app behind a valid session
// ============================================================
(async function boot() {
  const loggedIn = await checkAuthAndHealth();
  if (!loggedIn) {
    window.location.href = "login.html";
    return;
  }
  checkServerTranscriptionAvailable();
  checkAiAvailable();
  checkOcrAvailable();
  await initProfile();
  await loadAndRender();
  updateTasksBadge();
  await loadTags();
  loadDashboard();
  el("notifWrap").hidden = false;
  try {
    const notice = sessionStorage.getItem("pendingSecurityNotice");
    if (notice) {
      sessionStorage.removeItem("pendingSecurityNotice");
      showToast(notice, "error");
    }
  } catch (e) {
    // Storage unavailable — nothing to show, nothing to break.
  }
  requestDesktopNotifyPermission();
  generateNotifications(); // rule engine (+ AI digest if configured/enabled) runs on boot...
  setInterval(generateNotifications, NOTIF_POLL_INTERVAL_MS); // ...and every few minutes after, so a "due soon" reminder actually arrives close to when it's due, not just whenever the app is next opened
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) generateNotifications(); // also catch up immediately when you switch back to this tab
  });
})();
