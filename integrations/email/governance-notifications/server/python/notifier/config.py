"""Settings, loaded from the cookbook's `.env`.

Everything is validated at startup. A server that would accept deliveries on a
guessable URL, or email addresses outside your organization, refuses to boot
rather than logging a warning and carrying on.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchor `.env` and the database to the cookbook root (the directory holding
# README.md), three levels up from this file, so the server behaves the same
# whatever directory it is started from.
COOKBOOK_ROOT = Path(__file__).resolve().parents[3]

# `secrets.token_urlsafe(32)` produces 43 characters; anything shorter than 32
# is too easy to guess for a URL that should stay private.
MIN_TOKEN_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=COOKBOOK_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- Credo AI ---------------------------------------------------------
    credo_api_key: str
    credo_tenant: str
    # Integration Service host; the SDK appends /api/v1/integration itself.
    credo_api_base_url: str = "https://api.credo.ai"
    # Where the "View model" button points. Never taken from a webhook payload.
    credo_app_base_url: str = "https://app.credo.ai"

    # ---- Webhook endpoint -------------------------------------------------
    # The secret part of the webhook URL: POST /webhook/<token>.
    webhook_path_token: str
    # Comma-separated. A recipient whose domain isn't listed is never emailed.
    allowed_email_domains: str

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

    # ---- Server -----------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8000
    database_path: Path = COOKBOOK_ROOT / "notifier.sqlite3"
    # On the very first start, mark every model that already exists as
    # "already emailed". See README "Why existing models are skipped".
    seed_existing_models: bool = True
    # How many times a delivery is retried (Credo AI or SMTP unavailable)
    # before it is given up on and logged as failed.
    max_attempts: int = 6
    log_level: str = "INFO"

    @field_validator("webhook_path_token")
    @classmethod
    def _token_long_enough(cls, value: str) -> str:
        value = value.strip()
        if len(value) < MIN_TOKEN_LENGTH:
            raise ValueError(
                f"must be at least {MIN_TOKEN_LENGTH} characters; generate one with "
                "python -c 'import secrets; print(secrets.token_urlsafe(32))'"
            )
        return value

    @field_validator("allowed_email_domains")
    @classmethod
    def _domains_present(cls, value: str) -> str:
        if not _split_domains(value):
            raise ValueError(
                "list at least one domain, e.g. ALLOWED_EMAIL_DOMAINS=example.com"
            )
        return value

    @field_validator("credo_api_base_url", "credo_app_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @model_validator(mode="after")
    def _smtp_needed_unless_dry_run(self) -> Settings:
        if not self.mail_dry_run and not self.smtp_host:
            raise ValueError("SMTP_HOST is required unless MAIL_DRY_RUN=true")
        return self

    @property
    def allowed_domains(self) -> frozenset[str]:
        return _split_domains(self.allowed_email_domains)


def _split_domains(raw: str) -> frozenset[str]:
    return frozenset(d.strip().lower().lstrip("@") for d in raw.split(",") if d.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
