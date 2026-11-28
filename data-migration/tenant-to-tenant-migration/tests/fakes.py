import copy
import json as jsonlib

from kit.clients.base import APIError
from kit.config import TenantConfig

CONFIG = TenantConfig(base_url="https://api.example.com", tenant="tenant-b", api_key="secret-key")


class FakeResponse:
    def __init__(self, status_code, body=None, text=None, headers=None):
        self.status_code = status_code
        self._body = body
        self.text = text if text is not None else (jsonlib.dumps(body) if body is not None else "")
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("No JSON body")
        return self._body


class FakeSession:
    """Stands in for requests.Session. Returns queued responses in order and records every call."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self, method, url, kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def request(self, method, url, **kwargs):
        return self._next(method, url, kwargs)

    def post(self, url, **kwargs):
        return self._next("POST", url, kwargs)


def token(value="tok-1"):
    return FakeResponse(200, {"access_token": value})


def no_sleep(_seconds):
    pass


class FakeTenant:
    """In-memory stand-in for an API client. `collections` maps a path to its items; POST appends to it."""

    def __init__(
        self,
        collections=None,
        dry_run=False,
        post_response=None,
        tenant="target-tenant",
        jsonapi=False,
        post_responses=None,
        delete_response=None,
        json_bodies=None,
        patch_response=None,
    ):
        self.collections = {path: [dict(i) for i in items] for path, items in (collections or {}).items()}
        self.dry_run = dry_run
        self.post_response = post_response  # if set, returned for every POST instead of creating
        self.post_responses = post_responses or {}  # {path: response} for POSTs to that path only
        self.delete_response = delete_response
        self.json_bodies = json_bodies or {}  # {path: body or Exception} served by get_json
        self.patch_response = patch_response
        self.jsonapi = jsonapi  # internal API style: bodies and responses wrapped in {"data": ...}
        self.posts = []
        self.deletes = []
        self.delete_bodies = []  # bodies of DELETEs that sent one
        self.gets = []
        self.patches = []
        self.config = TenantConfig(base_url="https://fake", tenant=tenant, api_key="k")
        self._counter = 0

    def get_data(self, path, params=None):
        if path not in self.collections:
            raise APIError(f"GET {path} failed: 404", 404)
        return [dict(i) for i in self.collections[path]]

    def get_all(self, path, params=None):
        return [dict(i) for i in self.collections.get(path, [])]

    get_all_offset = get_all
    get_all_cursor = get_all

    def get_json(self, path, params=None):
        self.gets.append((path, params))
        if path not in self.json_bodies:
            return {}
        body = self.json_bodies[path]
        if isinstance(body, Exception):
            raise body
        return copy.deepcopy(body)

    def post(self, path, json):
        if self.dry_run:
            return None
        self.posts.append((path, json))
        if path in self.post_responses:
            return self.post_responses[path]
        if self.post_response:
            return self.post_response
        self._counter += 1
        if self.jsonapi:
            attrs = dict(json["data"].get("attributes") or {})
            item = {"id": attrs.pop("id", None) or f"new-{self._counter}", "attributes": attrs}
            self.collections.setdefault(path, []).append(item)
            return FakeResponse(201, {"data": item})
        item = dict(json, id=f"new-{self._counter}")
        self.collections.setdefault(path, []).append(item)
        return FakeResponse(201, item)

    def patch(self, path, json):
        if self.dry_run:
            return None
        self.patches.append((path, json))
        return self.patch_response or FakeResponse(200, {})

    def delete(self, path, json=None):
        if self.dry_run:
            return None
        self.deletes.append(path)
        if json is not None:
            self.delete_bodies.append(json)
        return self.delete_response or FakeResponse(204)


def make_ctx(tmp_path, source=None, target=None, source_internal=None, target_internal=None, dry_run=False):
    from kit.context import Context

    source = source or FakeTenant(dry_run=True)
    source_internal = source_internal or FakeTenant(dry_run=True)
    for client in (source, source_internal):
        client.config = TenantConfig(base_url="https://fake", tenant="source-tenant", api_key="k")
    target = target or FakeTenant()
    target_internal = target_internal or FakeTenant()
    for client in (target, target_internal):
        client.dry_run = dry_run  # a dry-run context must not hold a target fake that writes
    return Context(
        source=source,
        target=target,
        source_internal=source_internal,
        target_internal=target_internal,
        dry_run=dry_run,
        data_dir=tmp_path,
    )
