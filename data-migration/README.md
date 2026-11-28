# Credo AI Data Migration Recipes

Self-serve tools for migrating your data into Credo AI: getting it into a tenant in bulk, and moving it between tenants.

Unlike [integrations](../integrations/README.md), these aren't live connections to another system. You run them once, or now and then, when you need to load or move data.

---

## Available recipes

| Recipe                                                     | What it does                                                                                                                                     | Status       |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ------------ |
| [Use Case Bulk Import](./use-case-bulk-import)             | Upload a spreadsheet of use cases, plus their models, vendors, questionnaire answers, custom fields, and risk scenario links, into a tenant      | ✅ Available |
| [Tenant-to-Tenant Migration](./tenant-to-tenant-migration) | Copy governance data from one Credo AI tenant to another, from configuration such as questionnaires and triggers to use cases with their answers | ✅ Available |

**Which one do I need?**

- Your inventory lives **outside Credo AI** (a spreadsheet, an export from another tool) → Use Case Bulk Import.
- Your data is **already in a Credo AI tenant** and needs to land in another one → Tenant-to-Tenant Migration.

---

## How to use a recipe

From the repo root:

```bash
mise install && uv sync
cp example.env .local.env   # set CREDOAI_API_KEY, CREDOAI_TENANT, CREDOAI_API_URL
cd data-migration/use-case-bulk-import
uv run python migrate.py --dry-run
```

Each recipe's README covers its own inputs, flags, and caveats. Tenant-to-Tenant Migration has its own setup instead: its own `requirements.txt`, and a `.env` with an API key for each tenant. Follow [its README](./tenant-to-tenant-migration/README.md).

---

## Conventions every recipe here follows

- **Public API first.** Recipes use `pycredoai` or the public Credo AI API with a single API key. Tenant-to-Tenant Migration also calls the platform's application API for what the public API can't copy, such as users, teams and triggers, with one admin API key per tenant.
- **Dry run first.** A dry run shows everything the recipe would write and makes no write calls. Use Case Bulk Import takes `--dry-run`; Tenant-to-Tenant Migration dry-runs every load unless you add `--live --target <tenant>`.
- **Safe to re-run.** Records that already exist are matched and skipped, not duplicated.
- **Run summary.** Each run writes a result file with counts and the ids it created.
- **No customer data in the repo.** Sample files use made-up data only.

Test against a sandbox tenant before running against production.

---

## Adding a recipe

1. Create `data-migration/<what-it-does>/`, using a kebab-case name for the job it does (for example `tenant-to-tenant-migration`), not the mechanism.
2. Include a `README.md` with prerequisites, setup, a dry-run step, a real-run step, and troubleshooting. Use [Use Case Bulk Import](./use-case-bulk-import/README.md) as the model.
3. Add dependencies to the root `dev` group with `uv add --group dev <package>`.
4. Add a row to the table above and to the root [README](../README.md).
