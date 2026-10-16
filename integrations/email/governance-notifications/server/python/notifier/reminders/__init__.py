"""Timed reminders (#6, #8): there is no "due soon" event, so these poll.

Each reminder has a period (an ISO week, or a day, in `REMINDER_TIMEZONE`),
and each person gets at most one reminder email per period. A run is queued as
an outbox row with an id naming the reminder and its period, so the worker
thread sends it with the same retries as a webhook delivery, and queueing the
same period twice does nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from .. import events
from ..handlers import Context, Outcome
from . import pending_controls, pending_questionnaires


@dataclass(frozen=True)
class Reminder:
    name: str
    period: Callable[[datetime], str]
    run: Callable[[events.Event, Context], Outcome]

    @property
    def subject(self) -> str:
        return f"reminder.{self.name}"


def iso_week(now: datetime) -> str:
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def day(now: datetime) -> str:
    return now.date().isoformat()


REMINDERS = {
    r.name: r
    for r in (
        Reminder("pending_controls", iso_week, pending_controls.run),
        Reminder("pending_questionnaires", day, pending_questionnaires.run),
    )
}


def event_for(reminder: Reminder, now: datetime) -> tuple[str, dict]:
    """The outbox id and payload for this reminder's run in `now`'s period."""
    period = reminder.period(now)
    payload = {"subject": reminder.subject, "data": {"period": period}}
    return f"reminder:{reminder.name}:{period}", payload
