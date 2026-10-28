# Testing — Credo AI Web Components, Use Case Forms

Work through these in order. Steps 1 and 2 need no Credo AI tenant, so you can confirm the recipe
installs, builds and fails cleanly before arranging access.

---

## Step 1: Automated checks, no tenant

```bash
cd server/python
pip install -r requirements-dev.txt
pytest                                  # token endpoint and exchange tests, no network

cd ../../web
npm ci
npm run build                           # build first: one suite tests the built bundles
npm test
npm run lint
```

Expected: every suite passes and `web/dist/` holds `use-case-intake-form.js`,
`use-case-edit-form.js` and `use-case-preview.js`.

The built-bundle suite evaluates each file with `process` and `global` deleted, which is what a
browser has. If it fails with `ReferenceError: process is not defined`, the bundle would crash on
load in a real page even though the source-level tests pass.

---

## Step 2: Elements fail cleanly, no tenant

```bash
cd web
cp public/config.example.js public/config.js    # leave every value empty
npm run dev                                      # http://localhost:5173
```

Open the three pages under `/registry/`.

| Page        | Expected                                                                    |
| ----------- | --------------------------------------------------------------------------- |
| Intake form | A "Not configured" notice. No blank page, no uncaught error in the console. |
| Edit form   | The same notice.                                                            |

---

## Step 3: Token service against your tenant

Fill `.env` (see `.env.example`), then:

```bash
cd server/python
uvicorn app.main:app --port 8000        # in one terminal
python scripts/smoke.py <use_case_id>   # in another
```

| Result                                               | Meaning                                              |
| ---------------------------------------------------- | ---------------------------------------------------- |
| `Private JWT: ok` and `Integration bearer token: ok` | The API key and tenant are valid                     |
| A use case name and `Integration API read ... ok`    | Both Credo AI surfaces accept the minted tokens      |
| `HTTP 404` on a `use case ...` read                  | That route is not live in your environment (note it) |
| Non-zero exit                                        | Credentials, tenant or use case ID are wrong         |

Confirm the browser never sees the key:

```bash
curl -s -X POST http://localhost:8000/api/token | python -m json.tool
```

The response must contain only `integration_api_token`.

---

## Step 4: Intake form

Set `tenant`, `user`, `questionnaireId` and `tokenEndpoint` in `web/public/config.js`, then open
`/registry/use-case-intake-form.html`.

1. The form renders the questionnaire's questions, with every supported question visible.
2. With `disableQuestionnaireSelection: true` the questionnaire dropdown is locked.
3. Leave a required question empty: Submit stays disabled. The browser console logs
   `form_state_changed` with `isValid: false` and the missing questions.
4. Fill everything in and submit. The console logs `use_case_created` with a `useCaseId`, and the
   use case appears in your tenant.
5. In DevTools > Network, confirm no request or page source contains the API key.

---

## Step 5: Edit form

Set `useCaseId` in `web/public/config.js`, then open `/registry/use-case-edit-form.html`.

1. Existing answers are pre-filled.
2. Answer a question that controls others: dependent questions appear and disappear.
3. Change a value and save. The console logs `use_case_updated`, and the change shows in your tenant.
4. Expire a token (restart the token service with a different key, then restore it) and save again:
   the element refetches tokens on the 401 and the save succeeds.

---

## Step 6: Preview

Set `useCaseId` in `web/public/config.js`, then open `/registry/use-case-preview.html`.

1. The preview renders read-only, with the use case title, description, every attached questionnaire
   and the footer. The console logs `loaded` with `{ useCaseId, questionnaireIds }`.
2. Set `previewQuestionnaireOrder` to a JSON array of ids and reload: those questionnaires move to
   the top.
3. Set `previewAllowedQuestionnaires` to one id: only that questionnaire remains.
4. Set `previewCustomFields: '[]'`: no custom fields show. Set it to a list of names: only those show,
   in that order.
5. Set `hideHeaderText: 'true'`: the title and description disappear.

---

## Step 7: Read-only and hidden sections

In `web/public/config.js`:

| Setting                  | Expected                                          |
| ------------------------ | ------------------------------------------------- |
| `allowSubmit: false`     | Both forms render with no Submit or Update button |
| `hideCustomFields: true` | The custom fields section is not rendered         |

---

## Step 8: Clean up

Delete the use cases you created in Step 4, and never commit `.env` or `web/public/config.js`.
