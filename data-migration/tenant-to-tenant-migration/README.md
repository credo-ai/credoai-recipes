# Credo AI Tenant Migration Kit

A command-line tool that copies your configuration and content from one Credo AI tenant (the **source**) to another
(the **target**), for example from `my-sandbox` to `my-prod`. It works in either direction: sandbox to production, or
production to sandbox.

- **The source is only read.** The kit's source connections refuse every write, so a bug can't change the source.
- **Nothing is written to the target without `--live`.** Every `load` is a dry run unless you add `--live`, and a live
  run also needs `--target <tenant>` naming the target tenant, so a wrong `.env` can't send writes elsewhere.
- **Re-runs are safe only while `data/` is kept.** The kit records what it created in `data/` and skips it next time,
  so you can stop, fix something and run again. Copy the `data/` folder somewhere safe after each live run.
- **Answer and custom field values are never written to the logs.** Logs name questions and fields, not values, and
  API keys are redacted.
- All code runs on your own machine. The kit is provided by Credo AI for use with Credo AI tenants.

Terms such as _matched_, _draft_ and _Partial Pause_ are explained in the [Glossary](#glossary).

---

## What it copies

Entities load in this order, because later ones refer to earlier ones. Each name in the first column is what the
`extract` and `load` commands accept.

| Entity              | What's included                                                    | Notes                                                                                                |
| ------------------- | ------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------- |
| `risk_types`        | Custom risk types                                                  | Matched by name; a same-named type on the target is used as is                                       |
| `risk_type_weights` | Risk type weighting for the risk scoring classification            | Replaces the target's weighting (see below); built-in and custom risk types matched by name          |
| `custom_fields`     | Custom field definitions and the entity types they apply to        | Matched by name; differences in type or options are logged as warnings                               |
| `controls`          | Custom standalone controls: evidence requirements, risk types      | Created as **drafts**; matched by key                                                                |
| `policy_packs`      | Custom policy packs with the controls inside them                  | Created as **drafts**; matched by key                                                                |
| `risk_scenarios`    | Custom risk scenarios                                              | Matched by name among custom scenarios                                                               |
| `questionnaires`    | Custom questionnaires: sections and questions, with their IDs kept | Created as **drafts**; matched by key; "hidden from create" is kept                                  |
| `vendors`           | Custom vendors, their questionnaires and custom field values       | Matched by name                                                                                      |
| `users`             | Users with an email, with their role                               | Matched by email, ignoring case; see [Invite emails](#invite-emails) and `--raise-roles`             |
| `teams`             | Teams and their members (with member roles)                        | Matched by exact name (letter case counts); an existing team is never changed                        |
| `triggers`          | Questionnaire triggers and their actions                           | Only on questionnaires the kit created; both tenants must be on Triggers & Actions v3                |
| `models`            | Custom models: owner, vendor links, questionnaires, custom fields  | Creates the model sources (AI provider names) they need; a same-named model on the target is skipped |
| `domains`           | Custom domains                                                     | Matched by ID                                                                                        |
| `use_cases`         | Use cases with everything attached (see below)                     | A same-named use case on the target is skipped                                                       |
| `model_answers`     | Questionnaire answers on custom models the kit created             |                                                                                                      |
| `vendor_answers`    | Questionnaire answers on custom vendors the kit created            |                                                                                                      |

**Use cases** are copied with their owner and other stakeholders, custom field values, risk classification, policy
packs, risk scenarios and their control links, control evidence, domains, model and vendor links, questionnaires and
their answers, and workflow stage and step.

**Risk type weighting** (Risk configuration, then "Scoring classifications" in the UI) is the one setting the kit
changes on the target rather than adopting. Every active risk type on the target gets the source's weight, or 0 if the
source doesn't have that type, and the log lists the target's previous non-zero weights. If a risk type with a
non-zero weight on the source is missing or hidden on the target, nothing is changed and the step fails, since the
weights must total 100%. If some of the source's weights are on risk types hidden or deleted there, they aren't
copied, and the rest are scaled to total 100% with their ratios kept; scores depend only on those ratios. When risk
scoring is on, the target recalculates its use cases' scores after the change. A re-run that finds the weights already
matching skips the step.

**How matching works.** When the target already has an item with the same name (or key, or email), the kit records
it as `matched` and uses it as it is: it never changes an item it didn't create (except a user's role with
`--raise-roles`, see [Roles of existing users](#roles-of-existing-users)). Models and use cases are the
exception: one whose name is already on the target is skipped with a warning, not adopted. A matched team keeps its
members; the log lists how they differ from the source.

**How answers are copied.**

- **Never overwritten.** The kit writes an answer only where the target has none.
- **Matched by question.** An answer is matched to the target's questionnaire version by question ID, then by section
  title and question text, then by question text.
- **Text becomes a selection** where the target question is a select question and the text is one of its options
  (for a multi-select, each `;`-separated part must be an option). Otherwise the answer is left out and logged.
- **Answers to user questions aren't copied**, because they hold user IDs, which differ between tenants. The log
  lists them.

---

## What it doesn't copy

- **Built-in (Credo-supplied) items**, such as built-in questionnaires, controls, policy packs, risk scenarios,
  models, vendors and domains. They are already on every tenant. This includes answers on built-in models and
  vendors, which come with Credo's catalog (or with the integration that synced them).
- **Archived custom items.**
- **Other risk settings**: the scoring classification bands, the switch that shows the scoring classification on
  use cases, and the risk classification rubric (the "Classifications" tab). Set them in the target's UI.
- **Workflow stage configuration.** Use cases are moved to the stage with the same name on the target, so the
  target's stages must already be set up.
- **Roles.** Each user keeps their source role, which must already exist on the target.
- **Comments, tasks, reviews and audit history.**
- **Files**: file answers and file evidence. The extract counts them; add them on the target by hand.
- **Answers to user questions** (see above).
- **Residual risk values.** The target works them out itself. Where a source residual value differs from the
  inherent one, the load lists it for you to set by hand.
- **Approval status of models and vendors.** The extract warns about each one to set by hand.
- **Triggers on v2 tenants.** Triggers are copied only when both tenants use Triggers & Actions v3. `check` shows
  each tenant's version.

---

## Prerequisites

| Item              | Details                                                                                                    |
| ----------------- | ---------------------------------------------------------------------------------------------------------- |
| Python            | 3.11 or later                                                                                              |
| API keys          | One for the source tenant and one for the target, each from a user with the Admin role                     |
| Tenant names      | The source and target tenant names, for example `my-sandbox` and `my-prod` (see the [Glossary](#glossary)) |
| API host          | Your tenants' API address, for example `https://api.credo.ai`                                              |
| Network access    | From the machine running the kit to the API host                                                           |
| Triggers (if any) | Both tenants on Triggers & Actions v3                                                                      |

### API keys

- **Use a dedicated admin service user for each key**, not a person's account. Items whose owner isn't on the
  target end up owned by the key's user, and the items the kit creates show that user as their creator.
- **Ask your Credo AI contact** how to create a key, and which API host your tenants use.

### Direction

- **Sandbox to production** is the usual case.
- **Production to sandbox** works the same way, but copies real users into the sandbox. Use `USER_EMAIL_DOMAINS` or
  `SKIP_USER_EMAIL_DOMAINS` (below) to choose which users come across.

### Environment variables

The kit reads these from `.env` in the kit folder. A variable set in your shell wins over the one in `.env`.

| Variable                  | Required | What it is                                                                         |
| ------------------------- | -------- | ---------------------------------------------------------------------------------- |
| `SOURCE_BASE_URL`         | Yes      | The source tenant's API host, for example `https://api.credo.ai`                   |
| `SOURCE_TENANT`           | Yes      | The source tenant's name, for example `my-sandbox`                                 |
| `SOURCE_API_KEY`          | Yes      | An API key for the source tenant                                                   |
| `TARGET_BASE_URL`         | Yes      | The target tenant's API host                                                       |
| `TARGET_TENANT`           | Yes      | The target tenant's name, for example `my-prod`. `--target` must match it exactly  |
| `TARGET_API_KEY`          | Yes      | An API key for the target tenant                                                   |
| `VERIFY_SSL`              | No       | `true` (default). Set to `false` only for a self-signed certificate                |
| `USER_EMAIL_DOMAINS`      | No       | Comma-separated email domains. When set, only users in these domains are extracted |
| `SKIP_USER_EMAIL_DOMAINS` | No       | Comma-separated email domains. Users in these domains are never extracted          |

The domain filters match the whole domain after the `@`, ignoring case: `example.com` doesn't match
`sub.example.com`.

---

## Step 1: Set up

Get the kit by cloning the [credoai-recipes](https://github.com/credo-ai/credoai-recipes) repository, where it is in
`data-migration/tenant-to-tenant-migration/`, or by unpacking the `.zip` or `.tar.gz` your Credo AI contact sent you.
The kit has its own `requirements.txt` and virtual environment; it doesn't use the repository's root setup.

From the kit folder (the one holding this README):

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
```

Once the virtual environment is active, `python` runs the kit's Python. Every command below uses `python` and
assumes the environment is active.

Edit `.env` and fill in the variables above. `.env` holds your API keys: keep it private.

Optionally, confirm the install by running the kit's tests (they make no API calls):

```bash
python -m pytest -q
```

The kit always uses the `.env`, `data/` and `logs/` next to `migrate.py`, whichever folder you run it from.

---

## Step 2: Check access

```bash
python migrate.py check
```

This only reads. It shows:

- **Access**: whether both APIs answer for each tenant. If either fails, fix `.env` (URL, tenant name or key) before
  going on.
- **Triggers & Actions version** of each tenant (v2 or v3).
- **Invite emails** on the target: whether Credo AI may email an invitation to each user the kit creates. See
  [Invite emails](#invite-emails).
- **Counts** of items per endpoint for the source and target side by side. The `policy_packs` and `questionnaires`
  counts include only those with a published version (see the [Glossary](#glossary)).

---

## Step 3: Extract from the source

```bash
python migrate.py extract all
```

This reads the source and writes one file per entity to `data/` (for example `data/use_cases.json`). Nothing is
written to either tenant. Read the log for warnings, such as items the kit will skip or values it can't copy.

- **Run `extract all` first.** The triggers extract reads the questionnaires extract to know which questionnaire
  versions were copied, and `load all` checks that every extract file is there before it starts.
- **Re-extract at any time.** It replaces the extract files and leaves the load records alone. To extract one entity,
  name it: `python migrate.py extract use_cases`. After re-extracting questionnaires, extract triggers again.

---

## Step 4: Dry run

```bash
python migrate.py load all
```

Early in the log you'll see `DRY RUN: nothing will be written. Add --live to write.` The kit reads the target and
logs what it would do for each item (`would create ...`), then prints a summary table:

| Column    | Meaning                                                                   |
| --------- | ------------------------------------------------------------------------- |
| `created` | New on the target (in a dry run: would be created)                        |
| `matched` | Already on the target with the same name or key; used as is               |
| `skipped` | Already done by an earlier run, or left out on purpose (the log says why) |
| `failed`  | Couldn't be loaded; the log says why                                      |

Under the table, **Needs attention** lists items that need a fix by hand, and **Notes** repeats reminders.

### What a dry run predicts

- **It writes nothing**: not to the target, and no `loaded_` files or target record in `data/`.
- **It carries would-create items into later steps** within the same run. Risk types, custom fields, users and
  questionnaires it would create count as present for the steps after them: for example, teams see the new users and
  triggers see the new questionnaires. Use `load all` for this: a dry run of `load triggers` alone stops if
  questionnaires have never been loaded live.
- **It reports items that would wait on a publish exactly as the first live run will.** Expect it to list drafts to
  publish, and vendors, models, use cases and triggers that wait on them. The [recipe](#recipe) below clears them.

---

## Step 5: Live run

### Before your first live run

- **Arrange invite emails with Credo AI.** See [Invite emails](#invite-emails). If the target may email
  invitations, a live load stops at users and loads nothing after them.
- **Use a dedicated admin service user's key** for the target (see [API keys](#api-keys)).
- **Plan to copy `data/` after each live run.** It is the kit's only record of what it created.

### Recipe

Follow these steps in order. Each live command is safe to repeat: items already loaded are skipped.

1. **Load everything:** `python migrate.py load all --live --target my-prod`. This creates controls, policy packs and
   questionnaires as drafts and loads triggers onto the draft questionnaires. The log lists everything waiting on a
   publish: the new drafts that were published on the source, by name (`publish them on the target when ready`;
   drafts that were drafts on the source too are only counted), and each vendor, model, use case and trigger that
   needs one of them published first.
2. **Publish the new controls and policy packs** on the target.
3. **Load triggers again:** `python migrate.py load triggers --live --target my-prod`. This adds the triggers whose
   actions were waiting on those controls and packs.
4. **If a trigger still waits on a questionnaire** (an action that adds a questionnaire, with the message
   `publish questionnaire <KEY> on the target, then re-run`), publish that questionnaire, then load triggers again.
   Publish it only once its own triggers have loaded.
5. **Publish the remaining new questionnaires**, once no trigger is waiting on them. Each triggers load ends by
   listing the questionnaires that gained a trigger in that run (not ones whose triggers loaded in an earlier run),
   and, separately, any questionnaire with a trigger or action that didn't load: don't publish those yet.
6. **Load everything again:** `python migrate.py load all --live --target my-prod`. Vendors, models, use cases and
   their answers now load. A use case an earlier run left half done is completed.
7. **Re-run step 6 until nothing new is listed.** Then fix by hand what **Needs attention** still lists.

**Why this order.** A published questionnaire version can't take new triggers: to add one afterwards you'd have to
create a new draft version on the target. Trigger actions can point only at published controls, policy packs and
questionnaires. Vendors, models and use cases can use only published questionnaires, and use cases only published
policy packs.

**Before publishing**, review any item the load lists as a copy of the source's unpublished draft: it holds changes
the source never released.

### Loading one entity at a time

Name the entity instead of `all`:

```bash
python migrate.py load users                              # dry run
python migrate.py load users --live --target my-prod      # live
```

A single entity needs only its own extract file, but it relies on the entities before it in the load order having
been loaded.

### After your final run: turn triggers back on

See [Partial Pause](#partial-pause). **Turn triggers back on only after your final re-run, because the kit won't
attach questionnaires or write answers on a use case whose triggers are on.**

---

## Invite emails

The target has a setting that stops Credo AI from emailing invitations to new users (called `LOCAL_USERS`). `check`
shows its state.

- **Arrange it with your Credo AI contact before the live run.** Ask them to set the target so no invitations are
  sent while the kit loads users, and to set it back afterwards if the tenant uses invitations.
- **If the target may send invitations**, or the kit can't tell, a live load stops at users and loads nothing after
  them. A dry run only warns.
- **`--allow-invites` is all or nothing.** It loads users even though the target will email them, so every user the
  kit creates gets an invitation. There is no way to pick which users are emailed.
- **Vendor-portal users are always emailed** when created, whatever the setting, so the kit leaves them out unless you
  add `--allow-invites`. Otherwise, add them by hand.

## Roles of existing users

A user already on the target keeps their target role, even where it differs from the source. When a matched user's
role on the target is lower than on the source, the users load lists them.

- **`--raise-roles`** raises those users to their source role. It follows the built-in order _user_, _reviewer_,
  _admin_, and only ever raises: a target role that is higher is kept.
- **Other roles are never changed**: custom roles, _vendor_ and _read-only admin_, on either side.
- **Changing a role needs an admin.** Use an API key that belongs to an admin on the target.
- **A role change that fails** is listed under "Needs attention" and the run exits 1; set that role by hand.

---

## Partial Pause

Partial Pause is a trigger setting on a use case (also on models and vendors, where the target supports it). While
it is set, triggers still show and hide the questions they control, but every other trigger action (risk category
changes, controls, policy packs, risk scenarios, alerts, review tasks and the rest) is held back.

- **The kit sets it on each use case it creates**, and on custom models and vendors before writing their answers, so
  the copied answers don't fire triggers and the copies match the source.
- **The kit never turns triggers back on.** To do so: open the use case, then the gear menu, then **Triggers and
  actions settings**, and choose **Enabled**. It previews the held-back changes before applying them.
- **Turn triggers back on only after your final re-run, because the kit won't attach questionnaires or write answers
  on a use case whose triggers are on.**

---

## Duration and scale

Rough guidance from real runs:

| Work                                        | Time            |
| ------------------------------------------- | --------------- |
| About 80 use cases with about 1,400 answers | About 4 minutes |
| About 120 triggers with 275 actions         | Under a minute  |

- **Rate limits are handled.** A request the API rate-limits (429) is retried, waiting as long as the API asks (up to
  a minute), up to 3 tries in all.
- **Expired sign-ins are handled.** When the API answers 401, the kit signs in again once and repeats the request.
- **Re-running is the recovery path.** If a run stops on an error, fix the cause if there is one, then run the same
  command again.

---

## Reading the results

### Exit codes

| Code | Meaning                                                                                                    |
| ---- | ---------------------------------------------------------------------------------------------------------- |
| `0`  | Everything worked                                                                                          |
| `1`  | The run finished, but something failed or needs attention, or a load stopped on purpose (such as at users) |
| `2`  | The run stopped: a configuration problem, an API error, a missing extract file, or the wrong tenant        |

### Logs: `logs/`

- One log per run, named after the command and time, for example `load_all_20261103_142501.log`. The last line on
  screen gives its path (`Full log: ...`).
- The screen shows the main lines; the log file also has the detail.
- Each load also writes `<log name>_summary.json` with the counts, the Needs attention list and the notes.
- Logs contain item names, keys and user emails. They never contain answer values, custom field values or API keys.

### Tracking: `data/`

| File                   | What it holds                                                                                             |
| ---------------------- | --------------------------------------------------------------------------------------------------------- |
| `<entity>.json`        | What `extract` read from the source                                                                       |
| `loaded_<entity>.json` | What each source item became on the target, and whether the kit `created` it or `matched` an existing one |
| `run_meta.json`        | The source and target tenant this folder belongs to                                                       |

- **Keep `data/` between runs against the same target, and copy it after each live run.** The `loaded_` files are
  how the kit knows what it created. Without them, a re-run treats the kit's own earlier work as someone else's:
  models and use cases it created are skipped as already on the target (and never completed), and triggers aren't
  added to questionnaires it created.
- **A `data/` folder belongs to one source and target pair.** The source is recorded on the first extract and the
  target on the first live load; a run against a different tenant stops with `data/ belongs to ...`. See
  [Starting over](#starting-over).
- A dry run never writes `loaded_` files.

---

## Interrupted runs

A run can stop part way: Ctrl-C, a crash, or a lost connection.

- **What's done is kept.** Each item is recorded in its `loaded_` file as soon as it is created, so nothing created
  before the stop is forgotten.
- **Re-run the same command to continue.** Recorded items are skipped, and a use case left half done is completed.
- **After Ctrl-C** the summary table and `_summary.json` aren't written for that run; the log file has everything up
  to the stop.
- **"May have been created" message.** If the connection drops while a model or use case is being created, the log
  says it `may have been created on the target before the connection was lost`. Check the target: if it is there,
  delete it, then re-run. Otherwise the re-run skips it as already on the target and never completes it.
- **Other items in the same situation** are adopted on the re-run as existing items (`matched`), not as the kit's own.
  For a questionnaire or a team that matters: triggers go only onto questionnaires the kit created, and a matched team
  never gets its members added. If you're sure the earlier run made it, delete it on the target, remove its row from
  `data/loaded_<entity>.json` if there is one, and re-run.

---

## Starting over

To run a fresh migration from the start (for example against a different target, or after a trial run):

1. **Move every `data/*.json` file** out of `data/`, including the `loaded_` files and `run_meta.json`. Keep them
   until you're sure you don't need them: they list what the earlier run created.
2. **Optionally move `logs/`** as well, to keep the new run's logs apart.
3. **Run `extract all` again**, then continue from the dry run.

**What it means for the target.** Without the `loaded_` files, the kit treats everything already on the target as
pre-existing, including what an earlier run created there:

- Risk types, custom fields, controls, policy packs, risk scenarios, questionnaires, vendors, users and teams with
  the same name, key or email, and domains with the same ID, are `matched` and used as they are.
- Models and use cases with the same name are skipped.
- Triggers aren't added to matched questionnaires.

To get fresh copies of what an earlier run made on the same target, delete those items first (see
[Undoing a run](#undoing-a-run)).

---

## Undoing a run

The kit has no undo command. To remove what it created, delete the items on the target in the Credo AI app. The
`loaded_<entity>.json` files list them: rows with `"origin": "created"` were made by the kit; `matched` rows were
already there and should be left alone.

- **Drafts** (questionnaires, controls, policy packs) can be deleted while they're unpublished. A published
  questionnaire can't be deleted, so review drafts before publishing.
- **Model sources** the kit creates can't be deleted; it creates only the ones the new models need.
- **Risk type weighting** has no `loaded_` file. The live run's log lists the target's previous non-zero weights;
  set them back on the target's "Scoring classifications" page.
- **Users the kit created** may have been emailed an invitation if the target sends invitations or you used
  `--allow-invites`.
- After deleting items on the target, remove their rows from the matching `loaded_<entity>.json` (or follow
  [Starting over](#starting-over)) before running again.

---

## Edge cases

- **"Applies to" is set when an item is created.** Risk scenarios, controls, policy packs and questionnaires that
  apply only to some entity types (for example, use cases only) are created with the same entity types. Credo AI
  can widen this setting later but never narrow it, so an item already on the target keeps its own setting; if it
  should apply to fewer types, delete it on the target before loading.
- **Workflow stage missing on the target.** The use case is still created, but its stage isn't set; Needs attention
  lists it to move by hand. A use case already past that stage on the target is left where it is.
- **Owner not on the target.** A use case or model whose owner isn't on the target is owned by the API key's user;
  Needs attention lists each one to change by hand.
- **Deleted users and users without an email** aren't extracted. Every other user the source lists is, subject to
  the email-domain filters.
- **Team members missing from the users list** (such as hidden system accounts) are left out of the team, with a
  warning.
- **Two source use cases with the same name.** The first is copied; the second is skipped. Rename one on the source
  and re-extract to copy both.
- **Triggers on an older questionnaire version** aren't copied; only those on the version the questionnaires extract
  took.
- **Extract dependencies.** Run `extract all` first. The triggers extract uses the questionnaires extract, and
  `load all` stops before writing anything if any extract file is missing.

---

## Troubleshooting

**`has no published version on the target; publish it on the target, then re-run`.** A model, vendor, use case or
trigger action uses a draft. Publish it on the target and re-run, following the [recipe](#recipe).

**`already exists on the target; using it as is (the kit doesn't change it)`.** The target already had an item with
this key. If an earlier kit run made it and you want a fresh copy: delete it on the target (possible only while it
is an unpublished draft), remove its row from `data/loaded_<entity>.json`, then re-run.

**`a use case named '...' is already on the target; skipping it`** (or a model). The kit doesn't adopt existing
models or use cases. If an earlier kit run created it and was interrupted, delete it on the target and re-run.

**`may have been created on the target before the connection was lost`.** See [Interrupted runs](#interrupted-runs).

**`published versions can't take triggers`.** The questionnaire was published before its triggers were loaded.
Create a new draft version of it on the target, then re-run.

**`the target's LOCAL_USERS entitlement is on`.** See [Invite emails](#invite-emails).

**`role '...' isn't on the target`.** The user's source role doesn't exist on the target (the message lists the
roles it has). Add that user on the target by hand with a suitable role.

**`--target ... doesn't match the configured target tenant`.** `--target` must equal `TARGET_TENANT`. Check `.env`
and your shell for a different value.

**`No extract file for ...`.** Run the `extract` command the message gives.

**`Configuration error: Missing required settings`.** A required variable is missing from `.env` and your shell.

**A tenant on v2 triggers.** Triggers are skipped and listed under "Needs attention"; every other entity is still
extracted and loaded. With a v2 target, the triggers load is flagged and the run exits with code `1`. With a v2
source, `extract all` flags the triggers and exits with code `1`; the later `load all` finds no triggers to load and
may exit `0`.

To copy the triggers with the kit, run it again for triggers alone (`extract triggers`, then `load triggers`) once
both tenants are on v3 and before you publish the questionnaires the kit created: a published questionnaire version
can't take new triggers. If those questionnaires are already published, add the triggers by hand on the target, or
ask your Credo AI contact.

### Reporting a problem

Send your Credo AI contact:

- the kit version, from `python migrate.py --version`
- the run's log file and its `_summary.json` from `logs/` (check you're comfortable sharing the names and emails in
  them)

---

## Glossary

| Term                      | Meaning                                                                                                                                                                            |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Tenant name               | The tenant's ID as the API uses it (for example `my-prod`), not the display name shown in the app. Ask your Credo AI contact if you're unsure; `check` fails with a wrong one      |
| Source, target            | The tenant the kit reads from, and the tenant it writes to                                                                                                                         |
| Dry run                   | A `load` without `--live`: reads the target and reports what it would do, writing nothing                                                                                          |
| Live run                  | A `load` with `--live --target <tenant>`: writes to the target                                                                                                                     |
| Built-in, custom          | Built-in items are supplied by Credo AI and already on every tenant; custom items were made in your tenant. The kit copies only custom items                                       |
| `created`                 | Made on the target by this run (in a dry run: would be made)                                                                                                                       |
| `matched`                 | Already on the target with the same name, key or email; used as is, never changed                                                                                                  |
| `skipped`                 | Done by an earlier run, or left out on purpose; the log says why                                                                                                                   |
| `failed`                  | Not loaded this run; the log says why. Often cleared by a publish and a re-run                                                                                                     |
| Needs attention           | Items the kit couldn't finish that need a fix by hand. Makes the run exit `1`                                                                                                      |
| Notes                     | Reminders, such as turning triggers back on. They don't change the exit code                                                                                                       |
| Draft, published          | A draft version can still be edited and deleted; a published version is released for use and can't be deleted. The kit creates questionnaires, controls and policy packs as drafts |
| Integration API           | Credo AI's public API, used for everything it covers                                                                                                                               |
| Application API           | The API the Credo AI web app uses (not a published contract); the kit uses it for what the Integration API can't do. See "How it talks to Credo AI"                                |
| Partial Pause             | A trigger setting: triggers show and hide questions but hold back every other action. See [Partial Pause](#partial-pause)                                                          |
| Triggers & Actions v2, v3 | Two generations of Credo AI's questionnaire triggers. The kit copies triggers only between v3 tenants; `check` reports each tenant's version                                       |
| Published-only counts     | `check` counts policy packs and questionnaires through the Integration API, which lists only those with a published version, so new drafts don't appear in those counts            |

---

## How it talks to Credo AI

The kit uses two Credo AI APIs on the same host, with the same API key for each tenant:

- **The Integration API** (`/api/v1/integration`), Credo AI's public API, for every operation it covers.
- **Credo AI's application API** (`/api/v2/<tenant>`, the API the Credo AI web app uses) for what the Integration
  API can't do or doesn't copy exactly: controls and policy packs, custom risk scenarios, users, teams, trigger
  actions, domains, risk type weighting, creating use cases, stakeholders, workflow stage and step, risk
  classification and trigger pause settings. Code that uses it is in `kit/internal/` (and marked where an entity mixes the two).

**The application API isn't a published contract.** Credo AI can change it with a platform update, and tests each
kit release against the platform as it is at release time. So:

- Use the kit version Credo AI gives you, and ask for the latest before a new migration.
- If a step that worked before starts failing after a platform update, stop and send your Credo AI contact the run's
  log file (see "Reporting a problem"). Re-running after a fix is safe while `data/` is kept.

Rate-limited requests are retried. Reads that fail with a server error or a dropped connection are retried; writes
that fail that way are not, so a create is never sent twice.
