"""Which handler gets which delivery.

One subscription carries all five event types (see `events.SUBSCRIBED`).
Handlers filter on the entity type and the payload themselves; several
scenarios share an event type, and one handler covers them. Reminders arrive
through the outbox under a `reminder.<name>` subject.
"""

from __future__ import annotations

from collections.abc import Callable

from . import events
from .handlers import (
    Context,
    Outcome,
    answers,
    control_tasks,
    models,
    reviews,
    workflow,
)
from .reminders import REMINDERS

Handler = Callable[[events.Event, Context], Outcome]

WEBHOOK_HANDLERS: dict[str, Handler] = {
    events.ENTITY_CREATED: models.handle,  # 5
    events.WORKFLOW_STAGE_UPDATED: workflow.handle,  # 1, 4
    events.REVIEW_STATUS_UPDATED: reviews.handle,  # 2, 7
    events.CONTROL_TASK_STATUS_UPDATED: control_tasks.handle,  # 3
    events.EVIDENCE_CREATED: answers.handle,  # 9
}

REMINDER_HANDLERS: dict[str, Handler] = {r.subject: r.run for r in REMINDERS.values()}


def accepts(subject: str) -> bool:
    """Whether a webhook delivery with this subject is worth storing."""
    return subject in WEBHOOK_HANDLERS


def handler_for(subject: str) -> Handler | None:
    return WEBHOOK_HANDLERS.get(subject) or REMINDER_HANDLERS.get(subject)
