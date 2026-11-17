"""#5: a model was added to the registry → email the person who added it."""

from __future__ import annotations

from urllib.parse import quote

from .. import events, mailer
from ..credo import is_user_id
from . import FOREVER, Context, Drop, Outcome, deliver


def model_added_key(model_id: str) -> str:
    return f"model_added:{model_id}"


def handle(event: events.Event, ctx: Context) -> Outcome:
    """Email the creator of a new model.

    The recipient is the event's acting user, looked up by id through the
    users API: their current address, and nobody who has been deleted or
    hasn't accepted their invitation. The address in the payload is never
    used. One email per model, ever.
    """
    if event.data.get("entity_type") != "model":
        raise Drop("not a model")
    model_id = event.text("model_id")
    if model_id is None:
        raise Drop("no modelId in payload")

    key = model_added_key(model_id)
    if ctx.store.sent_within(key, FOREVER):
        raise Drop("already emailed for this model")

    user = event.data.get("user")
    user_id = user.get("user_id") if isinstance(user, dict) else None
    if not is_user_id(user_id):
        raise Drop("no user id in payload (an automated change has no user)")

    # Fail closed: a model we can't find means a stale or unexpected event.
    model = ctx.credo.model(model_id)
    if model is None:
        raise Drop("model not found in Credo AI")
    person = ctx.credo.user(user_id)
    if person is None:
        raise Drop(
            "the user who added it doesn't exist, was deleted or hasn't accepted their invitation"
        )

    link = f"{ctx.settings.credo_app_base_url}/models/{quote(model.id, safe='')}"
    email = mailer.render(
        "model_added",
        to=person.email,
        subject=f"[Credo AI] New model added: {model.name}",
        person=person,
        model=model,
        link=link,
    )
    outcome = Outcome()
    deliver(ctx, outcome, key, FOREVER, email)
    return outcome
