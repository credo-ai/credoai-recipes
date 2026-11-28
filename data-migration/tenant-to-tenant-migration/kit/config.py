"""Tenant settings from the environment or .env. Shell variables win over .env values."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class TenantConfig:
    base_url: str
    tenant: str
    api_key: str
    verify_ssl: bool = True


def load_tenant_config(prefix: str, env_file=".env") -> TenantConfig:
    """Read {prefix}_BASE_URL, {prefix}_TENANT and {prefix}_API_KEY."""
    load_dotenv(env_file, override=False)
    names = [f"{prefix}_BASE_URL", f"{prefix}_TENANT", f"{prefix}_API_KEY"]
    missing = [n for n in names if not os.getenv(n)]
    if missing:
        raise ConfigError(f"Missing required settings: {', '.join(missing)} (see .env.example)")
    return TenantConfig(
        base_url=os.environ[names[0]].rstrip("/"),
        tenant=os.environ[names[1]],
        api_key=os.environ[names[2]],
        verify_ssl=os.getenv("VERIFY_SSL", "true").strip().lower() != "false",
    )


def email_domains(name):
    """Lowercased email domains from a comma-separated setting, without a leading @. Empty if unset."""
    return {d.strip().lower().lstrip("@") for d in os.getenv(name, "").split(",") if d.strip()}
