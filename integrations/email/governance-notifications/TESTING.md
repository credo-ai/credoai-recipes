# Testing: Email Governance Notifications

## Local test (no webhook subscription, no email sent)

In `.env`, set `MAIL_DRY_RUN=true` (SMTP settings can stay as placeholders). Start the server:

```bash
cd server/python
python main.py
```

On the first start the log says `first start: marked N existing models as already emailed`.

Create a model in Credo AI **after** that line (so it isn't one of the seeded ones), copy its id
from the URL (`/models/<id>`), then, in a second terminal:

```bash
cd server/python
python replay.py --model-id <the new model id> --email you@<an allowed domain>
```

`replay.py` prints `202`. The server log shows the rendered email (`MAIL_DRY_RUN: not sending`)
followed by `dry_run model_added:<id> to you@...`. Run the same command again: it prints `202`, and
the log says `nothing sent (already emailed for this model)`.

To start over, stop the server and delete `notifier.sqlite3*` in the cookbook root.

## Things that should be ignored

```bash
# A use case, not a model: 204, nothing stored
python replay.py --fixture ../../fixtures/entity_created_use_case.json

# Wrong token: 404
python replay.py --token not-the-real-token --model-id <id> --email you@<allowed domain>

# Same svix-id twice: the second is 204
python replay.py --svix-id test-1 --model-id <id> --email you@<allowed domain>
python replay.py --svix-id test-1 --model-id <id> --email you@<allowed domain>
```

## End-to-end test

1. Set `MAIL_DRY_RUN=false` and real SMTP settings (Mailtrap's sandbox works well).
2. Expose the server over HTTPS, for example `ngrok http 8000`.
3. Create the webhook subscription (README Step 5) with `https://<ngrok host>/webhook/<token>`.
4. Add a model in Credo AI as a user whose email is in `ALLOWED_EMAIL_DOMAINS`.
5. The email arrives within a few seconds.

---

## Common failures

| Symptom                                                                  | Cause → fix                                                                                                                                                                             |
| ------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Configuration error in .env` on start                                   | A required setting is missing, or `WEBHOOK_PATH_TOKEN` is shorter than 32 characters. The message names the setting.                                                                    |
| `First start needs the list of existing models ...` and the server exits | The API key, tenant or `CREDO_API_BASE_URL` is wrong, or Credo AI is unreachable. Fix and restart; nothing was stored.                                                                  |
| Credo AI shows deliveries failing with `404`                             | The token in the webhook URL doesn't match `WEBHOOK_PATH_TOKEN`, or the URL is missing the `/webhook/` prefix.                                                                          |
| Log: `recipient domain '...' is not in ALLOWED_EMAIL_DOMAINS`            | The user who added the model has an address outside your domains (common for API-key users). Add the domain, or accept that those models aren't emailed about.                          |
| Log: `already emailed for this model` for a brand-new model              | The model existed when the server first started, so it was seeded. Only models added after the first start are emailed about.                                                           |
| Log: `model not found in Credo AI`                                       | The event names a model the API key can't see: deleted since, a different tenant, or a fake event. Check the key's user can open the model in the app.                                  |
| Log: `retrying in ...` then `giving up after 6 attempts (MailError ...)` | The SMTP server rejected the login or the connection. Check `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY` and credentials. Mailtrap's free tier also needs `SMTP_MIN_INTERVAL_SECONDS=1.2`. |
| No **Settings → Webhooks** page in Credo AI                              | Webhook settings aren't enabled for your tenant. Ask your Credo AI contact.                                                                                                             |
