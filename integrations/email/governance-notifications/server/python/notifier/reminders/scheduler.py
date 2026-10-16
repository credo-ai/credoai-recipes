"""Queue the reminders on their cron schedules, inside the server process.

With `REMINDERS_IN_PROCESS=false` nothing is scheduled here; run `remind.py`
from your own cron instead. Either way the same period is sent once.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from ..config import Settings
from ..store import Store
from . import REMINDERS, Reminder, event_for

log = logging.getLogger("notifier.reminders")


def queue(store: Store, reminder: Reminder, now: datetime) -> bool:
    """Queue this period's run. False when it was already queued."""
    svix_id, payload = event_for(reminder, now)
    return store.enqueue(svix_id, reminder.subject, payload)


class Scheduler:
    def __init__(
        self, settings: Settings, store: Store, notify: Callable[[], None]
    ) -> None:
        self._settings = settings
        self._store = store
        self._notify = notify
        self._scheduler = BackgroundScheduler(timezone=settings.timezone)
        crons = {
            "pending_controls": settings.pending_controls_cron,
            "pending_questionnaires": settings.questionnaire_reminder_cron,
        }
        for name, cron in crons.items():
            self._scheduler.add_job(
                self._fire,
                CronTrigger.from_crontab(cron, timezone=settings.timezone),
                args=[REMINDERS[name]],
                id=name,
                # A run missed while the server was down is made up when it
                # starts again, if it's still in the same hour.
                misfire_grace_time=3600,
                coalesce=True,
            )

    def start(self) -> None:
        self._scheduler.start()
        for job in self._scheduler.get_jobs():
            log.info("reminder %s: next run %s", job.id, job.next_run_time)

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)

    def _fire(self, reminder: Reminder) -> None:
        try:
            if queue(self._store, reminder, datetime.now(self._settings.timezone)):
                log.info("reminder %s: queued", reminder.name)
                self._notify()
            else:
                log.info("reminder %s: already queued for this period", reminder.name)
        except Exception:
            log.exception("reminder %s: couldn't queue it", reminder.name)
