// ============================================================
// Frontend config.
//
// Day 8 serves the frontend FROM the Flask app itself (same
// origin, e.g. http://localhost:5000), so API_BASE_URL is just
// empty — fetch('/api/...') already goes to the right place. No
// API key here either: Day 8 replaces that with real per-user
// login (session cookie), which the browser attaches to same-
// origin requests automatically.
// ============================================================
window.APP_CONFIG = {
  API_BASE_URL: "",
};
