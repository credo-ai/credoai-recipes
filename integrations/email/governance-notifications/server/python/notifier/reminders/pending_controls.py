"""#6: weekly digest of pending controls → each use case owner.

"Pending" starts from the platform's own rule, the one the in-app task counts
use: a control task that isn't done, whose control's evidence isn't provided
yet (unless a reviewer asked for changes). A task marked ready for validation
is left out: it's waiting on a reviewer, not on the owner, and #3 already
announced it. Controls with nobody assigned have no task, so they never
appear here.

Each owner gets one email listing the pending controls on every use case they
own; a control on a use case with two owners is in both digests.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

from .. import events, mailer
from ..credo import Person
from ..handlers import FOREVER, Context, Outcome, deliver, entity_link, log


@dataclass(frozen=True)
class Item:
    use_case: str
    control: str
    state: str
    due: str | None
    overdue: bool
    link: str


def run(event: events.Event, ctx: Context) -> Outcome:
    period = event.text("period") or "unknown"
    tz = ctx.settings.timezone
    now = datetime.now(tz)

    by_entity = defaultdict(list)
    for task in ctx.credo.active_control_tasks():
        # The platform still counts these as open until the evidence is
        # provided, but they're waiting on a reviewer, not the owner.
        if task.entity_id and task.sub_status != "ready_for_validation":
            by_entity[task.entity_id].append(task)

    digests: dict[str, tuple[Person, list[Item]]] = {}
    teams = 0
    for entity_id, tasks in by_entity.items():
        entity = ctx.credo.entity(entity_id)
        if entity is None or entity.entity_type != "use_case" or not entity.use_case_id:
            continue
        owners = ctx.credo.owners(entity_id)
        teams += owners.skipped_teams
        if not owners.people:
            continue
        keys = ctx.credo.control_keys(entity.use_case_id)
        items = []
        for task in tasks:
            control_id = task.use_case_control_id or ""
            due = task.due_date.astimezone(tz) if task.due_date else None
            items.append(
                Item(
                    use_case=entity.name or "Untitled use case",
                    control=keys.get(control_id, "A control"),
                    state=_state(task.status, task.sub_status),
                    due=due.date().isoformat() if due else None,
                    overdue=due is not None and due < now,
                    link=entity_link(
                        ctx.settings, entity, f"/controls/{quote(control_id, safe='')}"
                    )
                    if control_id in keys
                    else entity_link(ctx.settings, entity),
                )
            )
        for person in owners.people:
            digests.setdefault(person.address_key, (person, []))[1].extend(items)

    if teams:
        log.info("pending controls: skipped %d team owner(s)", teams)
    outcome = Outcome()
    for address_key, (person, items) in digests.items():
        items.sort(key=lambda i: (i.due is None, i.due or "", i.use_case, i.control))
        email = mailer.render(
            "weekly_pending_controls",
            to=person.email,
            subject=f"[Credo AI] {len(items)} control(s) waiting on your use cases",
            person=person,
            items=items,
            overdue=sum(i.overdue for i in items),
        )
        deliver(
            ctx, outcome, f"pending_controls:{address_key}:{period}", FOREVER, email
        )
    return outcome


def _state(status: str | None, sub_status: str | None) -> str:
    if sub_status == "change_request":
        return "Changes requested"
    if status == "in_progress":
        return "In progress"
    return "To do"
