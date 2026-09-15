# Testing — Splunk Shadow AI Event Ingestion

> Steps 4–6 below need a Credo AI tenant with the `SHADOW_AI` entitlement
> enabled (see the preview note in [README.md](./README.md) — this cookbook
> calls the backend Shadow AI bulk endpoint directly, not the public
> Integration Service). Without that entitlement, you can still validate
> everything through Step 3 against Splunk alone.

## Step 1: Confirm Splunk access, no Credo AI involved

```bash
curl -sk -u "$SPLUNK_USERNAME:$SPLUNK_PASSWORD" \
  "https://$SPLUNK_HOST:$SPLUNK_PORT/services/search/jobs/export" \
  -d "search=search index=$SPLUNK_INDEX sourcetype=$SPLUNK_SOURCETYPE earliest=-1h | head 5" \
  -d "output_mode=json"
```

Expect newline-delimited JSON result objects. If this returns nothing, fix
the index/sourcetype/credentials before going further — nothing past this
point will work either.

## Step 2: Dry-run the transform, no network calls

Run `main.py` up to `run_cycle` in a REPL, or temporarily comment out the
`credo.send_batch(...)` line, and confirm `transform()` produces sane output
for a handful of real events — correct `timestamp`, no unexpectedly-`None`
fields for data you know exists.

## Step 3: Checkpoint behavior

1. Run the poller for one cycle, then stop it (Ctrl-C).
2. Confirm `checkpoint.txt` now holds a timestamp past your test data's
   latest event.
3. Add one new matching event to your test index.
4. Restart the poller — confirm the next cycle's `seen` count is 1, not a
   replay of everything from Step 1. This is the behavior that keeps a
   restart from re-sending already-delivered events.

## Step 4: Local test against Credo AI

Point `.env` at a **lab/sandbox tenant only** — never production. Run the
poller against a Splunk instance with a handful of known test events, and
confirm the log line:

```text
cycle: seen=5 sent=5 malformed=0
```

`sent` should track `seen` minus anything genuinely malformed — not zero,
not stuck.

## Step 5: Confirm in Credo AI

Log into the sandbox tenant, reload `/ai-discovery/insights` (this surface
doesn't auto-poll), and confirm the event count matches what the poller
reported as `sent`.

## Step 6: Failure-mode checks

| Scenario                                      | Expected behavior                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| One event in a batch has no `_time`           | Dropped, counted in `malformed`, rest of the batch still sent                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Credo AI returns 401 mid-run                  | Token refreshed once automatically, retried                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Credo AI returns 404 on the bulk endpoint     | Batch logged as dropped (`WARNING`) — permanent failure, not retryable. Checkpoint advances past it and the poller moves on to the next batch instead of stalling the whole cycle                                                                                                                                                                                                                                                                                                                                                                                            |
| Credo AI returns 422 (bad event in the batch) | Same as 404 — batch dropped, logged with the response body, checkpoint advances past it, poller continues with the next batch                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Credo AI returns 5xx / times out on a batch   | Transient — retried up to 3x with backoff, then the cycle stops there. Checkpoint does **not** advance past this batch, so it's retried next cycle instead of skipped                                                                                                                                                                                                                                                                                                                                                                                                        |
| Splunk unreachable for one cycle              | Logged as a network error, loop continues on the next interval — not a crash                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Poller restarted mid-batch                    | Checkpoint is saved after every batch (not once per cycle), so at most the one in-flight batch may be re-sent — everything already handled earlier in the cycle is not re-fetched. No backend idempotency key exists yet, so an in-memory fingerprint cache (`_seen_fingerprints` in `main.py`) skips re-sending any event already delivered earlier in the same process's lifetime; it does not survive a restart, so the eventual backend should still tolerate re-delivery of that one in-flight batch (flag this as an open question for the backend design, see README) |

## Cold-setup test (per the Cookbook Validation Process)

Hand this repo to someone who has never seen it. Time how long it takes them
to reach a running poller printing `cycle: ...` lines against their own test
Splunk data, using only this README and this file. Log every point of
confusion — that log is part of the validation report.
