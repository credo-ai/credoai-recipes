# Contributing an integration

Four stages, each one a gate for the next: **build → QA/testing → enablement → publish readiness.**

## 1. Build

Create `integrations/<target-system>/<integration-pattern>/` with:

- `README.md`, `.env.example`, `TESTING.md`
- `server/{python,typescript}/` if this integration needs a server you host (delivery type: **cookbook**),
  or the in-platform script embedded directly in the README if it doesn't (delivery type: **native
  integration**)

Ship both Python and TypeScript for cookbook-delivery integrations. Run it end-to-end against a real
Integration Service instance before opening a PR — a skeleton that doesn't hit a real endpoint isn't done.

## 2. QA / testing

- `TESTING.md` lists the top 5–7 failure modes: exact symptom → exact fix
- Hand it to someone with zero Credo AI context and time them to a running state. Target: under your own
  stated Quick Start estimate, no help needed
- `pre-commit run --all-files` clean, `detect-secrets` clean

## 3. Enablement

- The Advisory/CS demo for this integration should be generated from your Quick Start + Recipe sections,
  not written separately
- Draft the webpage Guide page in `credoai-integration-service` (`docs/docs/cookbooks/`), tagged by
  target system, integration pattern, and delivery type — see that repo's `docs/docs/tags.yml`

## 4. Publish readiness

- Webpage page registered in `docs/sidebars.js`, linked from the Cookbooks index, Code link resolves
- This repo's README/`integrations/README.md` status flips to "✅ Available" — don't list it as shipped
  before it is
- A maintainer runs the docs publish job separately; merging this repo's PR doesn't publish the webpage

Full lifecycle detail and rationale: see the `cookbook_authoring_lifecycle` doc (ask in
`#credo-ai-integrations` if you don't have a link).

## Don't see an integration you need?

[Open an integration request](https://github.com/credo-ai/credoai-recipes/issues/new?template=integration_request.md).
Enough requests for the same thing promotes it into Stage 1.
