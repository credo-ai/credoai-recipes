"""What the scenario handlers share.

A handler takes an event and the `Context`, and either returns an `Outcome`
(what it sent and skipped), or raises:

- `Drop`: nothing will ever be sent for this delivery (wrong entity type, a
  state that has moved on, nobody to email);
- `credo.CredoUnavailable` / `mailer.MailError`: try again later.

**Read this before adding a handler.** The signature check proves a delivery
came from Credo AI, but it can still be stale: Svix retries, and the state may
have moved on since. So each handler re-reads the state it is about to
announce through the SDK and drops the email if that read disagrees or fails.
Names and addresses come from those reads; nothing typed by a user is taken
from the payload.

Each email is sent through `deliver`, under a content key with a window. A key
already sent inside its window is skipped, so a retry after a partly sent batch
sends only what is left, and a redelivery sends nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import quote

from .. import mailer
from ..config import Settings
from ..credo import Credo, Entity, People
from ..stages import StageMessages
from ..store import Store

log = logging.getLogger("notifier.handlers")

HOUR = 3600.0
DAY = 24 * HOUR
FOREVER = None


class Drop(Exception):
    """Nothing to send for this delivery, now or later."""


@dataclass(frozen=True)
class Context:
    settings: Settings
    store: Store
    credo: Credo
    mailer: mailer.Mailer
    stages: StageMessages


@dataclass
class Outcome:
    sent: list[str] = field(default_factory=list)
    already_sent: int = 0

    def __str__(self) -> str:
        parts = [f"sent {len(self.sent)}"]
        if self.already_sent:
            parts.append(f"{self.already_sent} already sent")
        return ", ".join(parts)


def deliver(
    ctx: Context, outcome: Outcome, key: str, window: float | None, email: mailer.Email
) -> None:
    """Send `email` unless `key` was already sent inside `window` seconds."""
    if ctx.store.sent_within(key, window):
        outcome.already_sent += 1
        return
    ctx.mailer.send(email)
    ctx.store.record_sent(
        key, email.to, "dry_run" if ctx.settings.mail_dry_run else "sent"
    )
    outcome.sent.append(key)


def require(people: People, what: str) -> People:
    """Log skipped teams, and drop when nobody is left to email."""
    if people.skipped_teams:
        log.info(
            "%s: skipped %d team(s); teams aren't emailed yet (see README)",
            what,
            people.skipped_teams,
        )
    if not people.people:
        raise Drop(f"no {what} to email")
    return people


def entity_link(settings: Settings, entity: Entity, suffix: str = "") -> str:
    """A deep link into the app, built from an entity the SDK returned."""
    base = settings.credo_app_base_url
    if entity.entity_type == "use_case" and entity.use_case_id:
        return f"{base}/use-cases/{quote(entity.use_case_id, safe='')}{suffix}"
    if entity.entity_type == "model" and entity.model_id and not suffix:
        return f"{base}/models/{quote(entity.model_id, safe='')}"
    return f"{base}/entities/{quote(entity.id, safe='')}{suffix}"
