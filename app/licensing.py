"""Bot license issuance + verification.

Pro users get a license key embedded in their downloaded bot bundle. The bot
phones home to /api/license/verify on startup and periodically. If the user's
subscription lapses, payments.py marks licenses revoked and the next heartbeat
fails — the bot refuses to run.
"""
from __future__ import annotations

import secrets
import time
from typing import Optional

from app.storage import conn, get_user_by_id, user_is_pro


def _new_key() -> str:
    # 4 chunks of 6 hex chars — looks like CC-XXXXXX-XXXXXX-XXXXXX-XXXXXX
    chunks = [secrets.token_hex(3).upper() for _ in range(4)]
    return "CC-" + "-".join(chunks)


def issue_license(user_id: int) -> dict:
    """Create a new active license for the user. Returns the license row."""
    key = _new_key()
    now = int(time.time())
    with conn() as c:
        c.execute(
            "INSERT INTO bot_licenses (user_id, key, status, issued_at) "
            "VALUES (?, ?, 'active', ?)",
            (user_id, key, now),
        )
        row = c.execute("SELECT * FROM bot_licenses WHERE key = ?", (key,)).fetchone()
        return dict(row)


def get_active_license(user_id: int) -> Optional[dict]:
    with conn() as c:
        row = c.execute(
            "SELECT * FROM bot_licenses WHERE user_id = ? AND status = 'active' "
            "ORDER BY issued_at DESC, id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


def list_licenses(user_id: int) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM bot_licenses WHERE user_id = ? ORDER BY issued_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def ensure_license(user_id: int) -> dict:
    """Return existing active license or mint a new one."""
    lic = get_active_license(user_id)
    if lic:
        return lic
    return issue_license(user_id)


def revoke_license(license_id: int) -> None:
    now = int(time.time())
    with conn() as c:
        c.execute(
            "UPDATE bot_licenses SET status = 'revoked', revoked_at = ? WHERE id = ?",
            (now, license_id),
        )


def revoke_all_for_user(user_id: int) -> int:
    """Revoke every active license for a user (called when subscription ends)."""
    now = int(time.time())
    with conn() as c:
        cur = c.execute(
            "UPDATE bot_licenses SET status = 'revoked', revoked_at = ? "
            "WHERE user_id = ? AND status = 'active'",
            (now, user_id),
        )
        return cur.rowcount


def regenerate_license(user_id: int) -> dict:
    """Revoke existing active licenses and issue a fresh one."""
    revoke_all_for_user(user_id)
    return issue_license(user_id)


def verify(key: str, machine_id: str = "", ip: str = "") -> dict:
    """Verify a license key. Returns response dict for the API endpoint.

    {valid, plan, status, expires_at, message}
    """
    if not key or not key.startswith("CC-"):
        return {"valid": False, "status": "invalid", "message": "Malformed license key."}

    with conn() as c:
        row = c.execute("SELECT * FROM bot_licenses WHERE key = ?", (key,)).fetchone()
        if not row:
            return {"valid": False, "status": "unknown", "message": "License not found."}
        lic = dict(row)

    user = get_user_by_id(lic["user_id"])
    if not user:
        return {"valid": False, "status": "orphaned", "message": "License has no associated user."}

    if lic["status"] != "active":
        return {
            "valid": False,
            "status": lic["status"],
            "message": "This license has been revoked. Please regenerate from your dashboard or renew your subscription.",
        }

    if not user_is_pro(user):
        # Self-heal: revoke so we don't keep checking
        revoke_license(lic["id"])
        return {
            "valid": False,
            "status": "no_subscription",
            "plan": user.get("plan", "free"),
            "message": "Your Pro subscription is not active. The bot will stop until you renew.",
        }

    # Touch — record heartbeat
    now = int(time.time())
    with conn() as c:
        c.execute(
            "UPDATE bot_licenses SET last_seen_at = ?, last_seen_ip = ?, "
            "machine_id = COALESCE(NULLIF(?, ''), machine_id) WHERE id = ?",
            (now, ip[:64], machine_id[:128], lic["id"]),
        )

    return {
        "valid": True,
        "status": "active",
        "plan": user["plan"],
        "expires_at": user.get("current_period_end"),
        "message": "OK",
    }
