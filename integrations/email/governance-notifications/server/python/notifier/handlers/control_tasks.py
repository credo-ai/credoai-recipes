"""#3: a control's evidence is ready for validation → email the use case's owners."""

from __future__ import annotations

from urllib.parse import quote

from .. import events, mailer
from ..credo import CredoUnavailable
from . import DAY, Context, Drop, Outcome, deliver, entity_link, log, require

READY = "ready_for_validation"


def handle(event: events.Event, ctx: Context) -> Outcome:
    if event.data.get("entity_type") != "use_case":
        raise Drop("not a use case")
    # Only the move *into* ready: re-saving a task that was already ready
    # fires the event again without anything new to say.
    if event.data.get("sub_status") != READY:
        raise Drop("not ready for validation")
    if event.data.get("previous_sub_status") == READY:
        raise Drop("was already ready for validation")
    entity_id = event.text("entity_id")
    task_id = event.text("task_id")
    if entity_id is None or task_id is None:
        raise Drop("no entityId or taskId in payload")

    if not ctx.credo.task_in_state(entity_id, task_id, READY):
        raise Drop("stale: the task isn't ready for validation any more")
    entity = ctx.credo.entity(entity_id)
    if entity is None or not entity.use_case_id:
        raise Drop("use case not found in Credo AI")

    owners = require(ctx.credo.owners(entity_id), "use case owner")
    control_id = event.text("entity_control_id")
    try:
        control_key = ctx.credo.control_keys(entity.use_case_id).get(control_id or "")
    except CredoUnavailable as exc:
        # Only the wording and the link depend on it; the email can go without.
        log.warning("control key unavailable, sending without it: %s", exc)
        control_key = None
    if control_id and control_key:
        link = entity_link(
            ctx.settings, entity, f"/controls/{quote(control_id, safe='')}"
        )
    else:
        link = entity_link(ctx.settings, entity)

    outcome = Outcome()
    for person in owners.people:
        email = mailer.render(
            "control_ready_for_validation",
            to=person.email,
            subject=f"[Credo AI] Evidence ready for validation: {entity.name}",
            person=person,
            entity=entity,
            control_key=control_key,
            link=link,
        )
        # A reviewer can send it back (change request) and it can come back
        # ready, which deserves a new email; a day apart is the limit.
        key = f"control_ready:{task_id}:{person.address_key}"
        deliver(ctx, outcome, key, DAY, email)
    return outcome
