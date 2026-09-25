"""Client for the Credo AI public integration API — models and use cases.

Endpoint paths, pagination params (``page_limit``/``page_after``) and body
shapes follow the same live API contract every Credo AI integration uses.

Auth: exchange the API token for a bearer token via
``POST /api/v1/integration/auth/token`` with ``X-API-Key`` + ``X-Tenant``. On a
401 the token is refreshed once; 5xx responses are retried with exponential
backoff.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Self

import httpx

from bedrock_sync.config import Settings

logger = logging.getLogger("bedrock_sync.credo")

PAGE_LIMIT = 100
MAX_ATTEMPTS = 3


class CredoApiError(Exception):
    def __init__(self, message: str, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def _source_hint(resp: httpx.Response) -> str:
    """Name the likeliest cause when a create is rejected over `source`.

    `source` is a reference to a Source record, not free text, so a tenant
    without one rejects every create — and the raw 422 says only that a field
    was invalid, which sends people looking in the wrong place.
    """
    if resp.status_code != 422 or "source" not in resp.text.lower():
        return ""
    return (
        " — this usually means the Source record is missing from the tenant. "
        "Run ensure_credo_source.py once (see README Step 3)."
    )


class CredoClient:
    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        if not settings.credo_api_key or not settings.credo_tenant:
            raise CredoApiError("CREDO_API_KEY and CREDO_TENANT are required")
        self.api_key = settings.credo_api_key
        self.tenant = settings.credo_tenant
        self.base_url = settings.credo_api_base_url.rstrip("/")
        # `http` exists so a caller can supply its own client — a shared
        # connection pool, a proxy, or a stub in a test. The CLI never does.
        self._http = http or httpx.Client(timeout=30)
        self._token: str | None = None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- auth / transport ------------------------------------------------------

    def _fetch_token(self) -> str:
        resp = self._http.post(
            f"{self.base_url}/api/v1/integration/auth/token",
            headers={"X-API-Key": self.api_key, "X-Tenant": self.tenant},
        )
        if resp.status_code != 200:
            raise CredoApiError(
                f"integration token exchange failed: {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
            )
        return resp.json()["access_token"]

    def _request(
        self,
        method: str,
        path: str,
        json: dict | None = None,
        params: dict | None = None,
    ) -> httpx.Response:
        url = f"{self.base_url}/api/v1/integration{path}"
        refreshed = False
        for attempt in range(MAX_ATTEMPTS):
            if self._token is None:
                self._token = self._fetch_token()
            resp = self._http.request(
                method,
                url,
                json=json,
                params=params,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "X-Tenant": self.tenant,
                },
            )
            if resp.status_code == 401 and not refreshed:
                self._token = None
                refreshed = True
                continue
            if resp.status_code >= 500 and attempt < MAX_ATTEMPTS - 1:
                delay = 2**attempt
                logger.warning(
                    "%s %s -> %s, retrying in %ss",
                    method,
                    path,
                    resp.status_code,
                    delay,
                )
                time.sleep(delay)
                continue
            if resp.status_code >= 400:
                raise CredoApiError(
                    f"{method} {path} failed: {resp.status_code}{_source_hint(resp)}",
                    status_code=resp.status_code,
                    body=resp.text,
                )
            return resp
        raise CredoApiError(f"{method} {path} failed after {MAX_ATTEMPTS} attempts")

    def _paginated(self, path: str) -> list[dict]:
        items: list[dict] = []
        params: dict[str, Any] = {"page_limit": PAGE_LIMIT}
        while True:
            data = self._request("GET", path, params=params).json()
            items.extend(data["items"])
            pagination = data.get("pagination") or {}
            cursor = pagination.get("next_cursor")
            if not pagination.get("has_more") or not cursor:
                return items
            params["page_after"] = cursor

    # -- models ----------------------------------------------------------------

    def list_models(self) -> list[dict]:
        return self._paginated("/models")

    def create_model(self, attrs: dict) -> dict:
        return self._request("POST", "/models", json=attrs).json()

    def patch_model(self, model_id: str, attrs: dict) -> dict:
        return self._request("PATCH", f"/models/{model_id}", json=attrs).json()

    # -- use cases -------------------------------------------------------------

    def list_use_cases(self) -> list[dict]:
        return self._paginated("/use_cases")

    def create_use_case(self, attrs: dict) -> dict:
        return self._request("POST", "/use_cases", json=attrs).json()

    def patch_use_case(self, use_case_id: str, attrs: dict) -> dict:
        return self._request("PATCH", f"/use_cases/{use_case_id}", json=attrs).json()

    # -- vendors ---------------------------------------------------------------

    def list_vendors(self) -> list[dict]:
        return self._paginated("/vendors")

    def create_vendor(self, name: str) -> dict:
        # `VendorCreate` requires only a name; description, questionnaires and
        # custom fields are optional and deliberately left unset.
        return self._request("POST", "/vendors", json={"name": name}).json()

    def list_vendor_models(self, vendor_id: str) -> list[dict]:
        # Read links per vendor, not per model: a region has a handful of
        # providers and can have hundreds of models, and this covers the same
        # ground either way.
        return self._paginated(f"/vendors/{vendor_id}/models")

    def add_model_vendor(self, model_id: str, vendor_id: str) -> dict:
        return self._request(
            "POST", f"/models/{model_id}/vendors", json={"id": vendor_id}
        ).json()

    # -- use case <-> model relationship ---------------------------------------

    def list_use_case_models(self, use_case_id: str) -> list[dict]:
        return self._paginated(f"/use_cases/{use_case_id}/models")

    def add_use_case_model(self, use_case_id: str, model_id: str) -> dict:
        return self._request(
            "POST", f"/use_cases/{use_case_id}/models", json={"id": model_id}
        ).json()
