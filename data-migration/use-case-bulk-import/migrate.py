"""
Use case bulk import: reads a spreadsheet and creates the use cases (with
custom fields and questionnaire answers), models, vendors, and risk scenario
links it describes in Credo AI.

Supports two spreadsheet shapes:

  - Single-sheet intake (an exported intake form): one sheet, one row per
    use case. A handful of BASE_COLUMNS carry identity and metadata
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

Question column headers are matched against the questionnaire's question
text, exactly or ignoring case. A leading number like "1.1 " in a header is
stripped first, since it isn't part of the question text. Add an entry to
mapping.py's QUESTION_TEXT_MAP only when a header still won't match (a typo
or reworded question).

Usage:
    uv run python migrate.py --dry-run                          # preview, no API calls
    uv run python migrate.py --questionnaire-key INTK1+1        # real run
    uv run python migrate.py --source my-intake.xlsx \\
                             --sheet "AI Intake" \\
                             --questionnaire-key INTK1+1
    uv run python migrate.py --questionnaire-key INTK1+1,RA1+1  # use case spans two questionnaires
    uv run python migrate.py --questionnaire-key INTK1+1 --assign-owners  # also set owners

Every row is handled on its own: a failure is logged, recorded in
migration_result.json with the row's name and the error, and the run moves
on to the next row. Re-runs are safe — use cases, models, and vendors that
already exist (matched by exact name) are reused, not created again.
"""

import argparse
import json
import logging
import re
from pathlib import Path

import credo_client
import openpyxl
from mapping import CUSTOM_FIELD_NAME_MAP, QUESTION_TEXT_MAP

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
XLSX_PATH = HERE / "use_case_migration_template.xlsx"
RESULT_PATH = HERE / "migration_result.json"

# Columns on the use case sheet that are identity or metadata, never questions.
# "" catches blank headers on empty trailing columns. Add any other
# non-question column in your export (internal ids, review notes) here, or it
# will be treated as a question and reported as "not found".
BASE_COLUMNS = {
    "use_case_name",
    "use_case",
    "use_case_id",
    "use_case_number",
    "description",
    "owner_email",
    "questionnaire_key",
    "",
}

# Fixed (non-question) columns on the other tabs. Headers matching any of
# these, or BASE_COLUMNS, are matched case-insensitively and with spaces
# treated as underscores, so "Use Case Name" and "use_case_name" both work.
# Question and custom field headers keep their original text.
OTHER_FIXED_COLUMNS = {
    "model_name",
    "summary",
    "source",
    "status",
    "vendor_name",
    "risk_scenario_name",
}
FIXED_COLUMNS = (BASE_COLUMNS - {""}) | OTHER_FIXED_COLUMNS

NUMBER_PREFIX_RE = re.compile(r"^\d+(\.\d+)*[.)]?\s+")

# The API currently rejects use case names containing any of these characters
# (HTTP 422). The dry run flags these rows, and a failure says why.
UNSUPPORTED_NAME_CHARS_RE = re.compile(r"[&#+%]")

# Every per-row failure in the run: {"step", "name", "error"}. Written to
# migration_result.json so a failed row can be fixed and re-run.
FAILURES = []

# Owners left off because --assign-owners wasn't passed:
# {"use_case", "owner_email"}. Written to migration_result.json so the emails
# can be checked before re-running with --assign-owners.
OWNERS_NOT_ASSIGNED = []


def record_failure(step, name, error):
    message = str(error)
    if step == "use_case" and UNSUPPORTED_NAME_CHARS_RE.search(name):
        message += (
            " — the name contains & # + or %, which the API currently rejects. "
            "Rename the use case and re-run."
        )
    logger.error(
        "  %s %r failed: %s", step.replace("_", " ").capitalize(), name, message
    )
    FAILURES.append({"step": step, "name": name, "error": message})


def normalize_header(header):
    """Lower-case fixed column headers so matching doesn't depend on how the sheet was typed."""
    text = "" if header is None else str(header).strip()
    key = text.lower().replace(" ", "_")
    return key if key in FIXED_COLUMNS else text


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
    headers = [normalize_header(h) for h in rows[0]]
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
    return [normalize_header(h) for h in first_row if h is not None and str(h).strip()]


def clean(value):
    return "" if value is None else str(value).strip()


def normalize_use_case_name(row):
    """The name column can be called use_case_name or use_case."""
    return clean(row.get("use_case_name")) or clean(row.get("use_case"))


def resolve_question_text(label):
    """mapping.py override wins; otherwise strip a leading '1.1 ' style number prefix."""
    if label in QUESTION_TEXT_MAP:
        return QUESTION_TEXT_MAP[label]
    return NUMBER_PREFIX_RE.sub("", label).strip()


def resolve_answer_value(multiple, answer):
    """
    Shape an answer for the question it was matched to. Questions that take
    several values (`multiple` is True on the live question) get a list,
    split on semicolons ("EU / EEA; US" -> ["EU / EEA", "US"]). Everything
    else is sent as the plain string. The flag always comes from the target
    tenant's questionnaire, since sending the wrong shape fails the whole use
    case create.
    """
    if multiple:
        return [v.strip() for v in answer.split(";") if v.strip()]
    return answer


_CUSTOM_FIELD_NAME_MAP_CI = {
    k.strip().lower(): v for k, v in CUSTOM_FIELD_NAME_MAP.items()
}


def custom_field_name_for(label):
    """mapping.py lookup, ignoring case and surrounding whitespace in the spreadsheet header."""
    return _CUSTOM_FIELD_NAME_MAP_CI.get(label.strip().lower())


def validate_field_labels(field_labels):
    """Custom field names have no auto-derivation, so every field_label needs an explicit mapping."""
    missing_fields = {
        label for label in field_labels if not custom_field_name_for(label)
    }
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
        field_name = custom_field_name_for(label)
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


def build_questions_payload(use_case_name, evidence_rows, questionnaires_data, dry_run):
    """
    evidence_rows is wide format: one row per use case, one column per
    question. When there's no separate Questionnaire Evidence tab,
    evidence_rows is just the Use Cases rows themselves — each use case's
    question answers live inline on its own row alongside description etc.
    use_case_name is expected to appear at most once — if it appears more
    than once, the first match wins and a warning is logged (fix the
    spreadsheet rather than rely on that).

    questionnaires_data: {questionnaire_key: questionnaire_data} for every
    questionnaire this use case is attached to — a use case can span more
    than one (e.g. a separate Intake + Risk Assessment), so each question
    label is searched across all of them, in order, for its first match.
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
            # List vs. string is decided from the live question, which a dry
            # run doesn't fetch.
            logger.info(
                "  [DRY RUN] question %r (%s) = %r", label, question_text, answer
            )
            continue
        question_id, section_id, multiple, questionnaire_key = (
            credo_client.find_question_across(questionnaires_data, question_text)
        )
        if not question_id:
            logger.warning(
                "  Question text not found on any questionnaire (%s): %r (label: %s)",
                ", ".join(questionnaires_data),
                question_text,
                label,
            )
            continue
        value = resolve_answer_value(multiple, answer)
        payload.append(
            {
                "question_id": question_id,
                # The API places the answer by section_id; questionnaire_id
                # carries the questionnaire key.
                "questionnaire_id": questionnaire_key,
                "section_id": section_id,
                "value": value,
            }
        )
    return payload


def migrate_use_cases(
    use_case_rows,
    evidence_rows,
    custom_field_rows,
    default_questionnaire_keys,
    assign_owners,
    dry_run,
):
    """Returns ({use_case_name: use_case_id}, stats) for the Models/Vendors/Risk Scenarios steps below."""
    name_to_id = {}
    stats = {"created": 0, "reused": 0, "failed": 0, "owners_not_assigned": 0}

    for row in use_case_rows:
        name = normalize_use_case_name(row)
        if not name:
            continue
        description = clean(row.get("description"))
        owner_email = clean(row.get("owner_email"))
        if owner_email and not assign_owners:
            stats["owners_not_assigned"] += 1
            OWNERS_NOT_ASSIGNED.append({"use_case": name, "owner_email": owner_email})
            owner_email = ""
        row_override = clean(row.get("questionnaire_key"))
        questionnaire_keys = (
            [k.strip() for k in row_override.split(",") if k.strip()]
            if row_override
            else default_questionnaire_keys
        )

        logger.info("Use case: %s", name)

        if dry_run:
            if UNSUPPORTED_NAME_CHARS_RE.search(name):
                logger.warning(
                    "  [DRY RUN] name contains & # + or %% — the API currently rejects "
                    "these. Rename it, or this row will fail."
                )
            logger.info(
                "  [DRY RUN] would create use case (questionnaires=%s, owner=%s)",
                questionnaire_keys,
                owner_email or "-",
            )
            build_custom_fields_payload(name, custom_field_rows, dry_run=True)
            if questionnaire_keys:
                build_questions_payload(name, evidence_rows, {}, dry_run=True)
            name_to_id[name] = f"dry-run-{name}"
            stats["created"] += 1
            continue

        try:
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
            if questionnaire_keys:
                questionnaires_data = {
                    key: credo_client.get_questionnaire(key)
                    for key in questionnaire_keys
                }
                questions_payload = build_questions_payload(
                    name,
                    evidence_rows,
                    questionnaires_data,
                    dry_run=False,
                )

            use_case_id = credo_client.create_use_case(
                name=name,
                description=description,
                questionnaire_keys=questionnaire_keys or None,
                owner_email=owner_email or None,
                custom_fields=custom_fields_payload,
                questions=questions_payload,
            )
        except Exception as e:
            record_failure("use_case", name, e)
            stats["failed"] += 1
            continue

        credo_client.remember_use_case(name, use_case_id)
        logger.info(
            "  Created (id: %s) — %d custom field(s), %d question(s)",
            use_case_id,
            len(custom_fields_payload),
            len(questions_payload),
        )
        name_to_id[name] = use_case_id
        stats["created"] += 1

    if stats["owners_not_assigned"]:
        emails = sorted({o["owner_email"] for o in OWNERS_NOT_ASSIGNED})
        logger.info(
            "%d row(s) have an owner_email that was NOT applied: %s. Check these emails, then "
            "pass --assign-owners to set owners. An email with no Credo AI account gets a new "
            "user created for it, which may send that person an invite.",
            stats["owners_not_assigned"],
            ", ".join(emails),
        )
    return name_to_id, stats


def _migrate_assets(
    kind,
    rows,
    name_column,
    use_case_name_to_id,
    dry_run,
    get_id,
    create,
    remember,
    link,
):
    """
    Shared by models and vendors. For each row: check the use case it links
    to first (so a bad use_case_name never leaves an orphan behind), reuse an
    existing record with the same name or create one, then link it.
    """
    stats = {"created": 0, "reused": 0, "linked": 0, "already_linked": 0, "failed": 0}
    for row in rows:
        name = clean(row.get(name_column))
        if not name:
            continue
        use_case_name = normalize_use_case_name(row)

        if dry_run:
            logger.info(
                "[DRY RUN] %s: %s (link to: %s)",
                kind.capitalize(),
                name,
                use_case_name or "-",
            )
            stats["created"] += 1
            continue

        use_case_id = None
        if use_case_name:
            use_case_id = use_case_name_to_id.get(use_case_name)
            if not use_case_id:
                record_failure(
                    kind,
                    name,
                    f"use_case_name {use_case_name!r} wasn't created or found in this run "
                    "(check the Use Cases tab and any use case failures above); skipped so "
                    f"the {kind} isn't created unlinked",
                )
                stats["failed"] += 1
                continue

        try:
            record_id = get_id(name)
            if record_id:
                logger.info(
                    "%s reused: %s (id: %s)", kind.capitalize(), name, record_id
                )
                stats["reused"] += 1
            else:
                record_id = create(row, name)
                remember(name, record_id)
                logger.info(
                    "%s created: %s (id: %s)", kind.capitalize(), name, record_id
                )
                stats["created"] += 1
            if use_case_id:
                if link(use_case_id, record_id):
                    stats["linked"] += 1
                else:
                    stats["already_linked"] += 1
        except Exception as e:
            record_failure(kind, name, e)
            stats["failed"] += 1

    return stats


def migrate_models(model_rows, use_case_name_to_id, dry_run):
    def create(row, name):
        return credo_client.create_model(
            name,
            summary=clean(row.get("summary")),
            source=clean(row.get("source")),
            status=clean(row.get("status")) or "none",
        )

    return _migrate_assets(
        "model",
        model_rows,
        "model_name",
        use_case_name_to_id,
        dry_run,
        credo_client.get_model_id_by_name,
        create,
        credo_client.remember_model,
        credo_client.link_model_to_use_case,
    )


def migrate_vendors(vendor_rows, use_case_name_to_id, dry_run):
    return _migrate_assets(
        "vendor",
        vendor_rows,
        "vendor_name",
        use_case_name_to_id,
        dry_run,
        credo_client.get_vendor_id_by_name,
        lambda row, name: credo_client.create_vendor(name),
        credo_client.remember_vendor,
        credo_client.link_vendor_to_use_case,
    )


def migrate_risk_scenarios(risk_scenario_rows, use_case_name_to_id, dry_run):
    """Links use cases to existing library risk scenarios (by name) — nothing is created here."""
    stats = {"linked": 0, "already_linked": 0, "failed": 0}
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
            record_failure(
                "risk_scenario",
                scenario_name,
                f"use_case_name {use_case_name!r} wasn't created or found in this run",
            )
            stats["failed"] += 1
            continue

        try:
            scenario_id = credo_client.resolve_risk_scenario_id(scenario_name)
            if not scenario_id:
                raise LookupError("not found in this tenant's risk scenario library")
            created = credo_client.link_risk_scenario_to_use_case(
                use_case_id, scenario_id
            )
        except Exception as e:
            record_failure("risk_scenario", f"{scenario_name} → {use_case_name}", e)
            stats["failed"] += 1
            continue
        stats["linked" if created else "already_linked"] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Migrate use cases, models, and vendors into Credo AI"
    )
    parser.add_argument(
        "--source",
        default=XLSX_PATH,
        help=f"Path to the source .xlsx (default: {XLSX_PATH.name} in this folder)",
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
            "Default questionnaire key(s)+version for use cases that leave "
            "questionnaire_key blank in the spreadsheet. Comma-separate multiple "
            "keys (no spaces) when a use case's evidence spans more than one "
            "questionnaire (e.g. INTK1+1,RA1+1) — each question label is matched "
            "against all of them in order. The questionnaire_key column, if "
            "present in the sheet, accepts the same comma-separated format as a "
            "per-row override."
        ),
    )
    parser.add_argument(
        "--assign-owners",
        action="store_true",
        help=(
            "Set each use case's owner from the owner_email column. Off by default: an "
            "email with no Credo AI account gets a new user created for it (which may send "
            "them an invite), so check the emails first."
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

    default_questionnaire_keys = [
        k.strip() for k in (args.questionnaire_key or "").split(",") if k.strip()
    ]
    use_case_name_to_id, uc_stats = migrate_use_cases(
        use_case_rows,
        evidence_rows,
        custom_field_rows,
        default_questionnaire_keys,
        args.assign_owners,
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
    for label, st in (("Models", model_stats), ("Vendors", vendor_stats)):
        logger.info(
            "%s — created: %d, reused: %d, linked: %d, already linked: %d, failed: %d",
            label,
            st["created"],
            st["reused"],
            st["linked"],
            st["already_linked"],
            st["failed"],
        )
    logger.info(
        "Risk scenarios — linked: %d, already linked: %d, failed: %d",
        risk_scenario_stats["linked"],
        risk_scenario_stats["already_linked"],
        risk_scenario_stats["failed"],
    )
    for failure in FAILURES:
        logger.info(
            "  FAILED %s %r: %s", failure["step"], failure["name"], failure["error"]
        )

    if args.dry_run:
        logger.info("Dry run — %s left unchanged.", RESULT_PATH.name)
        return

    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {
                "use_cases": uc_stats,
                "models": model_stats,
                "vendors": vendor_stats,
                "risk_scenarios": risk_scenario_stats,
                "failures": FAILURES,
                "owners_not_assigned": OWNERS_NOT_ASSIGNED,
                "use_case_ids": dict(use_case_name_to_id.items()),
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    logger.info("Wrote %s", RESULT_PATH)


if __name__ == "__main__":
    main()
