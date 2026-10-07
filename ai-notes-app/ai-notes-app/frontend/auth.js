// ============================================================
// AI Notes — Day 8 — login & signup form handling
// Day 24: added inline field validation and a spinner on submit.
// Shared by login.html and signup.html (only one of the two
// forms exists on a given page, so only its listener attaches).
// ============================================================
const API_BASE = window.APP_CONFIG.API_BASE_URL;

const USERNAME_RE = /^[A-Za-z0-9_.-]{3,32}$/;

function showAuthError(message) {
  const box = document.getElementById("authError");
  box.textContent = message;
  box.hidden = false;
}

function clearAuthError() {
  document.getElementById("authError").hidden = true;
}

// ---------- inline field validation ----------
// Each field gets validated on blur (so we're not shouting "invalid" at
// someone who's still mid-keystroke) and again on submit. A field that's
// already marked invalid re-validates on every keystroke so the red
// state clears the moment it becomes valid, instead of waiting for the
// next blur.
function fieldError(input) {
  return input.parentElement.querySelector(".auth-field-error");
}

function validateUsername(input, { silent = false } = {}) {
  const value = input.value.trim();
  const valid = USERNAME_RE.test(value);
  if (!silent) {
    input.classList.toggle("invalid", !valid);
    const err = fieldError(input);
    if (err) err.textContent = "3-32 characters: letters, numbers, underscore, dot, or hyphen.";
  }
  return valid;
}

function validatePassword(input, { silent = false } = {}) {
  const valid = input.value.length >= 8;
  if (!silent) {
    input.classList.toggle("invalid", !valid);
    const err = fieldError(input);
    if (err) err.textContent = "At least 8 characters.";
  }
  return valid;
}

function wireInlineValidation(usernameInput, passwordInput, validator) {
  usernameInput.addEventListener("blur", () => validateUsername(usernameInput));
  usernameInput.addEventListener("input", () => {
    if (usernameInput.classList.contains("invalid")) validateUsername(usernameInput);
  });
  passwordInput.addEventListener("blur", () => validatePassword(passwordInput));
  passwordInput.addEventListener("input", () => {
    if (passwordInput.classList.contains("invalid")) validatePassword(passwordInput);
  });
}

async function submitAuth(path, { username, password }, submitBtn, busyLabel) {
  clearAuthError();
  submitBtn.disabled = true;
  const originalHtml = submitBtn.innerHTML;
  submitBtn.innerHTML = `<span class="spinner"></span> ${busyLabel}`;

  try {
    const res = await fetch(API_BASE + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ username, password }),
    });
    const body = await res.json().catch(() => ({}));

    if (!res.ok) {
      showAuthError(body.error || "Something went wrong. Please try again.");
      return false;
    }
    // Suspicious-activity warning (a burst of failed attempts right
    // before this successful login) survives the redirect via
    // sessionStorage — app.js shows it as a toast once the app loads,
    // since there's no UI to show it in on this page before navigating away.
    if (body.suspiciousActivity) {
      try {
        sessionStorage.setItem("pendingSecurityNotice", body.suspiciousActivity);
      } catch (e) {
        // Private-browsing mode or storage disabled — not worth blocking login over.
      }
    }
    window.location.href = "index.html";
    return true;
  } catch (e) {
    showAuthError(`Couldn't reach the server (${e.message}). Is the backend running?`);
    return false;
  } finally {
    submitBtn.disabled = false;
    submitBtn.innerHTML = originalHtml;
  }
}

const loginForm = document.getElementById("loginForm");
if (loginForm) {
  const usernameInput = document.getElementById("username");
  const passwordInput = document.getElementById("password");
  loginForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    clearAuthError();
    // Login only needs "did you type something" — full pattern/length
    // rules are a signup concern (we don't want to reject a legit
    // existing account just because it predates today's stricter regex).
    if (!usernameInput.value.trim() || !passwordInput.value) {
      showAuthError("Enter your username and password.");
      return;
    }
    const username = usernameInput.value.trim();
    const password = passwordInput.value;
    await submitAuth("/api/auth/login", { username, password }, document.getElementById("submitBtn"), "Logging in…");
  });
}

const signupForm = document.getElementById("signupForm");
if (signupForm) {
  const usernameInput = document.getElementById("username");
  const passwordInput = document.getElementById("password");
  wireInlineValidation(usernameInput, passwordInput);
  signupForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    clearAuthError();
    const usernameOk = validateUsername(usernameInput);
    const passwordOk = validatePassword(passwordInput);
    if (!usernameOk || !passwordOk) {
      showAuthError("Fix the highlighted fields before continuing.");
      return;
    }
    const username = usernameInput.value.trim();
    const password = passwordInput.value;
    await submitAuth("/api/auth/register", { username, password }, document.getElementById("submitBtn"), "Creating account…");
  });
}
