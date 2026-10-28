"""Server-side exchange of the Credo AI API key for a short-lived token.

The API key never leaves this backend — the browser only ever sees the
derived token. One token serves both Credo AI APIs (integration service
and platform API): both accept it as plain ``Authorization: Bearer`` on
data calls — the integration token reads the ``/api/v2/{tenant}`` platform
API, including questionnaire preview.

- ``POST {integration}/api/v1/integration/auth/token`` with ``X-API-Key`` +
  ``X-Tenant`` headers (the token encodes the tenant). Uses pycredoai's
  ``credoai.auth.create_auth_handler_async`` (confirmed against the
  installed package — this is the one Credo AI call in this recipe made from
  Python; every other call happens in the browser).
"""

import logging
from typing import Any

from credoai.auth import AuthenticationError as CredoSdkAuthError
from credoai.auth import create_auth_handler_async

from app.config import Settings

logger = logging.getLogger(__name__)


class CredoAuthError(Exception):
    """Raised when a token exchange with Credo AI fails."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_body: Any = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


async def exchange_integration_token(settings: Settings) -> str:
    """Exchange the API key for a bearer token via pycredoai.

    The token is accepted by both the integration service and the private
    backend.
    """
    base_url = (
        f"{settings.credo_integration_api_base_url.rstrip('/')}/api/v1/integration"
    )
    logger.info(
        f"Exchanging API key for an integration bearer token via pycredoai at {base_url}"
    )
    try:
        auth_handler = await create_auth_handler_async(
            base_url=base_url,
            auth_type="api_key",
            token=settings.credo_api_key,
            tenant=settings.credo_tenant,
            timeout=30.0,
        )
    except CredoSdkAuthError as e:
        raise CredoAuthError(
            f"Integration token exchange failed: {e}",
            status_code=e.status_code,
        )
    return auth_handler._access_token
