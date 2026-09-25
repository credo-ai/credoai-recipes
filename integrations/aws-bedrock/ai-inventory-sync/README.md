# Amazon Bedrock → Credo AI AI Inventory Sync

A scheduled command that reads your Amazon Bedrock account and keeps Credo AI current with the
models and agents that actually exist in it — no forms, no manual entry.

- All code runs in your own environment, wherever it can reach AWS and Credo AI
- **No AWS credentials are stored by this cookbook** — it uses boto3's default credential chain, so
  there is nothing here to leak
- Every AWS call is read-only; the sync cannot change anything in your AWS account

---

## What it syncs

```text
Amazon Bedrock (one region, one AWS account)
  ├─ bedrock (control plane)              -> Credo AI Models
  │    ├─ list_foundation_models   — the models AWS publishes in this region
  │    ├─ list_custom_models       — your fine-tuned / distilled models
  │    └─ list_imported_models     — your Custom Model Import models
  └─ bedrock-agentcore-control            -> Credo AI Use Cases
       └─ list_harnesses + get_harness  — one agent, one Use Case
            │
            └─ bedrock-sync (a scheduled one-shot command)
                 ├─ upsert, matched by name
                 │    ├─ not in Credo AI      -> create
                 │    ├─ in Credo AI, changed -> update
                 │    └─ in Credo AI, same    -> no call
                 ├─ link each model to its provider's Vendor
                 └─ link each agent to the Model its harness calls
```

**Models go to the registry, agents go to Use Cases.** A model is a capability; an agent is AI
applied to a purpose, which is what a Use Case governs. Policy packs, risk scenarios, controls and
questionnaires all attach to Use Cases — so an agent recorded as a Model would be invisible to every
governance workflow.

**What a governance reviewer gets.** Each agent's Use Case carries its model, its tools, its memory mode, its
iteration cap, and its system prompt — so "what is this agent actually instructed to do, and what can
it touch?" is answerable without AWS console access.

---

## Prerequisites

| Item                 | Where to get it                                |
| -------------------- | ---------------------------------------------- |
| Credo AI API key     | Governance App → Settings → Integrations       |
| Credo AI tenant name | Your organization's tenant identifier          |
| AWS credentials      | However you normally authenticate — see Step 2 |
| IAM permissions      | Six read-only actions — see Step 2             |
| Python               | 3.12+                                          |

---

## Step 1: Set your Credo AI credentials

From the **cookbook root** — the directory holding this README:

```bash
cd integrations/aws-bedrock/ai-inventory-sync
cp .env.example .env
# Edit .env — CREDO_API_KEY, CREDO_TENANT, CREDO_API_BASE_URL
```

`.env` belongs here, not in `server/python`. The sync looks for it at the cookbook root no matter
which directory you run it from, so a scheduled job does not depend on where cron starts it.

---

## Step 2: Give it read-only AWS access

This cookbook takes **no AWS keys of its own**. Point boto3 at AWS the way you normally would:

| Where it runs   | What you do                                             |
| --------------- | ------------------------------------------------------- |
| A laptop        | `aws configure` / `aws sso login`, or `AWS_PROFILE=...` |
| EC2 / ECS / EKS | Attach an instance, task or IRSA role — zero config     |
| CI              | Whatever your runner already exports                    |

Attach this policy to that identity:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:ListFoundationModels",
        "bedrock:ListCustomModels",
        "bedrock:ListImportedModels",
        "bedrock:ListInferenceProfiles",
        "bedrock-agentcore:ListHarnesses",
        "bedrock-agentcore:GetHarness"
      ],
      "Resource": "*"
    }
  ]
}
```

> **The `bedrock-agentcore:` prefix is not a typo.** AgentCore is a separate AWS service with its own
> IAM signing name, so `bedrock:*` does **not** cover it. A policy that grants everything under
> `bedrock:` syncs your models perfectly and fails every agent call — the most common setup mistake
> with this cookbook.

A policy missing any one action degrades to a partial sync and a non-zero exit, not a crash.

---

## Step 3: Create the `Source` record in your tenant

`source` is not free text on a Credo AI Model or Use Case — it is a reference to a `Source` record
that must already exist, so an unrecognized value is rejected on every create.

```bash
cd server/python
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python ensure_credo_source.py
```

> **Use a virtualenv.** Without one, `pip` and `python` can resolve to different interpreters and the
> install lands somewhere the sync cannot see — the failure looks like
> `ModuleNotFoundError: No module named 'dotenv'` straight after a successful install.
>
> On Debian and Ubuntu, `python3 -m venv` can produce a virtualenv with no `pip` inside. If that
> happens, either `sudo apt install python3-venv` or use [uv](https://docs.astral.sh/uv/):
> `uv venv .venv && source .venv/bin/activate && uv pip install -r requirements.txt`.

Expect `Created source 'Amazon Bedrock'` or `already exists`. Idempotent, safe to re-run, and needed
**once per tenant**.

> This one setup script uses Credo AI's v2 admin API, because the public Integration Service has no
> `/sources` endpoint. Everything the sync itself does uses the public API.
>
> It defaults to `CREDO_API_BASE_URL`, which is correct for a hosted tenant where both APIs sit
> behind one host. If yours serves them separately — a local or self-hosted stack usually does —
> point the script at the admin API instead:
>
> ```bash
> python ensure_credo_source.py --base-host https://your-admin-host
> ```
>
> A `404 Not Found` on `/auth/exchange` means the host you used does not serve that API.

---

## Step 4: Choose what to sync

Your own custom and imported models are always synced. Two settings control the rest:

| Setting                              | Default | Effect                                                                 |
| ------------------------------------ | ------- | ---------------------------------------------------------------------- |
| `BEDROCK_SYNC_ALL_FOUNDATION_MODELS` | `false` | `false` — only the base models your agents name; `true` — all of them  |
| `BEDROCK_SYNC_AGENTCORE`             | `true`  | `false` for an account or region with no AgentCore agents              |
| `BEDROCK_REGION`                     | —       | One region per run; several regions means several scheduled runs       |
| `LOG_LEVEL`                          | `INFO`  | `WARNING` keeps a scheduled run quiet unless something needs attention |

The default keeps the registry to the models actually in use. AWS publishes 100+ foundation models
per region, and importing all of them buries the ones you built.

Each of the first two has a flag that overrides it **for a single run**, without editing `.env`:

| Flag                      | Overrides                            |
| ------------------------- | ------------------------------------ |
| `--all-foundation-models` | `BEDROCK_SYNC_ALL_FOUNDATION_MODELS` |
| `--no-agentcore`          | `BEDROCK_SYNC_AGENTCORE`             |

---

## Step 5: Dry-run, then run

From `server/python`, with the virtualenv from Step 3 active:

```bash
python main.py --dry-run     # reads only, writes nothing
python main.py               # sync
python main.py --help        # every flag
```

| Flag                      | What it does                                                          |
| ------------------------- | --------------------------------------------------------------------- |
| `--dry-run`               | Reads AWS and Credo AI, logs what it would write, writes nothing      |
| `-v`, `--verbose`         | Per-record logging — what was created, updated, linked or skipped     |
| `--all-foundation-models` | This run only: the whole regional catalog instead of the narrowed set |
| `--no-agentcore`          | This run only: skip agents, sync models alone                         |

Start with `--dry-run -v`: it shows exactly which records the first real run will create.

`-v` overrides `LOG_LEVEL` for that run rather than adding to it, so a cron entry can stay at
`LOG_LEVEL=WARNING` while you still get full detail when you run it by hand.

Each run ends with four counted lines:

```text
Sync complete (region us-east-1): models      scanned=3 created=3 updated=0 unchanged=0 errors=0
Sync complete (region us-east-1): vendors     providers=1 created=1 linked=1 already_linked=0 skipped=2 errors=0
Sync complete (region us-east-1): use cases   scanned=1 created=1 updated=0 unchanged=0 errors=0
Sync complete (region us-east-1): model links agents=1 linked=1 already_linked=0 unresolved=0 non_bedrock=0 errors=0
```

Run it a second time with nothing changed in AWS and every counter reads `unchanged` /
`already_linked` with **zero writes**. That is what makes it safe to schedule.

Exit codes: `0` all good, `1` the run completed but something failed, `2` it could not start.

---

## Step 6: Schedule it

There is no server and no webhook — Bedrock has no event feed for its inventory, so this is a
one-shot command on a timer. Hourly or daily is plenty:

```bash
0 * * * * cd /opt/bedrock-sync/server/python && /usr/bin/python3 main.py
```

A Kubernetes `CronJob` or an EventBridge rule against a container works equally well.

---

## Troubleshooting

**`UnknownOperationException` when listing custom models.** `ListCustomModels` is not served in every
AWS region — it fails in `us-east-2` while the other listings succeed there, and the same call works
in `us-east-1`. The run records the failure, syncs everything else, and exits `1`. To cover custom
models in an affected region, run a second sync against a region that serves the call.

**`AccessDeniedException` on harnesses or agents.** Your policy is missing the `bedrock-agentcore:`
actions — see the note in Step 2. `bedrock:*` does not grant them.

**`NoRegionError: You must specify a region.`** boto3 reads `AWS_DEFAULT_REGION`, not `AWS_REGION`.
Set `BEDROCK_REGION` in `.env` and it stops mattering.

**Every model create is rejected.** The `Source` record is missing — run Step 3.

**Agents report `unresolved` in the model-links line.** The model an agent names isn't in the
registry. Most often `BEDROCK_SYNC_ALL_FOUNDATION_MODELS` is `false` _and_ the agent runs on a model
no other agent names — or the agent runs on a non-Bedrock provider, which is reported as
`non_bedrock` instead.

**Exit code 2 with a `Configuration:` message.** A key in `.env` is unrecognized or mistyped; compare
it against `.env.example`.

---

## How it matches records

**By name.** Credo AI Models and Use Cases are name-unique, so the name is the match key: present →
update, absent → create, identical → no call at all. Names come from Bedrock — `modelId` for
foundation models, `modelName` for custom and imported ones, and the harness name for agents.

**Agents name a profile, not a model.** A harness usually names an _inference profile_ — a routing
rule like `global.anthropic.claude-sonnet-4-6` — rather than a model id, because AWS recommends
profiles for cross-region capacity. The registry only ever holds model ids, so the sync resolves
profiles through `ListInferenceProfiles` before linking. A profile routing to more than one distinct
model is skipped rather than guessed at.

**Nothing is stored locally.** The run has to read Credo AI anyway to work out what changed, so Credo
AI is the state store. There is no checkpoint file to corrupt or reset.

---

## Limitations

- **Nothing is ever deleted.** A model removed from Bedrock stays in the registry, and a deleted
  agent stays as a Use Case. A governance record should outlive the deployment it describes — but it
  means Credo AI is a superset of AWS, not a mirror.
- **Links are add-only.** An agent that switches model gains a link to the new one and keeps the old.
  The sync cannot tell a link it created from one a person added by hand, and dropping someone's link
  is worse than carrying a stale one.
- **One region per run.** Inventory is regional; several regions means several scheduled runs into
  one tenant. The region is recorded on every record.
- **A rename creates a second record**, because matching is by name.
- **The system prompt is truncated to 2500 characters**, and an edit falling entirely past that cut
  is not detected — two prompts identical up to the limit produce identical records, so no update
  fires.
- **Runtimes are not synced.** A runtime holds no model, prompt or tools, and many harnesses can
  share one, so syncing them doubled the governance surface of every agent for a record with nothing
  to assess. The harness's backing runtime name is recorded in the agent's description.
- **Vendors are matched by name only**, case-insensitively. A tenant that already tracks
  "Anthropic PBC" will gain a second vendor called "Anthropic".

---

## Full guide

[Amazon Bedrock AI Inventory Sync](https://sdk.credo.ai/cookbooks/aws-bedrock-ai-inventory-sync) —
the narrative version of this cookbook, with the API calls and the reasoning behind each mapping.
