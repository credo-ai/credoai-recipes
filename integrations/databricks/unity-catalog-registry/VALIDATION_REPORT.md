# Validation Report — Databricks Unity Catalog Registry Sync Cookbook

Date: 2026-09-15
Cookbook: `integrations/databricks/unity-catalog-registry/`
Validated against: a live Databricks workspace (Unity Catalog metastore) and a live Credo AI sandbox
tenant. Workspace host, tenant name, model IDs, and credentials are deliberately omitted — this
repository is public.

## Summary

Everything in the cookbook was exercised against both live platforms: the one-time source setup, the
startup full sync, steady-state incremental cycles, mid-run change pickup, the periodic full sweep,
restart idempotency, and scoped mode. No step is asserted from documentation alone except where
noted under Coverage gaps.

Status stays **🧪 Preview** — `CONTRIBUTING.md` stage 4 gates ✅ on the Guide page being published in
`credoai-integration-service`, which has not happened.

## 1. Environment

| Component      | Version / detail                                                             |
| -------------- | ---------------------------------------------------------------------------- |
| Python         | 3.13, throwaway venv (not the repo `.venv`)                                  |
| databricks-sdk | 0.139.0 (pinned in `requirements.txt`)                                       |
| Databricks     | Unity Catalog metastore, 96 registered models                                |
| Credo AI       | sandbox tenant, 23 pre-existing models at first run                          |
| MLflow         | 3.16.0 — used only to create a test model version, not a cookbook dependency |

## 2. Unity Catalog API findings — established live

These drove the design and are not in the Databricks docs:

| Finding                                                                             | How established                                                                             |
| ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `order_by` is **silently ignored** on the models list; results are unsorted         | Passed `order_by=updated_at desc`, got a byte-identical list                                |
| `catalog_name` requires `schema_name`                                               | `400 INVALID_PARAMETER_VALUE: "Cannot have an empty schema if the catalog is set"`          |
| Metastore-wide list works with no parameters                                        | Returned all 96 models in one page                                                          |
| **94 of 96** models are `system.ai.*` Databricks foundation models                  | Counted by catalog: `{system: 94, demo: 2}`                                                 |
| UC enforces a real rate limit                                                       | Tripped `429 REQUEST_LIMIT_EXCEEDED / UC_REQUEST_LIMIT_EXCEEDED` during read-only probing   |
| `include_aliases=true` works on the single-model GET but is a **no-op on the list** | Aliases returned `None` for every model on the list call                                    |
| No SQL shortcut exists                                                              | `system.mlflow` holds only experiments/runs; `system.information_schema` has no models view |

### `updated_at` roll-up semantics — the load-bearing question, settled

Databricks documents `updated_at` only as "Last-update timestamp of the registered model" and never
says what mutates it. Tested each case directly against a throwaway model:

| Operation                          | Parent `updated_at` bumps? | Evidence           |
| ---------------------------------- | -------------------------- | ------------------ |
| Registered model comment updated   | ✅ yes                     | +2,382 ms          |
| **Model version created** (MLflow) | ✅ **yes**                 | +22,123 ms         |
| **Alias set**                      | ✅ **yes**                 | +26,220 ms         |
| Model **version** comment updated  | ❌ **no**                  | delta exactly 0 ms |

So the watermark catches every event this cookbook cares about — new models, new versions, alias
changes. The single gap is version-level metadata edits, which is precisely what
`FULL_SWEEP_EVERY_N_CYCLES` exists to close. This is documented in `.env.example`, the README
troubleshooting table, and `TESTING.md` Step 6.

## 3. Live run — setup

`setup_credo_source.py` against the sandbox tenant:

```text
Source 'Databricks' already exists: <id>
```

Idempotent, and confirms the same `Databricks` source serves both Databricks cookbooks.

## 4. Live run — cycles

Run with `POLL_INTERVAL_SECONDS=30`, `FULL_SWEEP_EVERY_N_CYCLES=5` to compress the test.

```text
seeded 23 existing Credo AI model(s)
cycle: scanned=3 changed=3 created=2 updated=1 failed=0 full_sweep=True
cycle: scanned=3 changed=0 created=0 updated=0 failed=0 full_sweep=False
cycle: scanned=3 changed=0 created=0 updated=0 failed=0 full_sweep=False
cycle: scanned=3 changed=1 created=0 updated=1 failed=0 full_sweep=False   <- alias set mid-run
cycle: scanned=3 changed=0 created=0 updated=0 failed=0 full_sweep=False
cycle: scanned=3 changed=3 created=0 updated=3 failed=0 full_sweep=True    <- periodic sweep
```

What each line proves:

- `scanned=3`, not 96 — the catalog exclusion works; **zero** `system.ai.*` models reached Credo AI.
- Steady-state cycles do no per-model calls and write nothing — the watermark is doing its job.
- An alias set mid-run was picked up on the very next cycle, touching only that one model.
- The periodic sweep re-synced all three with `created=0` — no duplicates.

Resulting Credo AI records:

```json
{
  "name": "demo.default.<test model>",
  "source": "Databricks",
  "summary": "Databricks Unity Catalog model, version 2. Aliases: challenger=2, champion=2."
}
```

## 5. Live run — restart idempotency

Stopped the poller and restarted it:

```text
seeded 25 existing Credo AI model(s)
cycle: scanned=3 changed=3 created=0 updated=3 failed=0 full_sweep=True
```

`created=0` on restart. This is what justifies carrying no state file: Credo AI's own model list is
the source of truth for what already exists.

## 6. Failure-mode checks

| Scenario                                            | Result                                                                 |
| --------------------------------------------------- | ---------------------------------------------------------------------- |
| `DATABRICKS_CATALOG` set, `DATABRICKS_SCHEMA` blank | Refused to start, naming both variables — verified                     |
| Explicitly scoped to one schema                     | `scope=demo.default`, `scanned=3` — verified                           |
| Dry-run listing (`TESTING.md` Step 3)               | Printed exactly the three models that would sync — verified            |
| `system` catalog excluded by default                | Verified: no `system.ai.*` model exists in the tenant after six cycles |

## 7. Repo gates

`ruff check`, `ruff format --check`, `prettier`, and `pre-commit run --all-files` — see the session
transcript for the final run. `.env` is gitignored and was never staged.

## 8. Cleanup

- Deleted the throwaway Databricks model created for the `updated_at` test, restoring the metastore
  to its original 96 models.
- Reverted the version comment edited on an existing sandbox model during the roll-up test.
- Deleted the MLflow experiment created by the version-creation test.
- Stopped the poller.

## Coverage gaps / open items

- **The 2 models synced into the Credo AI sandbox tenant were left in place.** The Integration API
  surface used here has no delete; removing them is a manual step.
- **`system.access.audit` action names for UC model events were not verified** — the audit-table
  path is documented as a reconciliation option only and is not used by this cookbook.
- **Lakeflow model update triggers were not exercised.** The README describes them from Databricks'
  documentation; entitlement on the test workspace was not confirmed. This is why no job artifact
  ships with the cookbook.
- **Credo AI 5xx handling was not triggered live** — the sandbox never returned one. The retry path
  is ported unchanged from the live-validated MLflow Model Registry cookbook.
- **A pre-existing Credo AI model keeps its original `source`.** One synced model shows
  `source: null` because it existed before this poller ran and `upsert_model` only patches
  `summary`. Documented in the README field-mapping and troubleshooting tables.
