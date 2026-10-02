# Validation Report — Databricks MLflow Model Registry Sync Cookbook

- **Date:** 2026-09-15
- **Cookbook path:** `integrations/databricks/mlflow-model-registry/`
- **Design basis:** the `databricks-mlflow.md` spec, as corrected by a prior reference
  implementation that was itself run against a live tenant. The five load-bearing findings that
  reference established (see **2. Ported findings**) are preserved here.

## Summary

Run end-to-end against a live Databricks workspace (legacy Workspace Model Registry) and a live
Credo AI sandbox tenant. Every step of the cookbook's own Quick Start was executed in order,
including a real webhook delivery from Databricks over a public tunnel.

All three handled event types were observed arriving from Databricks itself — not simulated — and
each produced the expected Credo AI model. The startup backfill synced the whole registry, and a
restart re-synced it with zero duplicates.

Status stays **🧪 Preview**: per `CONTRIBUTING.md` the flip to ✅ also requires the Guide page to be
registered and published in `credoai-integration-service`, which has not happened yet.

## 1. Environment

| Component  | What was used                                                                           |
| ---------- | --------------------------------------------------------------------------------------- |
| Databricks | Live workspace, **legacy Workspace Model Registry** (14 registered models pre-existing) |
| Credo AI   | Live sandbox tenant on `https://api.credo.ai` (10 models pre-existing)                  |
| Tunnel     | ngrok reserved domain → `127.0.0.1:8322`                                                |
| Python     | 3.13, clean venv, `pip install -r server/python/requirements.txt` → exit 0              |

Credentials came from a sandbox-only `.env`. Nothing sensitive is recorded below: the workspace
host, tenant name, tunnel URL, API keys and model IDs are all deliberately omitted.

## 2. Ported findings — confirmed live

The five constraints inherited from the reference implementation were re-confirmed in this run,
not assumed:

| #   | Finding                                                             | How this run confirmed it                                                                                            |
| --- | ------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| 1   | `source` resolves against a pre-existing `Source` record            | `setup_credo_source.py` found the `Databricks` source; all 13 models created in this run carry `source="Databricks"` |
| 2   | Credo AI model create accepts only `name`/`summary`/`source`/…      | Create body is exactly those three; returned records show no dropped-field surprises                                 |
| 3   | Registry webhooks are a Databricks-only surface, not `MlflowClient` | `register_webhook.py` (`databricks-sdk` → `model_registry`) registered, listed and deleted a webhook                 |
| 4   | Credo AI models have no version concept                             | Version and stage land in `summary`; repeat events PATCH rather than create                                          |
| 5   | The webhook payload carries no `run_id`/`description`               | Live deliveries parsed cleanly with only `event`/`model_name`/`version`/`to_stage`/`from_stage`                      |

### Newly settled in this run

- **Signature encoding.** The reference implementation flagged HMAC hex-vs-base64 as unconfirmed.
  A real Databricks delivery passed `verify_signature()` — HMAC-SHA256, **hex**, over the raw body.
  This is now verified, not inferred from docs.
- **`REGISTERED_MODEL_CREATED` fires.** Creating a registered model with no versions delivered the
  event and created the Credo AI model, confirming this cookbook covers "model created in
  Databricks" and not just "first version registered".

## 3. Live run — setup steps

```
python setup_credo_source.py   → Source 'Databricks' already exists: <id>   (re-run: identical, idempotent)
python register_webhook.py list    → 1 pre-existing webhook
python register_webhook.py register → Registered webhook <id> -> <tunnel>/webhooks/databricks-mlflow
```

## 4. Live run — startup backfill

First start, against a registry of 14 models with 10 models already in the tenant:

```
seeded 10 existing Credo AI model(s)
backfill: registry=14 created=12 updated=2 failed=0
```

All 12 created models were confirmed to carry `source="Databricks"`.

Restart after the live event tests (registry had grown to 15):

```
seeded 23 existing Credo AI model(s)
backfill: registry=15 created=0 updated=15 failed=0
```

`created=0` on restart is the load-bearing result: seeding `MODEL_IDS` from `credo.list_models()`
is sufficient to prevent duplicates, which is why this cookbook carries no state file.

## 5. Live run — real webhook deliveries

A test model was created in the Databricks registry, given a version, and transitioned to
`Staging`. Deliveries arrived from Databricks' own egress IP and were handled as follows:

| Event delivered by Databricks      | Result                                                    |
| ---------------------------------- | --------------------------------------------------------- |
| `REGISTERED_MODEL_CREATED`         | `created` — new Credo AI model, `source="Databricks"`     |
| `MODEL_VERSION_CREATED`            | `updated` — same model, summary gains the version         |
| `MODEL_VERSION_TRANSITIONED_STAGE` | `updated` — summary gains `Transitioned None -> Staging.` |

Resulting record:

```json
{
  "name": "<test model>",
  "source": "Databricks",
  "summary": "Databricks MLflow model, version 2. Transitioned None -> Staging."
}
```

A second webhook registered against the same URL meant every event was delivered twice. Both
duplicates resolved to `updated` on the same model ID — unplanned, but a direct demonstration of
at-least-once delivery being handled idempotently.

## 6. Failure-mode checks (`TESTING.md` Step 6)

Exercised against the running server with the live Credo AI client behind it:

| Scenario                                    | Expected                         | Result                                                                                         |
| ------------------------------------------- | -------------------------------- | ---------------------------------------------------------------------------------------------- |
| Signed `MODEL_VERSION_CREATED`, known model | 200 `updated`, no duplicate      | ✅ 200 `updated`                                                                               |
| Signed `MODEL_VERSION_TRANSITIONED_STAGE`   | 200 `updated`, stage in summary  | ✅ 200 `updated`                                                                               |
| Unhandled event type (`COMMENT_CREATED`)    | 200 `ignored_event_type`         | ✅ 200 `ignored_event_type`                                                                    |
| Event with no `model_name`                  | 200 `ignored_missing_model_name` | ✅ 200, warning logged                                                                         |
| No `X-Databricks-Signature` header          | 403                              | ✅ 403                                                                                         |
| Wrong signature                             | 403                              | ✅ 403                                                                                         |
| Model created with no versions              | synced anyway                    | ✅ backfill created all 12 version-less registry models                                        |
| Unreadable registry record                  | skipped, run continues           | Not triggered live — `failed=0` in both runs; covered by an injected-fault test pre-live       |
| Credo AI 5xx                                | 500, so Databricks retries       | Not triggered live — the sandbox returned no 5xx; covered by a stubbed-transport test pre-live |
| Unity Catalog model registered              | no webhook fires at all          | Not triggered — no UC model was registered in this workspace                                   |

## 7. Repo gates

```
uv run ruff check .        → clean
uv run ruff format --check → clean
prettier --check '**/*.md' → clean
pre-commit run --all-files → all hooks Passed (incl. detect-secrets)
```

`.env` confirmed gitignored and never staged.

## 8. Cleanup

Both platforms were returned to their pre-run state where the run had created scaffolding:

- Webhook registered by this run — deleted via `register_webhook.py delete <id>`.
- Test registered model — versions archived, model deleted; registry back to its original 14.
- The pre-existing webhook from the earlier reference implementation was **left untouched** (it
  points at the same tunnel path and is now stale — worth removing separately).

## Coverage gaps / open items

- **The 13 models this run synced into the sandbox tenant were left in place.** They are genuine
  registry contents, and the Integration API surface used here has no delete; removing them is a
  manual step if the tenant should be reset.
- **No second-person code review.**
- **Unity Catalog behavior asserted from Databricks' documentation, not observed** — no UC model
  was registered to demonstrate that no webhook fires.
- **Credo AI 5xx and malformed-registry-record paths** verified with injected faults before the
  live run, not triggered against the live platform.
- **Not published.** The `credoai-integration-service` Guide page does not exist yet, so status
  remains 🧪 Preview in both README tables.
- Nothing in this repo has been committed or opened as a PR yet.
