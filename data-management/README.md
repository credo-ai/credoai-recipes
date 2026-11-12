# Credo AI Data Management Recipes

Self-serve tools for managing your own Credo AI data: getting it into a tenant in bulk, and moving it between tenants.

Unlike [integrations](../integrations/README.md), these aren't live connections to another system. You run them once, or now and then, when you need to load or move data.

---

## Available recipes

| Recipe                                         | What it does                                                                                                                                | Status         |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | -------------- |
| [Use Case Bulk Import](./use-case-bulk-import) | Upload a spreadsheet of use cases, plus their models, vendors, questionnaire answers, custom fields, and risk scenario links, into a tenant | ✅ Available   |
| Tenant-to-Tenant Migration                     | Copy governance data from one Credo AI tenant to another                                                                                    | 🚧 In progress |

**Which one do I need?**

- Your inventory lives **outside Credo AI** (a spreadsheet, an export from another tool) → Use Case Bulk Import.
- Your data is **already in a Credo AI tenant** and needs to land in another one → Tenant-to-Tenant Migration.

---

## How to use a recipe

From the repo root:

```bash
mise install && uv sync
cp example.env .local.env   # set CREDOAI_API_KEY, CREDOAI_TENANT, CREDOAI_API_URL
cd data-management/use-case-bulk-import
uv run python migrate.py --dry-run
```

Each recipe's README covers its own inputs, flags, and caveats.

---

## Conventions every recipe here follows

- **Public API only.** Recipes use `pycredoai` or the public Credo AI API with a single API key.
- **Dry run first.** `--dry-run` shows everything the recipe would write and makes no write calls.
- **Safe to re-run.** Records that already exist are matched and skipped, not duplicated.
- **Run summary.** Each run writes a result file with counts and the ids it created.
- **No customer data in the repo.** Sample files use made-up data only.

Test against a sandbox tenant before running against production.

---

## Adding a recipe

1. Create `data-management/<what-it-does>/`, using a kebab-case name for the job it does (for example `tenant-to-tenant-migration`), not the mechanism.
2. Include a `README.md` with prerequisites, setup, a dry-run step, a real-run step, and troubleshooting. Use [Use Case Bulk Import](./use-case-bulk-import/README.md) as the model.
3. Add dependencies to the root `dev` group with `uv add --group dev <package>`.
4. Add a row to the table above and to the root [README](../README.md).
