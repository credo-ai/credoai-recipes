# Data Migration — Bulk Use Case Import

Migrate a spreadsheet of AI use cases (plus their models, vendors, questionnaire answers, custom field values, and risk scenario links) into Credo AI in one command. Runs against the [Credo AI SDK](https://docs.sdk.credo.ai/) (`pycredoai`).

- Reads Excel — either a purpose-built multi-tab template or a single-sheet intake export
- One `use_cases.create()` call per row: use case, questionnaire answers, and custom fields go in the same payload
- `--dry-run` first, then a real run
- Re-runnable: existing use cases (matched by name) are skipped, not duplicated

---

## Prerequisites

| Item                     | Where to get it                                   |
| ------------------------ | ------------------------------------------------- |
| Credo AI API key         | Credo AI Governance App → Settings → Integrations |
| Credo AI tenant name     | Your org's tenant identifier in Credo AI          |
| Custom fields configured | Created in the Credo AI platform first            |
| Questionnaire configured | The intake questionnaire you'll target            |
| Repo setup done          | `mise install && uv sync` from the repo root      |

The API can't create custom fields or questionnaires — those are configured in the platform, then this recipe references them by name.

---

## Step 1: Set credentials

At the repo root:

```bash
cp example.env .local.env
# edit .local.env — set CREDOAI_API_KEY, CREDOAI_TENANT, CREDOAI_API_URL
```

`mise` loads `.local.env` automatically (see `mise.toml`). Both `.env` and `.local.env` are gitignored.

---

## Step 2: Prepare your spreadsheet

Two shapes are supported. Pick the one that matches what you have.

**Multi-tab template** (`use_case_migration_template.xlsx` in this folder):

- `Use Cases` — one row per use case, columns: `use_case_name`, `description`, `owner_email`, `questionnaire_key`
- `Questionnaire Evidence` (optional) — wide format: `use_case_name` + one column per question label
- `Custom Fields` (optional) — wide format: `use_case_name` + one column per custom field label
- `Models` (optional) — `model_name`, `summary`, `source`, `status`, `use_case_name`
- `Vendors` (optional) — `vendor_name`, `use_case_name`
- `Risk Scenarios` (optional) — `risk_scenario_name`, `use_case_name`

Any tab you don't need, leave out. `migrate.py` skips missing tabs.

**Single-sheet intake** (an exported intake form):

- One sheet, one row per use case
- Identity columns (`use_case_name` or `use_case`, `description`, `owner_email`, `questionnaire_key`) plus one column per question, answered inline
- No other tabs needed

---

## Step 3: Fill in `mapping.py`

Custom field column headers on your spreadsheet map to the real custom field names in your Credo AI tenant. Every `Custom Fields` column needs an entry:

```python
CUSTOM_FIELD_NAME_MAP = {
    "business_function": "Business Function",
    "cost_center": "Cost Center",
    # your column header -> the exact Custom Field name in Credo AI
}
```

Question columns usually **don't** need an entry — `migrate.py` strips a leading `1.1 ` style number prefix and matches the rest against the live questionnaire text. Add an entry only when your header text differs from the live question (typo, reworded question, no number prefix):

```python
QUESTION_TEXT_MAP = {
    "Column header on the spreadsheet": "Exact question text on the live questionnaire",
}
```

Unmapped custom field labels are reported and skipped — fix them before running for real.

---

## Step 4: Dry run

```bash
uv run python migrate.py --dry-run --questionnaire-key INTK1+1
```

Prints every use case, custom field, and question it would send. No API calls made. Confirm the counts and mappings look right.

Use `--source my-intake.xlsx` if your file isn't the bundled template, and `--sheet <name>` if the use-case sheet isn't called `Use Cases` and the workbook has more than one sheet.

---

## Step 5: Real run

```bash
uv run python migrate.py --questionnaire-key INTK1+1
```

Order of operations: use cases → models → vendors → risk scenario links. A `migration_result.json` is written next to `migrate.py` with per-section counts and the name→id map.

Re-running is safe: use cases whose exact name already exists are reused, not re-created. **Custom fields and questionnaire evidence are only applied at creation time** — re-running does not backfill them onto an already-existing use case. If you need that, extend `migrate.py` with a `PATCH` step.

---

## Troubleshooting

| Symptom                                         | Likely cause                                       | Fix                                                                                                                                                                 |
| ----------------------------------------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 401 on first API call                           | Wrong `CREDOAI_API_KEY` or `CREDOAI_TENANT`        | Check `.local.env` values                                                                                                                                           |
| 404 Not Found                                   | Wrong `CREDOAI_API_URL`                            | Confirm base URL with Credo AI                                                                                                                                      |
| 422 "Input should be a valid list"              | You edited the payload code and passed `None`      | Omit the key entirely — see the note in `credo_client.py::create_use_case`                                                                                          |
| Model/vendor 404 on link                        | Wrong link endpoint shape                          | Use `POST use_cases/{id}/models` with `{"id": model_id}` — not `/models/{id}`                                                                                       |
| "Custom field ... not found in this tenant"     | Field name in `mapping.py` doesn't match Credo AI  | Copy the exact name from the platform UI                                                                                                                            |
| "Question text not found on live questionnaire" | Column header doesn't match after number-stripping | Add an override entry to `QUESTION_TEXT_MAP` in `mapping.py`                                                                                                        |
| Owner assignment silently ignored               | Recipe uses an unverified shape                    | Spot-check in the UI with an owner different from the API caller. The `{"owner_id": ..., "owner_type": "user"}` shape is accepted but not fully verified end-to-end |
| Custom fields not applied on re-run             | Use case already existed                           | Expected — this recipe only sets custom fields/evidence at creation. Extend `migrate.py` with a `PATCH` step to backfill                                            |

Still stuck? Slack: `#credo-ai-integrations` | Support: support.credo.ai
