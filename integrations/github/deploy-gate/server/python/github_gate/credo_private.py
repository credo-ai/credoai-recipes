"""The part of this cookbook that can't go through the official `pycredoai` SDK: webhooks.

Registering a webhook with Credo AI has no public Integration API / SDK endpoint yet, so it goes
through the private v2 JSON:API. Same auth as the SDK's own token exchange (`/auth/exchange`), just
a different path (`/api/v2/{tenant}/...` instead of `/api/v1/integration/...`). Everything else —
creating and deleting use cases — uses the SDK (see `credo.py`).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any

import httpx

from github_gate.config import Settings

logger = logging.getLogger("github_gate.credo_private")

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
        self, method: str, path: str, json: dict | None = None
    ) -> httpx.Response:
        """Return the raw response for every status < 500 — callers branch on the status."""
        url = f"{self.base_url}/api/v2/{self.tenant}/{path.lstrip('/')}"
        for attempt in range(MAX_ATTEMPTS):
            resp = self._http.request(method, url, json=json, headers=self._headers())
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

    # -- webhooks ----------------------------------------------------------------

    def create_webhook(
        self, description: str, event_types: list[str], url: str
    ) -> dict:
        attributes = {
            "description": description,
            "event_types": event_types,
            "url": url,
            "authentication_method": "none",
        }
        resp = self._request(
            "POST",
            "webhooks",
            {"data": {"type": "webhook", "attributes": attributes}},
        )
        if resp.status_code not in (200, 201):
            raise CredoPrivateApiError(
                f"create webhook failed: {resp.status_code} {resp.text[:300]}",
                resp.status_code,
                resp.text,
            )
        return resp.json()["data"]

    def delete_webhook(self, webhook_id: str) -> None:
        resp = self._request("DELETE", f"webhooks/{webhook_id}")
        # 404: already gone (e.g. deleted by hand) — that is the goal state, not an error.
        if resp.status_code not in (200, 204, 404):
            raise CredoPrivateApiError(
                f"delete webhook {webhook_id} failed: {resp.status_code} {resp.text[:300]}",
                resp.status_code,
                resp.text,
            )
