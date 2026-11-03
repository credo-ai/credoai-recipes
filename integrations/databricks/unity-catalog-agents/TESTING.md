# Testing — Databricks Unity Catalog Agent Sync

Work through these in order. Step 1 needs a Databricks workspace with at least one deployed agent but
touches nothing in Credo AI. Step 2 creates a Source record in your Credo AI tenant. Step 3 reads
Databricks only. Steps 4–6 write to a Credo AI tenant — use a **sandbox tenant only**, and never point
a first run at production. Step 7 covers Genie agents, which have their own read-only check and then
write Agent entities to the same tenant.

---

## Step 1: Confirm you can see deployed agents, no Credo AI involved

```bash
cd server/python
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -c "
from databricks.sdk import WorkspaceClient
from dotenv import load_dotenv; load_dotenv('../../.env')
w = WorkspaceClient()
deployments = w.api_client.do('GET', '/api/2.0/agents/deployments').get('chain_deployments', [])
print(f'{len(deployments)} deployment(s) visible')
for d in deployments:
    print(' ', d['model_name'], d['model_version'], d['endpoint_name'])
"
```

This is the same call as `databricks.agents.list_deployments()`. If it returns nothing, no agent has
been deployed with `agents.deploy(...)` — a registered model alone does not appear here.

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
for name, deployments in main.list_deployed_agents(w).items():
    print(name, [(d['endpoint_name'], d['model_version']) for d in deployments])
"
```

Every name printed becomes a Credo AI use case on the next run. The count must match Step 1 unless
`DATABRICKS_CATALOG` / `DATABRICKS_SCHEMA` narrow it.

## Step 4: First sync against a **sandbox tenant only**

```bash
python main.py
```

Expected first cycle:

```text
seeded N existing Credo AI use case(s)
cycle: scanned=1 created=1 updated=0 unchanged=0 skipped=0 failed=0
```

`scanned` must match the count from Step 3. Leave it running for a few cycles — every cycle after the
first should read `created=0 updated=0 unchanged=<scanned>` until something actually changes in
Databricks. In Credo AI, open the use case and check its description and that `source` is
`Databricks`.

## Step 5: Confirm incremental pickup

With the poller still running, change something that shows up in the description — set an alias is the
quickest:

```bash
curl -X PUT -H "Authorization: Bearer $DATABRICKS_TOKEN" -H "Content-Type: application/json" \
  -d '{"version_num":1}' \
  "$DATABRICKS_HOST/api/2.1/unity-catalog/models/<catalog>.<schema>.<model>/aliases/champion"
```

The next cycle should report `updated=1` and patch exactly one use case. Then confirm in Credo AI that
the description lists `Aliases: champion=1`.

## Step 6: Failure-mode checks

| Scenario                                            | Expected behavior                                                                       |
| --------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `Databricks` source missing (skip Step 2)           | `422` on the first create, with the source name in the error; `failed=1`                |
| Stop and restart the poller                         | `created=0`, everything `unchanged` — never duplicated                                  |
| No agent deployed, or none in scope                 | `scanned=0`, no error                                                                   |
| Registered model that was never deployed            | Not listed, never reaches Credo AI                                                      |
| Deployment whose model was renamed or deleted       | `skipping <name>: deployed, but no longer in Unity Catalog`, `skipped=1`, cycle goes on |
| `DATABRICKS_SCHEMA` set, `DATABRICKS_CATALOG` blank | Refuses to start with a message naming both variables                                   |
| A model the token cannot read                       | Logged as `skipping <name>`, cycle continues, `failed=1`                                |
| Credo AI returns 5xx                                | Retried with backoff; if it still fails, `failed=1` and the next cycle retries          |
| Use case deleted in Credo AI while the poller runs  | Next change logs `is gone; recreating` and creates it again                             |
| Model deployed on a second endpoint                 | Still one use case, with a second `Deployment:` line                                    |

## Step 7: Genie agents

**Read-only check, no Credo AI.** The Genie API still calls these "spaces":

```bash
python -c "
import main
from databricks.sdk import WorkspaceClient
w = WorkspaceClient()
spaces = main.list_genie_spaces(w)
print(f'{len(spaces)} Genie agent(s) visible')
for s in spaces:
    print(' ', s.space_id, s.title)
"
```

Every title printed becomes a Credo AI **agent** (not a use case). `DATABRICKS_CATALOG` /
`DATABRICKS_SCHEMA` do not narrow this list.

**Sync.** Needs `CREDO_BACKEND_BASE_URL` in `.env`. Run `python main.py`; the first cycle should add:

```text
seeded 0 existing Credo AI agent(s)
genie cycle: scanned=1 created=1 updated=0 unchanged=0 skipped=0 failed=0
```

In Credo AI, open the agents list and check the name (the space title) and the description. Later
cycles read `created=0 updated=0 unchanged=<scanned>`. To confirm incremental pickup, edit the
agent's description in Credo AI; the next cycle logs `updated` and restores it. Edit the Genie
space's title or description in Databricks and the description follows on the next cycle.

| Scenario                                             | Expected behavior                                                               |
| ---------------------------------------------------- | ------------------------------------------------------------------------------- |
| Stop and restart the poller                          | `created=0`, everything `unchanged` — never duplicated                          |
| `CREDO_BACKEND_BASE_URL` unset                       | One warning at startup; deployed agents sync, no `genie cycle` line             |
| `CREDO_BACKEND_BASE_URL` unreachable                 | `Genie agents will not be synced: token exchange failed …`; deployed still sync |
| Tenant has no `agent` entity type                    | `Genie agents will not be synced: this tenant has no 'agent' entity type`       |
| Two Genie agents with the same title                 | First syncs, second logs `already synced this cycle`, `skipped=1`               |
| Genie agent renamed in Databricks                    | A second Credo AI agent is created under the new title                          |
| Agent deleted in Credo AI while the poller runs      | Next change logs `is gone; recreating` and creates it again                     |
| `DATABRICKS_CATALOG` set to a catalog with no agents | `cycle: scanned=0`, Genie agents still sync                                     |

## Cold-setup test

Hand this cookbook to someone with no Credo AI context and time them from `git clone` to a first
`created=` line. Target: under 15 minutes, no help needed.
