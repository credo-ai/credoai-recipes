# Testing — Microsoft Agent 365 Governance Sync

Work through these in order. Steps 1–2 need no Credo AI tenant, so you can confirm the cookbook
installs, fails cleanly, and can reach Microsoft before arranging access to anything else.

Use a **lab** Microsoft 365 tenant and a **lab** Credo AI tenant, never production. Steps 7–8 block
a real agent: use one you own and are happy to see blocked for a few minutes.

---

## Step 1: Install and smoke-test, no Microsoft and no tenant

```bash
cd server/python
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py --help
```

The help text should name every flag in README Step 4. Then prove the two "could not start" paths,
neither of which needs credentials:

```bash
cp ../../.env.example ../../.env
printf 'AGENT365_NONSENSE=1\n' >> ../../.env       # an unrecognized key
python main.py --dry-run ; echo "exit=$?"          # expect exit=2 naming the key
```

Remove that line afterwards. With the placeholder values still in `.env`, a run must also exit `2`
with a message naming what is missing or wrong — never a traceback.

---

## Step 2: Confirm Microsoft access, no Credo AI involved

Prove the license, the app registration and the consent before introducing a tenant. Fill in
`MS_TENANT_ID`, `MS_CLIENT_ID` and `MS_CLIENT_SECRET` in `.env`, then:

```bash
python - <<'EOF'
from collections import Counter
from agent365_sync.config import get_settings
from agent365_sync.agent365 import Agent365Client

s = get_settings()
with Agent365Client(s.ms_tenant_id, s.ms_client_id, s.ms_client_secret) as client:
    packages = client.list_packages()
print(len(packages), "packages;", dict(Counter(p["type"] for p in packages)))
EOF
```

| Result                                               | Meaning                                                                      |
| ---------------------------------------------------- | ---------------------------------------------------------------------------- |
| `N packages; {'thirdParty': …, 'shared': …, …}`      | License, secret and `CopilotPackages.Read.All` consent are all good          |
| `no Microsoft Agent 365 license`                     | The tenant is not licensed. Nothing else will work until it is               |
| `access denied … CopilotPackages.Read.All`           | The permission is missing or was never consented — README Step 2, item 4     |
| `Entra refused the app credentials (invalid_client)` | The secret **ID** was used instead of the **Value**, or it expired           |
| `shared: 0` and `lob: 0`                             | Nobody has built an agent yet — create one in Agent Builder so there is data |

The `shared` and `lob` counts are the agents `AGENT365_PACKAGE_TYPES` selects by default.

---

## Step 3: Dry run — reads only

Fill in the Credo AI values, then:

```bash
python main.py --dry-run
```

Confirm:

- Every line is prefixed `[dry-run] would …` and the summary says **planned**, not complete
- Nothing appears in Credo AI afterwards
- `scanned` equals the `shared` + `lob` count from Step 2
- If two of your agents share a name, one `would create` line carries a suffix such as
  `Name (T_9f8e7d6c)` — names are unique in Credo AI, so duplicates must be told apart

---

## Step 4: Live run, a few agents

```bash
python main.py --limit 3
```

Confirm the summary reports `created=3` and `errors=0`, and that the exit code is `0`. In the
Governance App, each new Use Case should carry the agent's name, its description, who built it, and
an `Agent 365 asset ID:` line at the bottom — and the intake questionnaire, if you set one.

---

## Step 5: Idempotency — the important one

Run the identical command a second time without changing anything:

```bash
python main.py --limit 3
```

Expect **zero writes**:

```text
use cases scanned=3 created=0 updated=0 unchanged=3 questionnaires_attached=0 errors=0
```

`created=0` and `updated=0` on the second run is the whole safety argument for scheduling this. If
`created` is not `0`, the `Agent 365 asset ID:` line is not being read back — check the Use Case's
description.

---

## Step 6: Change detection

The marker stores when each agent last changed. Prove an edit is noticed, and a rename followed.
Pick two agents from Step 4 and run this with their asset IDs and Use Case names:

```bash
python - <<'EOF'
import re
from agent365_sync.config import get_settings
from agent365_sync.credo import CredoClient
from agent365_sync.sync import asset_id_of

ASSET_A, ASSET_B = "T_replace-with-an-asset-id", "T_replace-with-another"
s = get_settings()
with CredoClient(s) as credo:
    by_asset = {asset_id_of(u.description): u for u in credo.list_use_cases()}
    a, b = by_asset[ASSET_A], by_asset[ASSET_B]
    # A: backdate the stored timestamp, as if the agent had changed since the last sync
    stale = re.sub(r"(Agent 365 last modified: )\S+", r"\g<1>2000-01-01T00:00:00Z", a.description)
    credo.update_use_case(a.id, {"description": stale})
    # B: rename the Use Case, as if the agent had been renamed in Agent 365
    credo.update_use_case(b.id, {"name": b.name + " (renamed by hand)"})
EOF
python main.py --limit 3        # expect updated=2
python main.py --limit 3        # expect unchanged=3 again
```

The first run should report `updated=2`, put B's name back, and restore A's timestamp. The second
should be quiet again.

---

## Step 7: Enforcement

Enforcement blocks a real agent. Use one you own.

**Sign in once** (needs the README Step 2 items 5 and 6, and an account with the AI Administrator
or Global Administrator role):

```bash
python main.py --login
```

**Preview, then enforce on one agent:**

```bash
python main.py --enforce --dry-run --asset-id <your agent's asset id>
python main.py --enforce --asset-id <your agent's asset id>
```

Expect `block … its Use Case is not cleared`, then `enforcement … blocked=1`. Confirm it:

```bash
python - <<'EOF'
from agent365_sync.config import get_settings
from agent365_sync.agent365 import Agent365Client

s = get_settings()
with Agent365Client(s.ms_tenant_id, s.ms_client_id, s.ms_client_secret) as client:
    agent = next(p for p in client.list_packages() if p["id"] == "<your agent's asset id>")
print("isBlocked =", agent["isBlocked"])
EOF
```

Then run the `--enforce` command again. It must report `blocked=0 unblocked=0 unchanged=1` — no
call to Microsoft at all when nothing needs to change.

> **This is the step that proves the sign-in works for the agent type that matters.** An app-only
> token is rejected with a `424` when blocking agents built in Copilot Studio or Agent Builder.
> If your agent is one of those and the block succeeds, the delegated sign-in is doing its job.

---

## Step 8: Unblock on approval

1. In the Governance App, open the Use Case for the agent you blocked and take it through its
   whole workflow: complete the questionnaire, then clear every stage up to the last one.
2. Run `python main.py --enforce --asset-id <asset id>`.
3. Expect `unblock … its Use Case is cleared` and `unblocked=1`, and `isBlocked = False` when you
   check as in Step 7.

Also check the case that catches a naive implementation: take a **second** agent's Use Case through
only the **first** stage (intake) and clear it. Its step is now `clear`, but it has not finished
the workflow. `--enforce` must keep that agent **blocked**.

---

## Step 9: Failure modes

Each of these must degrade, not crash. Check the exit code every time.

| Scenario                         | How to trigger                                                          | Expected                                                                                            |
| -------------------------------- | ----------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Bad Credo AI key                 | `CREDO_API_KEY=wrong python main.py --dry-run`                          | Exit `2`: `Credo AI: Authentication failed: Invalid API key…`, no partial writes                    |
| Credo AI host unreachable        | `CREDO_API_BASE_URL=https://api.credo.invalid python main.py --dry-run` | Exit `2`, a readable message naming the host                                                        |
| Missing Microsoft secret         | `MS_CLIENT_SECRET= python main.py --dry-run`                            | Exit `2`: `Missing MS_CLIENT_SECRET…`                                                               |
| Wrong Microsoft secret           | `MS_CLIENT_SECRET=wrong python main.py --dry-run`                       | Exit `2`: `Entra refused the app credentials (invalid_client): AADSTS7000215…`                      |
| Unrecognized `.env` key          | Add `AGENT365_NONSENSE=1` to `.env`                                     | Exit `2` naming the key, not a traceback                                                            |
| Invalid package types            | `AGENT365_PACKAGE_TYPES=agents python main.py --dry-run`                | Exit `2` naming `AGENT365_PACKAGE_TYPES` and the allowed values                                     |
| Malformed questionnaire          | `CREDO_INTAKE_QUESTIONNAIRE=AGENT python main.py --dry-run`             | Exit `2` naming `CREDO_INTAKE_QUESTIONNAIRE` and the expected `<key>+<version>` shape               |
| Unknown asset ID                 | `python main.py --dry-run --asset-id T_nope`                            | Exit `2`: `Not in the Agent 365 catalog: T_nope`                                                    |
| `--enforce`, never signed in     | `AGENT365_TOKEN_CACHE=/tmp/none.json python main.py --enforce`          | Exit `2`: `No saved admin sign-in…` — before any write                                              |
| `--login`, no client ID          | `MS_CLIENT_ID= python main.py --login`                                  | Exit `2`: `Missing MS_CLIENT_ID`                                                                    |
| A Use Case operation is rejected | `CREDO_INTAKE_QUESTIONNAIRE=NOPE+9 python main.py --limit 3`            | Exit `1`; **every** agent still processed, one error per agent, the questionnaire named in the hint |
| No Agent 365 license             | Run against an unlicensed tenant                                        | Exit `2`: `no Microsoft Agent 365 license`                                                          |
| Throttled by Microsoft           | (rarely reproducible) Graph answers `429`                               | Retried with the `Retry-After` wait, up to 4 attempts, logged as warnings                           |

---

## Step 10: Confirm in Credo AI

In the Governance App:

1. Each in-scope agent appears as a **Use Case** named after the agent
2. Its description ends with the `Agent 365 asset ID:` and `Agent 365 last modified:` lines
3. The intake questionnaire is attached, if you configured one
4. Nothing was created for Microsoft's own or third-party apps, unless you set
   `AGENT365_PACKAGE_TYPES=all`

---

## Cold-setup test

Hand this cookbook to someone on your team who has never seen it. Time how long it takes them to
reach a successful `python main.py --dry-run` against their own tenants, using only `README.md` and
this file. Log every point of confusion.

Watch specifically for the secret **Value** versus **ID** in README Step 2, and the admin consent
that must be granted separately for the application and the delegated permission. Both are easy to
miss, and both fail in ways that don't obviously name their cause.
