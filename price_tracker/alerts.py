"""Alert delivery.

Two backends: Telegram Bot API (recommended - one HTTP call, instant to demo)
and SMTP email. The public function ``send`` never raises: a failed alert is
logged and reported via the return value, because a notification failure must
not abort a price-check run that already did useful work.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

import requests

from .config import settings

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def send(subject: str, body: str) -> bool:
    """Send an alert using the configured method. Returns True on success."""
    method = settings.resolved_alert_method()

    if method == "telegram":
        return _send_telegram(f"{subject}\n\n{body}")
    if method == "email":
        return _send_email(subject, body)

    # method == "none" (or misconfigured "auto"): make the alert visible anyway.
    print("\n[ALERT - no delivery method configured]")
    print(f"  {subject}")
    for line in body.splitlines():
        print(f"  {line}")
    return False


def _send_telegram(text: str) -> bool:
    url = _TELEGRAM_API.format(token=settings.telegram_bot_token)
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": settings.telegram_chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=settings.request_timeout,
        )
        if resp.status_code == 200 and resp.json().get("ok"):
            return True
        print(f"[alert] Telegram API error: HTTP {resp.status_code} {resp.text[:200]}")
        return False
    except (requests.RequestException, ValueError) as exc:
        print(f"[alert] Telegram request failed: {exc}")
        return False


def _send_email(subject: str, body: str) -> bool:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.email_from
    msg["To"] = settings.email_to
    msg.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.request_timeout) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as exc:
        print(f"[alert] email send failed: {exc}")
        return False
