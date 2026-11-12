"""
Credo AI API client for the use case bulk import recipe.

Uses the official pycredoai SDK (https://docs.sdk.credo.ai/) and only the
public Credo AI API, with a single API key.

Two operations aren't wrapped by pycredoai yet, so they call the public
endpoints through the SDK's own authenticated client
(`client.get_httpx_client()`):

  - Linking a model, vendor, or risk scenario to a use case:
    `POST use_cases/{id}/models` and `POST use_cases/{id}/vendors` with
    `{"id": ...}`, and `POST use_cases/{id}/risk_scenarios` with
    `{"risk_scenario_id": ...}`.
  - Looking up a custom field's id by name:
    `GET custom_fields?entity_type=use_case`.

If a later pycredoai release adds these, the functions marked "Not wrapped
by pycredoai" can switch to it.

Use case owners need no separate lookup: `UseCaseCreate.user` takes the
owner's email, and the API matches it to a user (creating one if needed).
The owner isn't returned when a use case is read back, so check it in the
Credo AI UI.
"""

import logging
import os

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
_custom_field_cache: dict[str, str] = {}  # name.lower() -> id
_questionnaire_cache: dict[
    str, object
] = {}  # questionnaire_key -> QuestionnaireResponse
_risk_scenario_cache: dict[str, str] = {}  # name.lower() -> id
# name -> id indexes for dedup, each filled by one list_all() on first use.
# None means "not loaded yet"; {} means "loaded, tenant has none".
_use_case_index: dict[str, str] | None = None
_model_index: dict[str, str] | None = None
_vendor_index: dict[str, str] | None = None


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


def resolve_custom_field_id(field_name):
    """Custom field id for a name (case-insensitive). Not wrapped by pycredoai: calls GET custom_fields."""
    key = field_name.strip().lower()
    if key in _custom_field_cache:
        return _custom_field_cache[key]
    if not _custom_field_cache:
        # The endpoint returns every use case custom field in one response,
        # so one call fills the cache.
        r = (
            sdk()
            .get_httpx_client()
            .get("custom_fields", params={"entity_type": "use_case"})
        )
        r.raise_for_status()
        for field in r.json().get("items", []):
            _custom_field_cache[(field.get("name") or "").strip().lower()] = field["id"]
    if key not in _custom_field_cache:
        logger.warning(
            "Custom field %r not found in this tenant — check mapping.py / Credo AI admin.",
            field_name,
        )
        return None
    return _custom_field_cache[key]


# ── Use cases ──────────────────────────────────────────────────────────────


def create_use_case(
    name,
    description,
    questionnaire_keys=None,
    owner_email=None,
    custom_fields=None,
    questions=None,
):
    """
    questionnaire_keys: questionnaire keys with versions, e.g. ["INTK1+1", "RA1+1"].
                        A use case can be attached to more than one questionnaire.
    custom_fields:      list of {"custom_field_id": ..., "value": ...}
    questions:          list of {"question_id": ..., "questionnaire_id": ..., "section_id": ..., "value": ...}
    """
    # Only set fields that have a value. An explicit None is sent as JSON
    # null, which the API rejects for list fields ("Input should be a valid
    # list"); leaving the field out is fine.
    kwargs = {"name": name, "description": description or ""}
    if questionnaire_keys:
        kwargs["questionnaire_ids"] = list(questionnaire_keys)
    if custom_fields:
        kwargs["custom_fields"] = [UseCaseCustomField(**f) for f in custom_fields]
    if questions:
        kwargs["questions"] = [QuestionEvidenceCreate(**q) for q in questions]
    if owner_email:
        # The API matches the email to a user (creating one if needed) and
        # sets them as owner.
        kwargs["user"] = owner_email

    data = UseCaseCreate(**kwargs)
    result = sdk().use_cases.create(data=data)
    return result.id


def get_use_case_id_by_name(name):
    """Id of an existing use case with this exact name, or None. Lists use cases once per run."""
    global _use_case_index
    if _use_case_index is None:
        _use_case_index = {uc.name: uc.id for uc in sdk().use_cases.list_all()}
    return _use_case_index.get(name)


def remember_use_case(name, use_case_id):
    """Record a use case created this run so a later row with the same name reuses it."""
    if _use_case_index is not None:
        _use_case_index[name] = use_case_id


# ── Questionnaire question id resolution ──────────────────────────────────


def get_questionnaire(questionnaire_key):
    if questionnaire_key in _questionnaire_cache:
        return _questionnaire_cache[questionnaire_key]
    data = sdk().questionnaires.get(questionnaire_key)
    _questionnaire_cache[questionnaire_key] = data
    return data


def find_question(questionnaire_data, question_text):
    """
    Returns (question_id, section_id, multiple) for the question whose text
    matches exactly, or ignoring case if there's no exact match. Returns
    (None, None, None) if nothing matches. `multiple` is the question's own
    flag for whether it takes a list of values (see resolve_answer_value in
    migrate.py).
    """
    wanted = question_text.strip()
    case_insensitive_match = None
    for section in questionnaire_data.sections:
        for question in section.questions:
            text = (question.question or "").strip()
            if text == wanted:
                return question.id, section.id, bool(question.multiple)
            if case_insensitive_match is None and text.lower() == wanted.lower():
                case_insensitive_match = (
                    question.id,
                    section.id,
                    bool(question.multiple),
                )
    return case_insensitive_match or (None, None, None)


def find_question_across(questionnaires_by_key, question_text):
    """
    Same as find_question, but searches multiple questionnaires (in the given
    order) and returns the first match — for use cases whose answers span
    more than one questionnaire (e.g. a separate Intake + Risk Assessment).
    Returns (question_id, section_id, multiple, questionnaire_key) or
    (None, None, None, None).
    """
    for key, data in questionnaires_by_key.items():
        question_id, section_id, multiple = find_question(data, question_text)
        if question_id:
            return question_id, section_id, multiple, key
    return None, None, None, None


# ── Models ─────────────────────────────────────────────────────────────────


def create_model(name, summary=None, source=None, status="none"):
    # Only set fields that have a value (see create_use_case).
    kwargs = {"name": name, "status": status}
    if summary:
        kwargs["summary"] = summary
    if source:
        kwargs["source"] = source
    data = ModelCreate(**kwargs)
    result = sdk().models.create(data=data)
    return result.id


def get_model_id_by_name(name):
    """Id of an existing model with this exact name, or None. Lists models once per run."""
    global _model_index
    if _model_index is None:
        _model_index = {m.name: m.id for m in sdk().models.list_all()}
    return _model_index.get(name)


def remember_model(name, model_id):
    if _model_index is not None:
        _model_index[name] = model_id


def link_model_to_use_case(use_case_id, model_id):
    """Not wrapped by pycredoai: calls POST use_cases/{id}/models."""
    return _link(f"use_cases/{use_case_id}/models", {"id": model_id})


# ── Vendors ────────────────────────────────────────────────────────────────


def create_vendor(name):
    data = VendorCreate(name=name)
    result = sdk().vendors.create(data=data)
    return result.id


def get_vendor_id_by_name(name):
    """Id of an existing vendor with this exact name, or None. Lists vendors once per run."""
    global _vendor_index
    if _vendor_index is None:
        _vendor_index = {v.name: v.id for v in sdk().vendors.list_all()}
    return _vendor_index.get(name)


def remember_vendor(name, vendor_id):
    if _vendor_index is not None:
        _vendor_index[name] = vendor_id


def link_vendor_to_use_case(use_case_id, vendor_id):
    """Not wrapped by pycredoai: calls POST use_cases/{id}/vendors."""
    return _link(f"use_cases/{use_case_id}/vendors", {"id": vendor_id})


# ── Risk scenarios ────────────────────────────────────────────────────────
# Risk scenarios are linked from the existing risk scenario library, never
# created here.


def link_risk_scenario_to_use_case(use_case_id, risk_scenario_id):
    """Not wrapped by pycredoai: calls POST use_cases/{id}/risk_scenarios."""
    return _link(
        f"use_cases/{use_case_id}/risk_scenarios",
        {"risk_scenario_id": risk_scenario_id},
    )


def resolve_risk_scenario_id(scenario_name):
    """Id of a library risk scenario by name (case-insensitive), or None. Lists scenarios once per run."""
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


# Linking something that's already linked returns a 422 with one of these
# messages. A re-run counts that as "already linked", not a failure.
_ALREADY_LINKED_MESSAGES = ("already associated", "already been taken")


def _link(path, payload):
    """Returns True if the link was created, False if it already existed. Raises on any other error."""
    r = sdk().get_httpx_client().post(path, json=payload)
    if r.status_code < 400:
        return True
    try:
        message = r.json().get("error", {}).get("message") or r.text[:500]
    except ValueError:
        message = r.text[:500]
    if r.status_code == 422 and any(m in message for m in _ALREADY_LINKED_MESSAGES):
        return False
    raise RuntimeError(f"POST {path} failed: HTTP {r.status_code}: {message}")
