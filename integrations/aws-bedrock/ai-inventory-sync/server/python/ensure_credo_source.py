#!/usr/bin/env python3
"""One-time setup: ensure the Credo AI "source" this integration uses exists.

`ModelCreate.source` (the public integration API `sync.py` calls) is validated
against a pre-existing Source record in the tenant — it is not free text, so an
unknown value 422s on every model creation. This script uses Credo AI's private v2
admin API to check for that Source and create it if missing; it only needs to
run once per tenant, and is idempotent so re-running is safe.


Auth/host: the private API is the same tenant and API key as the public
integration API `credo.py` uses, so this reads `CREDO_API_KEY` / `CREDO_TENANT`
/ `CREDO_API_BASE_URL` from this project's own `.env` (via `config.Settings`) —
no separate credentials to configure. `--token` is an escape hatch for an
already-minted JWT.

Usage (reads ../.env):
    python scripts/ensure_credo_source.py

    # or, with an already-minted JWT:
    python scripts/ensure_credo_source.py --token ...
"""

from __future__ import annotations

import argparse
import sys

import httpx
from bedrock_sync.config import get_settings

DEFAULT_SOURCE_NAME = "Amazon Bedrock"
PAGE_LIMIT = 1000


def exchange_token(base_host: str, api_token: str, tenant: str) -> str:
    resp = httpx.post(
        f"{base_host}/auth/exchange",
        json={"api_token": api_token, "tenant": tenant},
    )
    resp.raise_for_status()
    body = resp.json()
    if "access_token" not in body:
        raise SystemExit(
            f"/auth/exchange response has no 'access_token' field — got keys "
            f"{sorted(body)}. Confirm the field name against the real response."
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
    )
    resp.raise_for_status()
    items = resp.json()["data"]
    if len(items) >= PAGE_LIMIT:
        print(
            f"warning: exactly {PAGE_LIMIT} sources returned on one page — there may be "
            "more; this script only checks the first page.",
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
    )
    resp.raise_for_status()
    return resp.json()["data"]


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-host", default=settings.credo_api_base_url)
    parser.add_argument("--tenant", default=settings.credo_tenant)
    parser.add_argument("--name", default=DEFAULT_SOURCE_NAME)
    parser.add_argument("--api-token", default=settings.credo_api_key)
    parser.add_argument(
        "--token", default=None, help="Already-minted JWT; skips the exchange"
    )
    args = parser.parse_args()

    if not args.tenant:
        parser.error("--tenant or CREDO_TENANT (in .env) is required")
    if not args.token and not args.api_token:
        parser.error("--token, or --api-token/CREDO_API_KEY (in .env), is required")

    try:
        token = args.token or exchange_token(
            args.base_host, args.api_token, args.tenant
        )

        existing = find_source(args.base_host, args.tenant, token, args.name)
        if existing is not None:
            print(f"Source {args.name!r} already exists: {existing['id']}")
            return 0

        created = create_source(args.base_host, args.tenant, token, args.name)
    except httpx.HTTPStatusError as exc:
        # A wrong host is the common mistake — the admin API this script needs
        # is not always the same host as the Integration Service the sync uses.
        code = exc.response.status_code
        print(f"Source setup failed: {code} from {exc.request.url}", file=sys.stderr)
        if code == 404:
            print(
                f"{args.base_host} does not serve that API. Pass --base-host "
                "pointing at your Credo AI admin API — see README Step 3.",
                file=sys.stderr,
            )
        elif code in (401, 403):
            print("Check CREDO_API_KEY and CREDO_TENANT in .env.", file=sys.stderr)
        return 2
    except httpx.RequestError as exc:
        print(
            f"Source setup failed: {args.base_host} is unreachable ({exc}).",
            file=sys.stderr,
        )
        return 2

    print(f"Created source {args.name!r}: {created['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
