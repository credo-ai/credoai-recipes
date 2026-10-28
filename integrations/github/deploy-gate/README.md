# GitHub Deploy Gate → Credo AI

A GitHub PR cannot merge — and therefore cannot deploy — until a Credo AI use case review is
approved. A small service you run sits between GitHub and Credo AI: it opens a use case for every
gated PR, then flips the PR's required check to green or red when the reviewer decides.

- Runs in your own environment, anywhere GitHub and Credo AI can both reach it over HTTPS
- Credentials stored as environment variables, never in source
- Approval happens in the Credo AI UI — no extra tooling for reviewers

---

## How it works

```text
PR opened against the gate branch
  │
  └─ GitHub Action "Deploy Gate (notify)"
       ├─ sets commit status  deploy-gate/approval = pending     ⛔ merge blocked (required check)
       ├─ reads registered_model_name from scripts/deploy_agent.py (AST parse, nothing is run)
       └─ POST {GATE_SERVICE_URL}/gate/notify
            {repo, pr_number, pr_title, model_name, head_sha, html_url}
              │
              └─ Gate service
                   ├─ creates (or reuses) Credo AI use case "Databricks: <model_name>"
                   └─ remembers  use_case_id -> PR  in a local state file

Reviewer approves (or rejects) the use case review in Credo AI
  │
  └─ Credo AI webhook  entity_review_status_updated  ->  POST /webhooks/credo
       └─ Gate service looks up use_case_id -> PR
            ├─ approved  -> commit status = success   ✅ PR mergeable
            └─ rejected  -> commit status = failure   ⛔ PR stays blocked

Merge -> push to the gate branch -> "Deploy Agent (Databricks)" workflow runs
```

**Credo AI calls this cookbook makes**

| Call                          | API                                                |
| ----------------------------- | -------------------------------------------------- |
| Create / delete a use case    | Public Integration API, via the `pycredoai` SDK    |
| Register / delete the webhook | **Private API** — no public API / SDK endpoint yet |

Webhook registration stays on the private API (`github_gate/credo_private.py`) for that reason;
everything else uses the SDK. The one GitHub call the service makes is
`POST /repos/{repo}/statuses/{sha}`.

---

## Prerequisites

| Item                 | Where to get it                                                                          |
| -------------------- | ---------------------------------------------------------------------------------------- |
| Credo AI API key     | https://app.credo.ai/my-settings/tokens/                                                 |
| Credo AI tenant name | Your organization's tenant identifier (used to log in to Credo AI)                       |
| GitHub token         | Classic PAT: `repo:status` scope. Fine-grained: **Commit statuses: write** on the repo   |
| Public HTTPS URL     | Where the service is reachable. For local testing use a tunnel such as `ngrok http 8321` |
| Python               | 3.10+                                                                                    |

Both Credo AI (the webhook) and your GitHub workflow (the notify call) must be able to reach the
service, so `localhost` alone is not enough.

---

## Step 1: Configure the service

From the **cookbook root** — the directory holding this README:

```bash
cp .env.example .env
# Edit .env — CREDOAI_API_KEY, CREDOAI_TENANT, GITHUB_TOKEN, GATE_PUBLIC_BASE_URL
```

`.env` belongs here, not in `server/python`. The service finds it from any working directory.

---

## Step 2: Set up the GitHub repo

1. **Create the gate branch** (the branch whose merges deploy), e.g. `deploy-gate-develop`.
2. **Add both workflows** from [`workflows/`](workflows/) to the repo's `.github/workflows/`:
   - `deploy-gate-notify.yaml` — arms the gate on every PR against the gate branch
   - `deploy-agent-databricks.yaml` — deploys on push to the gate branch (the example deploy; swap
     in your own)

   If your gate branch is not `deploy-gate-develop`, change it in both files.

3. **Make the model name readable.** The notify workflow reads the deployed model name from
   `scripts/deploy_agent.py` — it looks for a `registered_model_name="..."` keyword argument and
   parses it with `ast`, so nothing is imported or executed. That file must exist and contain such a
   call, or the notify step fails. The name becomes the Credo AI use case name
   (`Databricks: <model_name>`).
4. **Tell the workflow where the service is:**

   ```bash
   gh variable set GATE_SERVICE_URL --body "https://<your-service-url>"
   # optional, pairs with GATE_SHARED_SECRET in .env:
   gh secret set GATE_SHARED_SECRET
   ```

5. **Require the check.** Add branch protection on the gate branch that requires the status check
   `deploy-gate/approval`. **This is the gate** — without it nothing blocks the merge. If GitHub
   offers a source choice for the check, pick **Any source**: the pending status comes from the
   workflow's token but the approval comes from your PAT.

> `deploy-gate/approval` is a contract string. It appears in the notify workflow, in
> `github_gate/github.py`, and in the branch protection rule. Rename it everywhere or PRs stay
> blocked indefinitely.

---

## Step 3: Install and run

```bash
cd server/python
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py serve
```

On startup the service registers a Credo AI webhook for `entity_review_status_updated` pointing at
`{GATE_PUBLIC_BASE_URL}/webhooks/credo` and logs its id. **Ctrl-C deletes it.**

| Command                                      | What it does                                       |
| -------------------------------------------- | -------------------------------------------------- |
| `python main.py serve`                       | Run the service                                    |
| `python main.py status`                      | Print the webhook id and every tracked PR          |
| `python main.py teardown`                    | Delete the tracked webhook and forget tracked PRs  |
| `python main.py teardown --delete-use-cases` | Also delete the tracked use cases from your tenant |
| `python main.py teardown --dry-run`          | Show what teardown would do                        |

`teardown` is the backstop for a crash: the webhook id is saved in `.github-gate-state.json`, so it
can still be cleaned up after a kill that skipped the shutdown cleanup.

### Service routes

| Route                  | Purpose                                                                             |
| ---------------------- | ----------------------------------------------------------------------------------- |
| `POST /gate/notify`    | Called by the workflow. Guarded by `X-Gate-Secret` when `GATE_SHARED_SECRET` is set |
| `POST /webhooks/credo` | Called by Credo AI when a review's status changes                                   |
| `GET /gate/state`      | Tracked PRs and the webhook id                                                      |
| `GET /health`          | Liveness                                                                            |

---

## Using it

1. Open a PR against the gate branch. The check `deploy-gate/approval` turns **pending**.
2. In Credo AI, open the use case `Databricks: <model_name>`, start a review, and **approve** it.
   (There is no API to start a review — that step is deliberately manual.)
3. The check turns green, the PR becomes mergeable. Merge it to deploy.
4. To see the rejection path: reject the review instead. The check turns **failure**, the PR stays
   blocked and the deploy never runs. Pushing a new commit re-arms the gate.

---

## Behavior to know about

- **One use case per model name, not per PR.** A second PR deploying the same model reuses the
  existing use case, and the state entry is re-pointed at the newest PR. Concurrent PRs for the
  _same_ model therefore collide; different model names are tracked independently.
- **Approval applies to the latest commit the service saw.** A push after approval re-arms the gate
  (a new pending status on the next notify, same use case).
- **Webhook deliveries are not signature-verified** — the webhook is registered with no
  authentication. `GATE_SHARED_SECRET` protects `/gate/notify` only. Put the service behind your own
  network controls before relying on it beyond a trial.
- **The rejection status string is tenant-dependent.** `approved` is handled; `rejected` and
  `denied` are treated as rejection. If your tenant uses another word, add it to `REJECTED_STATUSES`
  in `github_gate/gate.py`.
- **Single process, JSON state file, no locking.** Run one instance.
- **A tunnel URL changes between runs** (free ngrok). Update both `GATE_PUBLIC_BASE_URL` in `.env`
  and the `GATE_SERVICE_URL` repo variable, then restart.

---

## Troubleshooting

| Symptom                                              | Likely cause                                                                       |
| ---------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `Missing required config: ...` on `serve`            | `.env` missing or incomplete — see Step 1                                          |
| Notify step fails: `registered_model_name not found` | `scripts/deploy_agent.py` missing or has no `registered_model_name="..."` argument |
| Notify step fails with `403`                         | `GATE_SHARED_SECRET` set on the service but not (or differently) as a repo secret  |
| Notify step fails to connect                         | `GATE_SERVICE_URL` stale or tunnel not running                                     |
| PR stays pending after approval                      | Webhook never arrived — check `GATE_PUBLIC_BASE_URL` and the service log           |
| Service log: `Failed to update commit status`        | `GITHUB_TOKEN` lacks commit-status write on the repo                               |
| Merge not blocked while pending                      | Branch protection does not require `deploy-gate/approval`                          |

---

See [TESTING.md](TESTING.md) for the offline test suite and the end-to-end walkthrough.
