# Testing — Azure AI Foundry Governance Sync

Work through these in order. Step 1 needs no Azure account and no Credo AI tenant, so you can
confirm the cookbook installs and fails cleanly before arranging access to either.

---

## Step 1: Install and smoke-test, no Azure and no Credo AI

```bash
cd server/python
pip install -r requirements.txt
python main.py --help
```

The help text should name every flag in README Step 4. Then prove the two "could not start" paths,
neither of which needs credentials:

```bash
printf 'AZURE_NONSENSE=1\n' >> ../.env    # an unrecognized key
python main.py --dry-run ; echo "exit=$?" # expect exit=2 naming the key
```

Remove that line afterwards. With no `.env` at all, the run must also exit `2` naming the missing
Azure config keys — never a traceback.

---

## Step 2: Confirm Azure access, no Credo AI involved

Prove the app registration works before introducing a tenant:

```bash
python -c "
from azure_foundry_sync.config import get_settings
from azure_foundry_sync.azure_client import AzureClient
azure = AzureClient(get_settings())
print('token ok:', bool(azure.get_access_token()))
"
```

| Result                  | Meaning                                              |
| ----------------------- | ---------------------------------------------------- |
| `token ok: True`        | The app registration and secret are valid            |
| Non-JSON response error | Wrong `AZURE_TENANT_ID` — see README Troubleshooting |
| `401` error             | Wrong or expired `AZURE_CLIENT_SECRET`               |

---

## Step 3: Dry run — reads only

```bash
python main.py --dry-run -v
```

Confirm:

- Every planned write is prefixed `[dry-run] would ...`
- The summary lines say **plan**, not **complete**
- Nothing appears in Credo AI afterwards

---

## Step 4: Live run

```bash
python main.py -v
```

Confirm all four summary lines appear (unless a domain was skipped — see README Step 3), and that
the run's exit code is `0` (or `1` with an explained error — see Step 6). The first-ever run against
a fresh tenant also creates the "Azure AI Foundry" `Source` record automatically, as part of the
models sync — no separate step. On every run after that the same create is attempted again and Credo
AI answers `422` (already exists), which the sync accepts.

---

## Step 5: Idempotency

Run it a second time with nothing changed:

```bash
python main.py -v
```

Expect **zero writes** across all four domains:

```text
models         scanned=N created=0 updated=0 skipped=N errors=0
controls       scanned=20 created=0 updated=0 skipped=20 errors=0
custom fields  scanned=N created=0 updated=0 skipped=N errors=0
questionnaire  scanned=1 created=0 updated=0 skipped=1 errors=0
```

The only write attempt you should see in the log is `POST .../sources` returning `422`; no control,
custom field, model or questionnaire write should appear. The log also carries one warning naming
`evidence_requirements[].governance_bounds` — Credo AI doesn't return that field, so it is left out
of the control comparison (see README, "How it matches records").

If controls or the questionnaire show `updated=N` instead of `skipped=N` on a clean second run,
something's off — either the local `config/` content and what got published on the first run
genuinely differ (check `--dry-run -v`, it names which controls and why), or the content-comparison
logic itself has a bug.

**Content-change detection.** Edit one control's latest `vN.yaml` (e.g. tweak `MSFT-BLEU`'s
`description` under `info`), then:

```bash
python main.py --dry-run -v --skip-models --skip-custom-fields --skip-questionnaire
```

Confirm the log names that one control specifically ("latest published version differs") while
every other control still says "unchanged, would skip." Revert the edit afterward, or the next
real run will publish a version you didn't mean to.

Do the same for the questionnaire: change one question's text in `config/azure_questionnaire.json`
and run

```bash
python main.py --dry-run -v --skip-models --skip-controls --skip-custom-fields
```

Expect `[dry-run] would publish a new version of questionnaire 'DEFAULT_AZURE'`; with the edit
reverted it must say `unchanged, skipping`. Reordering two questions counts as a change too.

---

## Step 6: Failure modes

Each of these must degrade, not crash. Check the exit code every time.

| Scenario                     | How to trigger                               | Expected                                                                                              |
| ---------------------------- | -------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Bad Azure client secret      | Corrupt `AZURE_CLIENT_SECRET`                | Exit `2`, readable message, no partial writes                                                         |
| Bad Credo AI credentials     | Corrupt `CREDOAI_API_KEY`                    | Exit `2`, readable message, no partial writes                                                         |
| Unrecognized `.env` key      | Add `AZURE_NONSENSE=1` or `CREDO_NONSENSE=1` | Exit `2` naming the key, not a traceback                                                              |
| One malformed control config | Temporarily rename a control's `base.yaml`   | That control's `scanned` count still increments, `errors` +1, the rest of the 20 still sync; exit `1` |
| `SYNC_QUESTIONNAIRE=false`   | `python main.py --skip-questionnaire -v`     | No questionnaire line in the summary; the other three still run                                       |

---

## Step 7: Confirm in Credo AI

In the Governance App:

1. **Models** registry — synced models show `Source: Azure AI Foundry`
2. **Control library** — 20 `MSFT-*` controls present, published (not draft)
3. Open one control (e.g. `MSFT-BLEU`) — confirm its evidence requirement shows the Azure evaluator
   code template
4. **Custom fields** — the fields from `config/custom_fields.json` are present, and each shows up on
   Use Case records only, not on Models or Vendors (their `target` is `use_case`). On a fresh tenant
   this is the first place the entity-type lookup is exercised, so confirm the run reports
   `custom fields ... created=4 errors=0`
5. **Questionnaires** — `DEFAULT_AZURE` (or the merged variant, if `CREDO_QUESTIONNAIRE_OPTIONS=2`)
   exists and has both the Azure template sections and (if merged) your existing sections

Item 3 is worth checking closely — that code template is what a business user actually copies to
run the real evaluator and upload evidence; confirm it renders with real `${USE_CASE.ID}` /
`${CONTROL.ID}` values substituted, not literal placeholder text.

---

## Cold-setup test

Hand this cookbook to someone on your team who has never seen it. Time how long it takes them to
reach a successful `python main.py` against their own Azure subscription and Credo AI tenant, using
only `README.md` and this file. Log every point of confusion.

Watch specifically for the draft-vs-published branching in controls (Step 5) — a new person may not
realize a control stuck in draft from an earlier partial run behaves differently (PATCH-in-place)
than one already published (POST-then-PATCH). `--dry-run -v` names which branch each control takes.
