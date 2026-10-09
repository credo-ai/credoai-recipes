# Validation Report — Microsoft Agent 365 Governance Sync Cookbook

Tracks [INT-2](https://credo-ai.atlassian.net/browse/INT-2) — Cookbook Validation Process sign-off.

- **Date:** 2026-10-09
- **Validated by:** Chika Barton (validation run with Claude Code)
- **Cookbook path:** `integrations/microsoft-agent-365/agent-governance-sync/`
- **Design basis:** INT-2 (three flows: registry sync, publish-and-review, approve-and-unblock), the
  working demo that preceded this cookbook, and the team direction of 2026-10-06 to use
  `pycredoai` wherever it covers the call

## Summary

The cookbook is functionally verified end to end against a lab Microsoft 365 tenant (Agent 365
licensed) and a lab Credo AI tenant: registration, idempotency, change detection, enforcement
(block), and the setup failure modes that can be triggered on demand behave as `TESTING.md` specifies.

**Two things were not observed end to end and are called out in _Coverage gaps_ below:** the
combined **unblock-on-approval** path (the unblock call and the "cleared" decision were each
verified, but not together, because a person has to answer a questionnaire to approve a Use Case),
and the **cold-setup test**, which needs someone with no context. Neither is a known defect; both
should happen before the status in the README tables moves past Preview.

This cookbook covers **flows 1 and 3 of INT-2** (sync and enforce). Flow 2 — a trigger when an
agent is published — is not buildable: Agent 365 exposes no webhook or change notification, so the
"new agent" trigger is simply the next scheduled poll.

## 1. Structural check

- [x] Standard folder shape: `README.md`, `.env.example`, `TESTING.md`, `server/python/`
- [x] `.env.example` is placeholders only; `.env` and the saved admin token are gitignored
- [x] `TESTING.md` lists real failure modes, each one run (see _Live run_ below)
- [x] README carries the `hub:` frontmatter; `validate_metadata.py` passes
- [x] Python only. `CONTRIBUTING.md` says to ship Python and TypeScript for cookbook delivery, but
      the three most recently merged cookbooks (Splunk, Amazon Bedrock, Azure AI Foundry) are
      Python only; this follows them. **Flagged for a decision.**
- [ ] `logo.svg` is a **placeholder** glyph, not a Microsoft mark. It needs the approved brand asset.

## 2. Code read-through findings

Found and fixed while building, before the live runs:

| #   | Where                         | Finding                                                                                                                                            | Fix                                                                                                                    |
| --- | ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| 1   | `cli.py`, `run()`             | A "could not start" condition raised `SystemExit("message")`, which exits `1` — contradicting the documented contract that setup problems exit `2` | A dedicated `StartupError`, caught in `main()` and mapped to exit `2`. Re-tested for every case in `TESTING.md` Step 9 |
| 2   | `cli.py`, `run()`             | The code comment promised Credo AI would be connected before Microsoft was touched, but the admin sign-in check ran first                          | Credo AI connects first; the sign-in check runs next, before any write                                                 |
| 3   | `agent365.py`                 | Entra's `invalid_client` message ran its trace ID, correlation ID and timestamp onto the same line, burying the cause in every log                 | Truncated at `Trace ID:`; the cause and the app it names remain                                                        |
| 4   | `sync.py`, questionnaire step | A rejected attach reported only `API Error 422`, which does not say what to fix                                                                    | The message now names `CREDO_INTAKE_QUESTIONNAIRE` as the likely cause                                                 |

## 3. Differences from the demo this was built from

The demo proved the calls work. Running it against the lab tenants also surfaced problems that a
scheduled, customer-facing cookbook cannot inherit:

| Demo behavior                                                                                                                                                         | Cookbook behavior                                                                                                                                        |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Treats **step `clear`** as approved. In the lab tenant, several Use Cases sat at `clear` **in the first (intake) stage**, and their agents were therefore _available_ | "Cleared" means the **last stage** (type `end`) **and** step `clear`. Checked against real data: a Use Case at `clear` in a `start` stage is not cleared |
| Blocks every agent that is not cleared, by default                                                                                                                    | Registering agents never blocks anything. Blocking is `--enforce`, with `--dry-run` to preview, `--limit` and `--asset-id` to start small                |
| Syncs all 370 catalog entries — Microsoft's and third parties' apps included                                                                                          | Defaults to the 36 agents the organization built (`type` `shared`/`lob`); `AGENT365_PACKAGE_TYPES` widens it                                             |
| Keeps an `assetId -> Use Case` map in a local JSON file; lost on an ephemeral CI runner                                                                               | Stateless: the asset ID is written into the Use Case description and read back                                                                           |
| Lists every Use Case once **per agent**                                                                                                                               | Reads the Use Case list once per run                                                                                                                     |
| Fetches a detail record for every agent                                                                                                                               | The list response already carries description, `isBlocked` and `lastModifiedDateTime`; no per-agent call                                                 |
| Calls block/unblock on every run for every agent                                                                                                                      | Compares `isBlocked` first and calls Microsoft only when the state has to change                                                                         |
| Assumes agent names are unique                                                                                                                                        | They are not (three agents shared one name in the lab); the asset ID is the key and colliding names are told apart                                       |
| Raw HTTP against Credo AI                                                                                                                                             | `pycredoai` 1.3.0 for every call — no gap required raw HTTP                                                                                              |
| One-off script                                                                                                                                                        | Retries on `429`, a timeout on every call, per-agent error isolation, and the 0/1/2 exit-code contract                                                   |

## 4. Backend cross-check

Cross-checked against the source of truth for each side rather than against another doc.

| Area                     | Verified against                                                                            | Result                                                                                                                                                                                                                                                                        |
| ------------------------ | ------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Credo AI auth            | `credoai-integration-service` `endpoints/auth.py`; the SDK constructor                      | The SDK exchanges the key at `/auth/token` itself. Its `base_url` is the **host**: passing `https://api.credo.ai/api/v1/integration` returns `404`. Documented in README and `.env.example`                                                                                   |
| Use Case calls           | `pycredoai` 1.3.0 models: `UseCaseCreate`, `UseCaseUpdate`, `UseCaseResponse`               | Fields used (`name`, `description`, `questionnaire_ids`, `workflow_stage_step`) exist; exercised live                                                                                                                                                                         |
| Workflow semantics       | `credoai-integration-service` `schemas/resources/workflow_stage_schemas.py`                 | Stage types are `start`/`intermediate`/`end`; steps are `assessment`/`evidence_collection`/`clear`/`rejected`. The backend notes the step vocabulary has been extended before and that readers tolerate unknown values, so an unrecognized step is treated as **not cleared** |
| Questionnaire attach     | SDK `use_cases(id).questionnaires.add(QuestionnaireAttachment(key, version))`               | Works live. `UseCaseResponse.questionnaire_ids` reports attachments as `<key>+<version>`, which is what makes the "already attached" check free                                                                                                                               |
| Use Case name uniqueness | The merged Amazon Bedrock cookbook, which records a duplicate name as `422` "verified live" | Relied on, **not re-triggered here** — the cookbook is built never to send a duplicate                                                                                                                                                                                        |
| Catalog list             | Microsoft Learn reference for `GET /copilot/admin/catalog/packages`, and the live response  | Permissions: `CopilotPackages.Read.All`, Application and Delegated. Live items carry `id`, `displayName`, `type`, `platform`, `publisher`, `longDescription`, `isBlocked`, `lastModifiedDateTime`, `createdDateTime`                                                          |
| Block / unblock          | Microsoft Learn reference for `POST /beta/.../block` and `/unblock`                         | Delegated `CopilotPackages.ReadWrite.All` only; Application is listed "Not available". The cookbook therefore always uses a delegated token for these two calls                                                                                                               |
| `type` values            | The live catalog                                                                            | `thirdParty`, `firstParty`, `shared`, `lob`. `shared` and `lob` are the agents the organization built                                                                                                                                                                         |

## 5. Cold setup test

**Not performed.** It requires someone who has never seen the cookbook, and a run by its author
proves nothing. See _Coverage gaps_.

## 6. Live run — results

Lab Microsoft 365 tenant (Agent 365 licensed) and lab Credo AI tenant. No production tenant was
touched. Python 3.12, `pycredoai` 1.3.0.

**Catalog:** 370 packages — 304 `thirdParty`, 35 `shared`, 30 `firstParty`, 1 `lob`. The default
scope selects 36.

| #   | Run                                                                                                                                                                                                                           | Result                                                                                                            |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| 1   | `--dry-run`                                                                                                                                                                                                                   | `scanned=36 created=36 updated=0 unchanged=0 questionnaires_attached=36 errors=0`. Nothing written. Exit `0`      |
| 2   | `--limit 3`                                                                                                                                                                                                                   | `created=3 questionnaires_attached=3 errors=0`. Exit `0`                                                          |
| 3   | `--limit 3` again                                                                                                                                                                                                             | `created=0 updated=0 unchanged=3 questionnaires_attached=0` — **zero writes**                                     |
| 4   | Backdate one marker, rename one Use Case, `--limit 3`                                                                                                                                                                         | `updated=2 unchanged=1`; the name was restored and the timestamp repaired                                         |
| 5   | `--limit 3` again                                                                                                                                                                                                             | `unchanged=3`                                                                                                     |
| 6   | `--enforce --dry-run` on 3 agents                                                                                                                                                                                             | Planned `blocked=1 unchanged=2` (the other two were already blocked). The saved admin sign-in worked. Exit `0`    |
| 7   | `--enforce` on one Agent Builder agent I own                                                                                                                                                                                  | `blocked=1`. Microsoft reported `isBlocked: true`. The demo found an app-only token gets `424` on this agent type |
| 8   | The same command again                                                                                                                                                                                                        | `blocked=0 unblocked=0 unchanged=1` — no call made                                                                |
| 9   | The cookbook's unblock call, as the signed-in admin                                                                                                                                                                           | `isBlocked` went `true` → `false`                                                                                 |
| 10  | `is_cleared()` against a Use Case a person had cleared                                                                                                                                                                        | `True` — last stage (`end`), step `clear`                                                                         |
| 11  | `CREDO_INTAKE_QUESTIONNAIRE=NOPE+9 --limit 3`                                                                                                                                                                                 | Exit `1`; all 3 agents processed, `errors=3`, each naming the setting to check                                    |
| 12  | The setup-failure rows of `TESTING.md` Step 9: bad key, unreachable host, missing and wrong secret, unrecognized key, bad package types, bad questionnaire, unknown asset ID, no saved sign-in, `--login` without a client ID | Exit `2` with the documented message, no traceback                                                                |

**Logic checks (35, stubbed, nothing real touched)** — all pass: the enforcement decision table
(block, unblock, already-correct, dry-run makes zero calls, one failing call is isolated, an agent
whose Use Case failed is skipped and never blocked); `is_cleared` for `end`/`start`/`intermediate`
stages and for `rejected`; type scope, `--limit` keeping the newest, `--asset-id` overriding scope;
HTML descriptions, empty descriptions, and the marker round-trip; three agents sharing a name get
distinct stable names, including when the agents arrive in a different order; a hand-made Use Case
with a clashing name is not adopted; throttling retried with `Retry-After`; `@odata.nextLink`
followed; the license, empty-`403` and `424` messages; and `set_blocked` refusing to fall back to
app-only auth.

## 7. Coverage gaps

| Gap                                                    | Why it was not covered                                                                                                                                                                      |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Unblock-on-approval, end to end**                    | Needs a person to answer a Use Case's questionnaire. Its parts are verified separately (rows 9 and 10 above, plus the stubbed decision table). **Run `TESTING.md` Step 8 before promoting** |
| **Cold-setup test**                                    | Needs someone with no context                                                                                                                                                               |
| `--login` device-code sign-in                          | A sign-in saved earlier by the demo was reused, so the silent **refresh** path was exercised but the interactive device-code flow was not re-run for this cookbook                          |
| Live multi-page catalog                                | The lab catalog came back in a single page. `@odata.nextLink` following is covered only by the stubbed test                                                                                 |
| `429` throttling and the no-license `403`              | Not reproducible on demand; covered by the stubbed tests and by the exact error text from an unlicensed tenant seen during the demo                                                         |
| Use Case name collision `422`                          | Never sent, by design; relied on from the Bedrock cookbook's finding                                                                                                                        |
| A very large tenant, Windows, and a second tenant      | One lab tenant of each kind, macOS, Python 3.12 only                                                                                                                                        |
| Token-cache survival across scheduled `--enforce` runs | Reasoned from Entra rotating refresh tokens, not run over multiple days                                                                                                                     |

## 8. Decisions flagged for review

These are judgment calls a maintainer or the hub owner should confirm:

1. **`support_level: example`** — chosen over `credo-ai-maintained` because block and unblock sit on
   Microsoft's beta channel. Change it if Credo AI will maintain this.
2. **`category: "Agent gateways & AI security"`** and **`functions: Registry Sync, Deployment
Gating`** — the function values come from the nine-value list confirmed on DEV-8042; the category
   taxonomy is still provisional.
3. **Python only**, as noted in _Structural check_.
4. **`logo.svg` is a placeholder.**
5. **The Guide page** in `credoai-integration-service` (`docs/docs/cookbooks/`) is not part of this
   PR; Stage 3 of `CONTRIBUTING.md` still needs it.

## 9. Recommendation

**Promote to Preview; hold before "✅ Available."** The code, the failure handling and the
enforcement path are verified against real tenants. Before the status moves past Preview: run
`TESTING.md` Step 8 end to end, run the cold-setup test, and have a second human review the diff.
