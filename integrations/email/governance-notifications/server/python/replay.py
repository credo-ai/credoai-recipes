"""Send a sample webhook delivery to a locally running server.

    python replay.py --model-id <id of a real model> --email you@yourcompany.com

Posts `fixtures/entity_created_model.json` (or `--fixture`) to
http://localhost:<PORT>/webhook/<WEBHOOK_PATH_TOKEN>, with the model id and
email filled in and a fresh `svix-id`. Pair it with MAIL_DRY_RUN=true to see the
email in the server log instead of an inbox. See TESTING.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

import httpx
from notifier.config import COOKBOOK_ROOT, get_settings

FIXTURES = COOKBOOK_ROOT / "fixtures"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--fixture", type=Path, default=FIXTURES / "entity_created_model.json"
    )
    parser.add_argument("--model-id", help="replaces data.modelId")
    parser.add_argument("--email", help="replaces data.user.email")
    parser.add_argument(
        "--svix-id", default=None, help="reuse an id to test redelivery handling"
    )
    parser.add_argument(
        "--url", default=None, help="server base URL (default http://localhost:<PORT>)"
    )
    parser.add_argument(
        "--token", default=None, help="path token (default WEBHOOK_PATH_TOKEN)"
    )
    args = parser.parse_args()

    settings = get_settings()
    payload = json.loads(args.fixture.read_text())
    data = payload.setdefault("data", {})
    if args.model_id:
        data["modelId"] = args.model_id
    if args.email:
        data.setdefault("user", {})["email"] = args.email

    base = (args.url or f"http://localhost:{settings.port}").rstrip("/")
    token = args.token or settings.webhook_path_token
    svix_id = args.svix_id or f"msg_replay_{uuid.uuid4().hex}"

    resp = httpx.post(
        f"{base}/webhook/{token}",
        json=payload,
        headers={"svix-id": svix_id},
        timeout=10,
    )
    print(f"{resp.status_code} (svix-id {svix_id})")
    return 0 if resp.status_code < 400 else 1


if __name__ == "__main__":
    sys.exit(main())
