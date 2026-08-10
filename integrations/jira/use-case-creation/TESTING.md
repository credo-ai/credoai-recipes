# Testing — JIRA Use Case Creation

## Local test (no JIRA required)

Start the server, then send a test payload directly:

```bash
curl -X POST http://localhost:5000/webhooks/jira \
  -H "Content-Type: application/json" \
  -d '{
    "webhookEvent": "jira:issue_created",
    "issue": {
      "key": "TEST-1",
      "fields": {
        "summary": "My AI System",
        "description": "Test use case from JIRA"
      }
    }
  }'
```

Expected response:
```json
{ "use_case_id": "uc_..." }
```

Confirm in Credo AI: log in and check your workspace for the new use case.

---

## End-to-end test (with JIRA)

1. Start the server
2. Run `ngrok http 5000` — copy the public URL
3. Configure JIRA webhook (see README Step 5) using the ngrok URL
4. Create a real JIRA issue in your project
5. Check server logs for the incoming payload
6. Confirm use case appears in Credo AI

---

## Test ignored events

Confirm non-issue events are safely ignored:

```bash
curl -X POST http://localhost:5000/webhooks/jira \
  -H "Content-Type: application/json" \
  -d '{"webhookEvent": "jira:issue_updated"}'
```

Expected response:
```json
{ "message": "ignored" }
```
