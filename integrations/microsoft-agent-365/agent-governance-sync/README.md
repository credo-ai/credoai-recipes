---
hub:
  name: "Microsoft Agent 365 Governance Sync"
  logo: "logo.svg" # PLACEHOLDER glyph, not a Microsoft mark. Replace with the approved Microsoft Agent 365 brand asset before publishing.
  category: "Agent gateways & AI security" # PROVISIONAL — the Type taxonomy is not finalized (see the cookbook-hub-metadata schema notes).
  support_level: "example"
  functions:
    - "Registry Sync"
    - "Deployment Gating"
  targets:
    - "Microsoft Agent 365"
    - "Microsoft Entra ID"
  short_description: >
    Registers every agent built in Microsoft Agent 365 as a Credo AI Use Case, and
    can block each one until its governance workflow clears it.
  what_it_does: >
    A scheduled command that reads your Microsoft Agent 365 catalog and gives each
    agent your organization built — in Copilot Studio, Agent Builder or Foundry — a
    Use Case in Credo AI, kept current as the agent changes. With enforcement
    switched on, it also blocks every agent whose Use Case has not cleared its
    governance workflow and unblocks the ones that have, so only governed agents
    can run. Agent 365 has no webhook for new agents, so this polls on a schedule.
  data_flow:
    source: "Microsoft Agent 365"
    destination: "Credo AI"
    direction: "bidirectional"
  you_will_need:
    - "A Microsoft 365 tenant licensed for Microsoft Agent 365 (or Microsoft 365 E7, which includes it)"
    - "A Microsoft Entra app registration with the CopilotPackages.Read.All application permission, admin-consented"
    - "For enforcement: an account with the AI Administrator or Global Administrator role, and the delegated CopilotPackages.ReadWrite.All permission, admin-consented"
    - "A Credo AI API key and tenant name"
    - "Python 3.12+"
  github_url: "https://github.com/credo-ai/credoai-recipes/tree/main/integrations/microsoft-agent-365/agent-governance-sync"
---

# Microsoft Agent 365 → Credo AI Agent Governance Sync

A scheduled command that reads your Microsoft Agent 365 catalog and keeps Credo AI current with
the agents your organization built — then, when you switch it on, makes Agent 365 enforce what
Credo AI decides.

- All code runs in your own environment, wherever it can reach Microsoft Graph and Credo AI
- Every Credo AI call goes through the official [`pycredoai`](https://pypi.org/project/pycredoai/)
  SDK — there is no raw HTTP against Credo AI anywhere in this cookbook
- **Registering agents never changes who can use them.** Blocking and unblocking is a separate
  switch, `--enforce`, that is off unless you turn it on
- No state file to lose: Credo AI itself is the record of what has been synced

---

> ### 🧪 Status: Preview — block and unblock use Microsoft's beta Graph API
>
> Reading the catalog uses a generally available Graph endpoint. Blocking and unblocking an agent
> use `POST /beta/copilot/admin/catalog/packages/{id}/block` and `/unblock`. Microsoft describes
> the beta channel as its normal release path for these, but beta endpoints can change shape or
> move without notice. If they do, `--enforce` needs an update; registering agents does not.

---

## What it syncs

```text
Microsoft Agent 365 catalog  (GET /v1.0/copilot/admin/catalog/packages, app-only)
  └─ agents your organization built (type shared / lob)  ← narrowed by AGENT365_PACKAGE_TYPES
       │
       └─ agent365-sync (a scheduled one-shot command)
            ├─ upsert one Use Case per agent, matched by Agent 365 asset ID
            │    ├─ no Use Case yet        -> create (and attach the intake questionnaire)
            │    ├─ agent changed          -> update
            │    └─ agent unchanged        -> no call
            │
            └─ with --enforce, as a signed-in admin  (POST /beta/.../block | unblock)
                 ├─ Use Case not cleared   -> agent blocked
                 └─ Use Case cleared       -> agent unblocked
```

**Agents go to Use Cases.** A Use Case is the unit Credo AI's policy packs, risk scenarios,
controls, questionnaires and review workflow all attach to, so an agent recorded as one is visible
to every governance process.

**What a reviewer gets.** Each Use Case carries the agent's own description, who published it and
what it was built with, so "what is this agent for, and who made it?" is answerable without
Microsoft 365 admin access.

### When an agent counts as cleared

An agent is cleared when its Use Case has finished the **last stage of its workflow** — the stage
of type `end`, at the step `clear`. Anything else, including a Use Case that was rejected, is not
cleared, so with `--enforce` the agent stays blocked.

The stage type matters because `clear` is also the closing step of every earlier stage. A Use Case
that has only cleared intake sits at `clear` too, and treating that as approval would release an
agent that nobody has reviewed. A step this cookbook doesn't recognize is also treated as not
cleared, so a new step can only ever keep an agent blocked, never release one.

---

## Prerequisites

| Item                    | Where to get it                                                           |
| ----------------------- | ------------------------------------------------------------------------- |
| Credo AI API key        | Governance App → Settings → Integrations                                  |
| Credo AI tenant name    | Your organization's tenant identifier                                     |
| Agent 365 license       | Microsoft Agent 365, or Microsoft 365 E7 (which includes it) — see Step 2 |
| Entra app registration  | Entra admin center → App registrations — see Step 2                       |
| Admin account (enforce) | AI Administrator or Global Administrator — see Step 5                     |
| Python                  | 3.12+                                                                     |

---

## Step 1: Set your credentials

From the **cookbook root** — the directory holding this README:

```bash
cd integrations/microsoft-agent-365/agent-governance-sync
cp .env.example .env
# Edit .env — CREDO_API_KEY, CREDO_TENANT, MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET
```

`.env` belongs here, not in `server/python`. The sync looks for it at the cookbook root no matter
which directory you run it from, so a scheduled job does not depend on where cron starts it.

---

## Step 2: Create the Microsoft Entra app registration

The tenant must be licensed for Microsoft Agent 365 (or Microsoft 365 E7). Without a license
every call to the catalog API is refused outright — see Troubleshooting.

1. **Entra admin center → App registrations → New registration.** Single tenant is fine.
2. From the app's **Overview**, copy the **Directory (tenant) ID** and the **Application (client)
   ID** into `MS_TENANT_ID` and `MS_CLIENT_ID`.
3. **Certificates & secrets → New client secret.** Copy the secret's **Value** — it is shown once —
   into `MS_CLIENT_SECRET`. (Not the "Secret ID": that is the single most common mistake here.)
4. **API permissions → Add a permission → Microsoft Graph → Application permissions →
   `CopilotPackages.Read.All`.** Then **Grant admin consent** for the tenant. Until consent is
   granted the catalog call fails with an empty `403`.

That is everything registration needs. If you will use `--enforce`, add two more things now, since
they are cheaper to do together:

5. **Authentication → Advanced settings → Allow public client flows → Yes.** The one-time admin
   sign-in uses a device code, which is a public-client flow.
6. **API permissions → Add a permission → Microsoft Graph → Delegated permissions →
   `CopilotPackages.ReadWrite.All`**, then **Grant admin consent** again.

| Needed for              | Permission                      | Type        |
| ----------------------- | ------------------------------- | ----------- |
| Reading the catalog     | `CopilotPackages.Read.All`      | Application |
| Blocking and unblocking | `CopilotPackages.ReadWrite.All` | Delegated   |

> **Why two kinds of permission.** Microsoft's reference lists **Application** permission as "Not
> available" for the block and unblock calls, and an app-only token is rejected for Copilot Studio
> and Agent Builder agents with `424 … without user context is not supported`. So `--enforce`
> cannot run as the app; it runs as an admin who has signed in once. Reading the catalog needs no
> person at all.

---

## Step 3: Install

```bash
cd server/python
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py --help
```

> **Use a virtualenv.** Without one, `pip` and `python` can resolve to different interpreters and
> the install lands somewhere the sync cannot see — the failure looks like
> `ModuleNotFoundError: No module named 'pydantic_settings'` straight after a successful install.
>
> On Debian and Ubuntu, `python3 -m venv` can produce a virtualenv with no `pip` inside. If that
> happens, either `sudo apt install python3-venv` or use [uv](https://docs.astral.sh/uv/):
> `uv venv .venv && source .venv/bin/activate && uv pip install -r requirements.txt`.

---

## Step 4: Dry-run, then register

Always start with a dry run. It reads Microsoft and Credo AI and writes to neither:

```bash
python main.py --dry-run
```

Every line is prefixed `[dry-run] would …` and the summary says **planned**, not complete. Check
that the count under `Catalog:` is the number of agents you expect your people to have built. If it
is far too high, narrow `AGENT365_PACKAGE_TYPES`; if it is far too low, see Troubleshooting.

Then register a few agents first:

```bash
python main.py --limit 3      # the 3 most recently created agents in scope
```

Open the Governance App and check the new Use Cases, then register everything:

```bash
python main.py
```

| Flag            | What it does                                                                               |
| --------------- | ------------------------------------------------------------------------------------------ |
| `--dry-run`     | Log what would happen; write nothing, anywhere                                             |
| `--limit N`     | Only the N most recently created agents in scope — a safe first run                        |
| `--asset-id ID` | Only this agent, by its Agent 365 asset ID. Repeatable. Overrides `AGENT365_PACKAGE_TYPES` |
| `--enforce`     | Also block and unblock agents — see Step 5                                                 |
| `--login`       | One-time admin sign-in for `--enforce`, then exit                                          |
| `-v`            | Debug logging, including the agents that needed no change                                  |

---

## Step 5: Turn on enforcement

> **`--enforce` changes who can use which agents across your whole Microsoft 365 tenant.** The
> first live run blocks **every in-scope agent that has no cleared Use Case yet** — and on a tenant
> where agents already exist, that is all of them. Preview it, then start with one agent you own.

**1. Sign in once** as an account that can block agents:

```bash
python main.py --login
```

It prints a code and a URL; open the URL, enter the code, and sign in as an account with the **AI
Administrator** or **Global Administrator** role. The refresh token is saved to
`.agent365_token.json` (owner-only, gitignored). Every later run trades it for a fresh access token
silently, so a scheduled run needs no one at the keyboard.

> That file is **standing admin access to your Copilot catalog.** Treat it like a credential: keep
> it off shared disks and out of backups you don't control. For anything beyond a trial, put it
> somewhere you already protect secrets and point `AGENT365_TOKEN_CACHE` at it.

**2. Preview what would be blocked:**

```bash
python main.py --enforce --dry-run
```

**3. Enforce on one agent you own** before anything wider:

```bash
python main.py --enforce --asset-id <asset id>
```

Confirm the agent is now blocked — `isBlocked` is `true` on its entry in the Graph response, and
the Microsoft 365 admin center's agent list shows its state — and that running the same command
again changes nothing. Then approve its Use Case in Credo AI, run the command once more, and the
agent becomes available.

**4. Enforce for everything in scope:**

```bash
python main.py --enforce
```

Microsoft is only called when an agent's state has to change, so a run where nothing changed makes
no block or unblock calls at all.

---

## Step 6: Schedule it

Agent 365 has no webhook or event feed for new or changed agents, so there is nothing to listen
for: run the command on a schedule and a new agent is picked up on the next tick.

```cron
*/30 * * * *  /opt/agent365-sync/server/python/.venv/bin/python /opt/agent365-sync/server/python/main.py >> /var/log/agent365-sync.log 2>&1
```

Use whatever you already run jobs with — cron, a Kubernetes `CronJob`, a systemd timer, a scheduled
CI workflow.

- **Registration only** (no `--enforce`) keeps no state and runs anywhere, including an ephemeral
  CI runner.
- **With `--enforce`** the saved admin sign-in must survive between runs: Entra rotates the refresh
  token on every use, so a run that starts from a stale copy of `.agent365_token.json` will fail
  and exit `2`. Run it somewhere with a persistent disk, or restore the file before each run and
  save it after.

The saved sign-in stops working after 90 days without use, or sooner if the admin's password
changes, the sign-in is revoked, or a Conditional Access sign-in-frequency policy applies to that
account. The run then exits `2` saying so, before it changes anything. Run `--login` again.

---

## Configuration reference

| Variable                     | Default                | What it does                                                                 |
| ---------------------------- | ---------------------- | ---------------------------------------------------------------------------- |
| `CREDO_API_KEY`              | —                      | Credo AI API key                                                             |
| `CREDO_TENANT`               | —                      | Credo AI tenant name                                                         |
| `CREDO_API_BASE_URL`         | `https://api.credo.ai` | The host only — not including `/api/v1/integration`                          |
| `CREDO_INTAKE_QUESTIONNAIRE` | blank                  | `<key>+<version>` to attach to every agent's Use Case; blank attaches none   |
| `MS_TENANT_ID`               | —                      | Entra directory (tenant) ID                                                  |
| `MS_CLIENT_ID`               | —                      | Entra application (client) ID                                                |
| `MS_CLIENT_SECRET`           | —                      | Client secret **value**. Read-only catalog access; never used by `--enforce` |
| `AGENT365_PACKAGE_TYPES`     | `shared,lob`           | Which catalog entries are agents worth governing; `all` syncs every entry    |
| `AGENT365_TOKEN_CACHE`       | `.agent365_token.json` | Where `--login` saves the admin's refresh token                              |
| `LOG_LEVEL`                  | `INFO`                 | Standard Python level                                                        |

**Exit codes:** `0` all good · `1` the run completed but something failed (one agent's Use Case or
block call was rejected — the rest still ran) · `2` it could not start (missing or wrong
credentials, an unreachable host, no usable saved admin sign-in for `--enforce`).

---

## How matching works

An agent's name is not a key — several agents can share one, and Credo AI Use Case names are
unique. So each Use Case's description ends with a small block this cookbook reads back:

```text
Built with: Microsoft 365 Copilot Agent Builder
Publisher: <the person who built it>
Agent 365 asset ID: T_0a1b2c3d-4e5f-6789-abcd-ef0123456789
Agent 365 last modified: 2026-09-24T15:50:07.1408663Z
```

- **`Agent 365 asset ID`** is the match key. It is what lets a run find the right Use Case again
  with no local state, even from a fresh checkout on a fresh machine.
- **`Agent 365 last modified`** is change detection. If it equals what the catalog reports now,
  nothing is called for that agent; if not, the Use Case is updated.
- **Names.** The Use Case takes the agent's name. If another Use Case already holds it, the agent
  gets `Name (T_0a1b2c3d)` instead. The oldest agent keeps the plain name, and a name this
  cookbook already chose is never changed unless the agent itself is renamed, so names don't flip
  between runs.

Don't delete the `Agent 365 asset ID:` line from a Use Case's description: the next run won't
recognise it and will create a second one.

---

## Troubleshooting

| Symptom                                                              | Likely cause                                                                                  | Fix                                                                                |
| -------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `no Microsoft Agent 365 license`                                     | The tenant has no Agent 365 (or Microsoft 365 E7) license; the catalog API refuses every call | Assign a license. Microsoft 365 Copilot alone does not satisfy it                  |
| `access denied … CopilotPackages.Read.All`                           | The permission is missing, or was added but never consented to                                | Step 2, item 4 — **Grant admin consent**. Graph returns this with an empty message |
| `Entra refused the app credentials (invalid_client)`                 | You pasted the secret's **ID**, or the secret expired                                         | Use the secret **Value** from Certificates & secrets, or create a new secret       |
| `Credo AI: Authentication failed: … 404 Not Found`                   | `CREDO_API_BASE_URL` includes `/api/v1/integration`                                           | Use the host only: `https://api.credo.ai`                                          |
| `Credo AI: Authentication failed: Invalid API key`                   | Wrong `CREDO_API_KEY` or `CREDO_TENANT`                                                       | Check both against Governance App → Settings → Integrations                        |
| `Configuration: <KEY> — Extra inputs are not permitted`              | An unrecognized key in `.env`                                                                 | Compare `.env` with `.env.example`; the named key is the typo                      |
| `Not in the Agent 365 catalog: <id>`                                 | A mistyped `--asset-id`                                                                       | Copy the asset ID from a `--dry-run -v` run or the Graph response                  |
| `No saved admin sign-in` / `The saved admin sign-in no longer works` | `--login` was never run, or the saved sign-in expired, was revoked, or was rotated away       | `python main.py --login`                                                           |
| `block failed (403)` during `--enforce`                              | The signed-in account lacks the role, or the delegated permission is not consented            | Step 2, item 6; sign in with an AI Administrator or Global Administrator           |
| A second Use Case appeared for an agent that already had one         | Someone removed the `Agent 365 asset ID:` line from the description                           | Restore the line on the original, delete the duplicate                             |
| An agent I own isn't synced                                          | Its package type is not in `AGENT365_PACKAGE_TYPES`                                           | Check its `type` in the Graph response; add it to the setting or pass `--asset-id` |

Still stuck? Slack: `#credo-ai-integrations` | Support: support.credo.ai

---

## Limitations

- **Polling, not events.** A new agent is registered on the next scheduled run, not the moment it
  is published. Agent 365 offers no webhook or change notification to improve on that.
- **Agents removed from the catalog are not cleaned up.** Their Use Cases stay, as the record of
  what existed. Nothing is deleted from Credo AI or Microsoft 365.
- **Use Cases made by hand, or by an earlier script, are not adopted.** They carry no asset ID, so
  the agent gets a new Use Case. Add the `Agent 365 asset ID:` line to an existing one if you want
  it to be the match.
- **The description is a snapshot** taken when the agent last changed. Edits made to the Use Case's
  description are overwritten the next time the agent changes.
- **"Cleared" is the last stage of whatever workflow your tenant defines.** It does not read your
  controls, risk scores or evidence — only where the Use Case sits in its workflow.
- **Risk classification and lifecycle tags are not written back to Agent 365.** The agent's state
  there is exactly two things: blocked or available.
- **`--enforce` overrides manual blocks.** For an agent in scope, Credo AI's decision wins: an agent
  an admin blocked by hand will be unblocked once its Use Case clears.
- **Block and unblock are on Microsoft's beta channel** — see the status note at the top.
- **The saved admin sign-in expires.** See Step 6.
