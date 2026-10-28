# Testing — GitHub Deploy Gate

Work through these in order. Steps 1–2 need no GitHub repo and no Credo AI tenant.

---

## Step 1: Offline test suite

Fakes stand in for Credo AI and GitHub — no network, no credentials.

```bash
cd server/python
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest
```

All tests should pass. They cover use case create/reuse, re-push handling, approve and reject,
duplicate and unknown webhook deliveries, the shared secret, webhook register/delete on
startup/shutdown, and `teardown`.

---

## Step 2: Smoke-test the CLI, no credentials

```bash
python main.py --help
python main.py status             # prints an empty state
python main.py serve ; echo "exit=$?"
```

With no `.env`, `serve` must exit `2` naming the missing config keys — never a traceback.

---

## Step 3: End-to-end with a real repo

Use a throwaway **public** repo and a Credo AI dev tenant.

**Set up the repo** (README Step 2): gate branch, both workflows, a `scripts/deploy_agent.py`
containing `registered_model_name="demo-agent"`, the `GATE_SERVICE_URL` variable, and branch
protection requiring `deploy-gate/approval`. The deploy workflow will fail at the end without
Databricks secrets — that is expected; the gate itself does not need them.

**Start the service:**

```bash
ngrok http 8321                   # copy the https URL into .env as GATE_PUBLIC_BASE_URL
                                  # and into the repo variable GATE_SERVICE_URL
python main.py -v serve
```

The log should show `Registered webhook <id> -> <url>/webhooks/credo`.

### Approval path

1. Open a PR into the gate branch.
2. The `Deploy Gate (notify)` workflow runs; the PR check `deploy-gate/approval` is **pending** and
   the merge button is blocked.
3. The service log shows `Created use case ... for PR ...`. Confirm with
   `curl localhost:8321/gate/state` or `python main.py status`.
4. In Credo AI, open the use case `Databricks: demo-agent`, start a review, **approve** it.
5. The log shows `Gate approved ...`; the check turns **success** and the PR becomes mergeable.
6. Merge. The `Deploy Agent (Databricks)` workflow starts.

### Rejection path

Repeat with a new PR (or a new push to the same PR) and **reject** the review.

- The check turns **failure**, the PR stays blocked, no deploy runs.
- If it stays pending instead, your tenant's rejection status string differs — see README
  "Behavior to know about".

### Re-arm

Push a new commit to the open PR after approving. The check goes pending again on the new commit
and the same use case is reused (`action: sha_updated` in `/gate/state`'s log line).

---

## Step 4: Cleanup

1. Ctrl-C the server — it deletes the webhook. Confirm the log shows `Deleted webhook`.
2. If the server was killed instead: `python main.py teardown --dry-run`, then
   `python main.py teardown --delete-use-cases`.
3. Close the test PR; remove the two workflow files, the `GATE_SERVICE_URL` variable and the branch
   protection rule from the throwaway repo.
