"""#2 and #7: a use case's review was approved → email its owners and the admins.

One handler sends both. An owner who is also an admin gets the owner's email
only. The review is approved once and stays that way, so each person is
emailed about it once, ever.
"""

from __future__ import annotations

from .. import events, mailer
from . import FOREVER, Context, Drop, Outcome, deliver, entity_link, log


def handle(event: events.Event, ctx: Context) -> Outcome:
    if event.data.get("entity_type") != "use_case":
        raise Drop("not a use case")
    if event.data.get("status") != "approved":
        raise Drop("not an approval")
    entity_id = event.text("entity_id")
    review_id = event.text("review_id")
    if entity_id is None or review_id is None:
        raise Drop("no entityId or reviewId in payload")

    review = ctx.credo.review(review_id)
    if review is None:
        raise Drop("review not found in Credo AI")
    if review.status != "approved" or review.entity_id != entity_id:
        raise Drop("stale: the review isn't approved on this use case")
    entity = ctx.credo.entity(entity_id)
    if entity is None:
        raise Drop("use case not found in Credo AI")

    owners = ctx.credo.owners(entity_id)
    if owners.skipped_teams:
        log.info("review %s: skipped %d team owner(s)", review_id, owners.skipped_teams)
    owner_keys = {p.address_key for p in owners.people}
    admins = [p for p in ctx.credo.admins() if p.address_key not in owner_keys]
    if not owners.people and not admins:
        raise Drop("no owner or admin to email")

    # An automated approval (the auto-approval rule) has no acting user.
    automated = event.data.get("automated") is True
    link = entity_link(ctx.settings, entity)
    outcome = Outcome()
    for template, people in (
        ("review_approved_owner", owners.people),
        ("review_approved_admin", admins),
    ):
        for person in people:
            email = mailer.render(
                template,
                to=person.email,
                subject=f"[Credo AI] Review approved: {entity.name}",
                person=person,
                entity=entity,
                automated=automated,
                link=link,
            )
            key = f"review_approved:{review_id}:{person.address_key}"
            deliver(ctx, outcome, key, FOREVER, email)
    return outcome
