"""Credo AI access, through the official `pycredoai` SDK.

This is the only module that imports the SDK. It exposes the two reads the
cookbook needs and turns the SDK's failure modes into two outcomes the rest of
the code can act on:

- "this model doesn't exist" (`None`): the event is stale or forged, so
  nothing is sent;
- `CredoUnavailable`: Credo AI couldn't be asked right now (network, auth,
  5xx), so the delivery is retried later. Nothing is sent without a successful
  read; the server fails closed.

The synchronous `CredoAI` client is used on purpose. Calls run on the worker
thread, never on the web server's event loop.

The sync client exchanges the API key for a token once, in its constructor, and
doesn't renew it. The token lasts 24 hours, so this module builds a new client
every 12 hours, and after any failed call.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable, Iterator

import httpx
from credoai import CredoAI, ModelResponse
from credoai.auth import AuthenticationError
from credoai.errors import ApiError, CredoAIError

from .config import Settings

log = logging.getLogger("notifier.credo")

# A malformed id comes back as 400/422 rather than 404. For our purposes that is
# the same answer: there is no such model.
_NOT_FOUND = {400, 404, 422}

# Credo AI's access tokens last 24 hours; renew at half that.
CLIENT_MAX_AGE_SECONDS = 12 * 3600


class CredoUnavailable(Exception):
    """Credo AI couldn't answer; try again later."""


class Credo:
    def __init__(
        self,
        settings: Settings,
        client_factory: Callable[[], CredoAI] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory or self._new_client
        self._clock = clock
        self._client_instance: CredoAI | None = None
        self._built_at = 0.0

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

    def _unavailable(self, exc: Exception) -> CredoUnavailable:
        # Rebuild on the next call. That fixes a token that was revoked or
        # expired early (401); for any other failure it costs one extra request.
        self._discard_client()
        return CredoUnavailable(str(exc))

    def get_model(self, model_id: str) -> ModelResponse | None:
        try:
            return self._client.models.get(model_id=model_id)
        except ApiError as exc:
            if exc.status_code in _NOT_FOUND:
                return None
            raise self._unavailable(exc) from exc
        except (AuthenticationError, CredoAIError, httpx.HTTPError) as exc:
            raise self._unavailable(exc) from exc

    def model_ids(self) -> Iterator[str]:
        try:
            for model in self._client.models.list_all(page_size=100):
                yield model.id
        except (AuthenticationError, CredoAIError, httpx.HTTPError) as exc:
            raise self._unavailable(exc) from exc
