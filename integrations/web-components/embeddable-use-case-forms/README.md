# Credo AI Web Components — Use Case Forms and Preview

Embed Credo AI use case forms in your own portal with one `<script>` tag and one HTML element. The
elements fetch questionnaires from Credo AI at runtime, so nothing about your questionnaires is
hardcoded in the page or the bundle.

| Element                           | What it does                                                                                             |
| --------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `<credo-ai-use-case-intake-form>` | Registers a use case against a fixed questionnaire. Every question is shown up front.                    |
| `<credo-ai-use-case-edit-form>`   | Edits an existing use case and every questionnaire attached to it, with conditional questions.           |
| `<credo-ai-use-case-preview>`     | Read-only preview of a use case. Questionnaire order, visibility and custom fields are set by attribute. |

Each element builds to one self-contained JavaScript file. A host page needs no framework, import
map or module graph.

> **Status: preview recipe.** Re-verify the API calls against your tenant before relying on them. See
> [Known limitations](#known-limitations).

---

## How it works

```
Browser (your portal)              Your server                 Credo AI
─────────────────────              ───────────                 ────────
<credo-ai-...> element
   │  POST /api/token ───────────▶ token service
   │                               │  exchange API key ──────▶ integration API
   │  ◀── { integration_api_token } ┘
   │
   │  GET questionnaires, use cases ───────────────────────▶ Credo AI (Bearer token)
   │  POST/PATCH use case ─────────────────────────────────▶ Credo AI
   ▼
use_case_created / use_case_updated / loaded  (DOM events on the element)
```

The Credo AI API key stays in the token service's `.env`. The browser only ever holds one
short-lived token. When a call returns 401 the element asks the token service for a fresh one, so a
long-lived page keeps working.

---

## Prerequisites

- A Credo AI tenant and an API key (platform settings)
- Intake form: a published use case questionnaire, and its ID
- Edit form and preview: a use case with at least one questionnaire attached
- Python 3.11+ for the token service
- Node 20.19+ and npm to build the bundles

---

## Step 1: Configure and start the token service

```bash
cp .env.example .env
# edit .env: CREDO_API_KEY, CREDO_TENANT, and ALLOWED_ORIGINS for your portal

cd server/python
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

`POST http://localhost:8000/api/token` should now return:

```json
{ "integration_api_token": "eyJ..." }
```

Check the credentials against your tenant without a browser. The script is read-only and exits
non-zero if any check fails:

```bash
python scripts/smoke.py <use_case_id>
```

---

## Step 2: Build the bundles

```bash
cd web
npm ci
npm run build
```

This writes one file per element to `web/dist/`:

```
dist/use-case-intake-form.js
dist/use-case-edit-form.js
dist/use-case-preview.js
```

Each bundle is roughly 600 to 700 KB (about 200 KB gzipped) because it includes React and the Credo
AI SDK. Host these files on your own static hosting or CDN.

---

## Step 3: Embed in your page

```html
<script src="https://your-cdn.example.com/use-case-intake-form.js"></script>

<credo-ai-use-case-intake-form
  api-url="https://api.credo.ai"
  tenant="your-tenant"
  user="someone@example.com"
  questionnaire-id="your-questionnaire-id"
  token-endpoint="https://your-portal.example.com/api/credo-token"
></credo-ai-use-case-intake-form>
```

The edit form and preview work the same way, with `use-case-id` instead of `questionnaire-id`.

### Try it locally first

```bash
cd web
cp public/config.example.js public/config.js    # gitignored; fill in tenant, user, ids
npm run dev                                      # http://localhost:5173
```

The pages in `web/registry/` are the reference embeddings. View source to see exactly what a host
page has to do.

---

## Attribute reference

Attributes are kebab-case. Booleans are the strings `true` or `false`. Attributes that hold lists
take a JSON array string, for example `'["TCV2","AGV1"]'`.

### Shared by every element

| Attribute                     | Required | Description                                                           |
| ----------------------------- | -------- | --------------------------------------------------------------------- |
| `tenant`                      | Yes      | Your Credo AI tenant                                                  |
| `token-endpoint`              | Yes\*    | URL of your token service. Refreshes the token on 401.                |
| `integration-token`           | Yes\*    | Alternative to `token-endpoint`: a token you minted. Not refreshable. |
| `api-url`                     | No       | Credo AI API base URL. Default `https://api.credo.ai`.                |
| `integration-service-api-url` | No       | Integration API base URL. Defaults to `api-url`.                      |

\* Set `token-endpoint` or `integration-token`.

There is deliberately **no `api-key` attribute**. Accepting one would put a long-lived tenant
credential in your page source.

### Shared by the two forms

| Attribute            | Required | Description                                                            |
| -------------------- | -------- | ---------------------------------------------------------------------- |
| `user`               | Yes      | Email recorded as the author of anything the form writes               |
| `app-url`            | No       | When set, the form links to the created or edited use case in Credo AI |
| `hide-custom-fields` | No       | `true` hides the tenant custom fields section                          |
| `allow-submit`       | No       | `false` renders the form read-only, with no Submit or Update button    |

### `<credo-ai-use-case-intake-form>`

| Attribute                         | Required | Description                                                                                |
| --------------------------------- | -------- | ------------------------------------------------------------------------------------------ |
| `questionnaire-id`                | Yes      | The questionnaire the form submits against                                                 |
| `disable-questionnaire-selection` | No       | `true` locks the dropdown to `questionnaire-id`. `false` (default) lets the user pick one. |
| `source`                          | No       | Free-text provenance tag. **Leave empty**, see [Known limitations](#known-limitations).    |

### `<credo-ai-use-case-edit-form>`

| Attribute     | Required | Description                   |
| ------------- | -------- | ----------------------------- |
| `use-case-id` | Yes      | The use case to load and edit |

### `<credo-ai-use-case-preview>`

| Attribute                | Required | Description                                                                          |
| ------------------------ | -------- | ------------------------------------------------------------------------------------ |
| `use-case-id`            | Yes      | The use case to preview                                                              |
| `questionnaire-order`    | No       | Questionnaire ids shown first, in this order. Unlisted ones follow.                  |
| `allowed-questionnaires` | No       | Questionnaire ids to show. Unset shows every attached questionnaire.                 |
| `custom-fields`          | No       | Custom field names to show, in order. Unset shows all with a value. `[]` shows none. |
| `use-case-fields`        | No       | Use case properties shown as metadata rows. Only `name` and `description` resolve.   |
| `preview-footer-text`    | No       | Sanitized HTML shown in the footer. Default `Powered by Credo AI`.                   |
| `hide-header-text`       | No       | `true` hides the use case title and description                                      |

Questionnaire ids match on the base id, so `TCV2` matches `TCV2+25`. If a required attribute or
credential is missing, the element renders a "Not configured" notice instead of a blank page or an
exception.

---

## Events

Events are `composed` custom events that cross the shadow DOM boundary. Listen on the element itself.

| Event                 | Elements | `detail`                                              |
| --------------------- | -------- | ----------------------------------------------------- |
| `use_case_created`    | Intake   | `{ useCaseId }`                                       |
| `use_case_updated`    | Edit     | `{ useCaseId }`                                       |
| `form_values_changed` | Forms    | The current form values (fires while the user types)  |
| `form_state_changed`  | Forms    | `{ isValid, isDirty, missingRequired }`               |
| `loaded`              | Preview  | `{ useCaseId, questionnaireIds }`                     |
| `error`               | All      | `{ message }` (edit form and preview add `useCaseId`) |

```js
const form = document.querySelector("credo-ai-use-case-intake-form");

form.addEventListener("use_case_created", (e) => {
  window.location.href = `/use-cases/${e.detail.useCaseId}`;
});
form.addEventListener("error", (e) => console.error(e.detail.message));
```

Required questions that are currently **visible** block Submit. Questions the conditional engine has
hidden never do. `file` and `user` question types have no control and are excluded.

---

## Known limitations

- **The token endpoint is unauthenticated.** Anyone whose browser can reach it gets a working Credo AI
  token. CORS (`ALLOWED_ORIGINS`) only stops other websites from calling it. In production, put
  `POST /api/token` behind your own user authentication, or mint the token in your own backend and
  pass it as `integration-token`.
- **`source` is rejected by the integration service.** The service currently returns 422 for every
  non-empty value. Leave it empty.
- **Some calls use the platform API directly.** Custom field definitions and questionnaire preview
  have no SDK equivalent, so the elements call `/api/v2/{tenant}` with the same token. That API can
  change without notice.
- **Browser calls need CORS from the Credo AI API.** Both APIs allow cross-origin requests today. If
  your deployment restricts this, route data calls through a same-origin proxy.
- **Bundle size.** Each element is self-contained, so a page that embeds several loads React and the
  SDK once per element.

---

## Project layout

```
.
├── README.md  .env.example  TESTING.md
├── server/python/        Token service (FastAPI). Holds the API key, mints a token, proxies nothing.
│   ├── app/              config, credo_auth (key exchange via pycredoai), main (POST /api/token)
│   ├── scripts/smoke.py  Read-only live check
│   └── tests/
└── web/                  Elements (React + Vite + Tailwind, one IIFE bundle each)
    ├── src/elements/     One entry point per element
    ├── src/lib/          Pure logic: payloads, conditional visibility, required-field gating
    ├── src/api/          Credo AI clients and token sources
    └── registry/         Reference host pages
```

---

## Tests

```bash
cd server/python && pip install -r requirements-dev.txt && pytest
cd web && npm run build && npm test && npm run lint
```

Run `npm run build` before `npm test`: one suite evaluates the **built** bundles with `process` and
`global` removed, which catches load-time crashes that source-level tests cannot. See
[TESTING.md](./TESTING.md) for the full walkthrough against a tenant.
