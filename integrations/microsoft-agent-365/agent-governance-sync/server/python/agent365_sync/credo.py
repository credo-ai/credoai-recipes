"""Credo AI access, through the official SDK.

Everything this cookbook does in Credo AI goes through `pycredoai` — the SDK
handles the token exchange, pagination and response typing, and covers every
operation needed here (Use Cases, their questionnaires, and their workflow), so
there is no raw HTTP against Credo AI anywhere in this cookbook.

This module is a thin adapter exposing exactly the handful of operations
`sync.py` needs and nothing else, so the orchestration never imports the SDK and
stays free of any HTTP concern.

It is also the one place that knows the SDK is built on httpx. Two SDK failure
modes escape its own exception hierarchy — a failed token exchange raises
`credoai.auth.AuthenticationError`, and a transport failure raises
`httpx.RequestError`, neither of which is a `CredoAIError`. Both are re-raised as
`CredoAIError` here so the CLI can catch one type and report a "could not start"
exit.
"""

from __future__ import annotations

from functools import wraps

import httpx
from credoai import CredoAI, UseCaseCreate, UseCaseUpdate
from credoai.auth import AuthenticationError as SdkAuthenticationError
from credoai.errors import CredoAIError
from credoai.models import QuestionnaireAttachment

from agent365_sync.config import Settings

# One page per round trip when walking the Use Case listing. The SDK's
# `list_all` paginates for us; this only sets how many records each request
# carries.
PAGE_SIZE = 100


def _translated(fn):
    """Re-raise what the SDK lets through as `CredoAIError`."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except SdkAuthenticationError as exc:
            raise CredoAIError(str(exc)) from exc
        except httpx.RequestError as exc:
            raise CredoAIError(f"could not reach Credo AI: {exc}") from exc

    return wrapper


class CredoClient:
    """The operations this cookbook performs, backed by the SDK.

    Constructing this authenticates immediately: the SDK exchanges the API key
    for a token in its own constructor. That is why the CLI builds the client
    before it touches Microsoft — a missing key or an unreachable host is
    reported in a second rather than after reading the whole catalog.
    """

    @_translated
    def __init__(self, settings: Settings, client: CredoAI | None = None):
        # `client` exists so a caller can supply a pre-configured SDK instance
        # or a stub. The CLI never passes one.
        self._client = client or CredoAI(
            base_url=settings.credo_api_base_url,
            api_key=settings.credo_api_key,
            tenant=settings.credo_tenant,
        )

    def close(self) -> None:
        self._client.get_httpx_client().close()

    def __enter__(self) -> CredoClient:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    @_translated
    def list_use_cases(self) -> list:
        return list(self._client.use_cases.list_all(page_size=PAGE_SIZE))

    @_translated
    def create_use_case(self, name: str, description: str):
        return self._client.use_cases.create(
            UseCaseCreate(name=name, description=description)
        )

    @_translated
    def update_use_case(self, use_case_id: str, attrs: dict):
        return self._client.use_cases.update(use_case_id, UseCaseUpdate(**attrs))

    @_translated
    def attach_questionnaire(self, use_case_id: str, key: str, version: int):
        return self._client.use_cases(use_case_id).questionnaires.add(
            QuestionnaireAttachment(key=key, version=version)
        )

    @_translated
    def get_workflow(self, use_case_id: str):
        return self._client.use_cases(use_case_id).workflow.get()
