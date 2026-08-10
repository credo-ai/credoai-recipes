# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This is **Credo AI Recipes** — a public collection of runnable examples for the
[Credo AI](https://www.credo.ai) platform. It holds two kinds of content:

- `integrations/<system>/<pattern>/` — cookbooks connecting an external system to Credo AI. Each is
  self-contained: README, `.env.example`, `TESTING.md`, and either a `server/{python,typescript}/` app
  (delivery type: cookbook) or an in-platform script documented in the README (delivery type: native
  integration — nothing to run here, config/script lives in the target system).
- Top-level `.py` scripts or `.ipynb` notebooks — one task against the Credo AI SDK directly, end to end.

The SDK-facing part of this repo is deliberately **not a distributable package** (`package = false` in
`pyproject.toml`). There is nothing to build, version, or publish for that half. Integrations are their own
thing — each one pins its own deps (`requirements.txt` / `package.json`) inside its folder, independent of
the root `uv`/`pyproject.toml` setup.

## Repository Structure

```
.
├── .github/workflows/       # CI: pre-commit + ruff, detect-secrets
├── .github/ISSUE_TEMPLATE/  # integration_request.md — the custom-integration-request path
├── pyproject.toml           # Python version, dev deps, ruff config
├── uv.lock                  # Locked dev dependencies
├── example.env              # Commented credential placeholders
├── integrations/            # cookbooks — see integrations/README.md
└── <recipes>                # .py scripts and .ipynb notebooks
```

## Prerequisites

- [mise](https://mise.jdx.dev/) — installs the toolchain pinned in `.tool-versions` and loads env files
  declared in `mise.toml`
- [uv](https://docs.astral.sh/uv/) — Python dependency management (installed by mise)

## Setup

```bash
mise trust
mise install
uv sync
pre-commit install --install-hooks
cp example.env .local.env   # only needed if a recipe requires credentials
```

`mise install` installs everything pinned in `.tool-versions`: `python`, `uv`, `prettier`, `yamllint`,
`yamlfmt`, `pre-commit`. `uv sync` builds `.venv` from `uv.lock`. `pre-commit install` wires hooks into git
so they run on every commit.

If this is your first time committing, generate the secrets baseline once:

```bash
detect-secrets scan > .secrets.baseline
```

## Tooling

| Tool               | Scope                 | Purpose                                                                 |
| ------------------ | --------------------- | ----------------------------------------------------------------------- |
| `ruff`             | `*.py`, `*.ipynb`     | Python linting and formatting (config: `pyproject.toml`)                |
| `nbstripout`       | `*.ipynb`             | Strips notebook outputs so they never reach git                         |
| `uv`               | `pyproject.toml`      | Dependency resolution; `uv-lock` hook keeps `uv.lock` in sync           |
| `uv-sort`          | `pyproject.toml`      | Keeps dependency lists sorted                                           |
| `prettier`         | `*.md`                | Canonical Markdown formatter for the repo                               |
| `yamllint`         | `*.yaml`, `*.yml`     | YAML linting (config: `.yamllint.yaml`)                                 |
| `yamlfmt`          | `*.yaml`, `*.yml`     | YAML formatting (config: `.yamlfmt`)                                    |
| `actionlint`       | `.github/workflows/*` | Lints GitHub Actions workflows                                          |
| `detect-secrets`   | repo-wide             | Catches accidentally committed secrets                                  |
| `shellcheck`       | `*.sh`, `*.bash`      | Shell script linting                                                    |
| `pre-commit-hooks` | repo-wide             | Whitespace, EOF, JSON/YAML/TOML well-formedness, merge-conflict markers |

All hooks run via `pre-commit` on staged files at commit time. CI runs on **pull requests only** and checks
the files changed in the PR (`--from-ref origin/$base_ref --to-ref HEAD`). Nothing runs on push to `main`, so
run `pre-commit run --all-files` locally when you want full-tree coverage.

A separate `Detect Secrets` workflow scans every tracked file on each PR, as a full-tree backstop to the
changed-files-only `detect-secrets` pre-commit hook.

**Do not reference `credo-ai/gh-automation` (or any other private repo) from a workflow here.** This
repository is public, so private composite actions cannot be resolved. The detect-secrets steps are inlined
for exactly this reason — see the comment at the top of `.github/workflows/detect-secrets.yaml`.

## Common Commands

```bash
# Run every hook against the entire tree (broader than CI, which only checks changed files)
pre-commit run --all-files

# Lint and format Python and notebooks
uv run ruff check --fix .
uv run ruff format .

# Add a dev dependency (keeps uv.lock in sync)
uv add --group dev <package>

# Launch JupyterLab
uv run jupyter lab

# Lint and format YAML
yamllint .
yamlfmt -dstar '**/*.{yaml,yml}'

# Format all Markdown
prettier --write '**/*.md'
```

## Recipe Authoring

1. Add the recipe as a `.py` script or `.ipynb` notebook.
2. Add new dependencies with `uv add --group dev <package>` — never hand-edit `uv.lock`.
3. Document what it does, what it requires, and any caveats: the notebook's first Markdown cell, or the
   script's module docstring.
4. If it needs credentials, add a commented placeholder to `example.env` and read it from the environment.
5. Run `pre-commit run --all-files` and address any findings.
6. Open a PR against `main`.

## Important Notes

- **Notebook outputs are never committed.** `nbstripout` enforces this, and it runs _before_ `ruff` in the
  hook order so the formatters see already-stripped notebooks. Recipes must be runnable from a clean state.
- **`ruff` handles `.ipynb` natively.** Both `ruff-check` and `ruff-format` declare
  `types_or: [python, pyi, jupyter]`. Do not add `nbqa` — it is redundant here.
- **Markdown formatting is owned by `prettier`.** Do not add `markdownlint`; this project deliberately uses
  a single Markdown formatter.
- **YAML uses both `yamllint` (lint) and `yamlfmt` (format).** Keep their rules aligned: sequence
  indentation is disabled in `.yamllint.yaml` and in `.yamlfmt`. If you change one, mirror it in the other.
  This is why YAML files here have flush-left list items.
- **Pin tool versions in `.tool-versions`.** Don't introduce ad-hoc tool dependencies; if a new tool is
  needed, pin it via mise so every contributor and CI runs the same version. Dependabot watches the
  `github-actions`, `uv`, and `pre-commit` ecosystems, but it does **not** update `.tool-versions` — when a
  hook rev is bumped for a tool that is also pinned there, update both.
- **This repo is public.** Never commit credentials, customer data, or tenant identifiers. `example.env`
  holds commented placeholders only.
- **Not a package.** `pyproject.toml` sets `package = false`; dependencies live in the `dev` group. There is
  no build, no release, and no `src/` layout.

## About Credo AI

[Credo AI](https://www.credo.ai) is the AI Governance, Risk, and Compliance platform. The recipes here show
how to drive common platform workflows — policy review, evidence collection, integration glue — from Python.

For Credo AI product issues, contact your Credo AI representative. Issues with a recipe should be filed on
this repository.
