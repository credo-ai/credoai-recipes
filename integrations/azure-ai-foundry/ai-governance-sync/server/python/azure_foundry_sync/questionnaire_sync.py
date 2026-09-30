"""Create (or merge) the intake questionnaire from config/azure_questionnaire.json.

`CREDO_QUESTIONNAIRE_OPTIONS`:
  "1" — create a fresh `DEFAULT_AZURE` questionnaire from the Azure template alone.
  "2" — fetch the tenant's existing questionnaire (CREDO_QUESTIONNAIRE_ID + _VERSION) and
        combine its sections with the Azure template, so a tenant that already runs its own
        intake questionnaire doesn't lose it.
"""

from __future__ import annotations

import json
import logging

from azure_foundry_sync.config import Settings, config_path
from azure_foundry_sync.credo_v2 import CredoApiError, CredoClientV2
from azure_foundry_sync.summary import SyncCounts

logger = logging.getLogger("azure_foundry_sync.questionnaire_sync")

DEFAULT_QUESTIONNAIRE_ID = "DEFAULT_AZURE"


def _load_azure_template() -> dict:
    return json.loads(config_path("azure_questionnaire.json").read_text())


def _construct_questionnaire(existing_questionnaire: dict | None) -> dict:
    azure_questionnaire = _load_azure_template()
    if not existing_questionnaire:
        return azure_questionnaire

    existing_sections = (
        existing_questionnaire.get("data", {}).get("attributes", {}).get("sections", [])
    )
    combined_sections = azure_questionnaire["data"]["attributes"]["sections"]

    for section in existing_sections:
        new_section = {
            "description": section.get("description"),
            "title": section.get("title"),
            "questions": [
                {
                    "question": q.get("question"),
                    "evidence_type": q.get("evidence_type"),
                    "multiple": q.get("multiple"),
                    **(
                        {"select_options": q["select_options"]}
                        if q.get("select_options")
                        else {}
                    ),
                }
                for q in section.get("questions", [])
            ],
        }
        combined_sections.append(new_section)

    azure_questionnaire["data"]["attributes"]["sections"] = combined_sections
    return azure_questionnaire


def sync_questionnaire(
    credo: CredoClientV2, settings: Settings, *, dry_run: bool
) -> SyncCounts:
    counts = SyncCounts(label="questionnaire")
    counts.scanned = 1

    options = settings.credo_questionnaire_options
    if options not in ("1", "2"):
        logger.info(
            "CREDO_QUESTIONNAIRE_OPTIONS=%r — not 1 or 2, skipping questionnaire sync",
            options,
        )
        counts.skipped = 1
        return counts

    if options == "1":
        questionnaire_id = DEFAULT_QUESTIONNAIRE_ID
        existing = None
    else:
        if (
            not settings.credo_questionnaire_id
            or not settings.credo_questionnaire_version
        ):
            logger.error(
                "CREDO_QUESTIONNAIRE_OPTIONS=2 requires CREDO_QUESTIONNAIRE_ID and "
                "CREDO_QUESTIONNAIRE_VERSION"
            )
            counts.errors = 1
            return counts
        questionnaire_id = (
            f"{settings.credo_questionnaire_id} with {DEFAULT_QUESTIONNAIRE_ID}"
        )
        existing = None

    if dry_run:
        logger.info("[dry-run] would create/merge questionnaire %r", questionnaire_id)
        counts.created = 1
        return counts

    try:
        if options == "2":
            resp = credo.get_questionnaire(
                settings.credo_questionnaire_id, settings.credo_questionnaire_version
            )
            if resp.status_code in (200, 201):
                existing = resp.json()
            else:
                raise CredoApiError(
                    f"get_questionnaire failed: {resp.status_code}",
                    resp.status_code,
                    resp.text,
                )

        base_resp = credo.create_questionnaire_base(
            questionnaire_id, "Default Azure Intake Questionnaire"
        )
        if base_resp.status_code in (200, 201):
            base_id = base_resp.json()["data"]["id"]
        elif base_resp.status_code == 422:
            base_id = questionnaire_id
        else:
            raise CredoApiError(
                f"create_questionnaire_base failed: {base_resp.status_code}",
                base_resp.status_code,
                base_resp.text,
            )

        payload = _construct_questionnaire(existing)
        version_resp = credo.create_questionnaire_version(base_id, payload)
        if version_resp.status_code in (200, 201):
            counts.created = 1
        elif version_resp.status_code == 422:
            counts.skipped = 1
        else:
            raise CredoApiError(
                f"create_questionnaire_version failed: {version_resp.status_code}",
                version_resp.status_code,
                version_resp.text,
            )
    except CredoApiError as exc:
        logger.error("Failed to sync questionnaire %r: %s", questionnaire_id, exc)
        counts.errors = 1

    return counts
