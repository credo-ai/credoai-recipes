"""#9: a questionnaire answer was changed → email the admins and the open review's reviewers.

A changed answer is a new answer that replaces an earlier one
(`replacedEvidenceId` set). A first answer is ignored. Any entity type counts,
not just use cases. The person who made the change isn't left out: if they are
an admin or a reviewer, they get the email too.
"""

from __future__ import annotations

from .. import events, mailer
from . import FOREVER, Context, Drop, Outcome, deliver, entity_link, log


def handle(event: events.Event, ctx: Context) -> Outcome:
    if not event.text("question_id"):
        raise Drop("not a questionnaire answer")
    if not event.text("replaced_evidence_id"):
        raise Drop("a first answer, not a changed one")
    entity_id = event.text("entity_id")
    evidence_id = event.text("evidence_id")
    if entity_id is None or evidence_id is None:
        raise Drop("no entityId or evidenceId in payload")

    entity = ctx.credo.entity(entity_id)
    if entity is None:
        raise Drop("entity not found in Credo AI")

    reviewers = ctx.credo.open_review_reviewers(entity_id)
    if reviewers.skipped_teams:
        log.info(
            "evidence %s: skipped %d team reviewer(s)",
            evidence_id,
            reviewers.skipped_teams,
        )
    recipients = {p.address_key: ("admin", p) for p in ctx.credo.admins()}
    for person in reviewers.people:
        recipients.setdefault(person.address_key, ("reviewer", person))
    if not recipients:
        raise Drop("no admin or reviewer to email")

    link = entity_link(ctx.settings, entity, "/questionnaires")
    outcome = Outcome()
    for address_key, (role, person) in recipients.items():
        email = mailer.render(
            "answer_changed",
            to=person.email,
            subject=f"[Credo AI] A questionnaire answer changed: {entity.name}",
            person=person,
            entity=entity,
            role=role,
            link=link,
        )
        deliver(
            ctx, outcome, f"answer_changed:{evidence_id}:{address_key}", FOREVER, email
        )
    return outcome
