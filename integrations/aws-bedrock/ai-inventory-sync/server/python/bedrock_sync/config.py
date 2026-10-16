"""Application configuration loaded from environment variables / `.env`.

Deliberately absent: AWS credentials. Auth to Bedrock is boto3's default
credential chain (see README "Authentication"), so there is nothing for this
integration to hold, validate or leak — `BEDROCK_REGION` is the only AWS knob,
and even that falls through to boto3's own resolution when blank.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings

# Anchor the .env to the cookbook root — `server/python/bedrock_sync/config.py`
# is three directories down from it — so the command works from any cwd, and a
# scheduled run does not depend on where cron happens to start it.
_COOKBOOK_ROOT = Path(__file__).resolve().parents[3]
_ENV_FILE = _COOKBOOK_ROOT / ".env"


class Settings(BaseSettings):
    # Credo AI tenant (public integration API)
    credo_api_key: str = ""
    credo_tenant: str = ""
    credo_api_base_url: str = "https://api.credo.ai"

    # Bedrock. Blank region = let boto3 resolve it (AWS_REGION /
    # AWS_DEFAULT_REGION / active profile); a NoRegionError then surfaces as a
    # clean message from bedrock.py rather than a traceback.
    bedrock_region: str = ""

    # Foundation models are the region-wide catalog rather than this account's
    # own assets — 100+ records that some tenants want governed and others
    # consider noise. Custom and imported models are always synced.
    bedrock_sync_all_foundation_models: bool = False

    # AgentCore harnesses and runtimes, synced as Credo AI Use Cases. On by
    # default; set false for an account or region that doesn't use AgentCore,
    # where the listings would otherwise fail every run and exit non-zero.
    bedrock_sync_agentcore: bool = True

    log_level: str = "INFO"

    model_config = {
        "env_file": _ENV_FILE,
        "env_file_encoding": "utf-8",
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()
