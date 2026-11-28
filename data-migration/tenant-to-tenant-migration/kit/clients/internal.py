"""Internal API (/api/v2/{tenant}). Used only for what the Integration API can't do."""

from kit.clients.base import TIMEOUT, APIError, BaseClient


def _data(path, body):
    # A missing key must not read as "nothing exists": on a load that would create duplicates
    if not isinstance(body, dict) or "data" not in body:
        raise APIError(f"GET {path}: unexpected response shape (no 'data')", body=str(body)[:300])
    return body["data"]


class InternalClient(BaseClient):
    def _url(self, path):
        return f"{self.config.base_url}/api/v2/{self.config.tenant}/{path.lstrip('/')}"

    def _exchange_token(self):
        resp = self.session.post(
            f"{self.config.base_url}/auth/exchange",
            json={"api_token": self.config.api_key, "tenant": self.config.tenant},
            timeout=TIMEOUT,
            verify=self.config.verify_ssl,
        )
        return self._token_from(resp, "Internal API")

    def _headers(self):
        return {"Authorization": f"Bearer {self._ensure_token()}", "Content-Type": "application/json"}

    def get_data(self, path, params=None):
        """One GET; returns its 'data' member. For endpoints that return everything at once, like version lists."""
        return _data(path, self.get_json(path, params))

    def get_all_cursor(self, path, params=None, page_limit=100):
        items, cursor = [], None
        while True:
            query = dict(params or {})
            query["page[limit]"] = page_limit
            if cursor:
                query["page[after]"] = cursor
            body = self.get_json(path, query)
            items.extend(_data(path, body))
            cursor = (body.get("meta") or {}).get("after")
            if not cursor:
                return items

    def get_all_offset(self, path, params=None, page_size=100):
        items, seen, page = [], set(), 1
        while True:
            query = dict(params or {})
            query.update({"page[size]": page_size, "page[number]": page})
            batch = _data(path, self.get_json(path, query))
            new = [d for d in batch if d.get("id") not in seen]
            # A cursor endpoint ignores page[number] and repeats page 1; stop instead of looping forever
            if not new:
                return items
            seen.update(d.get("id") for d in new)
            items.extend(new)
            if len(batch) < page_size:
                return items
            page += 1
