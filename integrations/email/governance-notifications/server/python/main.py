"""Run the notification server.

    python main.py

Settings come from the cookbook's `.env` (see `.env.example`). The server
refuses to start when a required setting is missing or unsafe, and on its very
first start when Credo AI can't be reached (it needs the list of existing
models; see README "Why existing models are skipped").
"""

from __future__ import annotations

import logging
import sys

import uvicorn
from notifier.app import create_app
from notifier.config import get_settings
from pydantic import ValidationError


def main() -> int:
    try:
        settings = get_settings()
    except ValidationError as exc:
        print(f"Configuration error in .env:\n{exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
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
