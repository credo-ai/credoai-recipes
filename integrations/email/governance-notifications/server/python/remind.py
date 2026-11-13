"""Run a reminder now, from the command line or your own cron.

    python remind.py pending_controls        # #6, the weekly pending-controls digest
    python remind.py pending_questionnaires  # #8, the questionnaires-due check

Sends this period's emails (this ISO week for pending_controls, today for
pending_questionnaires, in REMINDER_TIMEZONE) and exits. Anyone already emailed
for the period is skipped, so running it again afterwards is safe. Exits 1 if
Credo AI or the mail server couldn't be reached; run it again later.

Two runs at the same moment can both email the same person, so when your cron
runs reminders, set REMINDERS_IN_PROCESS=false so the server doesn't too.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

from notifier import events
from notifier.app import build_context
from notifier.config import describe, get_settings
from notifier.credo import CredoUnavailable
from notifier.handlers import Drop
from notifier.mailer import MailError
from notifier.reminders import REMINDERS, event_for
from pydantic import ValidationError


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("reminder", choices=sorted(REMINDERS))
    args = parser.parse_args()

    try:
        settings = get_settings()
    except ValidationError as exc:
        print(f"Configuration error in .env:\n{describe(exc)}", file=sys.stderr)
        return 2
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # The SDK's HTTP client logs every request at INFO; keep the log about emails.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    log = logging.getLogger("notifier.remind")

    reminder = REMINDERS[args.reminder]
    _, payload = event_for(reminder, datetime.now(settings.timezone))
    event = events.parse(payload)
    assert event is not None

    ctx = build_context(settings)
    try:
        outcome = reminder.run(event, ctx)
    except Drop as exc:
        log.info("%s: nothing sent (%s)", reminder.name, exc)
        return 0
    except (CredoUnavailable, MailError) as exc:
        log.error(
            "%s: stopped (%s: %s); run it again later",
            reminder.name,
            type(exc).__name__,
            exc,
        )
        return 1
    finally:
        ctx.store.close()
    log.info("%s for %s: %s", reminder.name, event.text("period"), outcome)
    return 0


if __name__ == "__main__":
    sys.exit(main())
