"""
Credo AI API client for the use case migration recipe.

Built on the official pycredoai SDK (https://docs.sdk.credo.ai/) for
everything it covers: auth, use case / model / vendor creation,
questionnaire lookup, and listing for dedup.

Two things pycredoai does NOT expose (confirmed by inspecting the installed
package at the version pinned in pyproject.toml — nothing in its resource
list covers them):

  - Linking a model, vendor, or risk scenario to a use case. No relationship
    resource exists on the client (the SDK does have UseCaseRiskScenarioCreate
    as a data type, just no method that uses it). This reuses the SDK's own
    authenticated httpx client (`client.get_httpx_client()`), so it doesn't
    need a separate auth flow — it's a direct call to the same public API
    the SDK already talks to.
  - Resolving a custom field's definition id by name, and resolving/creating
    a use case owner by email. Neither exists in the public API at all —
    both are private-API-only (GET /custom_fields, GET/POST /users) and need
    their own token exchange (POST /auth/exchange), which is NOT the same
    token the SDK uses for the public API.

If pycredoai adds relationship-linking or custom-field/user resources in a
future release, the two sections marked "SDK GAP" below can be deleted in
favor of it.

Verified end-to-end against a live tenant: use case creation with an owner,
model/vendor creation, all three SDK-gap linking calls (models, vendors,
risk scenarios), and dedup-by-name lookup all work as written. Two real
bugs turned up during that test and are already fixed here:

  - Passing an explicit `None` for
    questionnaire_ids/custom_fields/questions/summary/source (instead of
    omitting the key) made the SDK send JSON `null`, which the live API
    rejects with 422 ("Input should be a valid list") even though omitting
    the key entirely is fine.
  - The model/vendor link endpoints are `POST use_cases/{id}/models` with
    `{"id": model_id}` in the body, NOT `POST use_cases/{id}/models/{model_id}`,
    which 404s. Risk scenario linking uses a different shape:
    `POST use_cases/{id}/risk_scenarios` with `{"risk_scenario_id": ...}`.

STILL UNVERIFIED: `{"owner_id": ..., "owner_type": "user"}` is accepted by
the API (no more 422) but `GET /use_cases/{id}` doesn't echo an `owner`
field back — only `creator_id` — so this only confirms the shape is valid,
not that it visibly sets a distinct owner in the platform UI. Spot-check
in the UI (with an owner different from the API caller) before relying on
this for a real migration.
"""

import logging
import os

import requests
from credoai import (
    CredoAI,
    ModelCreate,
    QuestionEvidenceCreate,
    UseCaseCreate,
    UseCaseCustomField,
    VendorCreate,
)
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

TENANT = os.getenv("CREDOAI_TENANT")
API_KEY = os.getenv("CREDOAI_API_KEY")
API_URL = os.getenv("CREDOAI_API_URL", "https://api.credo.ai")

_sdk_client = None
_private_token = None
_custom_field_cache: dict[str, str] = {}  # name.lower() -> id
_questionnaire_cache: dict[
    str, object
] = {}  # questionnaire_key -> QuestionnaireResponse
_risk_scenario_cache: dict[str, str] = {}  # name.lower() -> id


def _require_config():
    missing = [
        n
        for n, v in (("CREDOAI_TENANT", TENANT), ("CREDOAI_API_KEY", API_KEY))
        if not v
    ]
    if missing:
        raise SystemExit(
            f"Missing required env value(s): {', '.join(missing)}. "
            "Copy example.env to .local.env at the repo root and fill them in."
        )


def sdk():
    """The pycredoai client. Constructed lazily so --dry-run never needs credentials."""
    global _sdk_client
    if _sdk_client is None:
        _require_config()
        _sdk_client = CredoAI(api_key=API_KEY, tenant=TENANT, base_url=API_URL)
    return _sdk_client


# ── SDK GAP: private-API auth (custom fields, owner lookup only) ─────────
#
# This is a second, separate token from the one pycredoai obtains for the
# public API — the two are not interchangeable.


def _private_headers():
    global _private_token
    if _private_token is None:
        _require_config()
        r = requests.post(
            f"{API_URL}/auth/exchange",
            json={"api_token": API_KEY, "tenant": TENANT},
            timeout=30,
        )
        r.raise_for_status()
        _private_token = r.json()["access_token"]
    return {
        "Content-Type": "application/vnd.api+json",
        "Accept": "application/vnd.api+json",
        "Authorization": f"Bearer {_private_token}",
    }


def _private_get(endpoint, params=None):
    r = requests.get(
        f"{API_URL}/api/v2/{TENANT}/{endpoint}",
        headers=_private_headers(),
        params=params,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def _private_post(endpoint, payload):
    r = requests.post(
        f"{API_URL}/api/v2/{TENANT}/{endpoint}",
        headers=_private_headers(),
        json=payload,
        timeout=30,
    )
    if not r.ok:
        logger.error(
            "PRIVATE POST %s failed: HTTP %s: %s", endpoint, r.status_code, r.text[:500]
        )
    r.raise_for_status()
    return r.json()


def resolve_custom_field_id(field_name):
    """SDK GAP — no custom-field resource in pycredoai; private API is the only way to list definitions."""
    key = field_name.strip().lower()
    if key in _custom_field_cache:
        return _custom_field_cache[key]
    response = _private_get("custom_fields")
    for field in response.get("data", []):
        name = (field.get("attributes", {}).get("name") or "").strip()
        _custom_field_cache[name.lower()] = field["id"]
    if key not in _custom_field_cache:
        logger.warning(
            "Custom field %r not found in this tenant — check mapping.py / Credo AI admin.",
            field_name,
        )
        return None
    return _custom_field_cache[key]


def resolve_owner_id(email):
    """SDK GAP — no user resource in pycredoai; get-or-create a user by email via the private API."""
    if not email:
        return None
    response = _private_get("users", params={"filter[email]": email})
    results = response.get("data", [])
    if results:
        return results[0]["id"]

    first_name, last_name = email.split("@")[0], ""
    payload = {
        "data": {
            "type": "users",
            "attributes": {
                "email": email,
                "given_name": first_name,
                "family_name": last_name,
                "role": "user",
            },
        }
    }
    created = _private_post("users", payload)
    return created.get("data", {}).get("id")


# ── Use cases ──────────────────────────────────────────────────────────────


def create_use_case(
    name,
    description,
    questionnaire_key=None,
    owner_email=None,
    custom_fields=None,
    questions=None,
):
    """
    custom_fields: list of {"custom_field_id": ..., "value": ...}
    questions:     list of {"question_id": ..., "questionnaire_id": ..., "section_id": ..., "value": ...}
    """
    # Fields are only set on UseCaseCreate when there's a real value — passing
    # an explicit None (even though it's the field's own default) makes the
    # SDK serialize it as JSON `null`, which the live API's validator rejects
    # for questionnaire_ids/custom_fields/questions ("Input should be a valid
    # list") even though omitting the key entirely is fine. Confirmed against
    # a real tenant, not a guess — this bit the very first version of this
    # function.
    kwargs = {"name": name, "description": description or ""}
    if questionnaire_key:
        kwargs["questionnaire_ids"] = [questionnaire_key]
    if custom_fields:
        kwargs["custom_fields"] = [UseCaseCustomField(**f) for f in custom_fields]
    if questions:
        kwargs["questions"] = [QuestionEvidenceCreate(**q) for q in questions]
    if owner_email:
        owner_id = resolve_owner_id(owner_email)
        if owner_id:
            # Shape confirmed against a real tenant (see module docstring).
            kwargs["owner"] = {"owner_id": owner_id, "owner_type": "user"}
        else:
            logger.warning(
                "Could not resolve/create owner %r — creating use case without an owner.",
                owner_email,
            )

    data = UseCaseCreate(**kwargs)
    result = sdk().use_cases.create(data=data)
    return result.id


def get_use_case_id_by_name(name):
    """Dedup lookup for re-runs. list_all() paginates internally."""
    for use_case in sdk().use_cases.list_all():
        if use_case.name == name:
            return use_case.id
    return None


# ── Questionnaire question id resolution ──────────────────────────────────


def get_questionnaire(questionnaire_key):
    if questionnaire_key in _questionnaire_cache:
        return _questionnaire_cache[questionnaire_key]
    data = sdk().questionnaires.get(questionnaire_key)
    _questionnaire_cache[questionnaire_key] = data
    return data


def find_question(questionnaire_data, question_text):
    """Returns (question_id, section_id) for an exact question-text match, or (None, None)."""
    for section in questionnaire_data.sections:
        for question in section.questions:
            if (question.question or "").strip() == question_text.strip():
                return question.id, section.id
    return None, None


# ── Models ─────────────────────────────────────────────────────────────────


def create_model(name, summary=None, source=None, status="none"):
    # Same explicit-None-vs-omitted issue as create_use_case above.
    kwargs = {"name": name, "status": status}
    if summary:
        kwargs["summary"] = summary
    if source:
        kwargs["source"] = source
    data = ModelCreate(**kwargs)
    result = sdk().models.create(data=data)
    return result.id


def link_model_to_use_case(use_case_id, model_id):
    """SDK GAP — reuses the SDK's own authenticated httpx client, no separate auth needed."""
    _link(f"use_cases/{use_case_id}/models", {"id": model_id})


# ── Vendors ────────────────────────────────────────────────────────────────


def create_vendor(name):
    data = VendorCreate(name=name)
    result = sdk().vendors.create(data=data)
    return result.id


def link_vendor_to_use_case(use_case_id, vendor_id):
    """SDK GAP — reuses the SDK's own authenticated httpx client, no separate auth needed."""
    _link(f"use_cases/{use_case_id}/vendors", {"id": vendor_id})


# ── Risk scenarios ────────────────────────────────────────────────────────
# The SDK has UseCaseRiskScenarioCreate/Response data types but no resource
# method that uses them — same SDK-gap pattern as models/vendors above.
# risk_scenario_id must already exist in Credo AI's risk scenario library;
# use sdk().risk_scenarios.list_all() to look one up. Confirmed live: POST
# use_cases/{id}/risk_scenarios with {"risk_scenario_id": ...} returns 201.


def link_risk_scenario_to_use_case(use_case_id, risk_scenario_id):
    """SDK GAP — reuses the SDK's own authenticated httpx client, no separate auth needed."""
    _link(
        f"use_cases/{use_case_id}/risk_scenarios",
        {"risk_scenario_id": risk_scenario_id},
    )


def resolve_risk_scenario_id(scenario_name):
    """Looks up a library risk scenario's id by exact name (case-insensitive). Public API, cached."""
    key = scenario_name.strip().lower()
    if not _risk_scenario_cache:
        for scenario in sdk().risk_scenarios.list_all():
            _risk_scenario_cache[scenario.name.strip().lower()] = scenario.id
    if key not in _risk_scenario_cache:
        logger.warning(
            "Risk scenario %r not found in the risk scenario library.", scenario_name
        )
        return None
    return _risk_scenario_cache[key]


def _link(path, payload):
    r = sdk().get_httpx_client().post(path, json=payload)
    if r.status_code >= 400:
        logger.error("POST %s failed: HTTP %s: %s", path, r.status_code, r.text[:500])
    r.raise_for_status()
