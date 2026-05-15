"""Magic-link auth: user enters email -> we email a one-time URL -> click sets a signed cookie."""
from __future__ import annotations

import secrets
from typing import Optional

from fastapi import Request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import APP_BASE_URL, APP_SECRET
from app.email_send import send_email
from app.storage import (
    consume_magic_token,
    create_magic_token,
    get_user,
    upsert_user,
)

COOKIE_NAME = "cc_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

_serializer = URLSafeTimedSerializer(APP_SECRET, salt="cc-session")


def send_magic_link(email: str) -> None:
    email = email.strip().lower()
    upsert_user(email)
    token = secrets.token_urlsafe(32)
    create_magic_token(email, token)
    link = f"{APP_BASE_URL}/auth/verify?token={token}"
    body = (
        f"Click the link below to sign in to Finance Buddy:\n\n{link}\n\n"
        "This link expires in 15 minutes and can only be used once.\n\n"
        "If you didn't request this, you can ignore this email."
    )
    html = (
        f"<p>Click below to sign in to Finance Buddy:</p>"
        f"<p><a href='{link}'>{link}</a></p>"
        "<p>This link expires in 15 minutes and can only be used once.</p>"
        "<p style='color:#888;font-size:12px'>If you didn't request this, ignore this email.</p>"
    )
    send_email(email, "Your Finance Buddy sign-in link", body, html)


def verify_magic_token(token: str) -> Optional[str]:
    return consume_magic_token(token)


def make_session_cookie(email: str) -> str:
    return _serializer.dumps({"email": email})


def read_session_cookie(value: str) -> Optional[str]:
    try:
        data = _serializer.loads(value, max_age=COOKIE_MAX_AGE)
        return data.get("email")
    except BadSignature:
        return None
    except Exception:
        return None


def current_user(request: Request) -> Optional[dict]:
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        return None
    email = read_session_cookie(cookie)
    if not email:
        return None
    return get_user(email)
