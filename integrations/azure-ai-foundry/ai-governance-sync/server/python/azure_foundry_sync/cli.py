"""CLI entry point.

    python main.py --dry-run     # show the plan, write nothing
    python main.py               # sync
    python main.py --help        # every flag

Exit codes: 0 clean, 1 ran but at least one domain hit an error, 2 could not start
(bad config, Credo AI or Azure AD auth failure before any sync work happened).
"""

from __future__ import annotations

import argparse
import logging
import sys

from credoai.auth import AuthenticationError
from credoai.errors import ApiError
from pydantic import ValidationError

from azure_foundry_sync.azure_client import AzureApiError
from azure_foundry_sync.config import get_settings
from azure_foundry_sync.credo_private import CredoPrivateApiError
from azure_foundry_sync.sync import run

logger = logging.getLogger("azure_foundry_sync.cli")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Read only, write nothing"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Per-record logging for this run"
    )
    parser.add_argument(
        "--skip-models", action="store_true", help="Skip the Azure model catalog sync"
    )
    parser.add_argument(
        "--skip-controls",
        action="store_true",
        help="Skip the MSFT-* policy control sync",
    )
    parser.add_argument(
        "--skip-custom-fields", action="store_true", help="Skip custom field creation"
    )
    parser.add_argument(
        "--skip-questionnaire", action="store_true", help="Skip questionnaire creation"
    )
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except ValidationError as exc:
        logging.basicConfig(
            level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
        )
        for error in exc.errors():
            key = ".".join(str(p) for p in error["loc"])
            logger.error("Configuration error in .env: %s — %s", key, error["msg"])
        return 2

    level = (
        logging.DEBUG
        if args.verbose
        else getattr(logging, settings.log_level.upper(), logging.INFO)
    )
    logging.basicConfig(
        level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    settings.sync_models = settings.sync_models and not args.skip_models
    settings.sync_controls = settings.sync_controls and not args.skip_controls
    settings.sync_custom_fields = (
        settings.sync_custom_fields and not args.skip_custom_fields
    )
    settings.sync_questionnaire = (
        settings.sync_questionnaire and not args.skip_questionnaire
    )

    try:
        results = run(settings, dry_run=args.dry_run)
    except (AzureApiError, CredoPrivateApiError, ApiError, AuthenticationError) as exc:
        logger.error("Could not start: %s", exc)
        return 2
    except Exception:
        logger.exception("Could not start")
        return 2

    for counts in results:
        print(counts.dry_run_line() if args.dry_run else counts.line())

    return 1 if any(c.errors for c in results) else 0


if __name__ == "__main__":
    sys.exit(main())
