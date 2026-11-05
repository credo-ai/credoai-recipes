"""The webhook envelope Credo AI sends, and the one event this cookbook handles.

Every delivery looks like this (keys are camelCased on the wire):

    {
      "type": "entity_created",        # can be renamed per webhook; don't route on it
      "subject": "entity_created",     # always the real event type; route on this
      "time": "2026-10-13T22:00:00Z",
      "source": "CredoAI",
      "data": {"entityId": ..., "entityType": "model", "modelId": ..., "user": {...}}
    }

`data` holds identifiers only. The model's name, summary and status are read
back through the SDK, never taken from the payload.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

ENTITY_CREATED = "entity_created"

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


@dataclass(frozen=True)
class Event:
    subject: str
    data: dict[str, Any]


def parse(payload: Any) -> Event | None:
    """Return the event, or None when the body isn't a Credo AI envelope."""
    if not isinstance(payload, dict):
        return None
    subject = payload.get("subject")
    data = payload.get("data")
    if not isinstance(subject, str) or not isinstance(data, dict):
        return None
    return Event(subject=subject, data=_snake_keys(data))


def is_model_created(event: Event) -> bool:
    return event.subject == ENTITY_CREATED and event.data.get("entity_type") == "model"


def _snake_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {_to_snake(k): _snake_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_snake_keys(v) for v in value]
    return value


def _to_snake(key: str) -> str:
    return _CAMEL_BOUNDARY.sub("_", key).lower()
