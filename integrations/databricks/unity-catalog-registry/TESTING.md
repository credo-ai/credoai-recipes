# Testing — Databricks Unity Catalog Registry Sync

> Steps 1–2 need a Databricks workspace but touch nothing in Credo AI. Step 3 needs no Databricks
> workspace at all. Steps 4–6 write to a Credo AI tenant — use a **sandbox tenant only**. Models
> synced into Credo AI cannot be deleted through the Integration API, so never point a first run at
> production.

## Step 1: Confirm Unity Catalog access, no Credo AI involved

```bash
cd server/python
python -c "
from databricks.sdk import WorkspaceClient
from dotenv import load_dotenv; load_dotenv('../../.env')
w = WorkspaceClient()
models = list(w.registered_models.list())
print(f'{len(models)} model(s) visible')
for m in models[:10]:
    print(' ', m.full_name, m.updated_at)
"
```

If this returns nothing, the token lacks `USE CATALOG` / `USE SCHEMA` — Unity Catalog omits models
you cannot browse rather than erroring.

Expect the list to be dominated by `system.ai.*` entries. That is normal, and it is exactly what the
default catalog exclusion removes.

## Step 2: Confirm the `Databricks` source exists

```bash
python setup_credo_source.py
```

Expected: `Source 'Databricks' already exists: <id>`, or `Created source 'Databricks': <id>` the
first time. Run it twice — the second run must not create a duplicate.

## Step 3: Check your scope before you write anything

Confirm what _would_ sync, without touching Credo AI:

```bash
python -c "
import main
from databricks.sdk import WorkspaceClient
w = WorkspaceClient()
for m in main.list_models_scoped(w):
    print(m.full_name)
"
```

Every name printed becomes a Credo AI model on the next run. If you see `system.ai.*` entries here,
your scope is wrong — stop and fix it before Step 4.

## Step 4: First sync against a **sandbox tenant only**

```bash
python main.py
```

Expected first cycle:

```text
seeded N existing Credo AI model(s)
cycle: scanned=3 changed=3 created=2 updated=1 failed=0 full_sweep=True
```

`scanned` must match the count from Step 3. Leave it running for a few cycles — every cycle after
the first should read `changed=0 created=0 updated=0` until something actually changes in
Databricks.

## Step 5: Confirm incremental pickup

With the poller still running, change something in Databricks — set an alias is the quickest:

```bash
curl -X PUT -H "Authorization: Bearer $DATABRICKS_TOKEN" -H "Content-Type: application/json" \
  -d '{"version_num":1}' \
  "$DATABRICKS_HOST/api/2.1/unity-catalog/models/<catalog>.<schema>.<model>/aliases/champion"
```

The next cycle should report `changed=1` and patch exactly one model. Then confirm in Credo AI that
the model's summary lists the alias, and that `source` is `Databricks`.

## Step 6: Failure-mode checks

| Scenario                                            | Expected behavior                                                              |
| --------------------------------------------------- | ------------------------------------------------------------------------------ |
| `Databricks` source missing (skip Step 2)           | `422` on the first create, with the source name in the error                   |
| Stop and restart the poller                         | `created=0`, everything `updated` — never duplicated                           |
| Default scope, stock workspace                      | Zero `system.ai.*` models reach Credo AI                                       |
| `DATABRICKS_CATALOG` set, `DATABRICKS_SCHEMA` blank | Refuses to start with a message naming both variables                          |
| A model the token cannot read                       | Logged as `skipping <name>`, cycle continues, `failed=1`                       |
| Credo AI returns 5xx                                | Retried with backoff; if it still fails the watermark does not advance past it |
| Edit a _version's_ comment                          | Not picked up by the watermark — appears on the next `full_sweep=True` cycle   |
| New model version registered                        | Picked up on the next cycle; the parent model's `updated_at` bumps             |

That last pair is the one worth understanding: creating a version, setting an alias, and editing
model-level metadata all bump the registered model's `updated_at`, so the watermark catches them.
Editing a _version's_ comment or tags does not. `FULL_SWEEP_EVERY_N_CYCLES` exists for that gap.

## Cold-setup test

Hand this cookbook to someone with no Credo AI context and time them from `git clone` to a first
`full_sweep=True` line. Target: under 15 minutes, no help needed.
