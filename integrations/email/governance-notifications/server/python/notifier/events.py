"""The webhook envelope Credo AI sends, and the event types this cookbook handles.

Every delivery looks like this (keys are camelCased on the wire):

    {
      "type": "entity_created",        # can be renamed per webhook; don't route on it
      "subject": "entity_created",     # always the real event type; route on this
      "time": "2026-10-13T22:00:00Z",
      "source": "CredoAI",
      "data": {"entityId": ..., "entityType": "model", "modelId": ..., "user": {...}}
    }

`data` holds identifiers and a few bounded values (a status, a stage id). Names,
titles and email addresses are read back through the SDK, never taken from the
payload. `fixtures/` has a real delivery of each type.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# The one subscription `setup_webhook.py` creates carries these five types.
ENTITY_CREATED = "entity_created"
WORKFLOW_STAGE_UPDATED = "entity_workflow_stage_updated"
REVIEW_STATUS_UPDATED = "entity_review_status_updated"
CONTROL_TASK_STATUS_UPDATED = "entity_control_task_status_updated"
EVIDENCE_CREATED = "entity_evidence_created"

SUBSCRIBED = (
    ENTITY_CREATED,
    WORKFLOW_STAGE_UPDATED,
    REVIEW_STATUS_UPDATED,
    CONTROL_TASK_STATUS_UPDATED,
    EVIDENCE_CREATED,
)

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


@dataclass(frozen=True)
class Event:
    subject: str
    data: dict[str, Any]

    def text(self, key: str) -> str | None:
        """A non-empty string field of `data`, or None."""
        value = self.data.get(key)
        return value if isinstance(value, str) and value else None


def parse(payload: Any) -> Event | None:
    """Return the event, or None when the body isn't a Credo AI envelope."""
    if not isinstance(payload, dict):
        return None
    subject = payload.get("subject")
    data = payload.get("data")
    if not isinstance(subject, str) or not isinstance(data, dict):
        return None
    return Event(subject=subject, data=_snake_keys(data))


def _snake_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {_to_snake(k): _snake_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_snake_keys(v) for v in value]
    return value


def _to_snake(key: str) -> str:
    return _CAMEL_BOUNDARY.sub("_", key).lower()
