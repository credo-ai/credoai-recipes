# Use Case Bulk Import

Migrate a spreadsheet of AI use cases (plus their models, vendors, questionnaire answers, custom field values, and risk scenario links) into Credo AI in one command. Runs against the [Credo AI SDK](https://docs.sdk.credo.ai/) (`pycredoai`). Uses only the public Credo AI API, with one API key.

- Reads Excel — either a purpose-built multi-tab template or a single-sheet intake export
- One `use_cases.create()` call per row: use case, questionnaire answers, and custom fields go in the same payload
- `--dry-run` first, then a real run
- One bad row doesn't stop the run — it's logged, recorded in `migration_result.json`, and skipped
- Re-runnable: existing use cases, models, and vendors (matched by exact name) are reused, and existing links are left alone

---

## Prerequisites

| Item                     | Where to get it                                   |
| ------------------------ | ------------------------------------------------- |
| Credo AI API key         | Credo AI Governance App → Settings → Integrations |
| Credo AI tenant name     | Your org's tenant identifier in Credo AI          |
| Custom fields configured | The custom fields you'll fill in already exist    |
| Questionnaire configured | The intake questionnaire you'll target exists     |
| Repo setup done          | `mise install && uv sync` from the repo root      |

This recipe fills in custom fields and questionnaires that already exist in your tenant, referenced by name and key. It doesn't create them. Set them up in the platform first (or with the API's `POST /custom_fields` and `POST /questionnaires` endpoints).

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

Any tab you don't need, leave out. `migrate.py` skips missing tabs. The fixed column names above are matched ignoring case, and spaces count as underscores, so `Use Case Name` works too.

**Single-sheet intake** (an exported intake form):

- One sheet, one row per use case
- Identity columns (`use_case_name` or `use_case`, `description`, `owner_email`, `questionnaire_key`) plus one column per question, answered inline
- No other tabs needed

**Use case names can't contain `&`, `#`, `+`, or `%` yet.** The API currently rejects them with a 422. The dry run flags these rows so you can rename them first.

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

Question columns usually **don't** need an entry — `migrate.py` strips a leading `1.1 ` style number prefix and matches the rest against the live questionnaire text (exact match first, then ignoring case). Add an entry only when your header text differs from the live question (typo, reworded question):

```python
QUESTION_TEXT_MAP = {
    "Column header on the spreadsheet": "Exact question text on the live questionnaire",
}
```

Unmapped custom field labels are reported and skipped — fix them before running for real.

---

## Step 4: Dry run

Run from this folder (`data-migration/use-case-bulk-import/`):

```bash
uv run python migrate.py --dry-run --questionnaire-key INTK1+1
```

Prints every use case, custom field, and question it would send. No API calls made, and `migration_result.json` isn't touched. Confirm the counts and mappings look right.

Use `--source my-intake.xlsx` if your file isn't the bundled template, and `--sheet <name>` if the use-case sheet isn't called `Use Cases` and the workbook has more than one sheet.

If a use case's evidence is split across more than one questionnaire (e.g. a separate Intake + Risk Assessment), comma-separate the keys — `--questionnaire-key INTK1+1,RA1+1` — and each question label is matched against all of them in order. The `questionnaire_key` column, if present in your sheet, accepts the same comma-separated format as a per-row override. For questions that take several values, separate them with a semicolon in the cell (`EU / EEA; US`). The recipe checks each question in your tenant and sends a list only where the question accepts several values.

---

## Step 5: Real run

```bash
uv run python migrate.py --questionnaire-key INTK1+1
```

Order of operations: use cases → models → vendors → risk scenario links. A `migration_result.json` is written next to `migrate.py` with per-section counts, the name→id map, and a `failures` list naming each row that failed and why. Fix those rows and re-run.

**Owners are opt-in.** By default the `owner_email` column is ignored, and the emails it skipped are listed in the log and in `migration_result.json` (`owners_not_assigned`). Add `--assign-owners` to set each use case's owner from it:

```bash
uv run python migrate.py --questionnaire-key INTK1+1 --assign-owners
```

Check the emails first. If an email doesn't match an existing Credo AI user, the API creates a new user for it with the default "user" role, and that person may get an invite email. A typo creates a stray account.

Two more things to know:

- **Re-running is safe.** Use cases, models, and vendors whose exact name already exists are reused, and links that already exist are counted as "already linked". **Custom fields and questionnaire evidence are only applied at creation time** — re-running does not backfill them onto an already-existing use case. If you need that, extend `migrate.py` with a `PATCH /use_cases/{id}/custom_fields` step.
- **The API key's user is added as a contributor** on each use case attached to a questionnaire. Use a key belonging to a service or admin account if you don't want a person listed there.

---

## Troubleshooting

| Symptom                                        | Likely cause                                       | Fix                                                                                                   |
| ---------------------------------------------- | -------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| 401 on first API call                          | Wrong `CREDOAI_API_KEY` or `CREDOAI_TENANT`        | Check `.local.env` values                                                                             |
| 404 Not Found                                  | Wrong `CREDOAI_API_URL`                            | Confirm base URL with Credo AI                                                                        |
| Use case fails with 422, name has `& # + %`    | Known API issue with these characters in names     | Rename the use case and re-run                                                                        |
| Model/vendor "wasn't created or found"         | Its `use_case_name` failed or isn't on `Use Cases` | Fix the use case row (see the failures list), then re-run                                             |
| "Custom field ... not found in this tenant"    | Field name in `mapping.py` doesn't match Credo AI  | Copy the exact name from the platform UI                                                              |
| "Question text not found on any questionnaire" | Column header doesn't match after number-stripping | Add an override entry to `QUESTION_TEXT_MAP` in `mapping.py`                                          |
| Owners not set                                 | `--assign-owners` wasn't passed                    | Re-running won't update existing use cases; set the owner in the UI, or delete the use case and rerun |
| Custom fields not applied on re-run            | Use case already existed                           | Expected — see Step 5                                                                                 |

Still stuck? Contact your Credo AI representative or [support.credo.ai](https://support.credo.ai). Problems with the recipe itself can be filed as an issue on this repository.
