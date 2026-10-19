# Testing — Databricks MLflow Model Registry Sync

Work through these in order. Step 1 touches Databricks only and Step 3 needs no Databricks
workspace at all. Steps 2–4 write to a Credo AI tenant — use a **sandbox tenant only**. Steps 1 and
4 need a **legacy** Workspace Model Registry — if your workspace only offers Unity Catalog, you
cannot test the webhook path at all, because Databricks does not emit registry webhooks for UC
models.

---

## Step 1: Confirm Databricks access, no Credo AI involved

```bash
cd server/python
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -c "
from databricks.sdk import WorkspaceClient
from dotenv import load_dotenv; load_dotenv('../../.env')
for m in WorkspaceClient().model_registry.list_models():
    print(m.name, [v.version for v in (m.latest_versions or [])])
"
```

Expected: your registered models. An empty list means the registry is empty _or_ your models are
in Unity Catalog — check the Models page in the workspace before going further.

---

## Step 2: Confirm the `Databricks` source exists

```bash
python setup_credo_source.py
```

Expected on a second run: `Source 'Databricks' already exists: <id>`. If the first run fails, stop
here — every model create will 422 until it succeeds.

---

## Step 3: Simulate a webhook delivery, no Databricks needed

Leave `DATABRICKS_WEBHOOK_SECRET` blank, set `ALLOW_UNSIGNED_WEBHOOKS=true`, start `python main.py`, then:

```bash
curl -X POST http://localhost:5000/webhooks/databricks-mlflow \
  -H "Content-Type: application/json" \
  -d '{"event":"MODEL_VERSION_CREATED","model_name":"testing-cookbook","version":"1"}'
```

Expected: `"action":"created"`. Repeat the exact same command — expected:
`"action":"ignored_stale_event"` with no `model_id`. A redelivered `MODEL_VERSION_CREATED` carries
nothing newer than what Credo AI already holds, so it is dropped without a write.

Now send the same command with `"version":"2"` — expected: `"action":"updated"` with the **same**
`model_id` as the first call. Two different ids means the no-duplicates logic is broken.

To exercise signature verification, set `DATABRICKS_WEBHOOK_SECRET=shh` (and unset `ALLOW_UNSIGNED_WEBHOOKS`), restart, and sign the body:

```bash
BODY='{"event":"MODEL_VERSION_CREATED","model_name":"testing-cookbook","version":"2"}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "shh" -hex | awk '{print $NF}')
curl -X POST http://localhost:5000/webhooks/databricks-mlflow \
  -H "Content-Type: application/json" -H "X-Databricks-Signature: $SIG" -d "$BODY"
```

Expected: `200`. Drop the header and expect `403`.

---

## Step 4: Backfill against a **lab/sandbox tenant only** — never production

Start `python main.py` and read the startup lines:

```text
INFO [databricks-bridge] seeded N existing Credo AI model(s); ignored N from other sources
INFO [databricks-bridge] backfill: registry=N created=N updated=0 failed=0
```

Stop it and start it again. Expected on the second run: `created=0`, `updated=N`. A non-zero
`created` on the second run means models are being duplicated — stop and investigate before
pointing this at anything real.

---

## Step 5: Confirm in Credo AI

In the Credo AI Governance App, open the model registry and confirm:

- Each Databricks model appears exactly once
- `source` reads `Databricks`
- The summary names the version, and the stage for anything you transitioned

---

## Step 6: Failure-mode checks

| Scenario                                                        | Expected behavior                                                                                                                |
| --------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `Databricks` source not created yet                             | `200` with `"action":"failed_not_retryable"`; the log names the failing call                                                     |
| Restart the server                                              | Backfill reports `created=0`; no duplicate models appear                                                                         |
| Credo AI already has a same-name model from another source      | Counted under `ignored … from other sources`; its summary is untouched                                                           |
| Missing or wrong `X-Databricks-Signature`                       | `403`, nothing written to Credo AI                                                                                               |
| Unhandled event (`COMMENT_CREATED`)                             | `200` with `"action":"ignored_event_type"` — not an error, not a retry                                                           |
| Event with no `model_name`                                      | `200` with `"action":"ignored_missing_model_name"`                                                                               |
| Body isn't valid JSON, or isn't an object                       | `200` with `"action":"ignored_malformed_body"`                                                                                   |
| Older or repeated version than already synced                   | `200` with `"action":"ignored_stale_event"`, summary unchanged                                                                   |
| Credo AI model deleted after startup                            | Next event recreates it (`"action":"created"`) instead of failing                                                                |
| No `DATABRICKS_WEBHOOK_SECRET` and no `ALLOW_UNSIGNED_WEBHOOKS` | Server refuses to start                                                                                                          |
| Credo AI returns 5xx                                            | Retried 3× with backoff, then `500` so Databricks can retry the delivery; a permanent 4xx (not 401/403/408/429) is `200` instead |
| A registry model can't be read during backfill                  | That one is logged and skipped, `failed` increments, the rest still sync                                                         |
| Model registered in Unity Catalog                               | **No delivery at all** — expected; UC emits no registry webhooks                                                                 |

---

## Cold-setup test

Hand this cookbook to someone on your team who has never seen it. Time how long it takes them to
get from `git clone` to a model appearing in Credo AI, using only `README.md` and this file. Log
every point of confusion.
