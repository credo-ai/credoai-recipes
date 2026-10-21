"""
Use case migration orchestrator.

Supports two spreadsheet shapes:

  - Single-sheet intake (an exported intake form): one sheet, one row per
    use case. A handful of BASE_COLUMNS carry identity/metadata
    (use_case_name/use_case, description, ...); every other column is a
    question, answered inline on that same row. No separate
    Questionnaire Evidence / Custom Fields / Models / Vendors / Risk
    Scenarios tabs needed — each is simply skipped if the workbook doesn't
    have it.
  - Multi-tab template (use_case_migration_template.xlsx): a "Use Cases" tab
    plus optional Questionnaire Evidence / Custom Fields / Models / Vendors /
    Risk Scenarios tabs, each wide-format and keyed by use_case_name. Risk
    Scenarios only links use cases to existing library scenarios (by name,
    resolved via credo_client.resolve_risk_scenario_id) — nothing new is
    created there.

Question column headers are matched against the live questionnaire by exact
text. A header like "1.1 Who is the business owner of this use case?" has
its leading "1.1 " numbering stripped before matching, since that numbering
is a spreadsheet artifact and not part of the live question text. Add an
entry to mapping.py's QUESTION_TEXT_MAP only when a header doesn't already
equal the live question text after that stripping (typos, reworded
questions, etc).

Usage:
    uv run python migrate.py --dry-run                          # preview, no API calls
    uv run python migrate.py --questionnaire-key INTK1+1        # real run
    uv run python migrate.py --source my-intake.xlsx \\
                             --sheet use-cases-intake-v2 \\
                             --questionnaire-key INTK2+1
"""

import argparse
import json
import logging
import re

import credo_client
import openpyxl
from mapping import CUSTOM_FIELD_NAME_MAP, QUESTION_TEXT_MAP

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

XLSX_PATH = "use_case_migration_template.xlsx"

# Columns on the use-case sheet that are identity/metadata, never questions.
# "" catches blank trailing headers openpyxl sometimes reports for empty columns.
# cells_to_confirm/cells_needing_answer/codes_needing_answer are review-tool
# bookkeeping columns seen on intake exports — not data to send to Credo AI.
BASE_COLUMNS = {
    "use_case_name",
    "use_case",
    "use_case_id",
    "use_case_number",
    "description",
    "owner_email",
    "questionnaire_key",
    "cells_to_confirm",
    "cells_needing_answer",
    "codes_needing_answer",
    "",
}

NUMBER_PREFIX_RE = re.compile(r"^\d+(\.\d+)*[.)]?\s+")


def sheet_exists(path, sheet_name):
    return sheet_name in openpyxl.load_workbook(path, read_only=True).sheetnames


def pick_use_case_sheet(path, override=None):
    """Return the sheet name holding one-row-per-use-case data."""
    sheetnames = openpyxl.load_workbook(path, read_only=True).sheetnames
    if override:
        if override not in sheetnames:
            raise SystemExit(
                f"--sheet {override!r} not found in {path}. Available: {sheetnames}"
            )
        return override
    if "Use Cases" in sheetnames:
        return "Use Cases"
    if len(sheetnames) == 1:
        return sheetnames[0]
    raise SystemExit(
        f"Can't tell which sheet holds the use cases — no 'Use Cases' tab and "
        f"{len(sheetnames)} sheets present: {sheetnames}. Pass --sheet to pick one."
    )


def read_sheet(path, sheet_name):
    """Reads a sheet into a list of dicts, keyed by the header row. Blank rows are skipped."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet_name]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    records = []
    for row in rows[1:]:
        if all(cell is None or str(cell).strip() == "" for cell in row):
            continue
        record = {
            headers[i]: (row[i] if i < len(row) else None) for i in range(len(headers))
        }
        records.append(record)
    return records


def read_headers(path, sheet_name):
    """Returns just the header row, in order — used to discover question-label columns."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet_name]
    first_row = next(ws.iter_rows(values_only=True), ())
    return [str(h).strip() for h in first_row if h is not None and str(h).strip()]


def clean(value):
    return "" if value is None else str(value).strip()


def normalize_use_case_name(row):
    """The identity column is use_case_name on the old template, use_case on newer intake sheets."""
    return clean(row.get("use_case_name")) or clean(row.get("use_case"))


def resolve_question_text(label):
    """mapping.py override wins; otherwise strip a leading '1.1 ' style number prefix."""
    if label in QUESTION_TEXT_MAP:
        return QUESTION_TEXT_MAP[label]
    return NUMBER_PREFIX_RE.sub("", label).strip()


def validate_field_labels(field_labels):
    """Custom field names have no auto-derivation, so every field_label needs an explicit mapping."""
    missing_fields = set(field_labels) - set(CUSTOM_FIELD_NAME_MAP)
    if missing_fields:
        logger.error(
            "Unmapped field_label(s) in spreadsheet — add these to mapping.py: %s",
            sorted(missing_fields),
        )
    return not missing_fields


def build_custom_fields_payload(use_case_name, custom_field_rows, dry_run):
    """
    Custom Fields is wide format: one row per use case, one column per field
    label. use_case_name is expected to appear at most once in this tab — if
    it appears more than once, the first match wins and a warning is logged
    (fix the spreadsheet rather than rely on that).
    """
    payload = []
    matches = [
        r for r in custom_field_rows if normalize_use_case_name(r) == use_case_name
    ]
    if not matches:
        return payload
    if len(matches) > 1:
        logger.warning(
            "  %r appears more than once in Custom Fields — using the first row only.",
            use_case_name,
        )
    row = matches[0]

    for label, raw_value in row.items():
        label = clean(label)
        if label in BASE_COLUMNS:
            continue
        value = clean(raw_value)
        if not label or not value:
            continue
        field_name = CUSTOM_FIELD_NAME_MAP.get(label)
        if not field_name:
            continue  # already reported in validate_field_labels()
        if dry_run:
            logger.info(
                "  [DRY RUN] custom field %r (%s) = %r", label, field_name, value
            )
            continue
        field_id = credo_client.resolve_custom_field_id(field_name)
        if not field_id:
            continue
        payload.append({"custom_field_id": field_id, "value": value})
    return payload


def build_questions_payload(
    use_case_name, evidence_rows, questionnaire_key, questionnaire_data, dry_run
):
    """
    evidence_rows is wide format: one row per use case, one column per
    question. When there's no separate Questionnaire Evidence tab,
    evidence_rows is just the Use Cases rows themselves — each use case's
    question answers live inline on its own row alongside description etc.
    use_case_name is expected to appear at most once — if it appears more
    than once, the first match wins and a warning is logged (fix the
    spreadsheet rather than rely on that).
    """
    payload = []
    matches = [r for r in evidence_rows if normalize_use_case_name(r) == use_case_name]
    if not matches:
        return payload
    if len(matches) > 1:
        logger.warning(
            "  %r appears more than once in Questionnaire Evidence — using the first row only.",
            use_case_name,
        )
    row = matches[0]

    for label, raw_answer in row.items():
        label = clean(label)
        if label in BASE_COLUMNS:
            continue
        answer = clean(raw_answer)
        if not label or not answer:
            continue
        question_text = resolve_question_text(label)
        if dry_run:
            logger.info(
                "  [DRY RUN] question %r (%s) = %r", label, question_text, answer
            )
            continue
        question_id, section_id = credo_client.find_question(
            questionnaire_data, question_text
        )
        if not question_id:
            logger.warning(
                "  Question text not found on live questionnaire: %r (label: %s)",
                question_text,
                label,
            )
            continue
        payload.append(
            {
                "question_id": question_id,
                "questionnaire_id": questionnaire_key,
                "section_id": section_id,
                "value": answer,
            }
        )
    return payload


def migrate_use_cases(
    use_case_rows, evidence_rows, custom_field_rows, default_questionnaire_key, dry_run
):
    """Returns {use_case_name: use_case_id} for use in the Models/Vendors steps below."""
    name_to_id = {}
    stats = {"created": 0, "reused": 0, "failed": 0}

    for row in use_case_rows:
        name = normalize_use_case_name(row)
        if not name:
            continue
        description = clean(row.get("description"))
        owner_email = clean(row.get("owner_email"))
        questionnaire_key = (
            clean(row.get("questionnaire_key")) or default_questionnaire_key
        )

        logger.info("Use case: %s", name)

        if dry_run:
            logger.info(
                "  [DRY RUN] would create use case (questionnaire=%s, owner=%s)",
                questionnaire_key,
                owner_email,
            )
            build_custom_fields_payload(name, custom_field_rows, dry_run=True)
            if questionnaire_key:
                build_questions_payload(
                    name, evidence_rows, questionnaire_key, {}, dry_run=True
                )
            name_to_id[name] = f"dry-run-{name}"
            stats["created"] += 1
            continue

        existing_id = credo_client.get_use_case_id_by_name(name)
        if existing_id:
            logger.info(
                "  Already exists (id: %s) — skipping creation. Custom fields/evidence are only "
                "applied at creation time in this recipe; extend migrate.py if you need to "
                "backfill them onto existing use cases.",
                existing_id,
            )
            name_to_id[name] = existing_id
            stats["reused"] += 1
            continue

        custom_fields_payload = build_custom_fields_payload(
            name, custom_field_rows, dry_run=False
        )

        questions_payload = []
        if questionnaire_key:
            questionnaire_data = credo_client.get_questionnaire(questionnaire_key)
            questions_payload = build_questions_payload(
                name,
                evidence_rows,
                questionnaire_key,
                questionnaire_data,
                dry_run=False,
            )

        try:
            use_case_id = credo_client.create_use_case(
                name=name,
                description=description,
                questionnaire_key=questionnaire_key or None,
                owner_email=owner_email or None,
                custom_fields=custom_fields_payload,
                questions=questions_payload,
            )
            logger.info(
                "  Created (id: %s) — %d custom field(s), %d question(s)",
                use_case_id,
                len(custom_fields_payload),
                len(questions_payload),
            )
            name_to_id[name] = use_case_id
            stats["created"] += 1
        except Exception as e:
            logger.error("  Failed to create use case %r: %s", name, e)
            stats["failed"] += 1

    return name_to_id, stats


def migrate_models(model_rows, use_case_name_to_id, dry_run):
    stats = {"created": 0, "failed": 0, "linked": 0}
    for row in model_rows:
        name = clean(row.get("model_name"))
        if not name:
            continue
        summary = clean(row.get("summary"))
        source = clean(row.get("source"))
        status = clean(row.get("status")) or "none"
        use_case_name = normalize_use_case_name(row)

        if dry_run:
            logger.info(
                "[DRY RUN] Model: %s (status=%s, link to: %s)",
                name,
                status,
                use_case_name or "-",
            )
            stats["created"] += 1
            continue

        try:
            model_id = credo_client.create_model(
                name, summary=summary, source=source, status=status
            )
            logger.info("Model created: %s (id: %s)", name, model_id)
            stats["created"] += 1
        except Exception as e:
            logger.error("Failed to create model %r: %s", name, e)
            stats["failed"] += 1
            continue

        if use_case_name:
            use_case_id = use_case_name_to_id.get(use_case_name)
            if not use_case_id:
                logger.warning(
                    "Model %r references use_case_name %r, which wasn't found in the Use Cases tab.",
                    name,
                    use_case_name,
                )
                continue
            credo_client.link_model_to_use_case(use_case_id, model_id)
            stats["linked"] += 1

    return stats


def migrate_vendors(vendor_rows, use_case_name_to_id, dry_run):
    stats = {"created": 0, "failed": 0, "linked": 0}
    vendor_cache = {}
    for row in vendor_rows:
        name = clean(row.get("vendor_name"))
        if not name:
            continue
        use_case_name = normalize_use_case_name(row)

        if dry_run:
            logger.info(
                "[DRY RUN] Vendor: %s (link to: %s)", name, use_case_name or "-"
            )
            stats["created"] += 1
            continue

        vendor_id = vendor_cache.get(name)
        if not vendor_id:
            try:
                vendor_id = credo_client.create_vendor(name)
                vendor_cache[name] = vendor_id
                logger.info("Vendor created: %s (id: %s)", name, vendor_id)
                stats["created"] += 1
            except Exception as e:
                logger.error("Failed to create vendor %r: %s", name, e)
                stats["failed"] += 1
                continue

        if use_case_name:
            use_case_id = use_case_name_to_id.get(use_case_name)
            if not use_case_id:
                logger.warning(
                    "Vendor %r references use_case_name %r, which wasn't found in the Use Cases tab.",
                    name,
                    use_case_name,
                )
                continue
            credo_client.link_vendor_to_use_case(use_case_id, vendor_id)
            stats["linked"] += 1

    return stats


def migrate_risk_scenarios(risk_scenario_rows, use_case_name_to_id, dry_run):
    """Links use cases to existing library risk scenarios (by name) — nothing is created here."""
    stats = {"linked": 0, "failed": 0}
    for row in risk_scenario_rows:
        scenario_name = clean(row.get("risk_scenario_name"))
        use_case_name = normalize_use_case_name(row)
        if not scenario_name or not use_case_name:
            continue

        if dry_run:
            logger.info(
                "[DRY RUN] Risk scenario: %s (link to: %s)",
                scenario_name,
                use_case_name,
            )
            stats["linked"] += 1
            continue

        use_case_id = use_case_name_to_id.get(use_case_name)
        if not use_case_id:
            logger.warning(
                "Risk scenario %r references use_case_name %r, which wasn't found in the Use Cases tab.",
                scenario_name,
                use_case_name,
            )
            stats["failed"] += 1
            continue

        scenario_id = credo_client.resolve_risk_scenario_id(scenario_name)
        if not scenario_id:
            stats["failed"] += 1
            continue

        credo_client.link_risk_scenario_to_use_case(use_case_id, scenario_id)
        stats["linked"] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Migrate use cases, models, and vendors into Credo AI"
    )
    parser.add_argument(
        "--source",
        default=XLSX_PATH,
        help=f"Path to the source .xlsx (default: {XLSX_PATH})",
    )
    parser.add_argument(
        "--sheet",
        default=None,
        help=(
            "Name of the sheet holding one row per use case. Defaults to 'Use Cases' if "
            "present, or the only sheet if the workbook has just one."
        ),
    )
    parser.add_argument(
        "--questionnaire-key",
        default=None,
        help=(
            "Default questionnaire key+version (e.g. INTK1+1) for use cases that leave "
            "questionnaire_key blank in the spreadsheet"
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview without making any API calls"
    )
    args = parser.parse_args()

    if args.dry_run:
        logger.info("=" * 70)
        logger.info("DRY RUN — no API calls will be made")
        logger.info("=" * 70)

    use_case_sheet = pick_use_case_sheet(args.source, args.sheet)
    use_case_rows = read_sheet(args.source, use_case_sheet)

    has_evidence_sheet = sheet_exists(args.source, "Questionnaire Evidence")
    has_custom_fields_sheet = sheet_exists(args.source, "Custom Fields")
    has_models_sheet = sheet_exists(args.source, "Models")
    has_vendors_sheet = sheet_exists(args.source, "Vendors")
    has_risk_scenarios_sheet = sheet_exists(args.source, "Risk Scenarios")

    if has_evidence_sheet:
        evidence_rows = read_sheet(args.source, "Questionnaire Evidence")
        question_labels = [
            h
            for h in read_headers(args.source, "Questionnaire Evidence")
            if h not in BASE_COLUMNS
        ]
    else:
        logger.info(
            "No 'Questionnaire Evidence' tab — reading question answers inline from %r.",
            use_case_sheet,
        )
        evidence_rows = use_case_rows
        question_labels = [
            h
            for h in read_headers(args.source, use_case_sheet)
            if h not in BASE_COLUMNS
        ]

    custom_field_rows = (
        read_sheet(args.source, "Custom Fields") if has_custom_fields_sheet else []
    )
    if not has_custom_fields_sheet:
        logger.info("No 'Custom Fields' tab — skipping custom field population.")
    model_rows = read_sheet(args.source, "Models") if has_models_sheet else []
    if not has_models_sheet:
        logger.info("No 'Models' tab — skipping model creation.")
    vendor_rows = read_sheet(args.source, "Vendors") if has_vendors_sheet else []
    if not has_vendors_sheet:
        logger.info("No 'Vendors' tab — skipping vendor creation.")
    risk_scenario_rows = (
        read_sheet(args.source, "Risk Scenarios") if has_risk_scenarios_sheet else []
    )
    if not has_risk_scenarios_sheet:
        logger.info("No 'Risk Scenarios' tab — skipping risk scenario linking.")

    logger.info(
        "Loaded %d use case(s), %d question column(s), %d custom field row(s), "
        "%d model(s), %d vendor(s), %d risk scenario link(s)",
        len(use_case_rows),
        len(question_labels),
        len(custom_field_rows),
        len(model_rows),
        len(vendor_rows),
        len(risk_scenario_rows),
    )

    if has_custom_fields_sheet:
        field_labels = [
            h
            for h in read_headers(args.source, "Custom Fields")
            if h not in BASE_COLUMNS
        ]
        if not validate_field_labels(field_labels):
            logger.error(
                "Fix mapping.py before running for real (rows using an unmapped field_label are skipped, not applied)."
            )

    use_case_name_to_id, uc_stats = migrate_use_cases(
        use_case_rows,
        evidence_rows,
        custom_field_rows,
        args.questionnaire_key,
        args.dry_run,
    )
    model_stats = migrate_models(model_rows, use_case_name_to_id, args.dry_run)
    vendor_stats = migrate_vendors(vendor_rows, use_case_name_to_id, args.dry_run)
    risk_scenario_stats = migrate_risk_scenarios(
        risk_scenario_rows, use_case_name_to_id, args.dry_run
    )

    logger.info("=" * 70)
    logger.info("SUMMARY")
    logger.info("=" * 70)
    logger.info(
        "Use cases — created: %d, reused: %d, failed: %d",
        uc_stats["created"],
        uc_stats["reused"],
        uc_stats["failed"],
    )
    logger.info(
        "Models — created: %d, linked: %d, failed: %d",
        model_stats["created"],
        model_stats["linked"],
        model_stats["failed"],
    )
    logger.info(
        "Vendors — created: %d, linked: %d, failed: %d",
        vendor_stats["created"],
        vendor_stats["linked"],
        vendor_stats["failed"],
    )
    logger.info(
        "Risk scenarios — linked: %d, failed: %d",
        risk_scenario_stats["linked"],
        risk_scenario_stats["failed"],
    )

    with open("migration_result.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "use_cases": uc_stats,
                "models": model_stats,
                "vendors": vendor_stats,
                "risk_scenarios": risk_scenario_stats,
                "use_case_ids": dict(use_case_name_to_id.items()),
            },
            f,
            indent=2,
        )


if __name__ == "__main__":
    main()
