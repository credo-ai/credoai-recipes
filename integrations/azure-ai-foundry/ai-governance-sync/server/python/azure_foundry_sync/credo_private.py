"""The parts of this cookbook that can't go through the official `pycredoai` SDK.

Everything else goes through the SDK (see `sync.py`). Three things stay on Credo AI's private v2
JSON:API instead:

1. **Creating the `Source` record.** `source` on a Model is a reference to a `Source` record, and
   creating one has no public Integration API endpoint at all.
2. **Looking up entity type ids.** A custom field is scoped to an entity type by id, the ids differ
   per tenant, and neither the Integration API nor the SDK can list them.
3. **Policy control versions.** The public SDK's `EvidenceRequirement` schema only has `type`,
   `description`, and `required` — it has no fields for `code_template`, `mathematical_bound`, or
   `governance_bounds`, which is exactly the rich content the MSFT-* controls' evidence
   requirements actually carry (the runnable Azure evaluator skeleton scripts and their pass/fail
   thresholds). Routing controls through the public SDK would silently strip all of that. This is
   a gap in the public API/SDK, not a style choice.

Same auth mechanism the SDK's own token exchange is built on (`/auth/exchange`), just a different
host path (`/api/v2/{tenant}/...` instead of `/api/v1/integration/...`). On a real hosted tenant
that's the same host as `CREDOAI_API_URL`; some local dev setups split the two across different
services, so `settings.effective_private_api_url` falls back to `CREDOAI_PRIVATE_API_URL` when set.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any

import httpx

from azure_foundry_sync.config import Settings

logger = logging.getLogger("azure_foundry_sync.credo_private")

MAX_ATTEMPTS = 3
TOKEN_REFRESH_THRESHOLD = timedelta(minutes=5)
TOKEN_LIFETIME = timedelta(hours=1)


class CredoPrivateApiError(Exception):
    def __init__(self, message: str, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class CredoPrivateClient:
    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        if not settings.credoai_api_key or not settings.credoai_tenant:
            raise CredoPrivateApiError(
                "CREDOAI_API_KEY and CREDOAI_TENANT are required"
            )
        self.api_key = settings.credoai_api_key
        self.tenant = settings.credoai_tenant
        self.base_url = settings.effective_private_api_url.rstrip("/")
        self._http = http or httpx.Client(timeout=30)
        self._token: str | None = None
        self._token_expiry: datetime | None = None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "CredoPrivateClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- auth / transport ------------------------------------------------------

    def _should_refresh(self) -> bool:
        if self._token is None or self._token_expiry is None:
            return True
        return datetime.now() + TOKEN_REFRESH_THRESHOLD >= self._token_expiry

    def _fetch_token(self) -> str:
        resp = self._http.post(
            f"{self.base_url}/auth/exchange",
            json={"api_token": self.api_key, "tenant": self.tenant},
        )
        if resp.status_code != 200:
            raise CredoPrivateApiError(
                f"/auth/exchange failed: {resp.status_code}",
                resp.status_code,
                resp.text,
            )
        token = resp.json().get("access_token")
        if not token:
            raise CredoPrivateApiError(
                "/auth/exchange response has no access_token field"
            )
        return token

    def _headers(self) -> dict[str, str]:
        if self._should_refresh():
            self._token = self._fetch_token()
            self._token_expiry = datetime.now() + TOKEN_LIFETIME
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/vnd.api+json",
            "Accept": "application/vnd.api+json",
        }

    def _request(
        self,
        method: str,
        path: str,
        json: dict | None = None,
        params: dict | None = None,
    ) -> httpx.Response:
        """Return the raw response for every status < 500 — callers branch on 201 vs 422 vs other
        themselves, since "already exists" (422) is an expected outcome here, not an error."""
        url = f"{self.base_url}/api/v2/{self.tenant}/{path.lstrip('/')}"
        for attempt in range(MAX_ATTEMPTS):
            resp = self._http.request(
                method, url, json=json, params=params, headers=self._headers()
            )
            if resp.status_code == 401:
                self._token = None  # force a fresh exchange next call
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
            return resp
        raise CredoPrivateApiError(
            f"{method} {path} failed after {MAX_ATTEMPTS} attempts"
        )

    def _paginated(self, path: str, *, page_limit: int) -> list[dict]:
        items: list[dict] = []
        params: dict[str, Any] = {"page[limit]": page_limit}
        while True:
            resp = self._request("GET", path, params=params)
            if resp.status_code == 404:
                return items  # base doesn't exist yet — no versions, not an error
            if resp.status_code != 200:
                raise CredoPrivateApiError(
                    f"GET {path} failed: {resp.status_code}",
                    resp.status_code,
                    resp.text,
                )
            body = resp.json()
            items.extend(body.get("data", []))
            cursor = (body.get("meta") or {}).get("after")
            if not cursor:
                return items
            params["page[after]"] = cursor

    # -- source ----------------------------------------------------------------

    def create_source(self, name: str) -> httpx.Response:
        return self._request(
            "POST",
            "sources",
            {"data": {"type": "sources", "attributes": {"name": name}}},
        )

    # -- entity types ------------------------------------------------------------

    def entity_type_ids(self) -> dict[str, str]:
        """Entity type name -> id (e.g. `use_case` -> the tenant's Use Case type id).

        Custom fields are scoped by entity type *id*, and the ids are generated per tenant. Neither
        the Integration API nor the SDK can list them, so they come from here.
        """
        ids: dict[str, str] = {}
        for entity_type in self._paginated("entity_types", page_limit=100):
            name = (entity_type.get("attributes") or {}).get("name")
            if name and entity_type.get("id"):
                ids[name] = entity_type["id"]
        return ids

    # -- policy controls ---------------------------------------------------

    def create_control_base(self, control_key: str) -> httpx.Response:
        return self._request(
            "POST",
            "policy_control_bases",
            {"data": {"attributes": {"id": control_key}, "type": "resource-type"}},
        )

    def list_control_versions(
        self, control_key: str, page_limit: int = 100
    ) -> list[dict]:
        return self._paginated(
            f"policy_control_bases/{control_key}/versions", page_limit=page_limit
        )

    def create_control_version(self, control_key: str, payload: dict) -> httpx.Response:
        return self._request(
            "POST", f"policy_control_bases/{control_key}/versions", payload
        )

    def update_control_version(self, version_id: str, payload: dict) -> httpx.Response:
        return self._request("PATCH", f"policy_control_versions/{version_id}", payload)
