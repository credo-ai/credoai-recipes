# Changelog

Versions follow [semantic versioning](https://semver.org). Each release lists what changed for someone running the kit.

## 0.2.1 (2026-11-27)

- The kit is now also available from the public [credoai-recipes](https://github.com/credo-ai/credoai-recipes)
  repository, in `data-migration/tenant-to-tenant-migration/`. No change in behavior: the code was reformatted to that
  repository's style.

## 0.2.0 (2026-11-25)

- New entity `risk_type_weights`: copies the risk type weighting used by the risk scoring classification, matching
  risk types by name. It loads right after `risk_types`, and it replaces the target's weighting (the log lists the
  previous one).
- Fixed: risk scenarios, controls and policy packs that apply only to some entity types (for example, use cases
  only) were created applying to every entity type. They now keep the source's "Applies to".
- Fixed: a custom risk type without a description failed to load.
- Fixed: in a `load all` dry run, triggers on new questionnaires were reported as failed ("couldn't be read on the
  target"), and triggers whose actions add a new control or policy pack as "isn't on the target". The dry run now
  predicts them as the first live run will, as documented.
- Fixed: the use case extract stopped when a use case was attached to a questionnaire version the source can't return
  (seen on an archived questionnaire). It now warns that those answers aren't copied and goes on.
- Risk type weighting: when some of the source's weights are on risk types hidden or deleted there, the rest are
  scaled to total 100% (keeping their ratios) instead of being rejected by the target.
- Answers: a single selection now copies between a single select and a multi-select question when it is one of the
  target question's options.
- Fixed: the end of a triggers load listed a questionnaire as ready to publish even when one of its own triggers
  hadn't loaded (for example, one waiting on another questionnaire to be published). Such questionnaires are now
  left off that list, and a separate line says not to publish them yet.
- New `--raise-roles` option for `load`: raises users already on the target to their source role where the target's
  built-in role (user, reviewer, admin) is lower. Without it, the users load lists those users and changes nothing.

## 0.1.0 (2026-11-03)

First release. Copies configuration and content from one Credo AI tenant (the source) to another (the target). The
source is only read; nothing is written to the target unless a load is run with `--live`.

**What it copies**, in load order:

- Risk types and custom fields
- Controls, policy packs and custom risk scenarios (controls and packs as drafts)
- Questionnaires, with their section and question IDs kept (as drafts)
- Vendors, users and teams
- Triggers and actions (Triggers & Actions v3 tenants)
- Models, with their sources and vendor links, and custom domains
- Use cases: owner and other stakeholders, custom field values, risk classification, policy packs, risk scenarios,
  control evidence, domains, model and vendor links, questionnaires and answers, workflow stage and step
- Answers on custom models and vendors

**How it behaves**

- Every load is a dry run unless `--live` is given, and `--live` also needs `--target` naming the target tenant.
- Re-running is safe: items already copied are skipped, and an interrupted use case is completed on the next run.
- Existing items on the target are never changed. Shared reference data (risk types, custom fields, domains, users,
  and teams by exact name) with the same name or ID is reused; content with the same name (models, use cases) is
  skipped with a warning.
- Target answers are never overwritten. Answers on an older questionnaire version are matched to the target's version
  by question text, and text answers become selections where they match an option.
- Triggers are paused (Partial Pause) on migrated use cases, so the copies match the source; the run summary says how
  to turn them back on.
- `check` shows each tenant's Triggers & Actions version and whether the target would email invitations to new users.
- A live users load stops if the target may email invitations, unless `--allow-invites` is given.
- Answer and custom field values are never written to the logs.

**Known limits**

- Built-in (Credo-supplied) items aren't copied; they are already on every tenant, with their answers.
- Not copied: workflow stage configuration (stages are matched by name), roles, comments, tasks, reviews, audit history,
  file answers, and residual risk values (the platform derives them).
- Answers to user questions aren't copied, because users differ between tenants; the log lists them.
- Drafts the kit creates must be published on the target before items that use them can be loaded; the load lists them.
- Model sources the kit creates can't be deleted afterwards.
