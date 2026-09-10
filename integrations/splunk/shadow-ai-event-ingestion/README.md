# Splunk → Credo AI Shadow AI Event Ingestion

A poller that reads AI-usage / CASB-style activity events out of a Splunk index and
forwards them to Credo AI's Shadow AI surfaces (`/ai-discovery/*`) — no agent on
your Splunk instance, no forwarder changes, no export scheduling on your side
beyond running this process.

- All code runs in your own environment, next to (or wherever can reach) your Splunk instance
- Credentials stored as environment variables, never in source
- Works with any Splunk index/sourcetype that carries AI-tool activity — Splunk-native
  CASB add-ons, a custom TA, or events you already forward from Netskope/your CASB into Splunk

---

> ### 🧪 Status: Preview — talks to a backend API directly
>
> Shadow AI Discovery is a **Research Preview** feature and has no endpoint on
> the public Integration Service yet (not currently planned — see
> [INT-1](https://credo-ai.atlassian.net/browse/INT-1)). By team decision,
> this cookbook gets its bearer token the normal way — `POST /auth/token` on
> the Integration Service (`CREDO_BASE_URL`) — but sends the bulk event POST
> straight to the backend (`CREDO_BACKEND_BASE_URL`):
> `POST /api/v2/{tenant}/shadow_ai/ai_events/bulk`.
>
> That backend endpoint is **not part of the stable public API contract** —
> it can change shape or move without notice. If it does, this cookbook needs
> an update. Once Shadow AI ships on the public Integration Service, this
> cookbook should be migrated to use it instead.

---

## How it works

```text
Your Splunk (index=..., sourcetype=...)
   │
   │  REST search export, every POLL_INTERVAL_SECONDS
   ▼
this poller  (transform + checkpoint)
   │
   │  POST /auth/token                              (Integration Service)
   │  POST /api/v2/{tenant}/shadow_ai/ai_events/bulk (backend — see status above)
   ▼
Credo AI  →  /ai-discovery/{insights,ai-tools,user-activity}
```

Unlike the JIRA and ServiceNow cookbooks in this repo, there's no webhook to
register — Splunk doesn't push. This poller pulls on a schedule, using the same
`/services/search/jobs/export` REST API any external tool would use to read
your data back out. It keeps a local checkpoint of the last event timestamp
seen, so a restart never re-sends events already delivered.

---

## Prerequisites

| Item                          | Where to get it                                        |
| ------------------------------ | ------------------------------------------------------- |
| **`SHADOW_AI` entitlement**    | **Must be enabled on your Credo AI tenant — ask your Credo AI representative. Without it, the bulk endpoint returns `404` and no events are ingested.** |
| Integration Service base URL   | Provided by Credo AI                                     |
| Backend base URL                | Provided by Credo AI — see the preview note above; different host from the Integration Service base URL |
| API key                        | Credo AI Governance App → Settings → Integrations        |
| Tenant name                    | Your org's tenant identifier in Credo AI                  |
| Splunk REST access (port 8089) | Reachable from wherever you run this poller               |
| A Splunk service account       | See Step 2 — **read-only**, scoped to one index, not admin |

---

## Step 1: Set your Credo AI credentials

```bash
cp .env.example .env
# Edit .env — CREDO_API_KEY, CREDO_TENANT, CREDO_BASE_URL, CREDO_BACKEND_BASE_URL
```

---

## Step 2: Create a scoped Splunk service account

Don't point this at an admin account. In Splunk Web: **Settings → Users and
Authentication → Roles** → create a role with only the `search` capability,
restricted (via **Indexes searched by default**) to the index carrying your
AI-activity events. Create a user with that role, and use its credentials in
`.env` — this poller only ever runs read-only searches.

---

## Step 3: Point the poller at your data

Edit `.env` to match your Splunk instance's actual index/sourcetype, and edit
the SPL search in `main.py` (`SEARCH_TEMPLATE`) if your fields are named
differently. Then map your fields to Credo AI's schema:

| Credo AI Field         | Your Splunk Field (example)   | Notes                                   |
| ----------------------- | ------------------------------ | ---------------------------------------- |
| `timestamp`             | `_time`                        | required — events without a parseable time are dropped |
| `user_email`            | `user_email` / `user`          | who took the action                      |
| `app_name`              | `app_name` / `app`             | e.g. "ChatGPT", "GitHub Copilot" — drives `/ai-discovery/ai-tools` |
| `category`              | `category`                     | e.g. `chatbot`, `code_assistant`, `image_generation` |
| `action`                | `action`                       | e.g. "Allowed", "Blocked"                |
| `url` / `referrer_url`  | `url` / `referrer_url`         | optional                                 |
| `department`            | `department`                   | optional                                 |
| `device_hostname`       | `device_hostname`              | optional                                 |
| `risk_score`            | `risk_score`                   | optional, clamped 0–100 on send          |

If your Splunk data comes from a specific CASB TA, its field names almost
certainly differ from the example column above — adjust the `FIELD_MAP` dict
in `main.py` accordingly; don't rename your Splunk-side fields to match ours.

---

## Step 4: Run the poller

```bash
cd server/python
pip install -r requirements.txt
python main.py
```

There's no port to expose and nothing to tunnel — this isn't an inbound
webhook receiver, it's a long-running process that reaches out to both Splunk
and Credo AI. Run it as a systemd service, a container, or a scheduled task —
whatever fits how you run long-lived internal tooling today.

---

## Step 5: Test locally

See [TESTING.md](./TESTING.md) — no ngrok needed (nothing inbound to expose),
but you do need a Splunk instance with some matching data to poll against, even
a small sandbox one.

---

## Troubleshooting

| Symptom                                  | Likely Cause                                  | Fix                                              |
| ----------------------------------------- | ---------------------------------------------- | -------------------------------------------------- |
| `401` on `/auth/token`                    | Wrong `CREDO_API_KEY` or `CREDO_TENANT`        | Check `.env` values                                |
| `404` on bulk POST                        | `SHADOW_AI` entitlement not enabled, or wrong `CREDO_BACKEND_BASE_URL` | Confirm the entitlement and backend base URL with Credo AI. The poller logs a `WARNING`, treats the batch as permanently dropped (not retryable), and moves on to the next batch rather than stalling the cycle |
| Splunk search returns nothing             | Wrong `SPLUNK_INDEX` / `SPLUNK_SOURCETYPE`, or `earliest` past your checkpoint | Check `.env`; delete `checkpoint.txt` to reset the cursor |
| All fields null on ingested events        | Splunk's `| fields ...` clause not listing your field names | Update `SEARCH_TEMPLATE` in `main.py`             |
| Duplicate events after a restart          | Checkpoint file deleted or not persisted        | Confirm `CHECKPOINT_PATH` points at durable storage, not a tmpfs that clears on restart. Checkpoint is saved after every batch, and an in-memory fingerprint cache skips re-sending anything already delivered in the current process, so a restart can only duplicate the one batch that was in flight at crash time |
| Poller runs but nothing ever sends        | `risk_score`/timestamp parsing rejecting every event | Check poller logs for per-cycle `malformed` count |

Still stuck? Slack: `#credo-ai-integrations` | Support: support.credo.ai

---

## Full guide

Not yet published — pending Cookbook Validation Process sign-off (see
[INT-1](https://credo-ai.atlassian.net/browse/INT-1)).
