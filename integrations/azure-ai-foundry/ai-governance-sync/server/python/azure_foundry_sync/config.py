"""Application configuration loaded from environment variables / `.env`.

Single tenant, single Azure AD app registration — this cookbook is meant to be run by one
customer against their own Credo AI tenant and their own Azure subscription.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings

# Anchor the .env to the cookbook root — `server/python/azure_foundry_sync/config.py` is two
# directories down from it — so the command works from any cwd, and a scheduled run does not
# depend on where cron happens to start it.
_COOKBOOK_ROOT = Path(__file__).resolve().parents[3]
_ENV_FILE = _COOKBOOK_ROOT / ".env"
_CONFIG_DIR = _COOKBOOK_ROOT / "config"


class Settings(BaseSettings):
    # Same env var names the official `pycredoai` SDK reads itself — `CredoAI()` picks these up
    # with no args needed. Also used by the private-API calls this cookbook still makes
    # (the "Source" record, entity type ids for custom fields, and the policy controls — see
    # credo_private.py).
    credoai_api_key: str = ""
    credoai_tenant: str = ""
    credoai_api_url: str = "https://api.credo.ai"

    # Where /auth/exchange + /api/v2/{tenant}/... (the private API credo_private.py talks to)
    # actually live. On a real hosted tenant this is the same host
    # as CREDOAI_API_URL (the default below), but some local dev setups split the public
    # Integration API and the private API across two different services/ports — set this
    # explicitly if yours does.
    credoai_private_api_url: str = ""

    # Azure AD app registration (client-credentials flow)
    azure_tenant_id: str = ""
    azure_app_client_id: str = ""
    azure_client_secret: str = ""

    # What to sync — each domain can be switched off independently, e.g. for a tenant that
    # already has its own questionnaire and only wants models + controls.
    sync_models: bool = True
    sync_controls: bool = True
    sync_custom_fields: bool = True
    sync_questionnaire: bool = True

    # "1" = create a fresh DEFAULT_AZURE questionnaire. "2" = fetch the tenant's existing
    # questionnaire (CREDO_QUESTIONNAIRE_ID + CREDO_QUESTIONNAIRE_VERSION) and merge its
    # sections with the Azure template rather than replacing it.
    credo_questionnaire_options: str = "1"
    credo_questionnaire_id: str = ""
    credo_questionnaire_version: str = ""

    log_level: str = "INFO"

    model_config = {
        "env_file": _ENV_FILE,
        "env_file_encoding": "utf-8",
    }

    @property
    def effective_private_api_url(self) -> str:
        return self.credoai_private_api_url or self.credoai_api_url


@lru_cache
def get_settings() -> Settings:
    return Settings()


def config_path(*parts: str) -> Path:
    """Resolve a path under the cookbook's config/ directory (policy controls, custom fields, questionnaire)."""
    return _CONFIG_DIR.joinpath(*parts)
