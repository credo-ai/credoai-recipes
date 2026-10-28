"""Application configuration loaded from environment variables / `.env`.

Single tenant, single GitHub repo token — this cookbook is meant to be run by one customer against
their own Credo AI tenant.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings

# Anchor the .env and the state file to the cookbook root — `server/python/github_gate/config.py`
# is three directories down from it — so every command sees the same files from any cwd.
_COOKBOOK_ROOT = Path(__file__).resolve().parents[3]
_ENV_FILE = _COOKBOOK_ROOT / ".env"
STATE_FILE = _COOKBOOK_ROOT / ".github-gate-state.json"


class Settings(BaseSettings):
    # Same env var names the official `pycredoai` SDK reads itself, and the private-API calls
    # (webhook registration) reuse them — see credo_private.py.
    credoai_api_key: str = ""
    credoai_tenant: str = ""
    credoai_api_url: str = "https://api.credo.ai"

    # Only for local dev setups that split the public and private APIs across two hosts.
    credoai_private_api_url: str = ""

    # Token with commit-status write on the repo(s) being gated.
    github_token: str = ""

    # Public HTTPS URL of this service; the Credo AI webhook is registered as
    # {gate_public_base_url}/webhooks/credo.
    gate_public_base_url: str = ""
    gate_host: str = "127.0.0.1"
    gate_port: int = 8321
    gate_shared_secret: str = ""

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
