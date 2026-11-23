"""
One-time setup: create the "Databricks" source in your Credo AI tenant.

`source` on a Credo AI model is not free text — it resolves against a Source
record that has to exist first, so without this every model create returns
422. This is a one-time admin operation per tenant, on the private v2 API
(a different host and auth exchange than the Integration API main.py uses),
which is why it lives in its own script instead of running at startup.

Idempotent — safe to re-run.

Run: python setup_credo_source.py
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

DEFAULT_SOURCE_NAME = "Databricks"
PAGE_LIMIT = 1000


def exchange_token(base_host: str, api_token: str, tenant: str) -> str:
    resp = httpx.post(
        f"{base_host}/auth/exchange",
        json={"api_token": api_token, "tenant": tenant},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if "access_token" not in body:
        raise SystemExit(
            f"/auth/exchange response has no 'access_token' field — got keys {sorted(body)}"
        )
    return body["access_token"]


def find_source(base_host: str, tenant: str, token: str, name: str) -> dict | None:
    resp = httpx.get(
        f"{base_host}/api/v2/{tenant}/sources",
        params={"page[limit]": PAGE_LIMIT},
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.api+json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    items = resp.json()["data"]
    if len(items) >= PAGE_LIMIT:
        print(
            f"warning: {PAGE_LIMIT} sources returned on one page — there may be more, "
            "and this script only checks the first page.",
            file=sys.stderr,
        )
    for item in items:
        if item["attributes"]["name"] == name:
            return item
    return None


def create_source(base_host: str, tenant: str, token: str, name: str) -> dict:
    resp = httpx.post(
        f"{base_host}/api/v2/{tenant}/sources",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/vnd.api+json",
            "Accept": "application/vnd.api+json",
        },
        json={"data": {"type": "sources", "attributes": {"name": name}}},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["data"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-host",
        default=os.environ.get("CREDO_BACKEND_BASE_URL", ""),
        help="Backend host for the private v2 API (env: CREDO_BACKEND_BASE_URL)",
    )
    parser.add_argument("--tenant", default=os.environ.get("CREDO_TENANT", ""))
    parser.add_argument("--name", default=DEFAULT_SOURCE_NAME)
    parser.add_argument("--api-token", default=os.environ.get("CREDO_API_KEY", ""))
    parser.add_argument(
        "--token", default=None, help="Already-minted JWT; skips the exchange"
    )
    args = parser.parse_args()

    if not args.base_host:
        parser.error("--base-host or CREDO_BACKEND_BASE_URL (in .env) is required")
    if not args.tenant:
        parser.error("--tenant or CREDO_TENANT (in .env) is required")
    if not args.token and not args.api_token:
        parser.error("--token, or --api-token/CREDO_API_KEY (in .env), is required")

    base_host = args.base_host.rstrip("/")
    token = args.token or exchange_token(base_host, args.api_token, args.tenant)

    existing = find_source(base_host, args.tenant, token, args.name)
    if existing is not None:
        print(f"Source {args.name!r} already exists: {existing['id']}")
        return 0

    created = create_source(base_host, args.tenant, token, args.name)
    print(f"Created source {args.name!r}: {created['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
