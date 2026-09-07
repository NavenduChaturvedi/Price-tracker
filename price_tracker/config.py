"""Runtime configuration.

Everything is read once, at import time, into a single frozen ``Settings``
object. Values come from environment variables (loaded from a local ``.env``
file if present). Every setting has a default so the tool runs out of the box;
only the alert credentials actually need to be supplied by the user.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Load .env from the current working directory (if any) before we read os.environ.
# override=False means a real environment variable always wins over the file,
# which is the least surprising behaviour for CI / containers.
load_dotenv(override=False)


def _get_float(name: str, default: float) -> float:
    """Read a float env var, falling back to ``default`` on missing/garbage input.

    We swallow bad values instead of crashing because a typo in .env should not
    take the whole tool down - a slightly wrong delay is harmless.
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value if value is not None and value.strip() != "" else default


@dataclass(frozen=True)
class Settings:
    # --- politeness -------------------------------------------------------
    request_delay: float
    request_jitter: float
    request_timeout: float
    max_retries: int
    user_agent: str

    # --- storage ---------------------------------------------------------
    db_path: str

    # --- alerts --------------------------------------------------------
    alert_method: str  # auto | telegram | email | none
    telegram_bot_token: str
    telegram_chat_id: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    email_from: str
    email_to: str

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def email_configured(self) -> bool:
        return bool(
            self.smtp_host
            and self.smtp_user
            and self.smtp_password
            and self.email_from
            and self.email_to
        )

    def resolved_alert_method(self) -> str:
        """Turn ``auto`` into a concrete method based on what is configured."""
        if self.alert_method != "auto":
            return self.alert_method
        if self.telegram_configured:
            return "telegram"
        if self.email_configured:
            return "email"
        return "none"


def load_settings() -> Settings:
    return Settings(
        request_delay=_get_float("REQUEST_DELAY", 2.0),
        request_jitter=_get_float("REQUEST_JITTER", 1.0),
        request_timeout=_get_float("REQUEST_TIMEOUT", 20.0),
        max_retries=_get_int("MAX_RETRIES", 3),
        user_agent=_get_str(
            "USER_AGENT",
            "price-tracker-bot/1.0 "
            "(+https://github.com/NavenduChaturvedi/Price-tracker)",
        ),
        db_path=_get_str("DB_PATH", "price_history.db"),
        alert_method=_get_str("ALERT_METHOD", "auto").lower(),
        telegram_bot_token=_get_str("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_get_str("TELEGRAM_CHAT_ID"),
        smtp_host=_get_str("SMTP_HOST", "smtp.gmail.com"),
        smtp_port=_get_int("SMTP_PORT", 587),
        smtp_user=_get_str("SMTP_USER"),
        smtp_password=_get_str("SMTP_PASSWORD"),
        email_from=_get_str("ALERT_EMAIL_FROM"),
        email_to=_get_str("ALERT_EMAIL_TO"),
    )


# Module-level singleton - cheap to build, read everywhere else.
settings = load_settings()
