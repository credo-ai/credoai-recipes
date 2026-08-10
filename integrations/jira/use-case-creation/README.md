# JIRA → Credo AI Use Case Creation

When a new issue is created in JIRA, a webhook fires and automatically creates a matching use case in Credo AI — using the issue key, title, and description. No polling, no manual entry.

- All code runs in your own server environment
- Credentials stored as environment variables, never in source
- Works with any JIRA project and any AI intake workflow

---

## Prerequisites

| Item | Where to get it |
|---|---|
| Integration Service base URL | Provided by Credo AI |
| API key | Credo AI Governance App → Settings → Integrations |
| Tenant name | Your org's tenant identifier in Credo AI |
| JIRA admin access | To create and configure webhooks |

---

## Step 1: Set your credentials

```bash
cp .env.example .env
# Edit .env with your real values
```

---

## Step 2: Field mapping

| Credo AI Field | Your JIRA Field | Example |
|---|---|---|
| Use Case Name | (your field) | `issue.fields.summary` |
| Use Case Description | (your field) | `issue.fields.description` |

---

## Step 3: Run the server

**Python:**
```bash
cd server/python
pip install -r requirements.txt
uvicorn main:app --port 5000
```

**TypeScript:**
```bash
cd server/typescript
npm install
npx tsx main.ts
```

---

## Step 4: Test locally

```bash
ngrok http 5000
```

```bash
curl -X POST https://your-ngrok-url/webhooks/jira \
  -H "Content-Type: application/json" \
  -d '{
    "webhookEvent": "jira:issue_created",
    "issue": {
      "key": "TEST-1",
      "fields": { "summary": "My AI System", "description": "" }
    }
  }'
```

Expected: `{"use_case_id": "uc_..."}`

---

## Step 5: Configure JIRA

1. JIRA → **Settings → System → Webhooks → Create a Webhook**
2. URL: `https://your-domain.com/webhooks/jira`
3. Events: check **Issue → created**
4. Save

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| 401 on token exchange | Wrong `CREDO_API_KEY` or `CREDO_TENANT` | Check `.env` values |
| 404 Not Found | Wrong `CREDO_BASE_URL` | Confirm base URL with Credo AI |
| 400 / 422 Bad Request | Malformed payload | Check server logs |
| Use case blank name | `summary` field missing | Check JIRA issue type |
| `'ignored'` for every event | Wrong event type in JIRA | Confirm **Issue → created** is checked |
| JIRA delivery failed | Endpoint not reachable | Verify ngrok or deployment URL |

Still stuck? Slack: `#credo-ai-integrations` | Support: support.credo.ai

---

## Full guide

[docs.credo.ai/integrations/jira/use-case-creation](https://docs.credo.ai/integrations/jira/use-case-creation)
