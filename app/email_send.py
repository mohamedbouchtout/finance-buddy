"""Email sending. SMTP if configured; otherwise log to stdout (dev mode)."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.config import EMAIL_FROM, SMTP_HOST, SMTP_PASS, SMTP_PORT, SMTP_USER

log = logging.getLogger(__name__)


def send_email(to: str, subject: str, body: str, html_body: str | None = None) -> None:
    if not SMTP_HOST:
        log.info("---- DEV EMAIL ----\nTo: %s\nSubject: %s\n\n%s\n-------------------", to, subject, body)
        print(f"\n📧 [DEV] Would send email to {to}\n   Subject: {subject}\n   Body:\n{body}\n")
        return

    msg = EmailMessage()
    msg["From"] = EMAIL_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        if SMTP_USER:
            s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)
