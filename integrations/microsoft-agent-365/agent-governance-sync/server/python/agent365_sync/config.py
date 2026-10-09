"""Application configuration loaded from environment variables / `.env`.

Two sets of Microsoft credentials exist on purpose. Reading the catalog uses
app-only auth (`MS_CLIENT_SECRET`), so a scheduled run needs no human. Blocking
and unblocking an agent cannot: Microsoft rejects an app-only token for those
two calls with `424 ... without user context is not supported`, so `--enforce`
runs as a signed-in admin instead — see `delegated_auth.py` and README Step 5.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings

# Anchor the .env to the cookbook root — `server/python/agent365_sync/config.py`
# is three directories down from it — so the command works from any cwd, and a
# scheduled run does not depend on where cron happens to start it.
_COOKBOOK_ROOT = Path(__file__).resolve().parents[3]
_ENV_FILE = _COOKBOOK_ROOT / ".env"
_DEFAULT_TOKEN_CACHE = _COOKBOOK_ROOT / ".agent365_token.json"

# The `type` Microsoft Graph reports for a catalog package. `shared` and `lob`
# are agents your own people built or uploaded; the other two are Microsoft's
# and third-party store apps.
PACKAGE_TYPES = frozenset({"firstparty", "thirdparty", "shared", "lob"})


class Settings(BaseSettings):
    # Credo AI tenant (public integration API)
    credo_api_key: str = ""
    credo_tenant: str = ""
    credo_api_base_url: str = "https://api.credo.ai"

    # Optional. "<key>+<version>" of a questionnaire to attach to every agent's
    # Use Case, e.g. "AGENT+1". Blank attaches nothing.
    credo_intake_questionnaire: str = ""

    # Microsoft Entra app registration
    ms_tenant_id: str = ""
    ms_client_id: str = ""
    ms_client_secret: str = ""

    # Which catalog entries count as agents worth governing. The catalog also
    # lists every Microsoft and third-party app, hundreds of records that were
    # never built by your organization. `all` syncs the lot.
    agent365_package_types: str = "shared,lob"

    # Where the admin's refresh token lives. Blank = `.agent365_token.json` at
    # the cookbook root (gitignored).
    agent365_token_cache: str = ""

    log_level: str = "INFO"

    model_config = {
        "env_file": _ENV_FILE,
        "env_file_encoding": "utf-8",
    }

    @field_validator("agent365_package_types")
    @classmethod
    def _check_package_types(cls, value: str) -> str:
        parts = {p.strip().lower() for p in value.split(",") if p.strip()}
        if parts == {"all"}:
            return "all"
        unknown = parts - PACKAGE_TYPES
        if not parts or unknown:
            raise ValueError(
                f"expected `all` or a comma-separated list of {sorted(PACKAGE_TYPES)}"
                + (f", got unknown {sorted(unknown)}" if unknown else "")
            )
        return ",".join(sorted(parts))

    @field_validator("credo_intake_questionnaire")
    @classmethod
    def _check_questionnaire(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        key, sep, version = value.rpartition("+")
        if not (key and sep and version.isdigit()):
            raise ValueError('expected "<key>+<version>", for example "AGENT+1"')
        return value

    def in_scope_types(self) -> frozenset[str] | None:
        """Lower-cased package types to sync, or None for every type."""
        if self.agent365_package_types == "all":
            return None
        return frozenset(self.agent365_package_types.split(","))

    def intake_questionnaire(self) -> tuple[str, int] | None:
        """(key, version) to attach, or None. Credo AI ids a questionnaire
        version as "<key>+<version>" but attaching takes the two split."""
        if not self.credo_intake_questionnaire:
            return None
        key, _, version = self.credo_intake_questionnaire.rpartition("+")
        return key, int(version)

    def token_cache_path(self) -> Path:
        if self.agent365_token_cache:
            return Path(self.agent365_token_cache).expanduser()
        return _DEFAULT_TOKEN_CACHE


@lru_cache
def get_settings() -> Settings:
    return Settings()
