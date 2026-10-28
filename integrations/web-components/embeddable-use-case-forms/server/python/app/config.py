from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings

# Anchor the .env to the recipe root (next to .env.example) so the app works
# regardless of cwd, matching the other cookbooks
_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    """Backend configuration: only what's needed to mint Credo AI tokens.

    Everything else (tenant-facing business config — field names, permission
    tier, allow lists, etc.) is front-end-owned; see
    ``web/public/config.example.js``. Variable names follow the
    recipe convention:
    ``CREDO_API_KEY``, ``CREDO_TENANT``, ``CREDO_PRIVATE_API_BASE_URL``,
    ``CREDO_INTEGRATION_API_BASE_URL``.
    """

    # Credo AI API. In production both surfaces are served from the same
    # host, but local dev setups can point them at different ports/hosts.
    # Only the integration URL is used to mint the token; the private URL is
    # used by scripts/smoke.py alone (the browser gets its own from
    # config.js `apiUrl`).
    credo_private_api_base_url: str = "https://api.credo.ai"
    credo_integration_api_base_url: str = "https://api.credo.ai"
    credo_api_key: str = ""
    credo_tenant: str = ""

    # Application
    log_level: str = "INFO"

    # Browser origins allowed to call POST /api/token, comma-separated. CORS is
    # the only thing keeping other sites from minting tokens from a visitor's
    # browser, so list your portal's origin(s) exactly.
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    model_config = {
        "env_file": _ENV_FILE,
        "env_file_encoding": "utf-8",
    }

    @field_validator("credo_private_api_base_url", "credo_integration_api_base_url")
    @classmethod
    def validate_credo_api_base_url(cls, v: str) -> str:
        """Ensure the Credo AI API base URL is properly formatted"""
        v = v.rstrip("/")
        if not v.startswith(("http://", "https://")):
            raise ValueError(
                "Credo AI API base URL must start with http:// or https://"
            )
        return v

    @property
    def allowed_origin_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    def validate_credo_ai_config(self) -> None:
        """Validate Credo AI configuration, raise error if incomplete"""
        missing = []
        if not self.credo_api_key:
            missing.append("CREDO_API_KEY")
        if not self.credo_tenant:
            missing.append("CREDO_TENANT")
        if missing:
            raise ValueError(
                f"Credo AI configuration incomplete. Missing: {', '.join(missing)}"
            )


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()
