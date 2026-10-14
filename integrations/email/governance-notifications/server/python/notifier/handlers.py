"""Scenario handlers. Today there is one: a model was added to the registry.

A handler either returns a `Sent` (the worker records it), or raises:

- `Drop`: nothing will ever be sent for this delivery (wrong entity type,
  already emailed, recipient not allowed, model doesn't exist);
- `credo.CredoUnavailable` / `mailer.MailError`: try again later.

**Read this before adding a handler.** Signature verification is off (see the
README), so a payload is a hint, not a fact. Each handler confirms the state it
is about to announce through the SDK, emails only addresses it has vetted, and
puts nothing from the payload into the email.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from email.utils import parseaddr
from urllib.parse import quote

from . import events, mailer
from .config import Settings
from .credo import Credo
from .store import Store


class Drop(Exception):
    """Nothing to send for this delivery, now or later."""


@dataclass(frozen=True)
class Sent:
    key: str
    recipient: str


@dataclass(frozen=True)
class Context:
    settings: Settings
    store: Store
    credo: Credo
    mailer: mailer.Mailer


def model_added_key(model_id: str) -> str:
    return f"model_added:{model_id}"


def handle_model_added(event: events.Event, ctx: Context) -> Sent:
    """Email the address on a model's `entity_created` event.

    The address is the acting user on the event, which for a real event is the
    person (or API key) that created the model. Until the SDK can look users
    up, nothing confirms that, so the address must be on the domain allow-list
    and the email doesn't claim the recipient created anything.
    """
    if not events.is_model_created(event):
        raise Drop("not a model")

    model_id = event.data.get("model_id")
    if not isinstance(model_id, str) or not model_id:
        raise Drop("no modelId in payload")

    key = model_added_key(model_id)
    if ctx.store.was_sent(key):
        raise Drop("already emailed for this model")

    user = event.data.get("user")
    recipient = allowed_recipient(
        user.get("email") if isinstance(user, dict) else None,
        ctx.settings.allowed_domains,
    )

    # Fail closed: a model we can't find is a stale or forged event.
    model = ctx.credo.get_model(model_id)
    if model is None:
        raise Drop("model not found in Credo AI")

    link = f"{ctx.settings.credo_app_base_url}/models/{quote(model.id, safe='')}"
    email = mailer.render(
        "model_added",
        to=recipient,
        subject=f"[Credo AI] New model added: {model.name}",
        model=model,
        link=link,
    )
    ctx.mailer.send(email)
    return Sent(key=key, recipient=recipient)


# Deliberately narrower than RFC 5322: no quoting, `@`, `%` or `!` in the local
# part, so an address can't route mail out of an allowed domain.
_LOCAL_PART = re.compile(r"[A-Za-z0-9._+'-]+")


def allowed_recipient(raw: object, allowed_domains: frozenset[str]) -> str:
    """Return a normalized address, or raise Drop when it may not be emailed."""
    if not isinstance(raw, str) or not raw.strip():
        raise Drop("no user email in payload (an automated change has no user)")
    if any(c in raw for c in "\r\n,;<>"):
        raise Drop("recipient is not a single plain address")
    _, address = parseaddr(raw.strip())
    local, sep, domain = address.rpartition("@")
    if not sep or not domain or not _LOCAL_PART.fullmatch(local):
        raise Drop("recipient is not a valid address")
    domain = domain.lower()
    if domain not in allowed_domains:
        raise Drop(f"recipient domain {domain!r} is not in ALLOWED_EMAIL_DOMAINS")
    return f"{local}@{domain}"


# subject -> handler. A subject with no handler is acknowledged and ignored.
HANDLERS = {
    events.ENTITY_CREATED: handle_model_added,
}
