"""Azure AD authentication and Azure AI Foundry model catalog access.

Auth is the client-credentials flow against an Azure AD app registration (`POST
https://login.microsoftonline.com/{tenant}/oauth2/token`), resource `https://management.azure.com/`.
The catalog call itself (`api.catalog.azureml.ms/asset-gallery/v1.0/models`) only needs a valid bearer
token for that resource — no specific Azure RBAC role assignment is required, since it's public
catalog metadata, not a customer's own AI Foundry project data.
"""

from __future__ import annotations

import logging
import time

import httpx

from azure_foundry_sync.config import Settings

logger = logging.getLogger("azure_foundry_sync.azure_client")

MAX_ATTEMPTS = 3
CATALOG_URL = "https://api.catalog.azureml.ms/asset-gallery/v1.0/models"


class AzureApiError(Exception):
    pass


class AzureClient:
    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        missing = [
            name
            for name, value in (
                ("AZURE_TENANT_ID", settings.azure_tenant_id),
                ("AZURE_APP_CLIENT_ID", settings.azure_app_client_id),
                ("AZURE_CLIENT_SECRET", settings.azure_client_secret),
            )
            if not value
        ]
        if missing:
            raise AzureApiError(f"Missing required config: {', '.join(missing)}")

        self.tenant_id = settings.azure_tenant_id
        self.client_id = settings.azure_app_client_id
        self.client_secret = settings.azure_client_secret
        self._http = http or httpx.Client(timeout=30)
        self._token: str | None = None

    def close(self) -> None:
        self._http.close()

    def get_access_token(self) -> str:
        resp = self._http.post(
            f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "resource": "https://management.azure.com/",
            },
        )
        if resp.status_code != 200:
            raise AzureApiError(
                f"Azure AD token request failed: {resp.status_code} — check "
                f"AZURE_TENANT_ID/AZURE_APP_CLIENT_ID/AZURE_CLIENT_SECRET ({resp.text[:200]})"
            )
        try:
            body = resp.json()
        except ValueError as exc:
            raise AzureApiError(
                "Azure AD token endpoint returned a non-JSON response — usually means "
                "AZURE_TENANT_ID is wrong (the tenant doesn't exist or the app isn't registered "
                "in it)"
            ) from exc
        token = body.get("access_token")
        if not token:
            raise AzureApiError("Azure AD token response has no access_token field")
        return token

    def _headers(self) -> dict[str, str]:
        if self._token is None:
            self._token = self.get_access_token()
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _post_with_retry(self, url: str, payload: dict) -> dict:
        for attempt in range(MAX_ATTEMPTS):
            resp = self._http.post(url, headers=self._headers(), json=payload)
            if resp.status_code == 401:
                self._token = None  # force a fresh token next attempt
            if resp.status_code >= 500 and attempt < MAX_ATTEMPTS - 1:
                delay = 2**attempt
                logger.warning(
                    "POST %s -> %s, retrying in %ss", url, resp.status_code, delay
                )
                time.sleep(delay)
                continue
            if resp.status_code >= 400:
                raise AzureApiError(
                    f"POST {url} failed: {resp.status_code} {resp.text[:200]}"
                )
            return resp.json()
        raise AzureApiError(f"POST {url} failed after {MAX_ATTEMPTS} attempts")

    def list_foundation_models(self) -> list[dict]:
        """Paginated fetch of the standard-paygo model catalog (same filter the original
        integration used — narrows ~thousands of catalog entries to the deployable subset)."""
        all_models: list[dict] = []
        continuation_token: str | None = None
        page = 1
        while True:
            payload = {
                "filters": [
                    {
                        "field": "azureOffers",
                        "operator": "eq",
                        "values": ["standard-paygo"],
                    }
                ]
            }
            if continuation_token:
                payload["continuationToken"] = continuation_token
            response = self._post_with_retry(CATALOG_URL, payload)
            models = response.get("summaries", [])
            all_models.extend(models)
            logger.debug(
                "Fetched page %d: %d models (total %d)",
                page,
                len(models),
                len(all_models),
            )
            continuation_token = response.get("continuationToken")
            if not continuation_token:
                return all_models
            page += 1
