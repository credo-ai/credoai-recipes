"""Send a sample webhook delivery to a locally running server.

    python replay.py entity_created_model --set modelId=<a real model id> --set user.userId=<your user id>
    python replay.py entity_review_status_updated --set entityId=<id> --set reviewId=<id>
    python replay.py entity_created_model --bad-signature    # expect 401
    python replay.py entity_created_model --unsigned         # expect 401

Posts `fixtures/<name>.json` (or a path) to
http://localhost:<PORT>/webhook/<WEBHOOK_PATH_TOKEN> with a fresh `svix-id`,
signed with WEBHOOK_SIGNING_SECRET the way Svix signs real deliveries.
`--set` replaces a field of `data` (camelCase, dots for nesting; true, false,
null and JSON objects or arrays are read as JSON, anything else as text).

The server re-reads everything through the SDK, so ids must be real ones from
your tenant or nothing is sent. Pair it with MAIL_DRY_RUN=true to see the
email in the server log instead of an inbox. See TESTING.md.
"""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from notifier.config import COOKBOOK_ROOT, get_settings
from svix.webhooks import Webhook

FIXTURES = COOKBOOK_ROOT / "fixtures"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "fixture", help=f"a name in {FIXTURES.name}/ (without .json), or a path"
    )
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="FIELD=VALUE",
        help="replace data.FIELD, e.g. --set entityId=... (repeatable)",
    )
    parser.add_argument(
        "--svix-id", default=None, help="reuse an id to test redelivery handling"
    )
    signing = parser.add_mutually_exclusive_group()
    signing.add_argument(
        "--bad-signature",
        action="store_true",
        help="sign with a random secret; the server should answer 401",
    )
    signing.add_argument(
        "--unsigned",
        action="store_true",
        help="send no signature headers; the server should answer 401",
    )
    parser.add_argument(
        "--url", default=None, help="server base URL (default http://localhost:<PORT>)"
    )
    args = parser.parse_args()

    path = Path(args.fixture)
    if not path.exists():
        path = FIXTURES / f"{args.fixture}.json"
    payload = json.loads(path.read_text())
    data = payload.setdefault("data", {})
    for assignment in args.set:
        field, sep, raw = assignment.partition("=")
        if not sep or not field:
            parser.error(f"--set needs FIELD=VALUE, got {assignment!r}")
        _set(data, field.split("."), _value(raw))

    settings = get_settings()
    base = (args.url or f"http://localhost:{settings.port}").rstrip("/")
    svix_id = args.svix_id or f"msg_replay_{uuid.uuid4().hex}"
    body = json.dumps(payload)

    headers = {"content-type": "application/json", "svix-id": svix_id}
    if not args.unsigned:
        signing_key = settings.webhook_signing_secret
        if args.bad_signature:
            # A random key of the same shape, so the signature is well formed but wrong.
            signing_key = "whsec_" + base64.b64encode(secrets.token_bytes(24)).decode()
        now = datetime.now(timezone.utc)
        headers["svix-timestamp"] = str(int(now.timestamp()))
        headers["svix-signature"] = Webhook(signing_key).sign(svix_id, now, body)

    resp = httpx.post(
        f"{base}/webhook/{settings.webhook_path_token}",
        content=body,
        headers=headers,
        timeout=10,
    )
    print(f"{resp.status_code} (svix-id {svix_id})")
    return 0 if resp.status_code < 400 else 1


def _value(raw: str) -> Any:
    # Only literals and JSON objects/arrays; an id that looks like a number
    # stays text.
    if raw in {"true", "false", "null"} or raw[:1] in {"[", "{"}:
        return json.loads(raw)
    return raw


def _set(target: dict[str, Any], path: list[str], value: Any) -> None:
    for name in path[:-1]:
        child = target.get(name)
        if not isinstance(child, dict):
            child = target[name] = {}
        target = child
    target[path[-1]] = value


if __name__ == "__main__":
    sys.exit(main())
