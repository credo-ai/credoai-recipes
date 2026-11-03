# Databricks Unity Catalog → Credo AI Agent Sync

Keeps Credo AI in step with the agents in your Databricks workspace. Each poll syncs two kinds:

- **Deployed agents** (Databricks Agent Framework, in Unity Catalog) → one Credo AI **use case** each
- **Genie agents** (Databricks Genie spaces) → one Credo AI **agent** each

- All code runs in your own environment — nothing is hosted by Credo AI
- Credentials stored as environment variables, never in source
- Works with any Unity Catalog metastore, on AWS, Azure, or GCP

---

> ### 🧪 Status: Preview
>
> **Two kinds of agent are synced: deployed Agent Framework agents and Genie agents.** Unity Catalog
> has no "agent" object — an agent is a registered model. The list of _deployed_ agents
> (`databricks.agents.list_deployments()`) is what separates agents from ordinary models, so an agent
> that is registered but not yet deployed is not synced. See [Limitations](#limitations).
>
> **Genie agents go through Credo AI's private v2 API.** Credo AI has an Agent entity, but the public
> Integration API cannot create one yet. Deployed agents still become use cases through the public
> API. When the public API supports agents, the v2 client in `main.py` (`CredoAgentClient`) is the
> only part to replace.
>
> **This syncs Unity Catalog only.** Agents in the legacy Workspace Model Registry are not read.
>
> To sync plain (non-agent) models into the Credo AI model registry, use the
> [Unity Catalog model registry cookbook](../unity-catalog-registry). Models become Credo AI Models and
> agents become Credo AI use cases, so running both creates separate records. The model cookbook does
> not exclude agents: a deployed agent's registered model also appears there as a Credo AI Model.
>
> **Unity Catalog has no webhooks**, so this cookbook polls. The `databricks-sdk` is Beta, which is
> why `requirements.txt` pins it to an exact version.

---

## How it works

```text
                    ┌──────────────────────┐
  every N seconds   │  Databricks          │
  ┌────────────────▶│  Agent Framework     │  GET /api/2.0/agents/deployments
  │                 │  deployments         │  (what list_deployments() calls)
  │                 └──────────┬───────────┘
  │                            │ one entry per deployed agent
  │                            ▼
  │                 ┌──────────────────────┐
  │                 │  Unity Catalog       │  registered_models.get(include_aliases=True)
  │                 │  per agent           │  model_versions.list(full_name)
  │                 └──────────┬───────────┘
  │                            ▼
  │                 ┌──────────────────────┐
  └─────────────────│  Credo AI            │  POST /use_cases  (new)
                    │  Integration API     │  PATCH /use_cases/{id}  (description changed)
                    └──────────────────────┘
```

Genie agents follow the same cycle on a shorter path:

```text
  Databricks  GET /api/2.0/genie/spaces  (paged)  →  GET /api/2.0/genie/spaces/{id}
      │
      ▼
  Credo AI private v2 API   POST /api/v2/{tenant}/entities  (new Agent)
                            PATCH /api/v2/{tenant}/entities/{id}  (description changed)
```

The deployment list has no change timestamp and no server-side filter, so every cycle re-reads every
agent (one list call, then two Unity Catalog calls per agent) and compares the result with what Credo
AI already holds. An agent whose description has not changed makes no write.

The use case name in Credo AI is the **fully qualified** `catalog.schema.model`. Unity Catalog names
are only unique within a schema, so `ml.prod.support_agent` and `ml.dev.support_agent` would
otherwise collide.

A model can be deployed more than once (several endpoints or versions). Those collapse into a single
use case that lists each deployment.

The Credo AI agent name for a Genie agent is the Genie space **title**, and its description carries the
space ID. Genie spaces have no catalog or schema, so `DATABRICKS_CATALOG` / `DATABRICKS_SCHEMA` do not
apply to them; every Genie agent the token can see is synced. The two kinds are synced independently: a
failure on one side is logged and does not stop the other.

## Prerequisites

| Item                             | Where to get it                                                                    |
| -------------------------------- | ---------------------------------------------------------------------------------- |
| Credo AI API key                 | Credo AI Governance App → Settings → Integrations                                  |
| Credo AI tenant name             | The subdomain you sign in at                                                       |
| Databricks workspace URL         | The host you sign in at, e.g. `https://dbc-1234abcd-5678.cloud.databricks.com`     |
| Databricks personal access token | Databricks → Settings → Developer → Access tokens                                  |
| Unity Catalog read access        | `USE CATALOG`, `USE SCHEMA`, and `EXECUTE` on the agents you want synced           |
| A deployed agent                 | Deployed with `databricks.agents.deploy(...)` — it appears under Serving endpoints |
| A Genie agent (optional)         | The token's owner needs at least CAN VIEW on it                                    |
| Agent entity type in Credo AI    | Needed for Genie agents. The API key's role must be able to create and edit agents |
| Python 3.10+                     | Anything that runs a long-lived process — a VM, a container, a scheduled task      |

## Step 1: Set your credentials

From the **cookbook root** — the directory holding this README:

```bash
cd integrations/databricks/unity-catalog-agents
cp .env.example .env
# Edit .env — CREDO_API_KEY, CREDO_TENANT, CREDO_BACKEND_BASE_URL, DATABRICKS_HOST, DATABRICKS_TOKEN
```

`.env` belongs here, not in `server/python`. The poller and its scripts look for it at the cookbook
root no matter which directory you run them from, so a scheduled job does not depend on where it
starts. It is gitignored — never commit it. The Credo AI URLs in `.env.example` already point at
`https://api.credo.ai`; the tenant is set by `CREDO_TENANT`, not by the URL.

## Step 2: Create the `Databricks` source — one time per tenant

`source` on a Credo AI use case is not free text. It resolves against a Source record that has to
exist first, so without this every use case create returns **422**. This applies to deployed-agent
use cases only: Genie agents are Agent entities, which have no `source`, so they do not need it.

```bash
cd server/python
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python setup_credo_source.py
```

> **Use a virtualenv.** Without one, `pip` and `python` can resolve to different interpreters and the
> install lands somewhere the poller cannot see — the failure looks like
> `ModuleNotFoundError: No module named 'dotenv'` straight after a successful install.
>
> On Debian and Ubuntu, `python3 -m venv` can produce a virtualenv with no `pip` inside. If that
> happens, either `sudo apt install python3-venv` or use [uv](https://docs.astral.sh/uv/):
> `uv venv .venv && source .venv/bin/activate && uv pip install -r requirements.txt`.

Idempotent — safe to re-run, and it prints whether it created the source or found it already there.
If you already ran this for either of the other Databricks cookbooks, it is done; all of them use the
same `Databricks` source.

If your Credo AI deployment serves the private API from a different host than `api.credo.ai`, pass
it explicitly: `python setup_credo_source.py --base-host https://your-admin-host`.

## Step 3: Choose what to sync

By default the poller syncs **every agent deployed in the workspace**, plus every Genie agent. To
narrow the deployed agents, set these in `.env`. They do not affect Genie agents:

```bash
DATABRICKS_CATALOG=ml        # only agents in this catalog
DATABRICKS_SCHEMA=prod       # optional: only this schema within the catalog
```

A catalog on its own is valid, unlike in the model cookbook: the deployment list is workspace-wide
and filtered by this poller, not by a Unity Catalog call. A schema needs its catalog.

## Step 4: Run the poller

```bash
python main.py
```

You should see the same line each cycle, with `created` only on the first:

```text
seeded 4 existing Credo AI use case(s)
seeded 0 existing Credo AI agent(s)
cycle: scanned=2 created=2 updated=0 unchanged=0 skipped=0 failed=0
genie cycle: scanned=1 created=1 updated=0 unchanged=0 skipped=0 failed=0
cycle: scanned=2 created=0 updated=0 unchanged=2 skipped=0 failed=0
genie cycle: scanned=1 created=0 updated=0 unchanged=1 skipped=0 failed=0
```

`seeded` counts every use case (or agent) already in the tenant, whatever created it. `cycle` is the
deployed agents and `genie cycle` is the Genie agents. `skipped` counts deployed agents whose model no
longer exists in Unity Catalog, or Genie agents whose title repeats one already synced this cycle.

There is no port to expose and nothing to tunnel — this is not a webhook receiver, it is a
long-running process that reaches out to both Databricks and Credo AI. Run it as a systemd service,
a container, or a scheduled task, whatever fits how you run long-lived internal tooling today.

Restarting is safe. The poller seeds itself from what Credo AI already has, so a restart re-syncs
rather than duplicating — there is no state file to lose.

## Field mapping

| Credo AI Field | Databricks Source                      | Notes                                                                             |
| -------------- | -------------------------------------- | --------------------------------------------------------------------------------- |
| `name`         | model `full_name`                      | Fully qualified `catalog.schema.model` — UC names are only unique within a schema |
| `description`  | model, deployments, versions, aliases  | Composed text, see below. This is the only field updated after create             |
| `source`       | constant `Databricks`                  | Set on create only                                                                |
| —              | run ids, metrics, tools, system prompt | **Not read.** Only registry and deployment metadata is recorded                   |

Genie agents (Credo AI **agent**, not use case):

| Credo AI Field | Databricks Source | Notes                                                                        |
| -------------- | ----------------- | ---------------------------------------------------------------------------- |
| `name`         | Genie space title | Unique per entity type, case-insensitive                                     |
| `description`  | space metadata    | Composed text, see below. This is the only field updated after create        |
| —              | tables, SQL, chat | **Not read.** The space's `serialized_space` (needs CAN EDIT) is not fetched |

The description is plain text, rebuilt on every cycle:

```text
Databricks Unity Catalog agent.
Model: ml.prod.support_agent
Description: Answers tier-1 support questions        (only if the model has a comment)
Owner: someone@example.com
Deployment: endpoint agents_support_agent, version 3 (https://<workspace>/ml/endpoints/agents_support_agent)
Latest registered version: 4
Aliases: champion=3                                  (only if any alias is set)
```

The Genie agent description, rebuilt the same way:

```text
Databricks Genie agent.
Space ID: 01f1d7ce213f10b289a1ddb15ec0e73f
Description: Helps teams understand integration development   (only if the space has one)
Location: /Users/someone@example.com
SQL warehouse: 56a71f921352100d
Last updated: 2026-11-03T21:33:29.903Z
Link: https://<workspace>/genie/rooms/01f1d7ce213f10b289a1ddb15ec0e73f
```

## Troubleshooting

| Symptom                                                     | Likely Cause                                                                                                    | Fix                                                                                     |
| ----------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `422` on use case create                                    | The `Databricks` source does not exist                                                                          | Run Step 2                                                                              |
| `scanned=0`, no error                                       | No agent is deployed, or none is in scope                                                                       | Check Serving endpoints for an `agents_…` endpoint; clear `DATABRICKS_CATALOG`/`SCHEMA` |
| An agent is registered but never appears                    | It was never deployed with `agents.deploy`                                                                      | Deploy it — see [Limitations](#limitations)                                             |
| `skipping <name>: deployed, but no longer in Unity Catalog` | The model was renamed or deleted; its endpoint still points at the old name                                     | Redeploy under the new name, or delete the stale endpoint                               |
| `429 REQUEST_LIMIT_EXCEEDED`                                | Too many Unity Catalog calls                                                                                    | Raise `POLL_INTERVAL_SECONDS` and `REQUEST_DELAY_SECONDS`, or narrow the scope          |
| `DATABRICKS_SCHEMA requires DATABRICKS_CATALOG`             | A schema is set without its catalog                                                                             | Set the catalog too, or clear the schema                                                |
| Nothing syncs, no error                                     | The token lacks `USE CATALOG` / `USE SCHEMA` / `EXECUTE`                                                        | Check grants — an agent whose model the token cannot read is skipped or fails           |
| A use case's description was overwritten                    | It shares a fully qualified name with an agent — matching is by name                                            | Rename one of the two — see [How it matches records](#how-it-matches-records)           |
| `Genie agents will not be synced: …` at startup             | `CREDO_BACKEND_BASE_URL` is unset or unreachable, the tenant has no `agent` type, or the key cannot read agents | Fix the cause; deployed agents sync regardless. Restart to retry Genie                  |
| `genie cycle: scanned=0`                                    | The token cannot see any Genie agent                                                                            | Share the Genie agent with the token's owner (CAN VIEW or higher)                       |
| `skipping Genie agent …: title … already synced`            | Two Genie agents share a title                                                                                  | Rename one in Databricks — Credo AI agent names are unique                              |

---

## How it matches records

**By fully qualified name, from an upfront listing.** There's no server-side filter-by-name, so at
startup the poller pages through the Credo AI use cases once and builds a name → id map. A name in
that map is patched (`description` only, and only when the text differs); a name that isn't gets a
`POST /use_cases`. That one listing is what makes a restart safe, with no state file.

**Matching is by name alone, whatever created the use case.** Unlike the Credo AI model API, the use
case API does not return `source`, so the poller cannot tell a use case it created from one someone
made by hand. A use case already named `catalog.schema.model` is adopted and its description is
overwritten on the next change. Fully qualified Unity Catalog names make an accidental clash
unlikely, but this is the reason not to reuse them for hand-made use cases.

**Genie agents are matched the same way, by title.** At startup the poller lists the tenant's Agent
entities once and builds a title → id map, so a restart does not duplicate. Renaming a Genie agent in
Databricks creates a second Credo AI agent, and two Genie agents with the same title cannot both
sync: the first one wins each cycle and the other is skipped with a warning.

**A use case deleted in Credo AI is recreated.** If a patch returns `404` for an id the poller
cached, it forgets the id and creates the use case again, instead of failing every later cycle.

**Changes are detected by comparing the description, not a timestamp.** The deployment list carries
none, so there is no watermark: each cycle re-reads every agent and only writes when the text it
builds differs from what Credo AI holds.

---

## Limitations

- **Only deployed agents are discovered.** An agent registered in Unity Catalog but never deployed
  with `agents.deploy` is indistinguishable from an ordinary model, so it is not synced. Syncing it
  as a model is the job of the [model registry cookbook](../unity-catalog-registry).
- **The deployment list is not paginated.** The route behind `list_deployments()` returns everything
  in one response and takes no page parameters.
- **The deployments route is called directly, not through the `databricks-agents` package.** That
  package pulls in `mlflow-skinny`, `pandas`, `numpy`, `boto3` and more. The route is what
  `list_deployments()` calls today, but it is not part of the public `databricks-sdk`, so Databricks
  could change it.
- **Nothing is ever deleted.** An agent that is undeployed or removed stays in Credo AI. A governance
  record should outlive the deployment it describes — but it means Credo AI is a superset of
  Databricks, not a mirror.
- **A rename, or a move to another schema or catalog, creates a second use case**, because matching
  is by fully qualified name.
- **A same-name use case is always adopted**, even one created by hand, because the use case API
  does not expose `source` to tell them apart.
- **Only registry and deployment metadata is recorded**, as text in `description`. Tools, system
  prompts, run ids and evaluation metrics are not read.
- **Genie agents use a private API.** They are created through Credo AI's private v2 API, which can
  change without notice, until the public Integration API supports agents. If
  `CREDO_BACKEND_BASE_URL` is unset, Genie agents are skipped and everything else still syncs.
- **Deployed agents are still use cases, and Genie agents are agents.** The two are not linked or
  de-duplicated, because they are different Databricks objects.
- **A Genie agent rename creates a second agent**, because matching is by title.
- **Genie agent content is not read.** Tables, instructions and sample questions live in the space's
  `serialized_space`, which needs CAN EDIT and is not fetched. Only title, description, location,
  warehouse and last-updated time are recorded.
- **Polling, not events.** A change shows up on the next cycle, up to `POLL_INTERVAL_SECONDS` later.

---

Still stuck? Slack: `#credo-ai-integrations` | Support: support.credo.ai
