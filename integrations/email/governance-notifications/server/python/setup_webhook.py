"""Create this server's Credo AI webhook and write its settings to `.env`.

    python setup_webhook.py --public-url https://notify.example.com

1. Generates the URL token (`secrets.token_urlsafe(32)`).
2. Creates one webhook, delivering the five event types the server handles
   to <public-url>/webhook/<token>.
3. Reads the webhook's signing secret.
4. Writes WEBHOOK_PATH_TOKEN, WEBHOOK_SIGNING_SECRET and CREDO_WEBHOOK_ID to
   `.env` (kept at mode 600). Neither the token nor the secret is printed.

Needs CREDO_API_KEY, CREDO_TENANT and CREDO_API_BASE_URL in `.env` already,
and an API key whose user may manage webhooks (admins can). If `.env` already
names a webhook, pass --replace to create a new one; delete the old one in
Settings → Webhooks afterwards.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
from pathlib import Path
from urllib.parse import urlsplit

from notifier import events
from notifier.config import ENV_FILE, CredoSettings
from notifier.credo import Credo, CredoUnavailable
from pydantic import ValidationError

_PLACEHOLDER = re.compile(r"^(|paste-.*|your-.*|run-setup.*)$")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--public-url",
        required=True,
        help="where Credo AI can reach this server, e.g. https://notify.example.com",
    )
    parser.add_argument(
        "--description", default="Governance notifications (email cookbook)"
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="create a new webhook even though .env already names one",
    )
    args = parser.parse_args()

    public_url = args.public_url.rstrip("/")
    parts = urlsplit(public_url)
    if parts.scheme not in {"http", "https"} or not parts.netloc or parts.query:
        print(
            "--public-url must be an http(s) URL with no query string", file=sys.stderr
        )
        return 2
    if parts.scheme == "http":
        print("warning: http:// sends deliveries unencrypted; use https in production")

    try:
        settings = CredoSettings()
    except ValidationError as exc:
        print(f"Configuration error in .env:\n{exc}", file=sys.stderr)
        return 2

    existing = _read_env(ENV_FILE)
    if (
        not _PLACEHOLDER.match(existing.get("CREDO_WEBHOOK_ID", ""))
        and not args.replace
    ):
        print(
            f"{ENV_FILE.name} already names webhook {existing['CREDO_WEBHOOK_ID']}. "
            "Pass --replace to create a new one.",
            file=sys.stderr,
        )
        return 1

    token = secrets.token_urlsafe(32)
    try:
        webhook_id, secret = Credo(settings).create_webhook(
            url=f"{public_url}/webhook/{token}",
            event_types=list(events.SUBSCRIBED),
            description=args.description,
        )
    except CredoUnavailable as exc:
        print(f"Couldn't create the webhook: {exc}", file=sys.stderr)
        return 1

    _write_env(
        ENV_FILE,
        {
            "WEBHOOK_PATH_TOKEN": token,
            "WEBHOOK_SIGNING_SECRET": secret,
            "CREDO_WEBHOOK_ID": webhook_id,
        },
    )
    print(f"Created webhook {webhook_id} for {', '.join(events.SUBSCRIBED)}.")
    print(f"Wrote its URL token and signing secret to {ENV_FILE} (mode 600).")
    print("Start the server with: python main.py")
    return 0


def _read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        if sep and not name.lstrip().startswith("#"):
            out[name.strip()] = value.strip()
    return out


def _write_env(path: Path, values: dict[str, str]) -> None:
    """Set `values` in the env file, in place where the names already appear."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(values)
    for i, line in enumerate(lines):
        name, sep, _ = line.partition("=")
        if sep and name.strip() in remaining:
            lines[i] = f"{name.strip()}={remaining.pop(name.strip())}"
    lines.extend(f"{name}={value}" for name, value in remaining.items())
    # Create it private, then write: the file holds credentials.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    path.chmod(0o600)


if __name__ == "__main__":
    sys.exit(main())
