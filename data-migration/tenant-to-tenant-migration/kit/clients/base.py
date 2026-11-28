"""HTTP behavior shared by both API clients: token refresh, retries, and write blocking."""

import json as _jsonlib
import logging
import re
import time

import requests

log = logging.getLogger("kit.http")

MAX_TRIES = 3
TIMEOUT = 30
MAX_RETRY_AFTER = 60


class APIError(Exception):
    def __init__(self, message, status_code=None, body=""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


_ENTITY_CONTROLS = re.compile(r"^(use_cases|models|vendors)/[^/]+/controls(/|$)")


def _sensitive(path, json):
    """True for a request that can carry answer values, custom field values or evidence data, whose bodies the log
    must never show: anything under .../evidences or .../custom_fields, an entity's controls (their compliances can
    carry evidence), and any write with custom field values."""
    if "/evidences" in path or "custom_fields" in path or _ENTITY_CONTROLS.match(path):
        return True
    return isinstance(json, dict) and "custom_fields" in json


def _sensitive_response(resp):
    # A use case read or list carries its custom field values
    return "custom_fields" in (resp.text or "")


class BaseClient:
    """Subclasses implement _url(path), _exchange_token() and _headers().

    dry_run: writes are logged and skipped (returns None).
    read_only: writes raise APIError. Used for the source tenant, so a bug that tries to write there fails loudly.
    """

    def __init__(self, config, dry_run=True, session=None, sleep=time.sleep, read_only=False):
        self.config = config
        self.dry_run = dry_run
        self.read_only = read_only
        self.session = session or requests.Session()
        self._sleep = sleep
        self._token = None

    def _ensure_token(self):
        if self._token is None:
            self._token = self._exchange_token()
        return self._token

    def _token_from(self, resp, api_name):
        if resp.status_code == 200:
            try:
                return resp.json()["access_token"]
            except (ValueError, KeyError, TypeError):
                pass
        raise APIError(f"{api_name} sign-in failed for {self.config.tenant}", resp.status_code, resp.text)

    def _retry_delay(self, resp, attempt):
        retry_after = (getattr(resp, "headers", None) or {}).get("Retry-After")
        if retry_after and str(retry_after).isdigit():
            return min(int(retry_after), MAX_RETRY_AFTER)
        return 2**attempt

    def request(self, method, path, params=None, json=None):
        """Send one request. Returns the Response, or None for a write blocked by dry run."""
        is_write = method.upper() != "GET"
        if is_write and self.read_only:
            raise APIError(f"Refusing {method} {path}: the {self.config.tenant} client is read-only")
        sensitive = _sensitive(path, json)
        if is_write and self.dry_run:
            log.info(f"[DRY RUN] {method} {path}")
            if not sensitive:
                log.debug(f"[DRY RUN] {method} {path} body={json}")
            return None
        refreshed = False
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self.session.request(
                    method,
                    self._url(path),
                    headers=self._headers(),
                    params=params,
                    json=json,
                    timeout=TIMEOUT,
                    verify=self.config.verify_ssl,
                )
            except (requests.ConnectionError, requests.Timeout) as e:
                # A write may have reached the server before the connection dropped, so only reads retry
                if is_write:
                    raise APIError(
                        f"{method} {path} lost its connection: {e}. The item may or may not have been created; "
                        f"re-running matches it by name or key."
                    ) from e
                if attempt >= MAX_TRIES:
                    raise APIError(f"{method} {path} failed: {e}") from e
                self._sleep(2**attempt)
                continue
            if sensitive or _sensitive_response(resp):
                size = len(_jsonlib.dumps(json, default=str)) if json is not None else 0
                log.debug(
                    f"{method} {path} params={params} body: {size} bytes (not logged) -> {resp.status_code}, "
                    f"{len(resp.text or '')} bytes (not logged)"
                )
            else:
                log.debug(f"{method} {path} params={params} body={json} -> {resp.status_code} {resp.text[:2000]}")
            if resp.status_code == 401:
                if refreshed:
                    raise APIError(f"{method} {path}: still unauthorized after a token refresh", 401, resp.text)
                self._token = self._exchange_token()
                refreshed = True
                attempt -= 1
                continue
            retryable = resp.status_code == 429 or (not is_write and resp.status_code >= 500)
            if retryable and attempt < MAX_TRIES:
                self._sleep(self._retry_delay(resp, attempt))
                continue
            return resp

    def get_json(self, path, params=None):
        resp = self.request("GET", path, params=params)
        # An error body can repeat a value, and callers log APIError bodies
        body = "" if _sensitive(path, None) or _sensitive_response(resp) else resp.text
        if not 200 <= resp.status_code < 300:
            raise APIError(f"GET {path} failed: {resp.status_code}", resp.status_code, body)
        try:
            return resp.json()
        except ValueError as e:
            raise APIError(f"GET {path} returned a response that is not JSON", resp.status_code, body) from e

    def post(self, path, json):
        return self.request("POST", path, json=json)

    def patch(self, path, json):
        return self.request("PATCH", path, json=json)

    def delete(self, path, json=None):
        return self.request("DELETE", path, json=json)
