# Credo AI Recipes

Runnable recipes for the [Credo AI](https://www.credo.ai) platform — Python examples and Jupyter
notebooks that show how to do a specific thing end to end.

A recipe is small, self-contained, and runnable from a clean checkout. If it needs credentials, it reads
them from the environment and says so up front.

## Prerequisites

- [mise](https://mise.jdx.dev/) — installs the toolchain pinned in `.tool-versions` and loads the env
  files declared in `mise.toml`
- [uv](https://docs.astral.sh/uv/) — Python dependency management (installed by mise)

## Setup

```bash
mise trust
mise install
uv sync
pre-commit install --install-hooks
cp example.env .local.env   # only if a recipe needs credentials
```

`mise install` installs everything in `.tool-versions` (Python, uv, prettier, yamllint, yamlfmt,
pre-commit). `uv sync` creates `.venv` from `uv.lock`. `pre-commit install` wires the hooks into git so
they run on every commit.

If this is your first time committing, generate the secrets baseline once:

```bash
detect-secrets scan > .secrets.baseline
```

## Running a recipe

```bash
# Notebooks
uv run jupyter lab

# Scripts
uv run python path/to/recipe.py
```

Recipes are committed **without notebook outputs** — `nbstripout` strips them on commit. Run a notebook
yourself to see its results.

## Contributing a recipe

1. Add your recipe as a `.py` script or `.ipynb` notebook.
2. Add any new dependencies with `uv add --group dev <package>` so `uv.lock` stays in sync.
3. Document what the recipe does, what it needs, and any caveats — in the notebook's first Markdown cell
   or the script's module docstring.
4. If it needs credentials, add commented placeholders to `example.env`. Never commit real values.
5. Run `pre-commit run --all-files` and fix anything it flags.
6. Open a PR against `main`.

## Development

CI runs the same hooks on every pull request, but only against the files the PR changes. Run the full tree
locally before opening one:

```bash
# Run every hook against the entire tree
pre-commit run --all-files

# Lint and format Python and notebooks
uv run ruff check --fix .
uv run ruff format .
```

See [CLAUDE.md](./CLAUDE.md) for the full tooling reference and repo conventions.
