# ============================================================
# AI Notes — Security hardening: suspicious activity detection
#
#   Login attempt comes in (register/login)
#           |
#   is_rate_limited(username, ip)?  -> too many recent failures for
#                                       either the username or the IP?
#           |                            yes -> 429, attempt not even
#           |                                   tried against the DB
#           v
#   Credentials checked as normal (auth.py, unchanged)
#           |
#   record_attempt(username, ip, success, user_id)  -> always logged,
#                                                        win or lose
#           |
#   suspicious_activity_notifications(user_id)  -> read on demand by
#     notifications.py's rule engine: turns a burst of recent failed
#     attempts against an account that then succeeded ("did that
#     failed attempts I couldn't
#     explain?") into an actual notification in the bell, same as a
#     due task or a quiet high-priority note.
#
# Everything here is pure SQL + small pure-Python helpers — no new
# dependency, same pattern as every other module in this app.
# ============================================================
import time

from flask import request

from db import get_db

# A handful of failures from mistyping your own password shouldn't lock
# you out; a double-digit burst in a few minutes is what brute-forcing
# looks like. Two independent buckets (per-username and per-IP) so a
# single attacker can't dodge the limit just by trying many usernames
# from the same machine, or hammering one account from many machines.
MAX_FAILURES_PER_USERNAME = 8
MAX_FAILURES_PER_IP = 20
WINDOW_MINUTES = 15

# Threshold for flagging a login as "worth telling the account owner
# about" even once it succeeds — a handful of wrong passwords right
# before the right one is a plausible sign someone else was guessing.
SUSPICIOUS_FAILURE_THRESHOLD = 5


def client_ip():
    """Best-effort caller IP. Trusts X-Forwarded-For's first hop only
    when the app is actually behind a known proxy (set TRUST_PROXY=1
    in .env for that deployment) — otherwise a client could just spoof
    the header and blame someone else's IP for their own attempts."""
    import os

    if os.environ.get("TRUST_PROXY") == "1":
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def is_rate_limited(username):
    """True if either this username or this IP has racked up too many
    failed attempts in the last WINDOW_MINUTES. Checked BEFORE the
    password is even compared, so a locked-out attacker can't keep
    probing (and can't tell, from timing, whether the password they
    just sent was actually close)."""
    ip = client_ip()
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """SELECT count(*) AS n FROM login_attempts
               WHERE username = %s AND success = false
                 AND created_at > now() - (%s * interval '1 minute')""",
            (username, WINDOW_MINUTES),
        )
        by_username = cur.fetchone()["n"]
        cur.execute(
            """SELECT count(*) AS n FROM login_attempts
               WHERE ip_address = %s AND success = false
                 AND created_at > now() - (%s * interval '1 minute')""",
            (ip, WINDOW_MINUTES),
        )
        by_ip = cur.fetchone()["n"]
    return by_username >= MAX_FAILURES_PER_USERNAME or by_ip >= MAX_FAILURES_PER_IP


def record_attempt(username, success, user_id=None):
    """Logs one login/register attempt. Never raises — a logging
    failure should never be the reason someone can't log in."""
    try:
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO login_attempts (username, ip_address, success, user_id)
                   VALUES (%s, %s, %s, %s)""",
                (username, client_ip(), success, user_id),
            )
        db.commit()
    except Exception:
        pass


def recent_failure_count(username, minutes=WINDOW_MINUTES):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """SELECT count(*) AS n FROM login_attempts
               WHERE username = %s AND success = false
                 AND created_at > now() - (%s * interval '1 minute')""",
            (username, minutes),
        )
        return cur.fetchone()["n"]
