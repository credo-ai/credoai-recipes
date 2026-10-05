"""Credo AI access, through the official SDK.

Everything this cookbook writes goes through `pycredoai` — the SDK handles the
token exchange, pagination and response typing, so none of that is
reimplemented here.

This module is a thin adapter: it exposes exactly the handful of operations
`sync.py` needs (see the `Protocol` classes there) and nothing else. The
orchestration therefore never imports the SDK directly, which keeps it free of
any HTTP concern and easy to follow.

It is also the one place that knows the SDK is built on httpx. Two SDK failure
modes escape its own exception hierarchy — a failed token exchange raises
`credoai.auth.AuthenticationError`, and a transport failure raises
`httpx.RequestError`, neither of which is a `CredoAIError`. Both are
re-raised as `CredoAIError` here so the CLI can catch one type and report a
"could not start" exit, and so no httpx import leaks into the rest of the
cookbook.

One operation is deliberately *not* here: creating the `Source` record. The SDK
has no `sources` resource because the Integration Service has no `/sources`
endpoint, so that one-time setup step lives in `ensure_credo_source.py` and
talks to the admin API instead. See README "Step 3".
"""

from __future__ import annotations

import logging
from functools import wraps

import httpx
from credoai import (
    CredoAI,
    ModelCreate,
    ModelUpdate,
    UseCaseCreate,
    UseCaseUpdate,
    VendorCreate,
)
from credoai.auth import AuthenticationError as SdkAuthenticationError
from credoai.errors import ApiError, CredoAIError

# Imported directly: `CredoAI` exposes no `.model()` / `.use_case()` /
# `.vendor()` accessor, despite the SDK's own docstrings showing one.
from credoai.fluent import Model as _Model
from credoai.fluent import UseCase as _UseCase
from credoai.fluent import Vendor as _Vendor

from bedrock_sync.config import Settings

logger = logging.getLogger("bedrock_sync.credo")

# One page per round trip when walking a listing. The SDK's `list_all` paginates
# for us; this only sets how many records each request carries.
PAGE_SIZE = 100


def source_hint(exc: ApiError) -> str:
    """Name the likeliest cause when a create is rejected over `source`.

    `source` is a reference to a Source record, not free text, so a tenant
    without one rejects every create — and the raw 422 says only that a field
    was invalid, which sends people looking in the wrong place.
    """
    if getattr(exc, "status_code", None) != 422 or "source" not in str(exc).lower():
        return ""
    return (
        " — this usually means the Source record is missing from the tenant. "
        "Run ensure_credo_source.py once (see README Step 3)."
    )


def _translated(fn):
    """Re-raise what the SDK lets through as `CredoAIError`.

    `AuthenticationError` and `httpx.RequestError` are both plain exceptions,
    so without this they reach the CLI's catch-all and are reported as an
    unexpected error with exit 1 — when they are in fact the two most ordinary
    setup problems there are, and the README documents them as exit 2.
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except SdkAuthenticationError as exc:
            raise CredoAIError(str(exc)) from exc
        except httpx.RequestError as exc:
            raise CredoAIError(f"could not reach Credo AI: {exc}") from exc
        except ApiError as exc:
            # Already a CredoAIError; re-raised only to attach the hint, which
            # the raw 422 does not carry.
            hint = source_hint(exc)
            if not hint:
                raise
            raise CredoAIError(f"{exc}{hint}") from exc

    return wrapper


class CredoClient:
    """The operations this cookbook performs, backed by the SDK.

    Constructing this authenticates immediately: the SDK exchanges the API key
    for a token in its own constructor. That is why the CLI builds the client
    before it touches AWS — a missing key or an unreachable host is reported in
    a second rather than after a full Bedrock enumeration.
    """

    @_translated
    def __init__(self, settings: Settings, client: CredoAI | None = None):
        # `client` exists so a caller can supply its own — a pre-configured SDK
        # instance or a stub in a test. The CLI never passes one.
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

    # -- models ----------------------------------------------------------------

    @_translated
    def list_models(self) -> list:
        return list(self._client.models.list_all(page_size=PAGE_SIZE))

    @_translated
    def create_model(self, attrs: dict):
        return self._client.models.create(ModelCreate(**attrs))

    @_translated
    def patch_model(self, model_id: str, attrs: dict):
        return self._client.models.update(model_id, ModelUpdate(**attrs))

    # -- use cases -------------------------------------------------------------

    @_translated
    def list_use_cases(self) -> list:
        return list(self._client.use_cases.list_all(page_size=PAGE_SIZE))

    @_translated
    def create_use_case(self, attrs: dict):
        return self._client.use_cases.create(UseCaseCreate(**attrs))

    @_translated
    def patch_use_case(self, use_case_id: str, attrs: dict):
        return self._client.use_cases.update(use_case_id, UseCaseUpdate(**attrs))

    # -- vendors ---------------------------------------------------------------

    @_translated
    def list_vendors(self) -> list:
        return list(self._client.vendors.list_all(page_size=PAGE_SIZE))

    @_translated
    def create_vendor(self, name: str):
        # `VendorCreate` requires only a name; description, questionnaires and
        # custom fields are optional and deliberately left unset.
        return self._client.vendors.create(VendorCreate(name=name))

    @_translated
    def list_vendor_models(self, vendor_id: str) -> list:
        # Read links per vendor, not per model: a region has a handful of
        # providers and can have hundreds of models, and this covers the same
        # ground either way.
        #
        # One call returns the whole set: the Integration Service reads every
        # backend page for this endpoint and answers with `has_more: false` and
        # no cursor, whatever page size is asked for. The relationship
        # accessors have no `list_all` to walk, so if that contract ever
        # changes this is the line that has to learn to paginate.
        return list(_Vendor(self._client, vendor_id).models.list().items)

    @_translated
    def add_model_vendor(self, model_id: str, vendor_id: str):
        return _Model(self._client, model_id).vendors.add(vendor_id)

    # -- use case <-> model relationship ---------------------------------------

    @_translated
    def list_use_case_models(self, use_case_id: str) -> list:
        # Whole set in one call, as in `list_vendor_models` above.
        return list(_UseCase(self._client, use_case_id).models.list().items)

    @_translated
    def add_use_case_model(self, use_case_id: str, model_id: str):
        return _UseCase(self._client, use_case_id).models.add(model_id)
