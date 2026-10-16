"""Settings, loaded from the cookbook's `.env`.

Everything is validated at startup. A server that would accept deliveries on a
guessable URL, or without checking their signatures, refuses to boot rather
than logging a warning and carrying on.
"""

from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger
from pydantic import ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from svix.webhooks import Webhook

# Anchor `.env` and the database to the cookbook root (the directory holding
# README.md), three levels up from this file, so the server behaves the same
# whatever directory it is started from.
COOKBOOK_ROOT = Path(__file__).resolve().parents[3]
SERVER_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = COOKBOOK_ROOT / ".env"

# `secrets.token_urlsafe(32)` produces 43 characters; anything shorter than 32
# is too easy to guess for a URL that should stay private.
MIN_TOKEN_LENGTH = 32

# A Svix signing secret is `whsec_` and the base64 of a 24-byte key.
SIGNING_KEY_BYTES = 24


class CredoSettings(BaseSettings):
    """What it takes to talk to Credo AI. `setup_webhook.py` needs only this."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    credo_api_key: str
    credo_tenant: str
    # Integration Service host; the SDK appends /api/v1/integration itself.
    credo_api_base_url: str = "https://api.credo.ai"
    # Where the buttons in emails point. Never taken from a webhook payload.
    credo_app_base_url: str = "https://app.credo.ai"
    # How long owner, admin and user lookups are reused. One change in the app
    # often sends several events, so this saves repeated reads.
    recipient_cache_seconds: float = 300.0

    @field_validator("credo_api_base_url", "credo_app_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")


class Settings(CredoSettings):
    # ---- Webhook endpoint -------------------------------------------------
    # The secret part of the webhook URL: POST /webhook/<token>.
    webhook_path_token: str
    # The endpoint's signing secret (`whsec_…`). Every delivery's Svix
    # signature is checked against it. `setup_webhook.py` writes it.
    webhook_signing_secret: str

    # ---- Email ------------------------------------------------------------
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str
    # Some sandboxes (Mailtrap's free tier) reject more than one email a second.
    smtp_min_interval_seconds: float = 0.0
    # Log each rendered email instead of sending it.
    mail_dry_run: bool = False

    # ---- Scenarios --------------------------------------------------------
    # Per-stage wording for the stage-change emails (#1, #4).
    stage_messages_path: Path = SERVER_DIR / "stage_messages.yaml"

    # ---- Reminders (#6, #8) -----------------------------------------------
    # false = don't schedule them in this process; run `remind.py` from your
    # own cron instead.
    reminders_in_process: bool = True
    # The timezone the cron expressions, the reminder periods and the dates in
    # reminder emails use.
    reminder_timezone: str = "UTC"
    # Standard 5-field cron expressions.
    pending_controls_cron: str = "0 9 * * mon"
    questionnaire_reminder_cron: str = "0 9 * * *"
    # A questionnaire is reminded about once its due date is this close, or past.
    questionnaire_due_within_days: int = 7

    # ---- Server -----------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8000
    database_path: Path = COOKBOOK_ROOT / "notifier.sqlite3"
    # How many times a delivery is retried (Credo AI or SMTP unavailable)
    # before it is given up on and logged as failed. With the worker's backoff,
    # 9 attempts cover about an hour of outage.
    max_attempts: int = 9
    log_level: str = "INFO"

    @field_validator("webhook_path_token")
    @classmethod
    def _token_long_enough(cls, value: str) -> str:
        value = value.strip()
        if len(value) < MIN_TOKEN_LENGTH:
            raise ValueError(
                f"must be at least {MIN_TOKEN_LENGTH} characters; run setup_webhook.py, "
                "which generates one"
            )
        return value

    @field_validator("webhook_signing_secret")
    @classmethod
    def _secret_looks_right(cls, value: str) -> str:
        value = value.strip()
        hint = (
            "must be the webhook's signing secret (starts with whsec_); run "
            "setup_webhook.py, or copy it from Settings → Webhooks → Signing secret"
        )
        if not value.startswith("whsec_"):
            raise ValueError(hint)
        # `Webhook()` decodes leniently, so it accepts some malformed secrets
        # (`whsec_abc`) and every delivery then fails its signature check.
        # Decode strictly and check the length Svix generates.
        try:
            key = base64.b64decode(value.removeprefix("whsec_"), validate=True)
            Webhook(value)
        except Exception:
            key = b""
        if len(key) != SIGNING_KEY_BYTES:
            raise ValueError(f"isn't a valid signing secret; {hint}")
        return value

    @field_validator("reminder_timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("must be an IANA timezone, e.g. America/New_York") from exc
        return value

    @field_validator("pending_controls_cron", "questionnaire_reminder_cron")
    @classmethod
    def _valid_cron(cls, value: str) -> str:
        try:
            CronTrigger.from_crontab(value)
        except ValueError as exc:
            raise ValueError(
                f"not a 5-field cron expression ({exc}), e.g. '0 9 * * mon'"
            ) from exc
        return value

    @model_validator(mode="after")
    def _smtp_needed_unless_dry_run(self) -> Settings:
        if not self.mail_dry_run and not self.smtp_host:
            raise ValueError("SMTP_HOST is required unless MAIL_DRY_RUN=true")
        return self

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.reminder_timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def describe(exc: ValidationError) -> str:
    """The settings errors, one per line, without the values that failed.

    Pydantic's own message echoes the input, which here is the API key or the
    signing secret, and would print it to the terminal or a log.
    """
    lines = []
    for error in exc.errors(include_input=False, include_url=False):
        name = ".".join(str(part) for part in error["loc"]).upper()
        lines.append(f"  {name}: {error['msg']}" if name else f"  {error['msg']}")
    return "\n".join(lines)
