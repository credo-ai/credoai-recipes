# Databricks Unity Catalog → Credo AI Model Registry Sync

Keeps your Credo AI model inventory in step with the models registered in Databricks Unity Catalog.
On start it syncs every model it can see; after that it polls on an interval and re-syncs only the
models that actually changed.

- All code runs in your own environment — nothing is hosted by Credo AI
- Credentials stored as environment variables, never in source
- Works with any Unity Catalog metastore, on AWS, Azure, or GCP

---

> ### 🧪 Status: Preview
>
> **Unity Catalog has no webhooks.** Databricks states plainly that "webhooks are not available when
> you use Models in Unity Catalog" — there is no event to subscribe to, which is why this cookbook
> polls. See [Is there a real-time option?](#is-there-a-real-time-option) for what Databricks offers
> instead.
>
> If your models live in the **legacy Workspace Model Registry** instead, use the
> [MLflow Model Registry cookbook](../mlflow-model-registry), which receives webhooks.
>
> The `databricks-sdk` is Beta, which is why `requirements.txt` pins it to an exact version.

---

## How it works

```text
                    ┌──────────────────────┐
  every N seconds   │  Unity Catalog       │
  ┌────────────────▶│  registered_models   │  list all models (cheap, one paginated call)
  │                 └──────────┬───────────┘
  │                            │ updated_at > watermark?
  │                            ▼
  │                 ┌──────────────────────┐
  │                 │  per changed model   │  registered_models.get(include_aliases=True)
  │                 │  only                │  model_versions.list(full_name)
  │                 └──────────┬───────────┘
  │                            ▼
  │                 ┌──────────────────────┐
  └─────────────────│  Credo AI            │  POST /models  (new)
                    │  Integration API     │  PATCH /models/{id}  (already known)
                    └──────────────────────┘
```

Unity Catalog offers no server-side change filter — no `updated_since`, no `order_by`, no timestamp
predicate — so every cycle has to enumerate every model. That part is cheap: one paginated call
covers the whole metastore. The expensive part is the per-model detail (two calls each), and that is
what the watermark skips. A steady-state cycle with nothing changed makes exactly one API call and
writes nothing.

The model name in Credo AI is the **fully qualified** `catalog.schema.model`. Unity Catalog names
are only unique within a schema, so `ml.prod.churn` and `ml.dev.churn` would otherwise collide.

## Is there a real-time option?

Not a webhook — but Databricks does have an event-driven replacement. Summary of every mechanism
that exists today:

| Mechanism                                                                                            | Works for UC models?                                                                                                                                                                         |
| ---------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [MLflow registry webhooks](https://docs.databricks.com/aws/en/mlflow/model-registry-webhooks)        | ❌ No. "Webhooks are not available when you use Models in Unity Catalog." Legacy Workspace Model Registry only.                                                                              |
| [MLflow 3.x OSS webhooks](https://mlflow.org/docs/latest/self-hosting/webhooks/)                     | ❌ Not on Databricks. "Only OSS MLflow supports webhooks. Databricks or other managed MLflow services may not support this feature." Needs a self-hosted tracking server with a SQL backend. |
| [Lakeflow Jobs model update triggers](https://docs.databricks.com/aws/en/jobs/model-update-triggers) | ✅ **Yes — this is the closest thing to a webhook.** Beta.                                                                                                                                   |
| [`system.access.audit`](https://docs.databricks.com/aws/en/admin/system-tables/)                     | ⚠️ Reconciliation only. "No support for real-time monitoring. Data is updated throughout the day."                                                                                           |

Databricks' own [migration guide](https://docs.databricks.com/aws/en/machine-learning/manage-model-lifecycle/migrate-to-uc)
names the trigger as the replacement: _"If you had webhooks set up for events, you can implement
them in Unity Catalog as model event job triggers."_ It fires `MODEL_CREATED`,
`MODEL_VERSION_READY`, and `MODEL_ALIAS_SET`, polls roughly once a minute, and can be scoped to a
model, a schema, or the whole metastore.

**This cookbook deliberately does not ship one.** Model update triggers are Beta, capped at 100 per
workspace, and require a job plus a notebook task to forward the event — a moving target to pin a
public cookbook to. If you want sub-minute latency, create a trigger whose job POSTs the injected
parameters to a service of your own, and keep this poller running alongside it for reconciliation.
For most governance workflows, a five-minute poll is well inside the useful window.

## Prerequisites

| Item                             | Where to get it                                                                |
| -------------------------------- | ------------------------------------------------------------------------------ |
| Credo AI API key                 | Credo AI Governance App → Settings → Integrations                              |
| Credo AI tenant name             | The subdomain you sign in at                                                   |
| Credo AI backend URL             | Ask your Credo AI representative (used once, in Step 2)                        |
| Databricks workspace URL         | The host you sign in at, e.g. `https://dbc-1234abcd-5678.cloud.databricks.com` |
| Databricks personal access token | Databricks → Settings → Developer → Access tokens                              |
| Unity Catalog read access        | `USE CATALOG`, `USE SCHEMA`, and `EXECUTE` on the models you want synced       |
| Python 3.10+                     | Anything that runs a long-lived process — a VM, a container, a scheduled task  |

## Step 1: Set your credentials

```bash
cd server/python
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../../.env.example ../../.env
```

Fill in `.env`. It is gitignored — never commit it.

## Step 2: Create the `Databricks` source — one time per tenant

`source` on a Credo AI model is not free text. It resolves against a Source record that has to exist
first, so without this every model create returns **422**.

```bash
python setup_credo_source.py
```

Idempotent — safe to re-run, and it prints whether it created the source or found it already there.
If you already ran this for the [MLflow Model Registry cookbook](../mlflow-model-registry), it is
done; both use the same `Databricks` source.

## Step 3: Choose what to sync

By default the poller syncs the **whole metastore, minus the `system` and `__databricks_internal`
catalogs**. That exclusion matters more than it sounds: `system.ai` holds Databricks' own built-in
foundation models, and on a stock workspace that is **94 of 96** registered models. Syncing them
would bury your real models under ninety-odd records nobody governs.

To narrow further, set both of these in `.env`:

```bash
DATABRICKS_CATALOG=ml
DATABRICKS_SCHEMA=prod
```

Both or neither — Unity Catalog rejects a catalog without a schema, so there is no way to scope to
an entire catalog in one call. If you need several schemas, leave these blank and let the default
metastore-wide scan cover them.

## Step 4: Run the poller

```bash
python main.py
```

You should see a full sweep on the first cycle, then quiet cycles:

```text
seeded 23 existing Credo AI model(s)
cycle: scanned=3 changed=3 created=2 updated=1 failed=0 full_sweep=True
cycle: scanned=3 changed=0 created=0 updated=0 failed=0 full_sweep=False
```

There is no port to expose and nothing to tunnel — this is not a webhook receiver, it is a
long-running process that reaches out to both Databricks and Credo AI. Run it as a systemd service,
a container, or a scheduled task, whatever fits how you run long-lived internal tooling today.

Restarting is safe. The poller seeds itself from what Credo AI already has, so a restart re-syncs
rather than duplicating — there is no state file to lose.

## Step 5: Field mapping

| Credo AI Field | Unity Catalog Source                 | Notes                                                                                                |
| -------------- | ------------------------------------ | ---------------------------------------------------------------------------------------------------- |
| `name`         | `full_name`                          | Fully qualified `catalog.schema.model` — UC names are only unique within a schema                    |
| `summary`      | latest version number + aliases      | Composed string, e.g. `Databricks Unity Catalog model, version 8. Aliases: champion=8.`              |
| `source`       | constant `Databricks`                | Set on create only; an existing Credo AI model keeps whatever source it already had                  |
| —              | `version`, `run_id`, `model_metrics` | **Nowhere to go.** A Credo AI Model has no version concept, so these live in `summary` or not at all |

Unity Catalog replaced the legacy registry's **stages** with **aliases**, so there is no
`Staging → Production` transition to record — the alias-to-version mapping is the closest
equivalent.

## Troubleshooting

| Symptom                                                     | Likely Cause                                                                 | Fix                                                                               |
| ----------------------------------------------------------- | ---------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `422` on model create                                       | The `Databricks` source does not exist                                       | Run Step 2                                                                        |
| `429 REQUEST_LIMIT_EXCEEDED` / `UC_REQUEST_LIMIT_EXCEEDED`  | Too many Unity Catalog calls                                                 | Raise `POLL_INTERVAL_SECONDS` and `REQUEST_DELAY_SECONDS`, or scope to one schema |
| Tenant filled with `system.ai.*` models                     | `DATABRICKS_CATALOG` was pointed at `system`                                 | Clear both scope vars; the default exclusion only applies metastore-wide          |
| `DATABRICKS_CATALOG and DATABRICKS_SCHEMA must both be set` | Only one of the pair is filled in                                            | Set both, or leave both blank                                                     |
| A model's edited version comment never syncs                | Editing a _version's_ metadata does not bump the parent model's `updated_at` | Wait for the next full sweep, or lower `FULL_SWEEP_EVERY_N_CYCLES`                |
| Nothing syncs, no error                                     | The token lacks `USE CATALOG` / `USE SCHEMA`                                 | UC silently omits models you cannot browse — check grants                         |
| A model exists in Credo AI with the wrong `source`          | It was created by something else before this poller ran                      | Expected: the poller only patches `summary` on models it did not create           |

Still stuck? Slack: `#credo-ai-integrations` | Support: support.credo.ai

---

## Full guide

Not yet published — pending Cookbook Validation Process sign-off.
