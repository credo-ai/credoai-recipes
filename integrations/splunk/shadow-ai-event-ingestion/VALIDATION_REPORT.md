# Validation Report — Splunk Shadow AI Event Ingestion Cookbook

Tracks [INT-1](https://credo-ai.atlassian.net/browse/INT-1) — Cookbook Validation Process sign-off.

- **Date:** 2026-09-07
- **Validated by:** Akshay Jayani
- **Cookbook path:** `integrations/splunk/shadow-ai-event-ingestion/`
- **Design basis:** team decision in `#credo-ai-integrations` Slack thread, 2026-09-03
  (auth via Integration Service `/auth/token`, bulk POST direct to backend
  `/api/v2/{tenant}/shadow_ai/ai_events/bulk`, cookbook flagged Research Preview)

## Summary

Cookbook is code-complete and functionally verified end-to-end against a local
stack (Splunk, `credoai-integration-service`, `credo-backend`). All reliability
behaviors documented in `TESTING.md` were exercised and matched spec. Two
scenarios (`401` mid-run, `422` bad-event) were not independently triggered —
see **Coverage gaps** below.

**Update, 2026-09-10:** `/code-review` was run against the full diff (both
this repo and the `credoai-integration-service` docs update). Five findings
in `main.py` were fixed and re-verified — see **7. Code review findings &
fixes** below. This still doesn't substitute for a second human reviewer.

## 7. Code review findings & fixes (2026-09-10)

`/code-review` flagged five issues in `main.py`, all fixed and re-verified
against the local stack:

| #   | File / line                                                         | Finding                                                                                                                                                                                                                              | Fix                                                                                                                                                                                                                                                                                                                             |
| --- | ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `main.py:98-102`, `SEARCH_TEMPLATE`                                 | No explicit sort — `head {limit}` on unsorted results could drop backlog events past the fetch limit, and out-of-order batches could make `run_cycle`'s per-batch checkpoint write regress backward, causing re-delivery on restart. | Added `                                                                                                                                                                                                                                                                                                                         | sort 0 _time`before` | head {limit}`. Verified ascending (oldest-first) order directly against real Splunk data (`security_lab` index) — see **Re-verification** below. |
| 2   | `main.py:262-268`, `CredoClient.send_batch`                         | Comment claimed a second consecutive `401` is "fatal, stop the poller," but it only raised `httpx.HTTPStatusError`, which `main()`'s outer loop catches and retries forever — contradicting the stated intent.                       | Changed to `raise SystemExit(1)`, which `main()`'s `except` clauses don't catch, so the process actually exits. Not independently live-triggered (needs a token that's valid at exchange but rejected at bulk-POST time — same hard-to-reproduce condition as the pre-existing `401`-mid-run gap); verified by code inspection. |
| 3   | `main.py:317-336`, `run_cycle`                                      | Dedup only checked the persisted `_seen_fingerprints` set, so two identical raw events fetched in the _same_ cycle weren't caught against each other (mark-as-sent only happens after a batch is sent).                              | Added a `seen_this_cycle` set checked alongside `_already_sent`. Not independently live-triggered (needs a genuine duplicate raw event in one fetch); logic reviewed, not a live-data reproduction.                                                                                                                             |
| 4   | `main.py:198-211` (`transform`) and `main.py:319-323` (`run_cycle`) | `_parse_time(raw.get("_time"))` was computed twice per event — once in `run_cycle`, again inside `transform()`.                                                                                                                      | `run_cycle` now computes `dt` once and passes it into `transform(raw, dt)`; `transform()` no longer parses time or returns `None` itself (malformed-timestamp handling moved entirely to the `dt is None` check in `run_cycle`, before `transform()` is ever called).                                                           |
| 5   | `main.py:165-179` (`fetch_events`), `226-260` (`CredoClient`)       | Every Splunk and Credo AI call opened a fresh `httpx.post(...)` — no connection reuse for a process that talks to the same two hosts for its whole lifetime.                                                                         | `main()` now creates two long-lived `httpx.Client()`s (Splunk and Credo AI kept separate — `SPLUNK_VERIFY_SSL`, often `false` for a lab's self-signed cert, must never apply to the Credo AI client), threaded through `fetch_events`/`CredoClient` instead of module-level `httpx.post`.                                       |

### Re-verification

Sort order (fix #1), checked directly against Splunk, independent of the
poller:

```
search index=security_lab sourcetype=shadow_ai_lab | sort 0 _time | head 5
→ 2026-08-21 17:25:19.016 UTC
  2026-08-21 17:25:40.693 UTC
  2026-08-21 17:25:40.698 UTC
  2026-08-21 17:25:40.701 UTC
  2026-08-21 17:25:40.704 UTC
```

Ascending, oldest first — correct.

Full poller re-run against the same local stack (fresh local API key,
`credo-backend` + `dev-app-1` + `splunk` all restarted since the original
run):

```
POST /auth/token                                    → 200 OK
POST /api/v2/credoai/shadow_ai/ai_events/bulk       → 201 Created
cycle: seen=45 sent=45 duplicates=0 malformed=0
```

Identical result to the original run (see **5. Live run — results** above),
confirming the `transform()` signature change (fix #4) and the persistent-
client refactor (fix #5) didn't regress anything. Boundary-tie dedup
(`seen=1 duplicates=1` on subsequent cycles) still correct.

Fixes #2 and #3 remain code-reviewed but not live-triggered — same category
of gap as the pre-existing `401`-mid-run/`422` items below, not a new one
introduced by this round.

## 1. Structural check

Cookbook matches the delivery-type-"cookbook" shape used by the JIRA and
ServiceNow cookbooks in this repo (`CLAUDE.md` — "Repository Overview"):

| Required                                                          | Present |
| ----------------------------------------------------------------- | ------- |
| `README.md` (setup, prerequisites, architecture, troubleshooting) | ✅      |
| `.env.example` (commented placeholders, no real credentials)      | ✅      |
| `TESTING.md`                                                      | ✅      |
| `server/python/` app + `requirements.txt`                         | ✅      |
| `.gitignore` (`.env`, `__pycache__/`, `*.pyc`, `checkpoint.txt`)  | ✅      |

No secrets committed — verified `.env` and `checkpoint.txt` are gitignored and
were never staged.

## 2. Code review (self-review)

Reviewed `server/python/main.py` against the design decision and against
`TESTING.md`'s documented behaviors:

- Auth (`CredoClient._get_token`) unchanged from the original design — still
  `POST {CREDO_BASE_URL}/auth/token` with `X-API-Key`/`X-Tenant`, per the
  Slack decision to keep using the Integration Service for token exchange.
- Bulk POST (`CredoClient.send_batch`) now targets
  `{CREDO_BACKEND_BASE_URL}/api/v2/{CREDO_TENANT}/shadow_ai/ai_events/bulk`,
  matching the decision to hit the backend directly.
- `BatchResult` enum (`DELIVERED`/`DROPPED`/`RETRY`) cleanly separates
  permanent failures (404/422 — skip and advance) from transient ones (5xx —
  hold checkpoint, retry next cycle). Matches INT-1's "error handling
  decision" AC line for line.
- Checkpoint is saved per-batch (`_save_checkpoint` inside the batch loop in
  `run_cycle`), not once per cycle — bounds a crash's re-send window to the
  one in-flight batch.
- In-memory fingerprint cache (`_seen_fingerprints`/`_seen_order`) prevents
  re-sending a batch caught twice by a checkpoint-boundary tie, without
  needing a backend idempotency key (none exists yet).

No correctness issues found in this pass. **Not a substitute for a second
reviewer** — flagging that as still open.

## 3. Backend cross-check

Cross-checked the implementation against the actual `credo-backend` and
`credoai-integration-service` source (both available locally) rather than
assuming the contract:

- `credoai-integration-service/src/app/core/clients/auth_client.py`
  (`AuthClient.exchange_token`) confirms `/auth/token` is a thin proxy: it
  calls `POST {backend}/auth/exchange` with `{api_token, tenant}` and returns
  that exact JWT. Same token contract the internal `cstool` demo bridge uses
  directly against `/auth/exchange`.
- `credo-backend/apps/credo_ai_web/lib/credo_ai_web/plug/auth/get_user.ex`
  validates JWTs generically (by issuer/claims) — no requirement that the
  token was obtained via `/auth/exchange` specifically, so a token from
  `/auth/token` is valid for the backend's `/api/v2/...` routes too.
- `credo_ai_web/lib/credo_ai_web/plug/auth/vendor_user_access.ex` only
  restricts `role: "vendor"` users; an API-key-derived service identity isn't
  that role, so it isn't blocked from the `shadow_ai` route scope.
- `credo_ai_web/lib/credo_ai_web/controllers/api/v2/shadow_ai/ai_event_controller.ex`
  confirms the `404`-on-missing-entitlement behavior
  (`check_shadow_ai_entitlement` plug) and the bulk-create path
  (`create_bulk`/`do_create_bulk`) — matches what the cookbook's
  Troubleshooting table and `TESTING.md` describe.

This is the basis for the "auth is compatible" call made mid-implementation —
verified in source, not assumed.

## 4. Cold-setup / live-run environment

**Not run against `gold_standard_demo`** (the tenant INT-1's AC names) or a
hosted lab tenant — QA/hosted access wasn't set up for this pass. Instead,
validated against a fully local stack, which INT-1's AC allows for
("equivalent lab tenant with entitlement on"):

- **Splunk:** `splunk/splunk:9.2.1` container (reused from
  `cstool/demos/shadow-ai-discovery/shadow_ai_pipeline`), index `security_lab`,
  sourcetype `shadow_ai_lab`, ~130k pre-seeded historical events with field
  names matching the cookbook's default `FIELD_MAP` — no mapping changes
  needed.
- **Integration Service:** `credoai-integration-service` dev container
  (`dev-app-1`), `CREDOAI_SERVER_BASE_URL` pointed at local `credo-backend`.
- **Backend:** local `credo-backend` (`mix phx.server`, port `4000`).
- **Tenant:** local `credoai` tenant, `SHADOW_AI` entitlement on, real API key.

**Real friction hit during setup** (worth noting for anyone reproducing this
locally — not a cookbook defect, an environment quirk):
`credo-backend`'s `AdminWeb.Endpoint` and `dev-app-1`'s published port both
default to host `5555` — running both locally requires remapping one
(`dev-app-1` moved to host `5556` here). Not documented anywhere; flagging in
case this trips up the next person setting up the same local stack.

## 5. Live run — results

```
POST https://localhost:8089/services/search/jobs/export       → 200 OK
POST http://localhost:5556/api/v1/integration/auth/token       → 200 OK
POST http://localhost:4000/api/v2/credoai/shadow_ai/ai_events/bulk → 201 Created
cycle: seen=45 sent=45 duplicates=0 malformed=0
```

45 seeded events round-tripped Splunk → transform → auth → bulk POST → `201`
on the first cycle. Subsequent cycles (`seen=1 duplicates=1`) show the
checkpoint-boundary-tie event being correctly caught by the in-memory
fingerprint cache instead of being re-sent — matches the documented dedup
behavior.

## 6. Failure-mode checks (`TESTING.md` Step 6)

| Scenario                                                       | Result                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| -------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Missing/garbage/empty `_time`                                  | ✅ Verified via direct unit call to `_parse_time()` — all three inputs return `None`, correctly counted as `malformed` and dropped in `run_cycle` before `transform()` is ever called. (Not reproducible through real Splunk indexing — Splunk always populates `_time` — so tested at the function level per `TESTING.md`'s own suggested approach. Note: as of the 2026-09-10 fixes, `transform()` itself no longer parses time or returns `None` — that check moved entirely to `run_cycle`, see **7. Code review findings & fixes**.) |
| Splunk unreachable                                             | ✅ Stopped the `splunk` container mid-run — logged `network error` every cycle, loop continued, no crash.                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Backend/Integration Service unreachable                        | ✅ Stopped `dev-app-1` mid-run — logged `network error` (fails at token exchange), checkpoint untouched.                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Backend `404`                                                  | ✅ Forced via a broken `CREDO_BACKEND_BASE_URL` — `WARNING` logged, batch dropped (`sent=0`), poller kept cycling, checkpoint not stalled.                                                                                                                                                                                                                                                                                                                                                                                                |
| Backend `422`                                                  | Not independently triggered — shares the exact same code branch as `404` (`BatchResult.DROPPED`, `main.py` `send_batch`), so the `404` result above covers it structurally.                                                                                                                                                                                                                                                                                                                                                               |
| `401` mid-run (token goes stale _after_ a successful exchange) | Not triggered — requires a real token expiry mid-run, not reproducible on demand. Code path (refresh once, retry, fatal on second `401`) reviewed but not exercised live.                                                                                                                                                                                                                                                                                                                                                                 |
| Restart mid-batch / checkpoint replay                          | ✅ Demonstrated across repeated runs — checkpoint advanced past delivered events; a restart only re-fetched the single checkpoint-boundary event, which the fingerprint cache caught as a duplicate.                                                                                                                                                                                                                                                                                                                                      |

## Coverage gaps / open items

- No second-person code review yet.
- Not run against `gold_standard_demo` or a hosted sandbox tenant — local-only.
- `401` mid-run and `422` failure paths verified by code inspection, not a
  live trigger.
- `credoai-integration-service` docs
  (`docs/docs/cookbooks/splunk-shadow-ai-ingestion.mdx`) updated to match —
  not yet committed/reviewed in that repo.
- Nothing in this repo has been committed or opened as a PR yet.
