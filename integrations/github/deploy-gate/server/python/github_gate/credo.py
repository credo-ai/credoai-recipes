"""One Credo AI client for the gate: the SDK for use cases, the private API for webhooks.

The rest of the service only sees this class, which keeps `gate.py` and `app.py` free of SDK and
httpx imports and gives the tests a single seam to fake.
"""

from __future__ import annotations

import logging

from credoai import CredoAI, UseCaseCreate

from github_gate.config import Settings
from github_gate.credo_private import CredoPrivateClient

logger = logging.getLogger("github_gate.credo")


class GateCredoClient:
    def __init__(self, settings: Settings):
        self._sdk = CredoAI(
            base_url=settings.credoai_api_url,
            api_key=settings.credoai_api_key,
            tenant=settings.credoai_tenant,
        )
        self._private = CredoPrivateClient(settings)

    def close(self) -> None:
        self._sdk.__exit__(None, None, None)
        self._private.close()

    # -- use cases (public API, via pycredoai) -------------------------------------

    def create_use_case(self, name: str, description: str) -> str:
        result = self._sdk.use_cases.create(
            UseCaseCreate(name=name, description=description)
        )
        return result.id

    def delete_use_case(self, use_case_id: str) -> None:
        self._sdk.use_cases.delete(use_case_id=use_case_id)

    # -- webhooks (private API) ----------------------------------------------------

    def create_webhook(
        self, description: str, event_types: list[str], url: str
    ) -> dict:
        return self._private.create_webhook(description, event_types, url)

    def delete_webhook(self, webhook_id: str) -> None:
        self._private.delete_webhook(webhook_id)
