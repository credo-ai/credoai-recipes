"""Delegated (signed-in admin) Microsoft Graph auth, used only by `--enforce`.

Blocking or unblocking a Copilot Studio / Agent Builder agent with an app-only
token fails with `424 ... without user context is not supported`, and
Microsoft's own reference lists Application permission as "Not available" for
both calls. So those two calls run as a user: an admin signs in once with a
device code (`python main.py --login`), the refresh token is saved, and every
later run silently trades it for a fresh access token — no interactive sign-in,
so a scheduled run still works unattended.

Plain HTTP against the two Entra OAuth endpoints (device-code grant and
refresh-token grant). Entra rotates the refresh token on every use, so the
newest one is always written back.

When the saved sign-in stops working — unused for 90 days, revoked, a password
change, or a Conditional Access sign-in-frequency policy — only a fresh
`--login` fixes it, and the run exits 2 saying so rather than failing midway.

Entra prerequisites on the app registration are in README Step 2: "Allow public
client flows" = Yes, and the *Delegated* `CopilotPackages.ReadWrite.All`
permission with admin consent.
"""

from __future__ import annotations

import base64
import json
import os
import time
from collections.abc import Callable
from pathlib import Path

import httpx

REQUEST_TIMEOUT_SECONDS = 30

SCOPE = (
    "https://graph.microsoft.com/CopilotPackages.ReadWrite.All "
    "offline_access openid profile"
)

# Refresh a little early so a token does not expire mid-request.
EXPIRY_MARGIN_SECONDS = 120


class DelegatedAuthError(Exception):
    """Sign-in or token refresh failed."""


class DelegatedLoginRequired(DelegatedAuthError):
    """No usable saved sign-in — run `python main.py --login`."""


class DelegatedAuth:
    def __init__(self, tenant_id: str, client_id: str, cache_path: Path):
        base = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0"
        self._token_url = f"{base}/token"
        self._device_code_url = f"{base}/devicecode"
        self._client_id = client_id
        self._cache_path = cache_path

    # -- cache -----------------------------------------------------------------

    def _load(self) -> dict | None:
        if not self._cache_path.exists():
            return None
        try:
            return json.loads(self._cache_path.read_text())
        except (OSError, ValueError):
            return None

    def _save(self, token_response: dict, username: str | None) -> dict:
        cached = {
            "access_token": token_response["access_token"],
            # Entra rotates refresh tokens — always keep the newest one.
            "refresh_token": token_response["refresh_token"],
            "expires_at": time.time() + int(token_response.get("expires_in", 3600)),
            "username": username,
        }
        # This file is standing admin access to the Copilot catalog. Create it
        # owner-only from the start rather than chmod-ing after the fact.
        fd = os.open(self._cache_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(cached, handle, indent=2)
        os.chmod(self._cache_path, 0o600)
        return cached

    def signed_in_user(self) -> str | None:
        cached = self._load()
        return cached.get("username") if cached else None

    # -- sign-in ---------------------------------------------------------------

    def login_with_device_code(self, announce: Callable[[str], None] = print) -> str:
        """One-time interactive sign-in. Returns the signed-in username."""
        response = httpx.post(
            self._device_code_url,
            data={"client_id": self._client_id, "scope": SCOPE},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        body = _json(response)
        if "error" in body:
            raise DelegatedAuthError(_describe(body))
        # "To sign in, use a web browser to open ... and enter the code ..."
        announce(body["message"])

        interval = int(body.get("interval", 5))
        deadline = time.time() + int(body.get("expires_in", 900))
        while time.time() < deadline:
            time.sleep(interval)
            response = httpx.post(
                self._token_url,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": self._client_id,
                    "device_code": body["device_code"],
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            result = _json(response)
            error = result.get("error")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            if error:
                raise DelegatedAuthError(_describe(result))
            username = _username_from_id_token(result.get("id_token"))
            self._save(result, username)
            return username or "(unknown user)"
        raise DelegatedAuthError(
            "The sign-in code expired before it was used — run --login again."
        )

    # -- use -------------------------------------------------------------------

    def get_access_token(self) -> str:
        """A valid delegated access token, refreshing silently when needed."""
        cached = self._load()
        if not cached:
            raise DelegatedLoginRequired(
                "No saved admin sign-in. Run `python main.py --login` once, "
                "as an account that can block agents in Agent 365."
            )
        if cached["expires_at"] - EXPIRY_MARGIN_SECONDS > time.time():
            return cached["access_token"]

        response = httpx.post(
            self._token_url,
            data={
                "grant_type": "refresh_token",
                "client_id": self._client_id,
                "refresh_token": cached["refresh_token"],
                "scope": SCOPE,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        body = _json(response)
        if "error" in body:
            raise DelegatedLoginRequired(
                f"The saved admin sign-in no longer works ({_describe(body)}). "
                "Run `python main.py --login` again."
            )
        return self._save(body, cached.get("username"))["access_token"]


def _json(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        raise DelegatedAuthError(
            f"Entra returned HTTP {response.status_code} with no JSON body."
        ) from None
    return body if isinstance(body, dict) else {}


def _describe(body: dict) -> str:
    # Entra's error_description is multi-line (trace id, timestamp); the first
    # line is the part a person needs.
    detail = (body.get("error_description") or "").splitlines()
    first = detail[0].split(" Trace ID:")[0].strip() if detail else ""
    return (
        f"{body.get('error', 'error')}: {first}"
        if first
        else body.get("error", "error")
    )


def _username_from_id_token(id_token: str | None) -> str | None:
    # For display only — the id token is not used for authorization, so there
    # is nothing to gain from verifying its signature here.
    if not id_token:
        return None
    payload = id_token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload))
    return claims.get("preferred_username") or claims.get("name")
