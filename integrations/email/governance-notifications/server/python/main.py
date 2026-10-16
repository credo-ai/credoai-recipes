"""Run the notification server.

    python main.py

Settings come from the cookbook's `.env` (see `.env.example`; `setup_webhook.py`
writes the webhook settings). The server refuses to start when a required
setting is missing or unsafe, or when `stage_messages.yaml` doesn't parse.
"""

from __future__ import annotations

import logging
import sys

import uvicorn
from notifier.app import create_app
from notifier.config import get_settings
from notifier.stages import StageMessages
from pydantic import ValidationError
from yaml import YAMLError


def main() -> int:
    try:
        settings = get_settings()
    except ValidationError as exc:
        print(f"Configuration error in .env:\n{exc}", file=sys.stderr)
        return 2
    try:
        StageMessages(settings.stage_messages_path)
    except (OSError, ValueError, YAMLError) as exc:
        print(f"Can't use {settings.stage_messages_path}:\n{exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # The SDK's HTTP client logs every request at INFO; keep the log about emails.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # access_log=False: an access log line contains the full request path, and
    # the path contains the webhook token.
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        access_log=False,
        log_level=settings.log_level.lower(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
