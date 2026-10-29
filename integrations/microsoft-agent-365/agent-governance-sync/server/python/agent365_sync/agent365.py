"""Microsoft Agent 365 access, through Microsoft Graph.

Two calls, two auth modes — and that split is the one non-obvious thing here:

- **Reading the catalog** (`GET /v1.0/copilot/admin/catalog/packages`) uses an
  app-only token (client credentials), so an unattended schedule works.
- **Blocking / unblocking** (`POST /beta/.../packages/{id}/block|unblock`) uses
  the signed-in admin's delegated token. App-only is rejected for Copilot Studio
  and Agent Builder agents with `424`, and Microsoft's reference lists it as
  unsupported for both calls.

Block and unblock are on Microsoft's `/beta` Graph channel. Microsoft has said
that is their normal release path, but beta endpoints can change without notice.

Microsoft exposes no webhook or change notification for agent creation or
publication, so this module only ever polls.
"""

from __future__ import annotations

import logging
import time

import httpx

from agent365_sync.delegated_auth import DelegatedAuth

logger = logging.getLogger("agent365_sync.agent365")

GRAPH_BASE_URL = "https://graph.microsoft.com"

# Every call gets a timeout: without one a single stalled response hangs a
# scheduled run indefinitely.
REQUEST_TIMEOUT_SECONDS = 30

# Graph throttles with 429 (and sometimes 503) plus a Retry-After header.
_RETRY_STATUSES = {429, 503}
_MAX_ATTEMPTS = 4
_MAX_RETRY_WAIT_SECONDS = 60


class Agent365Error(Exception):
    """A Microsoft-side failure, already worded for a person to act on."""


def _first_sentence(body: dict) -> str:
    # Entra's error_description ends with a trace id, correlation id and
    # timestamp; those belong in a support ticket, not in every log line.
    detail = (body.get("error_description") or "").splitlines()
    return detail[0].split(" Trace ID:")[0].strip() if detail else ""


def explain(status: int, body: str) -> str:
    """Turn a Graph error into the likely cause. The raw bodies are terse — a
    403 can arrive with an empty message — and each cause has a different fix."""
    lowered = body.lower()
    if status == 403 and "licensed for agent 365" in lowered:
        return (
            "this tenant has no Microsoft Agent 365 license. The catalog API "
            "refuses every call until one is assigned (Agent 365 or Microsoft "
            "365 E7)."
        )
    if status == 403:
        return (
            "access denied. Check the app registration has the *Application* "
            "permission CopilotPackages.Read.All and that an admin granted "
            "consent for it (README Step 2). Graph returns this with an empty "
            "message when the permission is missing."
        )
    if status == 401:
        return "the access token was rejected. It may have expired or been issued for another tenant."
    if status == 424:
        return (
            "Microsoft rejected this call without a signed-in user. Block and "
            "unblock must run as an admin — run `python main.py --login`."
        )
    return body[:300] or "no detail returned"


class Agent365Client:
    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        delegated: DelegatedAuth | None = None,
        http: httpx.Client | None = None,
    ):
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._delegated = delegated
        self._http = http or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
        self._app_token: str | None = None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Agent365Client:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- auth ------------------------------------------------------------------

    def _get_app_token(self) -> str:
        if self._app_token:
            return self._app_token
        try:
            response = self._http.post(
                f"https://login.microsoftonline.com/{self._tenant_id}/oauth2/v2.0/token",
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": f"{GRAPH_BASE_URL}/.default",
                    "grant_type": "client_credentials",
                },
            )
        except httpx.RequestError as exc:
            raise Agent365Error(f"could not reach Microsoft Entra: {exc}") from exc
        if response.status_code != 200:
            try:
                body = response.json()
            except ValueError:
                body = {}
            raise Agent365Error(
                "Entra refused the app credentials"
                f" ({body.get('error', response.status_code)}): "
                f"{_first_sentence(body) or 'check MS_TENANT_ID, MS_CLIENT_ID and MS_CLIENT_SECRET'}"
            )
        self._app_token = response.json()["access_token"]
        return self._app_token

    # -- requests --------------------------------------------------------------

    def _send(
        self, method: str, url: str, token: str, params: dict | None = None
    ) -> httpx.Response:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = self._http.request(
                    method,
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.RequestError as exc:
                raise Agent365Error(f"could not reach Microsoft Graph: {exc}") from exc
            if response.status_code not in _RETRY_STATUSES or attempt == _MAX_ATTEMPTS:
                return response
            wait = min(
                int(response.headers.get("Retry-After", 2 * attempt)),
                _MAX_RETRY_WAIT_SECONDS,
            )
            logger.warning(
                "Graph returned %s; retrying in %ss (attempt %s/%s)",
                response.status_code,
                wait,
                attempt,
                _MAX_ATTEMPTS,
            )
            time.sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover

    # -- catalog ---------------------------------------------------------------

    def list_packages(self) -> list[dict]:
        """Every Copilot-hosted package in the tenant's catalog.

        The list response already carries each package's description, owner
        name, `isBlocked` and `lastModifiedDateTime`, so no per-package detail
        call is needed. Follows `@odata.nextLink` so a large tenant returns
        every page.

        The `$filter` is required: without it the endpoint also returns other
        package types (Office add-ins and the like), not just Copilot agents.
        """
        token = self._get_app_token()
        packages: list[dict] = []
        url: str | None = f"{GRAPH_BASE_URL}/v1.0/copilot/admin/catalog/packages"
        params: dict | None = {"$filter": "supportedHosts/any(x:x eq 'Copilot')"}
        while url:
            response = self._send("GET", url, token, params)
            if response.status_code != 200:
                raise Agent365Error(
                    f"listing the Agent 365 catalog failed ({response.status_code}): "
                    f"{explain(response.status_code, response.text)}"
                )
            data = response.json()
            packages.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
            params = None  # the nextLink already carries the query string
        return packages

    def set_blocked(self, asset_id: str, blocked: bool) -> None:
        """Block or unblock one agent, as the signed-in admin."""
        if self._delegated is None:
            raise Agent365Error(
                "blocking needs a signed-in admin; this client was built without one"
            )
        action = "block" if blocked else "unblock"
        token = self._delegated.get_access_token()
        response = self._send(
            "POST",
            f"{GRAPH_BASE_URL}/beta/copilot/admin/catalog/packages/{asset_id}/{action}",
            token,
        )
        if response.status_code not in (200, 204):
            raise Agent365Error(
                f"{action} failed ({response.status_code}): "
                f"{explain(response.status_code, response.text)}"
            )
