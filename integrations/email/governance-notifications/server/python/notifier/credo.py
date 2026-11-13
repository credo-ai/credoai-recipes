"""Credo AI access, through the official `pycredoai` SDK.

This is the only module that imports the SDK. It exposes the reads the
scenarios need and turns the SDK's failure modes into two outcomes the rest of
the code can act on:

- "this doesn't exist" (`None`, or an empty list): the event is stale (for
  example, the use case was deleted since), so nothing is sent;
- `CredoUnavailable`: Credo AI couldn't be asked right now (network, auth,
  5xx), so the delivery is retried later. Nothing is sent without a successful
  read; the server fails closed.

People come back as `Person`, with their *current* address from the users API.
Deleted users and invitees who haven't accepted yet are never returned, and
teams (an owner or a reviewer can be a team) are counted but skipped: there is
no public read of a team's members yet.

The synchronous `CredoAI` client is used on purpose. Calls run on the worker
thread, never on the web server's event loop, and only that thread uses this
object (reminders are queued to it rather than run on the scheduler's thread).

The sync client exchanges the API key for a token once, in its constructor, and
doesn't renew it. The token lasts 24 hours, so this module builds a new client
every 12 hours, and after any failed call.
"""

from __future__ import annotations

import contextlib
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

import httpx
from credoai import (
    CredoAI,
    EntityResponse,
    EntityReviewResponse,
    ModelResponse,
    PublicUserResponse,
    QuestionnaireAnswersResponse,
    QuestionnaireAttachmentResponse,
    TaskResponse,
    UseCaseResponse,
    WebhookCreate,
    WorkflowStageResponse,
)
from credoai.auth import AuthenticationError
from credoai.errors import ApiError, CredoAIError

from .config import CredoSettings

log = logging.getLogger("notifier.credo")

T = TypeVar("T")

# A malformed id comes back as 400/422 rather than 404. For our purposes that is
# the same answer: there is no such thing.
_NOT_FOUND = {400, 404, 422}

# Credo AI's access tokens last 24 hours; renew at half that.
CLIENT_MAX_AGE_SECONDS = 12 * 3600

# The SDK puts path parameters into the URL as they are, so an id from a
# payload is checked before it is used in one. User ids are 22-character
# ShortUUIDs; other ids are UUIDs or ShortUUIDs.
_USER_ID = re.compile(r"[A-Za-z0-9]{22}")
_ANY_ID = re.compile(r"[A-Za-z0-9-]{1,64}")

CONTROL_TASK_TYPES = ["use_case_control_evidence"]


# The SDK's entity read, under the name the handlers use.
Entity = EntityResponse


class CredoUnavailable(Exception):
    """Credo AI couldn't answer; try again later."""


@dataclass(frozen=True)
class Person:
    user_id: str
    email: str
    given_name: str | None = None

    @property
    def address_key(self) -> str:
        """The address as used in idempotency keys and de-duplication."""
        return self.email.lower()


@dataclass(frozen=True)
class People:
    """The people to email, plus the teams that were skipped."""

    people: list[Person] = field(default_factory=list)
    skipped_teams: int = 0


def is_user_id(value: object) -> bool:
    return isinstance(value, str) and _USER_ID.fullmatch(value) is not None


def is_id(value: object) -> bool:
    return isinstance(value, str) and _ANY_ID.fullmatch(value) is not None


class Credo:
    def __init__(
        self,
        settings: CredoSettings,
        client_factory: Callable[[], CredoAI] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory or self._new_client
        self._clock = clock
        self._client_instance: CredoAI | None = None
        self._built_at = 0.0
        self._cache: dict[tuple[str, str], tuple[float, Any]] = {}

    def _new_client(self) -> CredoAI:
        return CredoAI(
            base_url=self._settings.credo_api_base_url,
            api_key=self._settings.credo_api_key,
            tenant=self._settings.credo_tenant,
        )

    @property
    def _client(self) -> CredoAI:
        # Built on first use, inside the callers' error handling: the
        # constructor exchanges the API key for a token straight away, and that
        # can fail like any other call. Rebuilt well before the token expires.
        if (
            self._client_instance is not None
            and self._clock() - self._built_at > CLIENT_MAX_AGE_SECONDS
        ):
            self._discard_client()
        if self._client_instance is None:
            self._client_instance = self._client_factory()
            self._built_at = self._clock()
        return self._client_instance

    def _discard_client(self) -> None:
        client, self._client_instance = self._client_instance, None
        if client is not None:
            with contextlib.suppress(Exception):
                client.get_httpx_client().close()

    def _call(self, fn: Callable[[CredoAI], T], missing: T) -> T:
        """Run one read. `missing` is the answer when Credo AI says not found."""
        try:
            return fn(self._client)
        except ApiError as exc:
            if exc.status_code in _NOT_FOUND:
                return missing
            raise self._unavailable(exc) from exc
        except (AuthenticationError, CredoAIError, httpx.HTTPError) as exc:
            raise self._unavailable(exc) from exc

    def _unavailable(self, exc: Exception) -> CredoUnavailable:
        # Rebuild on the next call. That fixes a token that was revoked or
        # expired early (401); for any other failure it costs one extra request.
        self._discard_client()
        return CredoUnavailable(f"{type(exc).__name__}: {exc}")

    def _cached(self, kind: str, key: str, load: Callable[[], T]) -> T:
        now = self._clock()
        hit = self._cache.get((kind, key))
        if hit is not None and hit[0] > now:
            return hit[1]
        value = load()
        self._cache[(kind, key)] = (now + self._settings.recipient_cache_seconds, value)
        if len(self._cache) > 5000:
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
        return value

    # ---- things ------------------------------------------------------------

    def model(self, model_id: str) -> ModelResponse | None:
        if not is_id(model_id):
            return None
        return self._call(lambda c: c.models.get(model_id=model_id), None)

    def entity(self, entity_id: str) -> EntityResponse | None:
        if not is_id(entity_id):
            return None
        return self._call(lambda c: c.entities.get(entity_id=entity_id), None)

    def workflow_stage(self, stage_id: str) -> WorkflowStageResponse | None:
        if not is_id(stage_id):
            return None
        return self._call(
            lambda c: c.workflow_stages.get(workflow_stage_id=stage_id), None
        )

    def review(self, review_id: str) -> EntityReviewResponse | None:
        if not is_id(review_id):
            return None
        return self._call(lambda c: c.entity_reviews.get(review_id=review_id), None)

    def task_in_state(self, entity_id: str, task_id: str, sub_status: str) -> bool:
        """Whether the entity's task `task_id` is at `sub_status` right now."""
        if not is_id(entity_id):
            return False
        tasks = self._call(
            lambda c: list(
                c.tasks.list_all(entity_id=entity_id, sub_status=sub_status)
            ),
            [],
        )
        return any(t.id == task_id for t in tasks)

    def active_control_tasks(self) -> list[TaskResponse]:
        """Every control task that still owes its assignee work, tenant-wide.

        `active=True` is the platform's own rule (the in-app counts use it): a
        task stops being active once it is done or its control's evidence is
        provided, unless a reviewer asked for changes.
        """
        return self._call(
            lambda c: list(
                c.tasks.list_all(type=CONTROL_TASK_TYPES, active=True, page_size=100)
            ),
            [],
        )

    def control_keys(self, use_case_id: str) -> dict[str, str]:
        """A use case's controls: control id → control key."""
        if not is_id(use_case_id):
            return {}

        def load(c: CredoAI) -> dict[str, str]:
            keys: dict[str, str] = {}
            after = None
            while True:
                page = c.use_cases(use_case_id).controls.list(
                    page_limit=100, page_after=after
                )
                keys.update({control.id: control.control_key for control in page.items})
                after = page.pagination.next_cursor
                if not page.pagination.has_more or not after:
                    return keys

        return self._call(load, {})

    def use_cases(self) -> list[UseCaseResponse]:
        return self._call(lambda c: list(c.use_cases.list_all(page_size=100)), [])

    def questionnaires(self, use_case_id: str) -> list[QuestionnaireAttachmentResponse]:
        if not is_id(use_case_id):
            return []

        def load(c: CredoAI) -> list[QuestionnaireAttachmentResponse]:
            out: list[QuestionnaireAttachmentResponse] = []
            after = None
            while True:
                page = c.use_cases(use_case_id).questionnaires.list(
                    page_limit=100, page_after=after
                )
                out.extend(page.items)
                after = page.pagination.next_cursor
                if not page.pagination.has_more or not after:
                    return out

        return self._call(load, [])

    def answer_counts(
        self, use_case_id: str, attachment_id: str
    ) -> QuestionnaireAnswersResponse | None:
        if not (is_id(use_case_id) and is_id(attachment_id)):
            return None
        return self._call(
            lambda c: (
                c.use_cases(use_case_id)
                .questionnaires(attachment_id)
                .answers.get(mode="all")
            ),
            None,
        )

    def create_webhook(
        self, url: str, event_types: list[str], description: str
    ) -> tuple[str, str]:
        """Create a webhook and return its id and signing secret (setup only)."""
        try:
            webhook = self._client.webhooks.create(
                WebhookCreate(
                    description=description,
                    url=url,
                    event_types=event_types,
                    authentication_method="none",
                )
            )
        except (AuthenticationError, CredoAIError, httpx.HTTPError) as exc:
            raise self._unavailable(exc) from exc
        try:
            secret = self._client.webhooks.get_secret(webhook_id=webhook.id).secret
        except (AuthenticationError, CredoAIError, httpx.HTTPError) as exc:
            self._discard_client()
            raise CredoUnavailable(
                f"created webhook {webhook.id} but couldn't read its signing "
                f"secret ({type(exc).__name__}: {exc}); delete it in Settings → "
                "Webhooks, or copy its secret from there into .env"
            ) from exc
        return webhook.id, secret

    # ---- people ------------------------------------------------------------

    def user(self, user_id: str) -> Person | None:
        """The user's current address, or None for no such, deleted or pending user."""
        if not is_user_id(user_id):
            return None
        return self._cached(
            "user",
            user_id,
            lambda: _person(self._call(lambda c: c.users.get(user_id=user_id), None)),
        )

    def admins(self) -> list[Person]:
        def load() -> list[Person]:
            users = self._call(
                lambda c: list(c.users.list_all(role="admin", page_size=100)), []
            )
            return [p for p in map(_person, users) if p is not None]

        return self._cached("admins", "", load)

    def owners(self, entity_id: str) -> People:
        """The entity's owners who can be emailed."""
        if not is_id(entity_id):
            return People()
        stakeholders = self._cached(
            "owners",
            entity_id,
            lambda: self._call(
                lambda c: c.entities(entity_id).stakeholders.list(role="owner"), []
            ),
        )
        return self._people(
            (s.user_id, s.team_id) for s in stakeholders if s.role == "owner"
        )

    def open_review_reviewers(self, entity_id: str) -> People:
        """Everyone assigned to the entity's open review(s)."""
        if not is_id(entity_id):
            return People()

        def load() -> list[tuple[str | None, str | None]]:
            page = self._call(
                lambda c: c.entities(entity_id).reviews.list(
                    status="in_progress", page_limit=100
                ),
                None,
            )
            assignees: list[tuple[str | None, str | None]] = []
            for summary in page.items if page is not None else []:
                review = self.review(summary.id)
                for r in (review.reviewers or []) if review is not None else []:
                    assignees.append((r.user_id, r.team_id))
            return assignees

        return self._people(self._cached("reviewers", entity_id, load))

    def _people(self, assignees: Any) -> People:
        people: dict[str, Person] = {}
        teams = 0
        for user_id, team_id in assignees:
            if user_id:
                person = self.user(user_id)
                if person is not None:
                    people.setdefault(person.address_key, person)
            elif team_id:
                teams += 1
        return People(people=list(people.values()), skipped_teams=teams)


def _person(user: PublicUserResponse | None) -> Person | None:
    if user is None or not user.email:
        return None
    # A pending invitee was invited and hasn't accepted yet. Users who arrived
    # through single sign-on were never invited, so both fields are empty.
    if user.invitation_sent_at and not user.invitation_accepted_at:
        return None
    return Person(user_id=user.id, email=user.email.strip(), given_name=user.given_name)
