import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import get_settings
from app.credo_auth import CredoAuthError, exchange_integration_token

logging.basicConfig(
    level=getattr(logging, get_settings().log_level.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Credo AI Web Components Token Service",
    description=(
        "Mints short-lived Credo AI tokens from the backend-held API key; "
        "all Credo AI data calls, and all configuration, happen in the "
        "browser inside the embedded web components"
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origin_list,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class CredoTokens(BaseModel):
    integration_api_token: str


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


@app.post("/api/token")
async def issue_tokens() -> CredoTokens:
    """Exchange the server-held API key for a short-lived token."""
    settings = get_settings()
    try:
        settings.validate_credo_ai_config()
    except ValueError as err:
        raise HTTPException(status_code=503, detail=str(err)) from err

    try:
        integration_token = await exchange_integration_token(settings)
    except CredoAuthError as err:
        if err.status_code in (401, 403):
            logger.error(f"Credo AI rejected credentials: {err.response_body}")
            raise HTTPException(
                status_code=502,
                detail=(
                    "Credo AI rejected the backend's credentials — "
                    "check CREDO_API_KEY/CREDO_TENANT"
                ),
            ) from err
        logger.error(f"Token exchange failed: {err} — {err.response_body}")
        raise HTTPException(status_code=502, detail=str(err)) from err

    return CredoTokens(integration_api_token=integration_token)
