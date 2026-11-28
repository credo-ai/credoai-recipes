"""Public Integration Service API (/api/v1/integration). Used for everything it covers."""

from kit.clients.base import TIMEOUT, APIError, BaseClient


class IntegrationClient(BaseClient):
    def _url(self, path):
        return f"{self.config.base_url}/api/v1/integration/{path.lstrip('/')}"

    def _exchange_token(self):
        resp = self.session.post(
            f"{self.config.base_url}/api/v1/integration/auth/token",
            headers={"X-API-Key": self.config.api_key, "X-Tenant": self.config.tenant},
            timeout=TIMEOUT,
            verify=self.config.verify_ssl,
        )
        return self._token_from(resp, "Integration API")

    def _headers(self):
        # X-Tenant is required on every call, not only the token exchange
        return {"Authorization": f"Bearer {self._ensure_token()}", "X-Tenant": self.config.tenant}

    def get_all(self, path, params=None, page_limit=100):
        """Every item from a list endpoint, across cursor pages. Also handles endpoints that return a bare list."""
        items, cursor, seen = [], None, set()
        while True:
            query = dict(params or {}, page_limit=page_limit)
            if cursor:
                query["page_after"] = cursor
            body = self.get_json(path, query)
            if isinstance(body, list):
                return body
            # A missing key must not read as "nothing exists": on a load that would create duplicates
            if not isinstance(body, dict) or "items" not in body:
                raise APIError(f"GET {path}: unexpected response shape (no 'items')", body=str(body)[:300])
            items.extend(body["items"])
            pagination = body.get("pagination") or {}
            if not pagination.get("has_more") or not pagination.get("next_cursor"):
                return items
            cursor = pagination["next_cursor"]
            if cursor in seen:
                raise APIError(f"GET {path}: the server repeated a page cursor; stopping instead of looping")
            seen.add(cursor)
