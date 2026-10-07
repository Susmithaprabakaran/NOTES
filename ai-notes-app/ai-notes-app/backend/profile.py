# ============================================================
# AI Notes — Day 35 — user profile & account settings
#
#   Open Profile
#         |
#   GET /api/profile              -> name/email/phone/role/bio/avatar/
#                                     theme/notification prefs
#         |
#   Click "Edit Profile" -> edit fields -> Save
#         |
#   PATCH /api/profile            -> validates + persists whichever
#                                     fields were sent (partial update)
#         |
#   Change Password  -> POST /api/profile/password   (needs current pw)
#   Upload avatar     -> POST /api/profile/avatar     (multipart)
#   Remove avatar     -> DELETE /api/profile/avatar
#   Theme / notifications -> same PATCH /api/profile as the text fields,
#                             just different keys — one endpoint, so the
#                             frontend doesn't need to know these are
#                             "different kinds" of settings
#   Delete account    -> DELETE /api/profile          (re-enter password)
#   Alternate emails  -> same PATCH /api/profile, key "alternateEmails":
#                        a list of {"email", "label"} (max 3), replaced as
#                        a whole each time it's sent
#
# Avatar uploads reuse the exact validation approach Day 21 established
# for note attachments: a small extension allow-list, a size cap, and a
# magic-byte sniff on the actual file bytes — never trusting the
# browser-supplied filename or Content-Type. The file is saved on disk
# under a fresh server-generated name, same reasoning as attachments.py:
# sanitizing a user-supplied filename is cleaning up untrusted input;
# never using it as a path removes the problem instead.
# ============================================================
import json
import os
import re
import uuid

from flask import Blueprint, current_app, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

from auth import current_user_id, login_required, MIN_PASSWORD_LENGTH
from db import get_db

bp = Blueprint("profile", __name__, url_prefix="/api/profile")

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,24}$")
PHONE_RE = re.compile(r"^[0-9+\-.() \t]{7,24}$")

VALID_THEMES = {"dark", "light", "system"}
DEFAULT_NOTIFICATION_PREFS = {"emailDigest": True, "taskReminders": True, "aiUpdates": False}

MAX_NAME_LENGTH = 80
MAX_ROLE_LENGTH = 80
MAX_BIO_LENGTH = 500

# Alternate (backup) email addresses on top of the primary one. Capped so
# the profile card stays readable and the JSON column stays tiny.
MAX_ALTERNATE_EMAILS = 3
MAX_ALT_EMAIL_LABEL_LENGTH = 30

# Same allow-list approach as Day 21's attachments.py, scoped down to just
# the image types that make sense for a profile photo (no PDFs/audio here).
ALLOWED_AVATAR_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
AVATAR_MAGIC_SIGNATURES = {
    "png": [b"\x89PNG\r\n\x1a\n"],
    "jpg": [b"\xff\xd8\xff"],
    "jpeg": [b"\xff\xd8\xff"],
    "gif": [b"GIF87a", b"GIF89a"],
    # webp's real signature spans two non-contiguous fields (RIFF....WEBP);
    # checked specially in _looks_like_valid_image() below.
    "webp": [b"RIFF"],
}
MAX_AVATAR_BYTES = 5 * 1024 * 1024  # 5MB — generous for a profile photo, nowhere near a note attachment


def avatar_dir():
    """Resolves the directory avatars are stored in — same UPLOAD_FOLDER
    env var / config key as Day 21's attachments.upload_folder(), just
    with its own "avatars" subfolder so the two never collide."""
    configured = current_app.config.get("UPLOAD_FOLDER") or os.environ.get("UPLOAD_FOLDER")
    base = configured or os.path.join(os.path.dirname(__file__), "uploads")
    path = os.path.join(base, "avatars")
    os.makedirs(path, exist_ok=True)
    return path


def _looks_like_valid_image(ext, data):
    if ext == "webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return any(data.startswith(sig) for sig in AVATAR_MAGIC_SIGNATURES.get(ext, []))


def _delete_avatar_file(filename):
    if not filename:
        return
    path = os.path.join(avatar_dir(), filename)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass  # best-effort cleanup — a stray orphaned file isn't worth failing the request over


def _parse_notification_prefs(raw):
    try:
        parsed = json.loads(raw) if raw else {}
        if not isinstance(parsed, dict):
            parsed = {}
    except (TypeError, ValueError):
        parsed = {}
    return {**DEFAULT_NOTIFICATION_PREFS, **parsed}


def _parse_alternate_emails(raw):
    """Stored `alternate_emails` column (JSON text) -> list of
    {"email", "label"} dicts. Never raises: a corrupt or empty value just
    yields an empty list, same forgiving approach as the notification
    prefs above."""
    try:
        parsed = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    result = []
    for item in parsed:
        if isinstance(item, dict) and isinstance(item.get("email"), str) and item["email"].strip():
            label = item.get("label")
            result.append(
                {
                    "email": item["email"].strip(),
                    "label": label.strip() if isinstance(label, str) else "",
                }
            )
    return result[:MAX_ALTERNATE_EMAILS]


def _validate_alternate_emails(incoming):
    """Validates the client-supplied list. Returns (cleaned_list, None) on
    success or (None, error_message) on failure.

    Accepts either {"email", "label"} objects or bare email strings (handy
    for API callers). Blank entries — an empty row left in the edit form —
    are skipped rather than rejected. Duplicates within the list are
    rejected (case-insensitively); the "must differ from the primary
    email" rule needs the primary too, so it's checked by the caller."""
    if not isinstance(incoming, list):
        return None, "alternateEmails must be a list."

    cleaned = []
    seen = set()
    for item in incoming:
        if isinstance(item, str):
            email, label = item, ""
        elif isinstance(item, dict):
            email, label = item.get("email"), item.get("label")
        else:
            return None, "Each alternate email must be an object with an email address."

        email = email.strip() if isinstance(email, str) else ""
        label = label.strip() if isinstance(label, str) else ""
        if not email:
            continue  # an untouched, empty row from the form

        if not EMAIL_RE.match(email):
            return None, f"\"{email[:60]}\" doesn't look like a valid email address."
        if len(label) > MAX_ALT_EMAIL_LABEL_LENGTH:
            return None, f"Labels must be under {MAX_ALT_EMAIL_LABEL_LENGTH} characters."
        if email.lower() in seen:
            return None, f"{email} is listed more than once."
        seen.add(email.lower())
        cleaned.append({"email": email, "label": label})

    if len(cleaned) > MAX_ALTERNATE_EMAILS:
        return None, f"You can add up to {MAX_ALTERNATE_EMAILS} alternate emails."
    return cleaned, None


def serialize_profile(user):
    return {
        "id": user["id"],
        "username": user["username"],
        "fullName": user.get("full_name") or "",
        "email": user.get("email") or "",
        "phone": user.get("phone") or "",
        "alternateEmails": _parse_alternate_emails(user.get("alternate_emails")),
        "role": user.get("role") or "",
        "bio": user.get("bio") or "",
        "avatarUrl": f"/api/profile/avatar/{user['avatar_filename']}" if user.get("avatar_filename") else None,
        "themePreference": user.get("theme_preference") or "dark",
        "notificationPrefs": _parse_notification_prefs(user.get("notification_prefs")),
        "createdAt": user["created_at"].isoformat() if user.get("created_at") else None,
    }


PROFILE_COLUMNS = (
    "id, username, full_name, email, alternate_emails, phone, role, bio, avatar_filename, "
    "theme_preference, notification_prefs, password_hash, created_at"
)


def _fetch_user(cur, user_id):
    cur.execute(f"SELECT {PROFILE_COLUMNS} FROM users WHERE id = %s", (user_id,))
    return cur.fetchone()


@bp.get("")
@login_required
def get_profile():
    db = get_db()
    with db.cursor() as cur:
        user = _fetch_user(cur, current_user_id())
    if not user:
        return jsonify({"error": "User not found."}), 404
    return jsonify(serialize_profile(user)), 200


@bp.patch("")
@login_required
def update_profile():
    body = request.get_json(silent=True) or {}
    db = get_db()
    with db.cursor() as cur:
        user = _fetch_user(cur, current_user_id())
        if not user:
            return jsonify({"error": "User not found."}), 404

        updates = {}

        if "fullName" in body:
            full_name = (body.get("fullName") or "").strip()
            if len(full_name) > MAX_NAME_LENGTH:
                return jsonify({"error": f"Full name must be under {MAX_NAME_LENGTH} characters."}), 400
            updates["full_name"] = full_name

        if "email" in body:
            email = (body.get("email") or "").strip()
            if email and not EMAIL_RE.match(email):
                return jsonify({"error": "That doesn't look like a valid email address."}), 400
            updates["email"] = email or None

        if "phone" in body:
            phone = (body.get("phone") or "").strip()
            if phone and not PHONE_RE.match(phone):
                return jsonify({"error": "That doesn't look like a valid phone number."}), 400
            updates["phone"] = phone or None

        if "alternateEmails" in body:
            alternates, err = _validate_alternate_emails(body.get("alternateEmails"))
            if err:
                return jsonify({"error": err}), 400
            updates["alternate_emails"] = json.dumps(alternates)

        if "role" in body:
            role = (body.get("role") or "").strip()
            if len(role) > MAX_ROLE_LENGTH:
                return jsonify({"error": f"Role must be under {MAX_ROLE_LENGTH} characters."}), 400
            updates["role"] = role

        if "bio" in body:
            bio = (body.get("bio") or "").strip()
            if len(bio) > MAX_BIO_LENGTH:
                return jsonify({"error": f"Bio must be under {MAX_BIO_LENGTH} characters."}), 400
            updates["bio"] = bio

        if "themePreference" in body:
            theme = body.get("themePreference")
            if theme not in VALID_THEMES:
                return jsonify({"error": "themePreference must be one of: dark, light, system."}), 400
            updates["theme_preference"] = theme

        if "notificationPrefs" in body:
            incoming = body.get("notificationPrefs")
            if not isinstance(incoming, dict):
                return jsonify({"error": "notificationPrefs must be an object."}), 400
            merged = _parse_notification_prefs(user.get("notification_prefs"))
            for key in DEFAULT_NOTIFICATION_PREFS:
                if key in incoming:
                    merged[key] = bool(incoming[key])
            updates["notification_prefs"] = json.dumps(merged)

        if not updates:
            return jsonify(serialize_profile(user)), 200

        # An address can't be both the primary email and an alternate.
        # Checked against the *resulting* state (a field that isn't in this
        # request keeps its stored value), so it catches both "add an
        # alternate equal to your primary" and "change your primary to one
        # of your existing alternates".
        effective_primary = updates["email"] if "email" in updates else user.get("email")
        effective_alternates = (
            json.loads(updates["alternate_emails"])
            if "alternate_emails" in updates
            else _parse_alternate_emails(user.get("alternate_emails"))
        )
        if effective_primary and any(
            a["email"].lower() == effective_primary.lower() for a in effective_alternates
        ):
            return (
                jsonify({"error": "Your primary email can't also be listed as an alternate email."}),
                400,
            )

        # Case-insensitive email uniqueness, excluding the current account
        # itself (so re-saving your own unchanged email doesn't 409).
        if updates.get("email"):
            cur.execute(
                "SELECT id FROM users WHERE lower(email) = lower(%s) AND id != %s",
                (updates["email"], current_user_id()),
            )
            if cur.fetchone():
                return jsonify({"error": "That email is already in use by another account."}), 409

        set_clause = ", ".join(f"{col} = %s" for col in updates)
        values = list(updates.values()) + [current_user_id()]
        cur.execute(f"UPDATE users SET {set_clause} WHERE id = %s", values)
        updated = _fetch_user(cur, current_user_id())
    db.commit()
    return jsonify(serialize_profile(updated)), 200


@bp.post("/password")
@login_required
def change_password():
    body = request.get_json(silent=True) or {}
    current_password = body.get("currentPassword") or ""
    new_password = body.get("newPassword") or ""

    if not current_password or not new_password:
        return jsonify({"error": "Current and new password are both required."}), 400
    if len(new_password) < MIN_PASSWORD_LENGTH:
        return jsonify({"error": f"New password must be at least {MIN_PASSWORD_LENGTH} characters."}), 400

    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT password_hash FROM users WHERE id = %s", (current_user_id(),))
        user = cur.fetchone()
        if not user or not check_password_hash(user["password_hash"], current_password):
            return jsonify({"error": "Current password is incorrect."}), 401
        if check_password_hash(user["password_hash"], new_password):
            return jsonify({"error": "New password must be different from your current one."}), 400

        new_hash = generate_password_hash(new_password)
        cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (new_hash, current_user_id()))
    db.commit()
    return jsonify({"ok": True}), 200


@bp.post("/avatar")
@login_required
def upload_avatar():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded."}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected."}), 400

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_AVATAR_EXTENSIONS:
        return jsonify({"error": "Allowed image types: PNG, JPG, GIF, WEBP."}), 400

    data = file.read()
    if not data:
        return jsonify({"error": "Uploaded file is empty."}), 400
    if len(data) > MAX_AVATAR_BYTES:
        return jsonify({"error": "Image must be under 5MB."}), 400
    if not _looks_like_valid_image(ext, data):
        return jsonify({"error": "File content doesn't match a valid image — try a different file."}), 400

    new_filename = f"{uuid.uuid4().hex}.{ext}"
    with open(os.path.join(avatar_dir(), new_filename), "wb") as f:
        f.write(data)

    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT avatar_filename FROM users WHERE id = %s", (current_user_id(),))
        old = cur.fetchone()
        cur.execute("UPDATE users SET avatar_filename = %s WHERE id = %s", (new_filename, current_user_id()))
    db.commit()

    _delete_avatar_file(old["avatar_filename"] if old else None)

    return jsonify({"avatarUrl": f"/api/profile/avatar/{new_filename}"}), 200


@bp.get("/avatar/<path:filename>")
@login_required
def get_avatar(filename):
    # Deliberately not scoped to "only your own avatar" — avatars are meant
    # to be visible wherever a user is referenced, same as any avatar image
    # on the web. The filename itself is an unguessable uuid4, not a
    # sequential id, so this doesn't leak who has one just by probing.
    return send_from_directory(avatar_dir(), filename)


@bp.delete("/avatar")
@login_required
def delete_avatar():
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT avatar_filename FROM users WHERE id = %s", (current_user_id(),))
        user = cur.fetchone()
        cur.execute("UPDATE users SET avatar_filename = NULL WHERE id = %s", (current_user_id(),))
    db.commit()
    _delete_avatar_file(user["avatar_filename"] if user else None)
    return jsonify({"ok": True}), 200


@bp.delete("")
@login_required
def delete_account():
    body = request.get_json(silent=True) or {}
    password = body.get("password") or ""
    if not password:
        return jsonify({"error": "Enter your password to confirm account deletion."}), 400

    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT password_hash, avatar_filename FROM users WHERE id = %s", (current_user_id(),))
        user = cur.fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            return jsonify({"error": "Password is incorrect."}), 401
        # ON DELETE CASCADE on every other table's user_id FK (notes, tasks,
        # attachments, flowcharts, study_materials) means this one DELETE
        # is enough to remove everything the account ever created.
        cur.execute("DELETE FROM users WHERE id = %s", (current_user_id(),))
    db.commit()

    _delete_avatar_file(user["avatar_filename"] if user else None)
    session.clear()
    return jsonify({"ok": True}), 200
