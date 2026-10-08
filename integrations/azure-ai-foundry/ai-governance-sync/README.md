# Azure AI Foundry → Credo AI Governance Sync

A scheduled command that reads your Azure AI Foundry model catalog and pushes Microsoft's
responsible-AI evaluator controls, a governance intake questionnaire, and custom fields into Credo
AI — no forms, no manual entry, and a Control Library pre-wired to Azure's own evaluators (BLEU,
groundedness, jailbreak detection, content-safety, and more).

- All code runs in your own environment, wherever it can reach Azure AD and Credo AI
- Credentials stored as environment variables, never in source
- Every write is idempotent — re-running with nothing changed writes nothing (the one exception is a
  harmless `Source` create attempt that Credo AI answers with `422 already exists`)

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

**The official `pycredoai` SDK for most of it.** Models, custom fields, and the questionnaire all go
through `pip install pycredoai` — the standard client for the Credo AI public Integration API. Three
things stay on Credo AI's private v2 API instead:

- **The `Source` record.** `source` on a Model is a reference to a `Source` record, and creating
  one has no public Integration API endpoint. This sync creates it automatically at the start of
  every run. Once it exists Credo AI answers `422`, which the sync treats as "already there".
- **Entity type ids, for custom fields.** `config/custom_fields.json` scopes each field to an entity
  type (e.g. `use_case`), so it only shows up on that kind of record. The SDK scopes by entity type
  _id_, the ids differ on every tenant, and neither the SDK nor the Integration API can list them.
  The sync reads them once from the private `entity_types` endpoint, and only when a field actually
  needs creating.
- **Policy controls.** The public SDK's evidence-requirement schema (`type`/`description`/`required`)
  has no fields for `code_template`, `mathematical_bound`, or `governance_bounds` — exactly the rich
  content the MSFT-\* controls' evidence requirements actually carry. Routing them through the
  public API would silently drop all of that, so controls use the same private API the `Source`
  call does.

**The controls, not just the sync code, are the point.** Each `MSFT-*` control ships an
`evidence_requirements` block containing a ready-to-run Python skeleton that calls the matching
`azure.ai.evaluation` SDK evaluator and uploads the score straight to Credo AI as evidence — a
governance reviewer gets a pre-populated Control Library instead of 20 controls to author by hand.

---

## Prerequisites

| Item                      | Where to get it                                                    |
| ------------------------- | ------------------------------------------------------------------ |
| Credo AI API key          | https://app.credo.ai/my-settings/tokens/                           |
| Credo AI tenant name      | Your organization's tenant identifier (used to log in to Credo AI) |
| Azure AD app registration | See Step 2 — tenant ID, client ID, client secret                   |
| Python                    | 3.10+                                                              |

---

## Step 1: Set your Credo AI credentials

From the **cookbook root** — the directory holding this README:

```bash
cp .env.example .env
# Edit .env — CREDOAI_API_KEY, CREDOAI_TENANT, CREDOAI_API_URL
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
including controls and the questionnaire, which compare their content against the latest published
version rather than blindly posting (see "How it matches records" below). The only write attempted is
the `Source` create, which Credo AI rejects with `422` because it already exists.

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

| Symptom                                                                         | Likely Cause                                                                                                                    | Fix                                                                                                                             |
| ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| Non-JSON response acquiring Azure token                                         | Wrong `AZURE_TENANT_ID`, or app not registered in that tenant                                                                   | Recheck Entra ID → Overview → Tenant ID, and the app registration                                                               |
| `401` on Azure token request                                                    | Expired or wrong `AZURE_CLIENT_SECRET`                                                                                          | Certificates & secrets → create a new one, update `.env`                                                                        |
| `401` on `/auth/exchange`                                                       | Wrong `CREDOAI_API_KEY` or `CREDOAI_TENANT`, or the key has expired/been rotated                                                | Check `.env` values; mint a fresh key if it's been a while                                                                      |
| `404 Phoenix.Router.NoRouteError` on `POST /api/v1/integration/auth/token`      | `CREDOAI_API_URL` points at a backend that doesn't implement the public Integration API — common on a local/dev backend         | Models, custom fields, and the questionnaire need the public API; controls still work against such a backend (private API only) |
| `404` on `/auth/exchange`                                                       | `CREDOAI_API_URL` doesn't serve that host at all                                                                                | Confirm the base URL with Credo AI                                                                                              |
| Every model create rejected (422, mentions `source`)                            | Race on first-ever run — source creation and model creation happen in the same run                                              | Harmless; the source is created before models are posted, but re-run once if you see this on a brand-new tenant                 |
| `POST .../sources` returns `422` on every run                                   | The `Source` record already exists                                                                                              | Expected — the sync treats it as "already there"                                                                                |
| Custom field error: `targets entity type '...', which this tenant doesn't have` | `target` in `config/custom_fields.json` isn't an entity type on this tenant                                                     | Use one of the names listed in the error (e.g. `use_case`, `model`, `vendor`), or remove `target` to create the field unscoped  |
| Control shows `updated` every run even though nothing changed                   | Your local `config/` content genuinely differs from what's published (e.g. a version left in draft from an earlier partial run) | Not a bug — check `--dry-run -v` output, it names which controls differ and why (draft vs. published)                           |
| Questionnaire shows `updated` every run even though nothing changed             | Your local `config/azure_questionnaire.json` (or, with option `2`, your existing questionnaire) differs from what's published   | Check `--dry-run -v`; it says whether it would create or publish a new version                                                  |
| Exit code `2` with a config error                                               | Unrecognized or mistyped key in `.env`                                                                                          | Compare against `.env.example`                                                                                                  |

Still stuck? Support: support.credo.ai

---

## How it matches records

**Models and custom fields, by an upfront listing.** There's no server-side filter-by-name on
either resource yet, so this pages through the existing registry once per run (`list_all()`) and
builds a set of names. A record already in that set is skipped with **zero API calls** — no create
attempted at all. Only genuinely new records get a `create()`.

**The questionnaire, by comparing content against the latest version.** The SDK can read the latest
published version (`questionnaires.get_spec()`), so the sync does:

1. Read the questionnaire's latest version. A `404` means it doesn't exist yet → create it.
2. Compare its sections and questions against the desired ones. Order counts here, since section and
   question order is part of what the questionnaire looks like. Fields the server adds (ids,
   `hidden`/`required` defaults, nulls) are ignored, and a field left unset in
   `azure_questionnaire.json` means "don't care", so the server's own default can't cause a mismatch.
3. Identical → skip, no write. Different → publish a new version.

**Controls, by comparing content against the latest version.** `policy_control_bases/{key}/versions`
has no server-side dedup either, but the private API exposes what's needed to do it client-side:

1. List the control's existing versions, take the highest. Only when there are none is the control's
   base created, so an unchanged control costs one read and no writes.
2. Compare its `info`, `risk_type_ids`, `evidence_requirements` against the desired content from
   `config/` (lists order-normalized recursively; server-added fields outside what our local yaml
   defines are ignored, so backend metadata can't cause a false mismatch). The reverse also holds:
   a field our yaml defines that Credo AI accepts but never returns on read (today:
   `evidence_requirements[].governance_bounds`) is left out of the comparison, because it could
   never match and would otherwise post a new version on every run. The sync logs a warning naming
   such fields.
3. Identical → skip, no write at all.
4. Latest version is a **draft** and differs → one `PATCH` sets the new content and `draft: false`
   together. Never `POST` while a draft exists — the API rejects a new version in that state.
5. Latest version is **published** and differs → `POST` (which copies the previous version's
   content as a new draft), then one `PATCH` sets the real content and `draft: false` together.

Run `--dry-run -v` to see exactly which branch each control takes before running for real — a
brand-new control (no base yet) is correctly reported as "would create," not an error.

Changing a control's evidence requirements or description should still be done by adding a new
`vN.yaml` next to the existing ones in `config/policy_controls/MSFT-*/` (several already have both
`v1.yaml` and `v2.yaml`) — the sync always reads the latest local file and compares it against
what's actually published, so this is what drives whether anything gets written on the next run.
A change to `governance_bounds` alone is therefore not picked up on its own — change something else
in the same `vN.yaml` (a new version file is the normal way) if you need it published.

---

## Limitations

- **Nothing is ever deleted.** A control, custom field, or questionnaire version removed from this
  cookbook's `config/` stays live in Credo AI.
- **Custom fields are only created, never changed.** A field that already exists (matched by name)
  is left alone, including its entity type scoping — the API only lets a field's entity types be
  added to, not removed. A field created earlier without scoping (e.g. by an older version of this
  cookbook) keeps applying to every entity type until you change it in Credo AI.
- **An unknown `target` is an error, not a fallback.** If a field's `target` isn't an entity type on
  your tenant, that field is skipped and counted as an error, rather than being created for every
  entity type.
- **`governance_bounds` changes alone are not detected.** Credo AI accepts the field when a control
  version is written but does not return it on read, so there is nothing to compare it against (see
  "How it matches records").
- **Single-tenant, single Azure AD app.** One `.env`, one Credo AI tenant, one Azure subscription per
  run — several tenants means several scheduled runs.
- **The questionnaire merge (option `2`) is additive only** — it appends the Azure template's
  sections to your existing questionnaire's sections; it does not reconcile or deduplicate questions
  that already cover the same ground. The result is written to its own questionnaire, keyed
  `<CREDO_QUESTIONNAIRE_ID> with DEFAULT_AZURE`; your original is not modified. Only each question's
  text, type, `multiple` flag and select options are carried over from your existing questionnaire —
  other question settings (descriptions, `required`, `hidden`) are not.
