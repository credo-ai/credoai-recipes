"""Create (or merge) the intake questionnaire from config/azure_questionnaire.json.

`CREDO_QUESTIONNAIRE_OPTIONS`:
  "1" — create a fresh `DEFAULT_AZURE` questionnaire from the Azure template alone.
  "2" — fetch the tenant's existing questionnaire (CREDO_QUESTIONNAIRE_ID + _VERSION) and
        combine its sections with the Azure template, so a tenant that already runs its own
        intake questionnaire doesn't lose it.

Content-based dedup, same idea as controls: the latest published version's sections are read
back (`get_spec`) and compared against the desired sections. Identical -> skip, no write. Different
-> publish a new version. Missing -> create. Fields the server adds (ids, hidden/required defaults,
nulls) or never returns are left out of the comparison, and list order counts, since section and
question order is part of what the questionnaire looks like.
"""

from __future__ import annotations

import json
import logging

from credoai import CredoAI
from credoai.errors import ApiError
from credoai.models import (
    QuestionnairePublishWithContent,
    QuestionnaireSpec,
    QuestionnaireWithContentCreate,
)

from azure_foundry_sync.config import Settings, config_path
from azure_foundry_sync.content_compare import normalize, project, restrict
from azure_foundry_sync.summary import SyncCounts

logger = logging.getLogger("azure_foundry_sync.questionnaire_sync")

DEFAULT_QUESTIONNAIRE_ID = "DEFAULT_AZURE"
QUESTIONNAIRE_NAME = "Default Azure Intake Questionnaire"
# Matches config/custom_fields.json's target for these same Azure fields.
QUESTIONNAIRE_TARGET = "use_case"


def _load_template_sections() -> list[dict]:
    raw = json.loads(config_path("azure_questionnaire.json").read_text())
    return raw["data"]["attributes"]["sections"]


def _section_from_response(section) -> dict:
    return {
        "title": section.title,
        "description": section.description,
        "questions": [
            {
                "question": q.question,
                "evidence_type": q.evidence_type,
                "multiple": q.multiple,
                **({"select_options": q.select_options} if q.select_options else {}),
            }
            for q in (section.questions or [])
        ],
    }


def _combined_sections(existing_sections: list[dict] | None) -> list[dict]:
    template_sections = _load_template_sections()
    if not existing_sections:
        return template_sections
    return template_sections + existing_sections


def _latest_sections(credo: CredoAI, questionnaire_id: str) -> list[dict] | None:
    """Sections of the latest version, or None when the questionnaire doesn't exist yet."""
    try:
        spec = credo.questionnaires.get_spec(questionnaire_id)
    except ApiError as exc:
        if exc.status_code == 404:
            return None
        raise
    return [section.model_dump() for section in (spec.sections or [])]


def _sections_match(
    existing: list[dict], desired: list[dict], unreturned: set[str]
) -> bool:
    wanted = restrict(desired, existing, unreturned, "sections")
    return normalize(
        project(existing, wanted, ordered=True), ordered=True
    ) == normalize(wanted, ordered=True)


def _write_questionnaire(
    credo: CredoAI, questionnaire_id: str, sections: list[dict], *, exists: bool
) -> bool:
    """Returns True if a new questionnaire was created, False if a version was added to an
    existing one. A 422 on create means it already exists (e.g. created between our read and
    this write), so fall back to publishing a version onto it."""
    spec = QuestionnaireSpec(
        key=questionnaire_id,
        name=QUESTIONNAIRE_NAME,
        target=QUESTIONNAIRE_TARGET,
        sections=sections,
    )
    if not exists:
        try:
            credo.questionnaires.create_with_content(
                QuestionnaireWithContentCreate(spec=spec, publish=True)
            )
            return True
        except ApiError as exc:
            if exc.status_code != 422:
                raise

    # Same spec as the create call: the API validates the base attributes (key/name/target)
    # against the existing base and only takes the new version's content from `sections`.
    credo.questionnaires.publish_with_content(
        questionnaire_id, QuestionnairePublishWithContent(spec=spec)
    )
    return False


def sync_questionnaire(
    credo: CredoAI, settings: Settings, *, dry_run: bool
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

    try:
        existing_sections = None
        if options == "2":
            version = credo.questionnaires.get_version(
                settings.credo_questionnaire_id,
                int(settings.credo_questionnaire_version),
                expand_sections=True,
            )
            existing_sections = [
                _section_from_response(s) for s in (version.sections or [])
            ]

        sections = _combined_sections(existing_sections)
        latest = _latest_sections(credo, questionnaire_id)

        unreturned: set[str] = set()
        if latest is not None and _sections_match(latest, sections, unreturned):
            logger.info("questionnaire %r unchanged, skipping", questionnaire_id)
            counts.skipped = 1
            return counts
        if unreturned:
            logger.info(
                "Server does not return these questionnaire fields, so they are excluded from "
                "the change comparison: %s",
                ", ".join(sorted(unreturned)),
            )

        if dry_run:
            if latest is None:
                logger.info("[dry-run] would create questionnaire %r", questionnaire_id)
                counts.created = 1
            else:
                logger.info(
                    "[dry-run] would publish a new version of questionnaire %r",
                    questionnaire_id,
                )
                counts.updated = 1
            return counts

        created = _write_questionnaire(
            credo, questionnaire_id, sections, exists=latest is not None
        )
        if created:
            counts.created = 1
        else:
            counts.updated = 1
    except ApiError as exc:
        logger.error("Failed to sync questionnaire %r: %s", questionnaire_id, exc)
        counts.errors = 1

    return counts
