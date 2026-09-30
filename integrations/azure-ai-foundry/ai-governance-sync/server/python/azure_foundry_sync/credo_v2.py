"""Client for Credo AI's v2 JSON:API — models, policy controls, custom fields, questionnaires.

One API for every domain this cookbook syncs, matching the original `MSFT+CredoAI` integration this
was built from. Auth is `POST /auth/exchange` with `{api_token, tenant}`, and every resource lives
under `/api/v2/{tenant}/...` with JSON:API bodies (`application/vnd.api+json`).

A 4xx here is not always an error: `422` on a create means "already exists" for every one of these
resources, and callers branch on `response.status_code` directly rather than catching an exception —
matching the API's own idiom (there is no separate "exists" check endpoint).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any

import httpx

from azure_foundry_sync.config import Settings

logger = logging.getLogger("azure_foundry_sync.credo_v2")

MAX_ATTEMPTS = 3
TOKEN_REFRESH_THRESHOLD = timedelta(minutes=5)
TOKEN_LIFETIME = timedelta(hours=1)


class CredoApiError(Exception):
    def __init__(self, message: str, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class CredoClientV2:
    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        if not settings.credo_api_token or not settings.credo_tenant:
            raise CredoApiError("CREDO_API_TOKEN and CREDO_TENANT are required")
        self.api_token = settings.credo_api_token
        self.tenant = settings.credo_tenant
        self.base_url = settings.credo_base_path.rstrip("/")
        self._http = http or httpx.Client(timeout=30)
        self._token: str | None = None
        self._token_expiry: datetime | None = None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "CredoClientV2":
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
            json={"api_token": self.api_token, "tenant": self.tenant},
        )
        if resp.status_code != 200:
            raise CredoApiError(
                f"/auth/exchange failed: {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
            )
        token = resp.json().get("access_token")
        if not token:
            raise CredoApiError("/auth/exchange response has no access_token field")
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
        raise CredoApiError(f"{method} {path} failed after {MAX_ATTEMPTS} attempts")

    # -- models --------------------------------------------------------------

    def create_source(self, name: str) -> httpx.Response:
        return self._request(
            "POST",
            "sources",
            {"data": {"type": "sources", "attributes": {"name": name}}},
        )

    def create_model(self, attrs: dict) -> httpx.Response:
        return self._request("POST", "models", {"data": {"attributes": attrs}})

    def list_models(self, page_limit: int = 100) -> list[dict]:
        """Per Trevor Berreth (2026-09-29): models can't be filtered by name server-side yet —
        page through everything and check by name client-side.

        The server caps `page[limit]` at 100 regardless of what's requested, and paginates via
        a `page[after]` cursor returned in `meta.after` — `meta.total_count` confirms when done.
        """
        return self._paginated("models", page_limit=page_limit)

    def _paginated(self, path: str, *, page_limit: int) -> list[dict]:
        items: list[dict] = []
        params: dict[str, Any] = {"page[limit]": page_limit}
        while True:
            resp = self._request("GET", path, params=params)
            if resp.status_code != 200:
                raise CredoApiError(
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

    def publish_control_version(self, version_id: str, payload: dict) -> httpx.Response:
        return self._request("PATCH", f"policy_control_versions/{version_id}", payload)

    # -- custom fields -------------------------------------------------------

    def create_custom_field(self, field_data: dict) -> httpx.Response:
        payload = {"data": {"type": "custom-field", "attributes": field_data}}
        return self._request("POST", "custom_fields", payload)

    # -- questionnaire ---------------------------------------------------------

    def get_questionnaire(self, questionnaire_id: str, version: str) -> httpx.Response:
        return self._request("GET", f"questionnaires/{questionnaire_id}+{version}")

    def create_questionnaire_base(
        self, questionnaire_id: str, name: str
    ) -> httpx.Response:
        payload = {
            "data": {
                "attributes": {"id": questionnaire_id, "name": name},
                "type": "resource-type",
            }
        }
        return self._request("POST", "questionnaire_bases", payload)

    def create_questionnaire_version(
        self, base_id: str, payload: dict
    ) -> httpx.Response:
        return self._request("POST", f"questionnaire_bases/{base_id}/versions", payload)
