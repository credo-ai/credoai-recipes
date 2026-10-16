"""#1 and #4: a use case changed workflow stage → email its owners.

One handler covers both. #1 is the new stage's `on_enter` wording; #4 is the
Intake stage's `on_leave` wording (the "approved out of Intake" message). Both
come from `stage_messages.yaml`, so leaving Intake sends one email carrying
Intake's `on_leave` and then the next stage's `on_enter`.
"""

from __future__ import annotations

from .. import events, mailer
from . import DAY, Context, Drop, Outcome, deliver, entity_link, require


def handle(event: events.Event, ctx: Context) -> Outcome:
    if event.data.get("entity_type") != "use_case":
        raise Drop("not a use case")
    entity_id = event.text("entity_id")
    stage_id = event.text("workflow_stage_id")
    if entity_id is None or stage_id is None:
        raise Drop("no entityId or workflowStageId in payload")

    # The use case must still be in the stage the event announces; a retry that
    # arrives after a later move would otherwise send an out-of-date email.
    entity = ctx.credo.entity(entity_id)
    if entity is None:
        raise Drop("use case not found in Credo AI")
    if entity.workflow_stage_id != stage_id:
        raise Drop("stale: the use case has changed stage since")
    stage = ctx.credo.workflow_stage(stage_id)
    if stage is None:
        raise Drop("workflow stage not found in Credo AI")

    previous_id = event.text("previous_workflow_stage_id")
    previous = ctx.credo.workflow_stage(previous_id) if previous_id else None
    leaving = ctx.stages.on_leave(previous) if previous else None
    entering = ctx.stages.on_enter(stage)
    if not leaving and not entering:
        raise Drop(
            f"no wording in stage_messages.yaml for this move into {stage.name!r}"
        )

    owners = require(ctx.credo.owners(entity_id), "use case owner")
    link = entity_link(ctx.settings, entity)
    outcome = Outcome()
    for person in owners.people:
        email = mailer.render(
            "stage_changed",
            to=person.email,
            subject=f"[Credo AI] {entity.name} moved to {stage.name}",
            person=person,
            entity=entity,
            stage=stage,
            previous=previous,
            leaving=_paragraphs(leaving),
            entering=_paragraphs(entering),
            link=link,
        )
        key = f"stage_changed:{entity_id}:{stage_id}:{person.address_key}"
        deliver(ctx, outcome, key, DAY, email)
    return outcome


def _paragraphs(text: str | None) -> list[str]:
    if not text:
        return []
    return [" ".join(p.split()) for p in text.split("\n\n") if p.strip()]
