# Databricks → Credo AI MLflow Model Registry Sync

A webhook receiver that registers models in Credo AI when they are created or updated in the
Databricks MLflow Model Registry. On every start it also walks the registry and syncs everything
already there, so a first run — or a restart after downtime — trues up the models Credo AI missed
rather than only catching changes from that moment forward.

- All code runs in your own environment, wherever it can reach your Databricks workspace
- Credentials stored as environment variables, never in source
- Works with any legacy Workspace Model Registry, on AWS, Azure, or GCP

---

> ### 🧪 Status: Preview — legacy Workspace Model Registry only
>
> Registry webhooks are a **Workspace (legacy) Model Registry** feature. Databricks states plainly
> that "webhooks are not available when you use Models in Unity Catalog", so **this cookbook will
> not see UC models at all** — there is no event to subscribe to. Databricks also plans to
> deprecate the Workspace Model Registry in favor of Unity Catalog.
>
> If your models live in Unity Catalog, use the
> [Unity Catalog cookbook](../unity-catalog-registry), which polls for changes instead of
> subscribing to them.
>
> The `databricks-sdk` registry-webhook methods are Public Preview and the SDK itself is Beta,
> which is why `requirements.txt` pins it to an exact version.

---

## How it works

```text
Databricks legacy Workspace Model Registry
   │
   │  ① startup backfill — every model already in the registry
   │     model_registry.list_models()
   ▼
this server  (create or update, keyed by model name)
   ▲
   │  ② steady state — one registry webhook delivery per change
   │     POST {PUBLIC_BASE_URL}/webhooks/databricks-mlflow
   │     events: REGISTERED_MODEL_CREATED, MODEL_VERSION_CREATED,
   │             MODEL_VERSION_TRANSITIONED_STAGE
   │
   ▼
Credo AI  →  POST /models  (first time this name is seen)
             PATCH /models/{id}  (every time after)
```

A Credo AI Model has no version field, so the second event for a model name **updates** the
existing Model rather than creating a duplicate. At startup the server lists the models Credo AI
already has and matches on name, which is what makes the backfill safe to re-run on every restart.

---

## Prerequisites

| Item                                  | Where to get it                                                                                     |
| ------------------------------------- | --------------------------------------------------------------------------------------------------- |
| A **legacy** Workspace Model Registry | Databricks workspace → Models. If the page sends you to Unity Catalog, this cookbook does not apply |
| Credo AI API key                      | Credo AI Governance App → Settings → Integrations                                                   |
| Credo AI tenant name                  | The subdomain of your Credo AI URL                                                                  |
| Integration Service base URL          | Credo AI Governance App → Settings → Integrations                                                   |
| Backend base URL                      | Ask your Credo AI representative — needed once, to create the source                                |
| Databricks host + PAT                 | Databricks workspace → Settings → Developer → Access tokens                                         |
| A public HTTPS URL                    | [ngrok](https://ngrok.com) for local testing, or your own ingress                                   |
| Python 3.9+                           | Any environment that can reach both Databricks and Credo AI                                         |

---

## Step 1: Set your credentials

```bash
cp .env.example .env
```

Fill in the Credo AI and Databricks values. `DATABRICKS_HOST` and `DATABRICKS_TOKEN` are read by
the Databricks SDK's unified auth, so a configured CLI profile or an OAuth service principal works
instead of a PAT if you prefer.

---

## Step 2: Create the `Databricks` source — one time per tenant

**Do this before starting the server.** Credo AI's `source` field is not free text: it resolves
against a Source record that must already exist, so every model create returns **422** until this
has been run once.

```bash
cd server/python
pip install -r requirements.txt
python setup_credo_source.py
```

Expected: `Created source 'Databricks': <id>`, or `Source 'Databricks' already exists: <id>` on a
re-run. It is idempotent.

---

## Step 3: Run the server

```bash
python main.py
```

On startup it lists the models Credo AI already has, then backfills the Databricks registry:

```text
INFO [databricks-bridge] seeded 12 existing Credo AI model(s)
INFO [databricks-bridge] backfill: registry=42 created=7 updated=35 failed=0
```

Restart it and `created` drops to `0` — everything is already there and gets updated instead. Run
it as a systemd service, a container, or a scheduled task. In a container, set `HOST=0.0.0.0`;
the default binds to localhost so you have to put a tunnel or ingress in front of it deliberately.

---

## Step 4: Register the webhook

With the server reachable at `PUBLIC_BASE_URL`:

```bash
python register_webhook.py register
python register_webhook.py list
python register_webhook.py delete <webhook-id>
```

Registration is separate from the server on purpose — the webhook outlives any one process. Free
ngrok URLs rotate on restart, so re-run `register` when yours changes and `delete` the stale one.

Set `DATABRICKS_MODEL_NAME` to scope the webhook to a single model. Note that
`REGISTERED_MODEL_CREATED` is registry-wide only, so a scoped webhook won't see a brand-new model
until its first version lands.

---

## Step 5: Test locally

```bash
curl -X POST http://localhost:5000/webhooks/databricks-mlflow \
  -H "Content-Type: application/json" \
  -d '{
    "event": "MODEL_VERSION_CREATED",
    "webhook_id": "wh-test",
    "event_timestamp": 1725984000000,
    "model_name": "credit-risk-xgboost",
    "version": "4",
    "text": "Registered model version created."
  }'
```

Expected: `{"ok":true,"action":"created","model_id":"...","model_name":"credit-risk-xgboost","version":"4"}`.
Send it again and `action` becomes `updated` — that is the no-duplicates behavior. Leave
`DATABRICKS_WEBHOOK_SECRET` blank for this test, or sign the body (see [TESTING.md](./TESTING.md)).

---

## Step 6: Field mapping

| Credo AI Field | Databricks Source                    | Notes                                                                         |
| -------------- | ------------------------------------ | ----------------------------------------------------------------------------- |
| `name`         | `model_name`                         | The match key. Renaming a model in Databricks creates a second Credo AI Model |
| `summary`      | `version`, `to_stage` / `from_stage` | Composed sentence — see `build_summary` in `main.py`                          |
| `source`       | constant `"Databricks"`              | Must exist as a Source record first (Step 2)                                  |

A Credo AI Model accepts only `name`, `summary`, `source`, `questionnaire_ids`, and `status`. The
version number, stage, and MLflow run id have **nowhere else to go**, which is why the summary
string carries them. Unknown fields are silently dropped rather than rejected, so sending extra
data fails quietly — don't.

---

## Troubleshooting

| Symptom                                          | Likely Cause                                            | Fix                                                                       |
| ------------------------------------------------ | ------------------------------------------------------- | ------------------------------------------------------------------------- |
| `422` on every model create                      | The `Databricks` source doesn't exist                   | Run `python setup_credo_source.py` (Step 2)                               |
| Nothing arrives when a model changes             | Models are in Unity Catalog, which emits no webhooks    | Use the [Unity Catalog cookbook](../unity-catalog-registry) instead       |
| Webhook registered, still no deliveries          | `PUBLIC_BASE_URL` is stale — ngrok rotated it           | Re-run `register_webhook.py register`, then `delete` the old webhook      |
| `403 Bad or missing X-Databricks-Signature`      | `DATABRICKS_WEBHOOK_SECRET` doesn't match the webhook's | Re-register the webhook, or clear the variable for an unsigned local test |
| Duplicate models in Credo AI                     | The model was renamed in Databricks                     | Models match by name only; merge them in Credo AI                         |
| A new model doesn't sync until its first version | The webhook is scoped via `DATABRICKS_MODEL_NAME`       | Leave it blank so `REGISTERED_MODEL_CREATED` is included                  |
| `401` after startup                              | Wrong `CREDO_API_KEY` or `CREDO_TENANT`                 | The token is refreshed once, then gives up — check both values            |

Still stuck? Slack: `#credo-ai-integrations` | Support: support.credo.ai

---

## Full guide

Not yet published — pending Cookbook Validation Process sign-off.
