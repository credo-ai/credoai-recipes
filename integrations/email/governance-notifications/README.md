# Credo AI → Email: Governance Notifications

A small server you run that turns Credo AI webhooks into emails. This first release handles one
scenario:

> **A model is added to the registry → email the person who added it**, with the model's name,
> status and summary and a link to it in Credo AI.

More scenarios (workflow stage changes, review approvals, controls ready for validation, weekly
digests) will be added to the same server in later releases.

- All code runs in your own environment; you choose the mail provider
- Model details are read through the official [`pycredoai`](https://pypi.org/project/pycredoai/) SDK
- The API key only needs to **read models**

---

> [!WARNING]
> **Webhook signature verification is off.** Credo AI doesn't currently give customers the signing
> secret for their webhooks, so this server can't prove a request came from Credo AI. **Anyone who
> learns the webhook URL can send it fake events.** Treat the URL as a password. The server limits
> what a fake event can do:
>
> - **Secret URL.** It only answers at `/webhook/<token>`, where the token is long and random. A wrong token
>   gets the same `404` as any other unknown path.
> - **Allow-listed recipients.** It only emails addresses in your `ALLOWED_EMAIL_DOMAINS`.
> - **Checked against Credo AI.** It reads the model back through the SDK before every email and
>   sends nothing if the model doesn't exist. Nothing from the webhook payload goes into the email.
> - **One email per model, ever.** A repeated event, real or fake, never sends a second one.
>
> **What that still allows:** someone holding the URL can pick a model that hasn't been emailed
> about yet and send that one email to any address in your allowed domains. Nothing confirms the
> address in the event really belongs to whoever added the model, so the email doesn't claim it
> does; it says "a new model was added". See [Why existing models are skipped](#why-existing-models-are-skipped)
> for how the server shrinks this to models added since it was installed.
>
> Who else can see the URL: anyone in your Credo AI tenant who can view webhook settings, and the
> webhook delivery logs Credo AI's provider (Svix) keeps. "Hard to guess" is not "hidden from Credo AI
> admins".

---

## How it works

```text
Credo AI ── entity_created (model) ──► POST /webhook/<token>
                                         │  wrong token → 404
                                         │  not a model → 204, ignored
                                         ├─ stored in SQLite (deduplicated on svix-id)
                                         └─ 202 to Credo AI
                                       worker thread
                                         ├─ already emailed for this model? → skip
                                         ├─ address in ALLOWED_EMAIL_DOMAINS? → else skip
                                         ├─ pycredoai: models.get(model_id) → not found? skip
                                         └─ render + send email, record "emailed"
```

**Why the outbox.** The server answers Credo AI as soon as the delivery is safely stored, and sends
the email afterwards. If it crashes in between, the stored delivery is picked up again on the next
start, so an email is never lost after Credo AI has been told it was received. The trade-off: a crash
in the moment between sending an email and recording it means that one email is sent again on restart.

**Retries.** If Credo AI or your mail server is unavailable, the delivery is retried with growing
delays (30 s, 1 min, 2 min, ...; 6 attempts by default) and then logged as failed. While your server
is down, Credo AI's webhook provider keeps retrying deliveries on its own schedule, so short outages
lose nothing.

### Why existing models are skipped

On its **first** start, the server reads the list of every model already in your tenant and marks
each one as "already emailed". This is what stops someone holding the URL from sending one email
for every model you've ever had. After that, only models created since the install can be emailed
about, and their real event normally arrives within seconds.

Later restarts don't do this again, so a model created while the server was down still gets its
email when the delivery is retried. The first start fails, with a clear message, if Credo AI can't
be reached.

---

## Prerequisites

| Item                       | Where to get it                                                                              |
| -------------------------- | -------------------------------------------------------------------------------------------- |
| Credo AI API key           | Governance App → Settings → My settings → Tokens (sign in as a dedicated service user first) |
| Credo AI tenant name       | Your organization's tenant identifier                                                        |
| Access to webhook settings | Governance App → Settings → Webhooks (see Step 5)                                            |
| An SMTP server             | Your mail provider (SES, SendGrid, Microsoft 365, Mailtrap for testing)                      |
| A public HTTPS URL         | A reverse proxy, load balancer or tunnel in front of the server                              |
| Python                     | 3.12+                                                                                        |

---

## Step 1: Configure

From the **cookbook root** (the directory holding this README):

```bash
cp .env.example .env
chmod 600 .env
```

Fill in the Credo AI and SMTP settings. `.env` holds your API key and webhook token: it's gitignored,
and should stay readable only by the user that runs the server.

## Step 2: Generate the webhook token

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Put the output in `WEBHOOK_PATH_TOKEN`. Your webhook URL will be
`https://<your-host>/webhook/<that token>`. The server won't start with a token shorter than 32
characters.

## Step 3: Choose who may be emailed

Set `ALLOWED_EMAIL_DOMAINS` to your organization's email domains, comma-separated, for example
`ALLOWED_EMAIL_DOMAINS=example.com,example.co.uk`. Domains match exactly: `example.com` doesn't
cover `eu.example.com`, so list sub-domains you use.

Models created through an API key are attributed to that key's user, so if that user's address is
outside your domains, those models aren't emailed about.

## Step 4: Run the server

```bash
cd server/python
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Put it behind HTTPS. The server turns off its own access log because request paths contain the
token; **do the same, or redact the path, in your reverse proxy or load balancer logs.**

State lives in `notifier.sqlite3` in the cookbook root (outbox and the "already emailed" record).
Keep it between restarts; deleting it means the next start is a "first start" again.

`GET /healthz` returns `{"status": "ok"}` for your load balancer.

## Step 5: Subscribe in Credo AI

1. In the Governance App, go to **Settings → Webhooks**. If you don't see it, ask your Credo AI
   contact to enable webhook settings for your tenant.
2. Click **Add webhook**. Give it a name, and enter your URL from Step 2 as the **Endpoint URL**.
   Leave **Authentication** as **None**.
3. Under **Events**, search for and tick **`entity_created`** (in the Lifecycle group). It fires for
   every entity type; the server ignores everything except models.
4. Click **Add webhook**.

## Step 6: Try it

Add a model in Credo AI. Within a few seconds the server logs
`sent model_added:<id> to <address>` and the email arrives. To test without sending real email, see
[TESTING.md](./TESTING.md).

---

## Configuration reference

| Setting                       | Default                | Meaning                                                     |
| ----------------------------- | ---------------------- | ----------------------------------------------------------- |
| `CREDO_API_KEY`               | (required)             | API key; only needs to read models                          |
| `CREDO_TENANT`                | (required)             | Tenant identifier                                           |
| `CREDO_API_BASE_URL`          | `https://api.credo.ai` | Integration Service host                                    |
| `CREDO_APP_BASE_URL`          | `https://app.credo.ai` | Base of the "View model" link                               |
| `WEBHOOK_PATH_TOKEN`          | (required)             | Secret path segment, at least 32 characters                 |
| `ALLOWED_EMAIL_DOMAINS`       | (required)             | Comma-separated domains that may receive email              |
| `SMTP_HOST` / `SMTP_PORT`     | (required) / `587`     | Mail server                                                 |
| `SMTP_SECURITY`               | `starttls`             | `starttls`, `ssl` (port 465) or `none` (local testing only) |
| `SMTP_USERNAME` / `_PASSWORD` | empty                  | SMTP login, if your server needs one                        |
| `SMTP_FROM`                   | (required)             | From address                                                |
| `SMTP_MIN_INTERVAL_SECONDS`   | `0`                    | Gap between sends (`1.2` for Mailtrap's free sandbox)       |
| `MAIL_DRY_RUN`                | `false`                | Log emails instead of sending them                          |
| `HOST` / `PORT`               | `0.0.0.0` / `8000`     | Listen address                                              |
| `SEED_EXISTING_MODELS`        | `true`                 | First-start seeding (see above); leave it on                |
| `LOG_LEVEL`                   | `INFO`                 |                                                             |

---

## Code layout

```text
server/python/
  main.py              # entry point: python main.py
  replay.py            # post a sample event to a local server (TESTING.md)
  notifier/
    app.py             # web server: token check, outbox insert, 202
    worker.py          # background thread: outbox → handler → retry / done
    handlers.py        # the scenario: checks, model read, email
    credo.py           # the only module that uses the pycredoai SDK
    store.py           # SQLite outbox and "already emailed" record
    mailer.py          # templates and SMTP
    templates/         # model_added.txt.j2 / model_added.html.j2
fixtures/              # sample webhook deliveries
```

To change the wording, edit the two templates. To add a scenario, add a handler to `handlers.py` and
follow the rules at the top of that file.

---

## Limitations

- **One scenario.** Only model creation is handled; every other event is acknowledged and ignored.
- **The recipient comes from the event.** It's the user on the event, checked against your domains,
  not looked up in Credo AI. A future SDK release adds user lookups; this cookbook will switch to
  them.
- **Single instance.** Run one copy of the server. Two copies sharing one database file would both
  work the outbox.
- **No signature verification**, as described at the top.
