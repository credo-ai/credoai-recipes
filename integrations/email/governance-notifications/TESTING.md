# Testing: Email Governance Notifications

## Local test (no webhook subscription, no email sent)

In `.env`, set `MAIL_DRY_RUN=true` (SMTP settings can stay as placeholders). Without running
`setup_webhook.py`, put a token and a test secret in `.env` yourself:

```bash
python -c 'import secrets; print("WEBHOOK_PATH_TOKEN=" + secrets.token_urlsafe(32))' >> .env
python -c 'import base64, secrets; print("WEBHOOK_SIGNING_SECRET=whsec_" + base64.b64encode(secrets.token_bytes(24)).decode())' >> .env
```

(Remove the placeholder lines for those two settings first.) Start the server:

```bash
cd server/python
python main.py
```

In a second terminal, post a fixture with `replay.py`. It signs the post with `WEBHOOK_SIGNING_SECRET`, as
Credo AI does. The server re-reads everything through the SDK, so replace the fixture's ids with real ones from
your tenant (`--set` takes the camelCase field name under `data`):

```bash
cd server/python
# #5: a model you created, and your own user id
python replay.py entity_created_model --set modelId=<model id> --set user.userId=<your user id>
# #2/#7: an approved review on a use case
python replay.py entity_review_status_updated --set entityId=<use case entity id> --set reviewId=<review id>
# #1/#4: a use case and the stage it's in now (previousWorkflowStageId: the stage it came from)
python replay.py entity_workflow_stage_updated --set entityId=<id> --set workflowStageId=<id> \
  --set previousWorkflowStageId=<id>
```

`replay.py` prints `202`. The server log shows each rendered email (`MAIL_DRY_RUN: not sending`), then
`delivery msg_replay_… (<event type>): sent N (MAIL_DRY_RUN)`. Run the same command again: it prints `202`,
and the log says `sent 0, N already sent`. With made-up ids the log says why nothing was sent, for example
`nothing sent (review not found in Credo AI)`.

Reminders don't need a webhook at all:

```bash
python remind.py pending_controls
python remind.py pending_questionnaires
```

To start over, stop the server and delete `notifier.sqlite3*` in the cookbook root. Dry runs are recorded as
sent too, so delete it before a real test.

## Things that should be refused or ignored

```bash
# Signed with the wrong secret, or not signed: 401, nothing stored
python replay.py entity_created_model --bad-signature
python replay.py entity_created_model --unsigned

# A use case's entity_created: 202, then "nothing sent (not a model)"
python replay.py entity_created_use_case

# Same svix-id twice: the second is 204
python replay.py entity_created_model --svix-id test-1
python replay.py entity_created_model --svix-id test-1

# Wrong token: 404
curl -i -X POST http://localhost:8000/webhook/not-the-real-token
```

## End-to-end test

1. Set `MAIL_DRY_RUN=false` and real SMTP settings (Mailtrap's sandbox works well).
2. Expose the server over HTTPS, for example `ngrok http 8000`.
3. Run `python setup_webhook.py --public-url https://<ngrok host>` and start the server.
4. In Credo AI, as a use case owner: move a use case to its next stage (#1, #4); mark a control's evidence
   ready for validation (#3); open a review, change a questionnaire answer (#9), then approve the review
   (#2, #7). Add a model (#5).
5. Run `python remind.py pending_controls` and `python remind.py pending_questionnaires` (#6, #8; they need a
   use case with an assigned, open control task and a questionnaire due within 7 days).
6. Each email arrives within a few seconds.

When you're done, delete the webhook in **Settings → Webhooks**.

---

## Common failures

| Symptom                                                                                   | Cause → fix                                                                                                                                                                                                                    |
| ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `Configuration error in .env` on start                                                    | A required setting is missing or malformed: `WEBHOOK_PATH_TOKEN` shorter than 32 characters, `WEBHOOK_SIGNING_SECRET` not starting with `whsec_`, an unknown `REMINDER_TIMEZONE`, a bad cron expression. The message names it. |
| **Every** delivery gets `401`; log: `rejected a delivery with a bad or missing signature` | `WEBHOOK_SIGNING_SECRET` is wrong, belongs to another webhook, or the secret was rotated. Copy the current one from **Settings → Webhooks → Signing secret** (or rerun `setup_webhook.py --replace`) and restart.              |
| Some deliveries get `401`                                                                 | The server's clock is more than 5 minutes off. Fix its time sync (NTP).                                                                                                                                                        |
| Credo AI shows deliveries failing with `404`                                              | The token in the webhook URL doesn't match `WEBHOOK_PATH_TOKEN`, or the URL is missing the `/webhook/` prefix.                                                                                                                 |
| `setup_webhook.py`: `Couldn't create the webhook: ApiError ... 403`                       | The API key's user can't manage webhooks. Use an admin service user (README "API key permissions").                                                                                                                            |
| Log: `retrying in ...` with `CredoUnavailable: ApiError ... 403`                          | The API key's user is missing a permission: `user_view`/`user_list` for recipients, admin for task listing (#3, #6). Use an admin service user.                                                                                |
| Log: `nothing sent (no use case owner to email)`, `skipped 1 team(s)`                     | The use case's owners are teams, or users who were deleted or haven't accepted their invitation. Add a user owner.                                                                                                             |
| Log: `nothing sent (stale: ...)`                                                          | The state moved on before the delivery was handled (the use case changed stage again, the task left "ready for validation"). Nothing to fix.                                                                                   |
| Log: `nothing sent (no wording in stage_messages.yaml ...)`                               | The stage has no `on_enter`, the stage it left has no `on_leave`, and there's no `default`. Add wording if you want that email.                                                                                                |
| Log: `retrying in ...` then `giving up after 9 attempts (MailError ...)`                  | The SMTP server rejected the login or the connection for about an hour. Check `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY` and credentials. Mailtrap's free tier also needs `SMTP_MIN_INTERVAL_SECONDS=1.2`.                      |
| A reminder never arrives                                                                  | `REMINDERS_IN_PROCESS=false` with no cron running `remind.py`, or the cron time is in `REMINDER_TIMEZONE`, not the server's local time. The startup log prints each reminder's next run.                                       |
| `/healthz` answers `503`; log: `worker: unexpected error`                                 | The worker couldn't read or write `notifier.sqlite3` (disk full, file permissions, another process holding a lock). Fix the cause; the worker tries again every 30 s and recovers on its own.                                  |
