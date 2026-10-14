"""The background thread that turns outbox rows into emails.

It drains due rows one at a time, so sends are naturally serialized, and it
wakes immediately when the web handler stores a new delivery. Rows left
`pending` by a crash are simply due on the next start.
"""

from __future__ import annotations

import logging
import threading

from . import events
from .credo import CredoUnavailable
from .handlers import HANDLERS, Context, Drop
from .mailer import MailError
from .store import DROPPED, FAILED, Delivery

log = logging.getLogger("notifier.worker")

# Wait between retries: 30s, 1m, 2m, 4m, ... capped at 30m.
_BASE_DELAY = 30.0
_MAX_DELAY = 30 * 60.0
_IDLE_POLL = 60.0


class Worker:
    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="notifier-worker", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=30)

    def notify(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        store = self._ctx.store
        while not self._stop.is_set():
            delivery = store.next_due()
            if delivery is None:
                wait = store.seconds_until_next()
                self._wake.wait(
                    timeout=_IDLE_POLL if wait is None else min(wait, _IDLE_POLL)
                )
                self._wake.clear()
                continue
            self._process(delivery)

    def _process(self, delivery: Delivery) -> None:
        store = self._ctx.store
        event = events.parse(delivery.payload)
        handler = HANDLERS.get(delivery.subject)
        if event is None or handler is None:
            store.finish(delivery.svix_id, DROPPED, "no handler for this event")
            return

        try:
            sent = handler(event, self._ctx)
        except Drop as exc:
            log.info("delivery %s: nothing sent (%s)", delivery.svix_id, exc)
            store.finish(delivery.svix_id, DROPPED, str(exc))
        except (CredoUnavailable, MailError) as exc:
            self._retry(delivery, f"{type(exc).__name__}: {exc}")
        except Exception as exc:  # a bug; keep the row and retry rather than lose it
            log.exception("delivery %s: unexpected error", delivery.svix_id)
            self._retry(delivery, f"unexpected {type(exc).__name__}: {exc}")
        else:
            reason = "dry_run" if self._ctx.settings.mail_dry_run else "sent"
            store.complete(delivery.svix_id, sent.key, sent.recipient, reason)
            log.info(
                "delivery %s: %s %s to %s",
                delivery.svix_id,
                reason,
                sent.key,
                sent.recipient,
            )

    def _retry(self, delivery: Delivery, note: str) -> None:
        attempts = delivery.attempts + 1
        if attempts >= self._ctx.settings.max_attempts:
            log.error(
                "delivery %s: giving up after %d attempts (%s)",
                delivery.svix_id,
                attempts,
                note,
            )
            self._ctx.store.finish(delivery.svix_id, FAILED, note)
            return
        delay = min(_BASE_DELAY * 2 ** (attempts - 1), _MAX_DELAY)
        log.warning(
            "delivery %s: attempt %d failed, retrying in %.0fs (%s)",
            delivery.svix_id,
            attempts,
            delay,
            note,
        )
        self._ctx.store.retry_later(delivery.svix_id, attempts, delay, note)
