# Credo AI → Email: Governance Notifications

A small server you run that turns Credo AI governance activity into emails to the right people:
use case owners, admins, reviewers and model creators.

| #   | When                                                   | Who gets the email                                      | Template                       |
| --- | ------------------------------------------------------ | ------------------------------------------------------- | ------------------------------ |
| 1   | A use case moves to a new workflow stage               | Its owners                                              | `stage_changed`                |
| 4   | A use case leaves Intake (your own "approved" wording) | Its owners (one email with #1)                          | `stage_changed`                |
| 2   | A use case's review is approved                        | Its owners                                              | `review_approved_owner`        |
| 7   | A use case's review is approved                        | Admins (an owner who is also an admin gets #2 only)     | `review_approved_admin`        |
| 3   | A control's evidence is marked ready for validation    | The use case's owners                                   | `control_ready_for_validation` |
| 5   | A model is added to the registry                       | The person who added it                                 | `model_added`                  |
| 9   | A questionnaire answer is changed (not a first answer) | Admins, and the reviewers on the entity's open review   | `answer_changed`               |
| 6   | Weekly: controls still waiting for evidence            | Each use case owner, one digest for all their use cases | `weekly_pending_controls`      |
| 8   | Daily: questionnaires due within N days, or overdue    | Each use case owner, one digest                         | `questionnaire_reminder`       |

- All code runs in your own environment; you choose the mail provider
- Everything about Credo AI is read through the official [`pycredoai`](https://pypi.org/project/pycredoai/)
  SDK; names and addresses never come from the webhook payload
- Every delivery's signature is checked, so only Credo AI can trigger an email
- Each scenario is one small handler, so adding your own is a short change (see [Code layout](#code-layout))

> 🧪 **Preview: the SDK release this needs.** Scenario #5 runs on `pycredoai` 1.3.0. The others, and
> `setup_webhook.py`, use the SDK's users, entities, tasks, entity reviews and webhooks resources, which ship
> in the next `pycredoai` release. `requirements.txt` will pin that release when it's published; until then
> this cookbook is a preview.

---

## How it works

```text
Credo AI ── one webhook, five event types ──► POST /webhook/<token>
                                                │  wrong token → 404
                                                │  bad or missing signature → 401
                                                │  an event type it doesn't handle → 204
                                                ├─ stored in SQLite (deduplicated on svix-id)
                                                └─ 202 to Credo AI
                                              worker thread
                                                ├─ the scenario's handler re-reads the state
                                                │  through pycredoai (stale? → nothing sent)
                                                ├─ looks up the recipients' current addresses
                                                └─ renders and sends each email once
scheduler (in the server, or your cron) ──► the two reminders, through the same worker
```

| Event type                           | Scenarios |
| ------------------------------------ | --------- |
| `entity_workflow_stage_updated`      | #1, #4    |
| `entity_review_status_updated`       | #2, #7    |
| `entity_control_task_status_updated` | #3        |
| `entity_created` (models only)       | #5        |
| `entity_evidence_created`            | #9        |

**Signatures.** Credo AI signs every delivery. The server checks the signature with your webhook's signing
secret before it stores anything, and answers `401` if it's missing, wrong, or more than 5 minutes old (so
a captured delivery can't be replayed later).

**Re-reads.** A genuine delivery can still be out of date: deliveries are retried, and the state can move
on in between. So each handler re-reads what it's about to announce (the use case is really in that stage,
the review really is approved, the task really is ready for validation) and sends nothing if the read
disagrees or the thing no longer exists. If Credo AI can't be reached, the delivery is retried; nothing is
sent without a successful read.

**Each email once.** Every email is recorded under what it's about and who it went to, for example
`review_approved:<review id>:<address>`. A delivery that arrives again, even under a new delivery id, sends
nothing new. Some keys expire so a real repeat is emailed again:

| Scenario | Sent once per                                | For                                     |
| -------- | -------------------------------------------- | --------------------------------------- |
| #1, #4   | use case, stage, recipient                   | 24 hours                                |
| #2, #7   | review, recipient                            | ever (a review is approved once)        |
| #3       | task, recipient                              | 24 hours (it can be sent back and back) |
| #5       | model                                        | ever                                    |
| #9       | changed answer, recipient                    | ever                                    |
| #6       | recipient, ISO week (in `REMINDER_TIMEZONE`) | that week                               |
| #8       | recipient, day (in `REMINDER_TIMEZONE`)      | that day                                |

**Why the outbox.** The server answers Credo AI as soon as the delivery is safely stored, and sends the
emails afterwards. If it crashes in between, the stored delivery is picked up again on the next start, so an
email is never lost after Credo AI has been told it was received. The trade-off: a crash in the moment
between sending an email and recording it means that one email is sent again on restart.

**Retries.** If Credo AI or your mail server is unavailable, the delivery is retried with growing delays
(30 s, 1 min, 2 min, 4 min, 8 min, then every 15 min; 9 attempts by default), which covers an outage of about
an hour, and then logged as failed. A retry sends only the emails that hadn't gone out yet. While your own
server is down, Credo AI's webhook provider keeps retrying deliveries on its own schedule.

---

## Prerequisites

| Item                 | Where to get it                                                                                     |
| -------------------- | --------------------------------------------------------------------------------------------------- |
| Credo AI API key     | Governance App → Settings → My settings → Tokens, signed in as a dedicated service user (see below) |
| Credo AI tenant name | Your organization's tenant identifier                                                               |
| An SMTP server       | Your mail provider (SES, SendGrid, Microsoft 365, Mailtrap for testing)                             |
| A public HTTPS URL   | A reverse proxy, load balancer or tunnel in front of the server                                     |
| Python               | 3.12+                                                                                               |

### API key permissions

The API key acts as its user, so create a **dedicated service user with the admin role** and make the key as
that user. What the scenarios need:

- **`user_view` and `user_list`**: every recipient's address is looked up by user id (`users.get`), and #7
  and #9 list the admins (`users.list`). This means the key can read every user's email address; keep it as
  safe as any admin credential.
- **Listing tasks is admin-only**: #3 re-reads the task and #6 lists every pending control task.
- **Webhook management**: `setup_webhook.py` creates the webhook and reads its signing secret.
- **Read access to use cases, models, reviews and questionnaires** across the tenant.

---

## Step 1: Configure

From the **cookbook root** (the directory holding this README):

```bash
cp .env.example .env
chmod 600 .env
```

Fill in the Credo AI and SMTP settings. `.env` holds your API key, and will hold the webhook token and
signing secret: it's gitignored, and should stay readable only by the user that runs the server.

## Step 2: Install

```bash
cd server/python
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

## Step 3: Create the webhook

```bash
python setup_webhook.py --public-url https://notify.example.com
```

This creates one webhook in Credo AI for the five event types above, delivering to
`https://notify.example.com/webhook/<token>`, where the token is a fresh random 43-character string. It then
reads the webhook's **signing secret**, and writes the token, the secret and the webhook's id to `.env`
(`WEBHOOK_PATH_TOKEN`, `WEBHOOK_SIGNING_SECRET`, `CREDO_WEBHOOK_ID`). It prints neither the token nor the
secret.

The server won't start without the signing secret, or with a token shorter than 32 characters.

<details>
<summary>Prefer to create the webhook in the app?</summary>

1. Generate a token: `python -c 'import secrets; print(secrets.token_urlsafe(32))'`, and put it in
   `WEBHOOK_PATH_TOKEN`.
2. In the Governance App, go to **Settings → Webhooks** and click **Add webhook**. Enter
   `https://<your-host>/webhook/<token>` as the **Endpoint URL**, leave **Authentication** as **None**, and
   tick the five event types in the table above.
3. Open the webhook again, click **Reveal** next to **Signing secret**, and copy the `whsec_…` value into
   `WEBHOOK_SIGNING_SECRET`.

</details>

**About the URL token.** The signature is what proves a delivery is genuine; the token is a second layer. A
wrong token gets the same `404` as any unknown path. The token isn't secret from everyone, though: users who
can view webhook settings in Credo AI see the full URL, and so do the webhook provider's delivery logs.

## Step 4: Write your stage wording (#1, #4)

Stage-change emails use the wording in `server/python/stage_messages.yaml`. Each workflow stage can have an
`on_enter` message (sent when a use case moves into it) and an `on_leave` message (sent when it moves out);
stages are keyed by id or by name. #4 is the Intake stage's `on_leave`, your "approved out of Intake"
message. Moving out of Intake sends **one** email: Intake's `on_leave`, then the next stage's `on_enter`.
`default` fills in any hook a stage doesn't set; set a hook to `""` to opt that stage out. A move with no
wording sends nothing, so delete `default.on_enter` to email only about the stages you list.
The server reads the file when it starts.

## Step 5: Run the server

```bash
python main.py
```

Put it behind HTTPS. The server turns off its own access log because request paths contain the token; **do
the same, or redact the path, in your reverse proxy or load balancer logs.**

State lives in `notifier.sqlite3` in the cookbook root: the outbox and the record of emails already sent.
Keep it between restarts.

`GET /healthz` returns `{"status": "ok"}` for your load balancer, or a `503` when the background worker that
sends the emails has stopped working (for example, the database file can't be written).

## Step 6: Reminders (#6, #8)

There's no "due soon" event, so the two reminders run on a schedule. By default the server runs them itself:

| Reminder              | Setting                         | Default       | Sends                                                                 |
| --------------------- | ------------------------------- | ------------- | --------------------------------------------------------------------- |
| #6 pending controls   | `PENDING_CONTROLS_CRON`         | `0 9 * * mon` | each owner a digest of the controls still waiting on their use cases  |
| #8 questionnaires due | `QUESTIONNAIRE_REMINDER_CRON`   | `0 9 * * *`   | each owner a digest of incomplete questionnaires due soon, or overdue |
| (#8) how soon is soon | `QUESTIONNAIRE_DUE_WITHIN_DAYS` | `7`           |                                                                       |
| (both) whose clock    | `REMINDER_TIMEZONE`             | `UTC`         | the timezone of the cron times, the week or day, and the email dates  |

Cron expressions are the standard five fields (minute, hour, day of month, month, day of week). A run missed
while the server was down is made up when it starts, if that's within the hour.

**From your own cron instead.** Set `REMINDERS_IN_PROCESS=false` and schedule:

```bash
cd /path/to/governance-notifications/server/python
.venv/bin/python remind.py pending_controls        # e.g. Mondays 09:00
.venv/bin/python remind.py pending_questionnaires  # e.g. daily 09:00
```

`remind.py` exits `1` if Credo AI or the mail server couldn't be reached. Running it again afterwards is
safe: anyone already emailed for the week (#6) or day (#8) is skipped. For the same reason, a cron that fires
more often than weekly (#6) or daily (#8) sends nothing extra. Don't run it _at the same time_ as another
run or the server's own schedule, though: two runs in parallel can both email the same person. Hence
`REMINDERS_IN_PROCESS=false` when you use your own cron.

**What counts.** #6 starts from the platform's own rule for open control tasks, the one the in-app task counts
use: not done, and the control's evidence isn't provided yet (unless a reviewer asked for changes). It leaves
out tasks marked ready for validation, which wait on a reviewer rather than the owner (#3 already announced
them), so its count can be lower than the app's. Controls with nobody assigned have no task, so they never
appear. #8 counts a questionnaire that isn't complete and has a
due date within `QUESTIONNAIRE_DUE_WITHIN_DAYS` or in the past; one without a due date is never reminded
about. Credo AI marks a questionnaire complete once its required questions are answered, so one with no required
questions is never marked; #8 also skips any questionnaire whose questions are all answered.

## Step 7: Try it

Move a use case to its next workflow stage in Credo AI. Within a few seconds the server logs
`delivery msg_… (entity_workflow_stage_updated): sent N` and the owners' emails arrive. To test without
sending real email, see [TESTING.md](./TESTING.md).

---

## Configuration reference

| Setting                         | Default                   | Meaning                                                     |
| ------------------------------- | ------------------------- | ----------------------------------------------------------- |
| `CREDO_API_KEY`                 | (required)                | API key of an admin service user                            |
| `CREDO_TENANT`                  | (required)                | Tenant identifier                                           |
| `CREDO_API_BASE_URL`            | `https://api.credo.ai`    | Integration Service host                                    |
| `CREDO_APP_BASE_URL`            | `https://app.credo.ai`    | Base of the links in emails                                 |
| `RECIPIENT_CACHE_SECONDS`       | `300`                     | How long owner, admin and user lookups are reused           |
| `WEBHOOK_PATH_TOKEN`            | (required)                | Secret path segment, at least 32 characters                 |
| `WEBHOOK_SIGNING_SECRET`        | (required)                | The webhook's signing secret, `whsec_…`                     |
| `CREDO_WEBHOOK_ID`              | empty                     | The webhook `setup_webhook.py` created, for reference       |
| `SMTP_HOST` / `SMTP_PORT`       | (required) / `587`        | Mail server                                                 |
| `SMTP_SECURITY`                 | `starttls`                | `starttls`, `ssl` (port 465) or `none` (local testing only) |
| `SMTP_USERNAME` / `_PASSWORD`   | empty                     | SMTP login, if your server needs one                        |
| `SMTP_FROM`                     | (required)                | From address                                                |
| `SMTP_MIN_INTERVAL_SECONDS`     | `0`                       | Gap between sends (`1.2` for Mailtrap's free sandbox)       |
| `MAIL_DRY_RUN`                  | `false`                   | Log emails instead of sending them                          |
| `STAGE_MESSAGES_PATH`           | `server/python/stage_...` | The stage wording file                                      |
| `REMINDERS_IN_PROCESS`          | `true`                    | Run the reminders in the server; `false` to use `remind.py` |
| `REMINDER_TIMEZONE`             | `UTC`                     | IANA timezone for the reminder schedules and dates          |
| `PENDING_CONTROLS_CRON`         | `0 9 * * mon`             | When #6 runs                                                |
| `QUESTIONNAIRE_REMINDER_CRON`   | `0 9 * * *`               | When #8 runs                                                |
| `QUESTIONNAIRE_DUE_WITHIN_DAYS` | `7`                       | #8's window                                                 |
| `HOST` / `PORT`                 | `0.0.0.0` / `8000`        | Listen address                                              |
| `LOG_LEVEL`                     | `INFO`                    |                                                             |

---

## Code layout

```text
server/python/
  main.py                  # entry point: python main.py
  setup_webhook.py         # creates the webhook, writes its token and signing secret to .env
  remind.py                # runs a reminder now (for your own cron)
  replay.py                # posts a signed sample event to a local server (TESTING.md)
  stage_messages.yaml      # stage wording for #1 and #4
  notifier/
    app.py                 # web server: token, signature, outbox insert, 202
    worker.py              # background thread: outbox → handler → retry / done
    router.py              # which handler gets which event
    events.py              # the webhook envelope and the five event types
    credo.py               # the only module that uses the pycredoai SDK
    handlers/
      __init__.py          # shared: Drop, Context, deliver (send once), links
      workflow.py          # #1, #4
      reviews.py           # #2, #7
      control_tasks.py     # #3
      models.py            # #5
      answers.py           # #9
    reminders/
      scheduler.py         # cron schedules, inside the server
      pending_controls.py  # #6
      pending_questionnaires.py  # #8
    stages.py              # reads stage_messages.yaml
    store.py               # SQLite outbox and the record of emails sent
    mailer.py              # templates and SMTP
    templates/             # <name>.txt.j2 + <name>.html.j2 per email, a shared layout, the logo
fixtures/                  # one real delivery of each event type, anonymised
```

To change an email, edit its two templates (HTML is autoescaped). The logo is embedded in each email rather
than linked, because most mail clients block remote images; to use your own, replace `credo-ai-logo.png`
(280 × 48 px, shown at half size). To add a scenario, add a handler under `handlers/`, register it in
`router.py` (and its event type in `events.SUBSCRIBED`), and follow the rules at the top of
`handlers/__init__.py`.

---

## Good to know

- **Teams aren't emailed yet.** An owner or a reviewer can be a team, and there's no public way yet to read a
  team's members. Team owners and team reviewers are skipped and logged (`skipped N team(s)`); their members
  get nothing unless they're also owners or reviewers in their own right.
- **Who counts as a recipient.** Addresses come from the users API, so they're current. Deleted users and
  people who haven't accepted their invitation yet are never emailed.
- **#5 emails the model's creator** by user id, and never uses the address in the event.
- **#9 doesn't leave out the person who changed the answer.** If they're an admin or a reviewer on the open
  review, they get the email too.
- **Auto-approval sends several emails at once.** When a review is approved automatically and that moves the
  use case on, #2, #7 and the stage email (#1) arrive together. They say different things.
- **Other events are ignored.** Event types the server doesn't handle are acknowledged with a `204`, so a
  broader subscription is harmless. #1–#4 ignore entity types other than use cases; #5 ignores everything but
  models; #9 covers every entity type with questionnaires.
- **Single instance.** Run one copy of the server. Two copies sharing one database file would both work the
  outbox.
