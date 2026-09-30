# Azure AI Foundry → Credo AI Governance Sync

A scheduled command that reads your Azure AI Foundry model catalog and pushes Microsoft's
responsible-AI evaluator controls, a governance intake questionnaire, and custom fields into Credo
AI — no forms, no manual entry, and a Control Library pre-wired to Azure's own evaluators (BLEU,
groundedness, jailbreak detection, content-safety, and more).

- All code runs in your own environment, wherever it can reach Azure AD and Credo AI
- Credentials stored as environment variables, never in source
- Every write is idempotent — re-running with nothing changed makes zero API calls that matter

---

## What it syncs

```text
Azure AD app registration (client-credentials token)
  │
  ├─ api.catalog.azureml.ms/asset-gallery  -> Credo AI Models
  │    (standard-paygo model catalog)
  │
  └─ config/ (bundled with this cookbook, not fetched from Azure)
       ├─ policy_controls/MSFT-*/{base.yaml,vN.yaml}  -> Credo AI Policy Controls
       │    20 controls, one per Microsoft evaluator (BLEU, ROUGE, groundedness,
       │    jailbreak detection, hate/unfair, self-harm, violence, sexual content, ...)
       ├─ custom_fields.json                            -> Credo AI Custom Fields
       └─ azure_questionnaire.json                       -> Credo AI Questionnaire
```

**One Credo AI API for everything.** All four domains go through the same `/auth/exchange` +
`/api/v2/{tenant}/...` API — the same one the original `MSFT+CredoAI` integration this cookbook was
built from already uses. `source` on each model references a `Source` record named "Azure AI
Foundry" — this sync creates it automatically at the start of every run (idempotent, so re-running
it every time is harmless), exactly like the original.

**The controls, not just the sync code, are the point.** Each `MSFT-*` control ships an
`evidence_requirements` block containing a ready-to-run Python skeleton that calls the matching
`azure.ai.evaluation` SDK evaluator and uploads the score straight to Credo AI as evidence — a
governance reviewer gets a pre-populated Control Library instead of 20 controls to author by hand.

---

## Prerequisites

| Item                      | Where to get it                                                    |
| ------------------------- | ------------------------------------------------------------------ |
| Credo AI API token        | https://app.credo.ai/my-settings/tokens/                           |
| Credo AI tenant name      | Your organization's tenant identifier (used to log in to Credo AI) |
| Azure AD app registration | See Step 2 — tenant ID, client ID, client secret                   |
| Python                    | 3.10+                                                              |

---

## Step 1: Set your Credo AI credentials

From the **cookbook root** — the directory holding this README:

```bash
cp .env.example .env
# Edit .env — CREDO_API_TOKEN, CREDO_TENANT, CREDO_BASE_PATH
```

`.env` belongs here, not in `server/python`. The sync looks for it at the cookbook root no matter
which directory you run it from, so a scheduled job does not depend on where cron starts it.

---

## Step 2: Create an Azure AD app registration

This cookbook authenticates to Azure with its own app registration — a client-credentials flow, no
user login involved.

1. [portal.azure.com](https://portal.azure.com) → search **Microsoft Entra ID** → **Overview** →
   copy the **Tenant ID** → `AZURE_TENANT_ID`
2. Entra ID → **App registrations** → **+ New registration** → name it, single tenant, no redirect
   URI → **Register** → copy **Application (client) ID** → `AZURE_APP_CLIENT_ID`
3. Same app → **Certificates & secrets** → **+ New client secret** → copy the **Value** immediately
   (shown once) → `AZURE_CLIENT_SECRET`

> **No Azure RBAC role assignment is required.** The model catalog endpoint this cookbook calls
> (`api.catalog.azureml.ms/asset-gallery`) is Azure's public per-region model catalog, not your own
> AI Foundry project's data — any valid Azure AD bearer token for `https://management.azure.com/` is
> accepted. This is different from Amazon Bedrock, which needs a specific IAM policy; Azure needs
> only the app registration itself.

---

## Step 3: Install and choose what to sync

```bash
cd server/python
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

> **Use a virtualenv.** Without one, `pip` and `python` can resolve to different interpreters and
> the install lands somewhere the sync cannot see. If `python3 -m venv` fails with
> `ensurepip is not available` (common on Debian/Ubuntu), either `sudo apt install python3-venv` or
> use [uv](https://docs.astral.sh/uv/): `uv venv .venv && source .venv/bin/activate && uv pip install -r requirements.txt`.

All four domains are on by default. Turn any off in `.env`, or for a single run with a flag:

| `.env` setting             | Flag (this run only)   | Effect                               |
| -------------------------- | ---------------------- | ------------------------------------ |
| `SYNC_MODELS=false`        | `--skip-models`        | Skip the Azure model catalog sync    |
| `SYNC_CONTROLS=false`      | `--skip-controls`      | Skip the 20 `MSFT-*` policy controls |
| `SYNC_CUSTOM_FIELDS=false` | `--skip-custom-fields` | Skip custom field creation           |
| `SYNC_QUESTIONNAIRE=false` | `--skip-questionnaire` | Skip questionnaire creation          |

Questionnaire behavior is controlled separately by `CREDO_QUESTIONNAIRE_OPTIONS`:

| Value | Effect                                                                                                                                                     |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `1`   | Create a fresh `DEFAULT_AZURE` questionnaire from the Azure template alone (default)                                                                       |
| `2`   | Fetch your tenant's existing questionnaire (`CREDO_QUESTIONNAIRE_ID` + `_VERSION`) and merge its sections with the Azure template, instead of replacing it |

---

## Step 4: Dry-run, then run

From `server/python`, with the virtualenv from Step 3 active:

```bash
python main.py --dry-run     # reads only, writes nothing
python main.py               # sync
python main.py --help        # every flag
```

| Flag              | What it does                                                       |
| ----------------- | ------------------------------------------------------------------ |
| `--dry-run`       | Reads Azure and Credo AI, logs what it would write, writes nothing |
| `-v`, `--verbose` | Per-record logging — what was created or skipped                   |
| `--skip-*`        | Skip one domain for this run only — see Step 3                     |

Start with `--dry-run -v`: it shows exactly which records the first real run will create.

Each run ends with one summary line per domain:

```text
Sync complete: models         scanned=42 created=42 updated=0 skipped=0 errors=0
Sync complete: controls       scanned=20 created=20 updated=0 skipped=0 errors=0
Sync complete: custom fields  scanned=6  created=6  updated=0 skipped=0 errors=0
Sync complete: questionnaire  scanned=1  created=1  updated=0 skipped=0 errors=0
```

Run it a second time with nothing changed and every counter reads `skipped` with **zero writes** —
including controls, which now compare content against the latest published version rather than
blindly posting (see "How it matches records" below).

Exit codes: `0` all good, `1` the run completed but at least one domain hit an error, `2` it could
not start.

---

## Step 5: Schedule it

There is no server and no webhook — none of the four things this syncs has an event feed, so this
is a one-shot command on a timer. Daily is plenty; the model catalog and control set don't change
often:

```text
0 6 * * * cd /opt/azure-foundry-sync/server/python && /usr/bin/python3 main.py
```

A Kubernetes `CronJob` or a scheduled container run works equally well.

---

## Troubleshooting

| Symptom                                                       | Likely Cause                                                                                                                    | Fix                                                                                                                           |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Non-JSON response acquiring Azure token                       | Wrong `AZURE_TENANT_ID`, or app not registered in that tenant                                                                   | Recheck Entra ID → Overview → Tenant ID, and the app registration                                                             |
| `401` on Azure token request                                  | Expired or wrong `AZURE_CLIENT_SECRET`                                                                                          | Certificates & secrets → create a new one, update `.env`                                                                      |
| `401` on `/auth/exchange`                                     | Wrong `CREDO_API_TOKEN` or `CREDO_TENANT`                                                                                       | Check `.env` values                                                                                                           |
| `404` on `/auth/exchange`                                     | `CREDO_BASE_PATH` doesn't serve that host                                                                                       | Confirm the base path with Credo AI — a local/dev backend that only implements a subset of endpoints is the most common cause |
| Every model create rejected (422, mentions `source`)          | Race on first-ever run — source creation and model creation happen in the same run                                              | Harmless; the source is created before models are posted, but re-run once if you see this on a brand-new tenant               |
| Control shows `updated` every run even though nothing changed | Your local `config/` content genuinely differs from what's published (e.g. a version left in draft from an earlier partial run) | Not a bug — check `--dry-run -v` output, it names which controls differ and why (draft vs. published)                         |
| Exit code `2` with a config error                             | Unrecognized or mistyped key in `.env`                                                                                          | Compare against `.env.example`                                                                                                |

Still stuck? Slack: `#credo-ai-integrations` | Support: support.credo.ai

---

## How it matches records

**Models, by an upfront listing.** Per guidance from the Credo AI team (there's no server-side
filter-by-name yet), this pages through the existing models registry once at the start of every
run and builds a set of names. A model already in that set is skipped with **zero API calls** —
no create attempted at all. Only genuinely new models get a `POST`.

**Custom fields and the questionnaire, by 422.** No upfront list-and-diff for these two — a create
is attempted for every record, and the API's own "already exists" response (`422`) is the skip
signal, same as the original integration this cookbook was built from. (The API has no listing
endpoint for these that would make an upfront check worthwhile.)

**Controls, by comparing content against the latest version.** `policy_control_bases/{key}/versions`
has no server-side dedup either, but the API exposes what's needed to do it client-side — per
guidance from the Credo AI team:

1. List the control's existing versions, take the highest.
2. Compare its `info`, `risk_type_ids`, `evidence_requirements` against the desired content from
   `config/` (lists sorted, `None`/`[]` treated as equal).
3. Identical → skip, no write at all.
4. Latest version is a **draft** and differs → `PATCH` it in place, then `PATCH draft: false`.
   Never `POST` while a draft exists — the API rejects a new version in that state.
5. Latest version is **published** and differs → `POST` (which copies the previous version's
   content), then `PATCH` the actual desired content, then `PATCH draft: false`.

Run `--dry-run -v` to see exactly which branch each control takes before running for real.

Changing a control's evidence requirements or description should still be done by adding a new
`vN.yaml` next to the existing ones in `config/policy_controls/MSFT-*/` (several already have both
`v1.yaml` and `v2.yaml`) — the sync always reads the latest local file and compares it against
what's actually published, so this is what drives whether anything gets written on the next run.

---

## Limitations

- **Nothing is ever deleted.** A control, custom field, or questionnaire version removed from this
  cookbook's `config/` stays live in Credo AI.
- **Model listing only checks the first page** (see `list_models` in `credo_v2.py`) — a tenant with
  more models than that page size could see a duplicate create attempt on an old, unlisted model,
  though the API's own `422` still catches it as a fallback.
- **Custom fields and the questionnaire still rely on `422`, not a real listing check** — the API
  has no listing endpoint for either that would make an upfront comparison worthwhile.
- **Single-tenant, single Azure AD app.** One `.env`, one Credo AI tenant, one Azure subscription per
  run — several tenants means several scheduled runs.
- **The questionnaire merge (option `2`) is additive only** — it appends the Azure template's
  sections to your existing questionnaire's sections; it does not reconcile or deduplicate questions
  that already cover the same ground.

---

## Full guide

Not yet published — pending Cookbook Validation Process sign-off.
