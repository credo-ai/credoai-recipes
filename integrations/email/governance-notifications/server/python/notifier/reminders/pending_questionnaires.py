"""#8: daily check for questionnaires that are due soon or overdue → each use case owner.

A questionnaire counts when it isn't complete (`completed_at` is empty, and
it isn't known to be fully answered) and its due date is within
`QUESTIONNAIRE_DUE_WITHIN_DAYS`, or past. One without a due date is never
reminded about. Each owner gets at most one email a day, listing
every such questionnaire on the use cases they own, with how many questions
are answered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .. import events, mailer
from ..credo import Person
from ..handlers import FOREVER, Context, Outcome, deliver, entity_link, log


@dataclass(frozen=True)
class Item:
    use_case: str
    questionnaire: str
    due: str
    overdue: bool
    answered: int | None
    total: int | None
    link: str


def run(event: events.Event, ctx: Context) -> Outcome:
    period = event.text("period") or "unknown"
    tz = ctx.settings.timezone
    now = datetime.now(tz)
    horizon = now + timedelta(days=ctx.settings.questionnaire_due_within_days)

    digests: dict[str, tuple[Person, list[Item]]] = {}
    teams = 0
    for use_case in ctx.credo.use_cases():
        if not use_case.entity_id:
            continue
        pending = []
        for q in ctx.credo.questionnaires(use_case.id):
            due = _parse(q.due_date)
            if q.completed_at is not None or due is None or due > horizon:
                continue
            counts = ctx.credo.answer_counts(use_case.id, q.id)
            answered = counts.questions_with_answers if counts else None
            unanswered = counts.questions_with_no_answers if counts else None
            # `completed_at` is set only once every *required* question is
            # answered, so a questionnaire with no required questions never
            # gets one. Every question answered is done, whatever the flag.
            if unanswered == 0:
                continue
            pending.append((q, due.astimezone(tz), counts, answered, unanswered))
        if not pending:
            continue
        owners = ctx.credo.owners(use_case.entity_id)
        teams += owners.skipped_teams
        if not owners.people:
            continue
        entity = ctx.credo.entity(use_case.entity_id)
        if entity is None:
            continue
        link = entity_link(ctx.settings, entity, "/questionnaires")
        items = []
        for q, due, counts, answered, unanswered in pending:
            items.append(
                Item(
                    use_case=use_case.name,
                    questionnaire=(counts.questionnaire_name if counts else None)
                    or q.key,
                    due=due.date().isoformat(),
                    overdue=due < now,
                    answered=answered,
                    total=answered + unanswered
                    if answered is not None and unanswered is not None
                    else None,
                    link=link,
                )
            )
        for person in owners.people:
            digests.setdefault(person.address_key, (person, []))[1].extend(items)

    if teams:
        log.info("pending questionnaires: skipped %d team owner(s)", teams)
    outcome = Outcome()
    for address_key, (person, items) in digests.items():
        items.sort(key=lambda i: (i.due, i.use_case, i.questionnaire))
        email = mailer.render(
            "questionnaire_reminder",
            to=person.email,
            subject=f"[Credo AI] {len(items)} questionnaire(s) due on your use cases",
            person=person,
            items=items,
            overdue=sum(i.overdue for i in items),
            within_days=ctx.settings.questionnaire_due_within_days,
        )
        deliver(
            ctx,
            outcome,
            f"pending_questionnaires:{address_key}:{period}",
            FOREVER,
            email,
        )
    return outcome


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # A date without a time or offset is read as midnight UTC.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
