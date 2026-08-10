# ServiceNow → Credo AI Use Case Creation

When a new record is inserted into your AI intake table in ServiceNow, a Business Rule fires server-side and calls the Credo AI Integration Service to create a corresponding use case.

- No external JavaScript — all code runs inside ServiceNow
- All outbound API calls logged natively in Application Logs
- Credentials stored in System Properties, encrypted at rest

---

## Prerequisites

| Item | Where to get it |
|---|---|
| Integration Service base URL | Provided by Credo AI |
| API key | Credo AI Governance App → Settings → Integrations |
| Tenant name | Your org's tenant identifier in Credo AI |
| ServiceNow admin access | To create System Properties, Script Includes, Business Rules |

---

## Step 1: Store credentials in System Properties

Navigate to **System Properties** and create the following records:

| Property Name | Type | Value |
|---|---|---|
| `x_credo_ai.integration_url` | String | Credo AI Integration Service base URL |
| `x_credo_ai.api_key` | Password2 | Your Credo AI API key |
| `x_credo_ai.tenant` | String | Your Credo AI tenant name |

**Note:** Use `Password2` for the `api_key` field — it encrypts the value at rest and keeps it out of logs.

---

## Step 2: Column mapping

Before writing the Business Rule, identify which columns in your AI intake table map to each Credo AI field. Fill in the middle column and carry these values into Step 4.

| Credo AI Field | Your Table Column | Example |
|---|---|---|
| Use Case Name | (your column name) | `u_ai_system_name` |
| Use Case Description | (your column name) | `u_description` |
| Owner Email | (your column name) | `u_requested_by_email` |

---

## Step 3: Create the Script Include

Navigate to **System Definition → Script Includes** and create a new record:

- **Name:** `CredoAIIntegration`
- **Client callable:** false

Paste the following into the Script field:

```javascript
var CredoAIIntegration = Class.create();

CredoAIIntegration.prototype = {

    initialize: function() {
        this.baseUrl = gs.getProperty('x_credo_ai.integration_url');
        this.apiKey  = gs.getProperty('x_credo_ai.api_key');
        this.tenant  = gs.getProperty('x_credo_ai.tenant');
    },

    getToken: function() {
        var rm = new sn_ws.RESTMessageV2();
        rm.setEndpoint(this.baseUrl + '/auth/token');
        rm.setHttpMethod('POST');
        rm.setRequestHeader('X-API-Key', this.apiKey);
        rm.setRequestHeader('X-Tenant', this.tenant);
        try {
            var response = rm.execute();
            var status = response.getStatusCode();
            if (status === 200) {
                return JSON.parse(response.getBody()).access_token;
            }
            gs.error('CredoAI getToken failed [' + status + ']: ' + response.getBody());
            return null;
        } catch (e) {
            gs.error('CredoAI getToken exception: ' + e.message);
            return null;
        }
    },

    createUseCase: function(params) {
        var token = this.getToken();
        if (!token) return null;

        var rm = new sn_ws.RESTMessageV2();
        rm.setEndpoint(this.baseUrl + '/use_cases');
        rm.setHttpMethod('POST');
        rm.setRequestHeader('Authorization', 'Bearer ' + token);
        rm.setRequestHeader('X-Tenant', this.tenant);
        rm.setRequestHeader('Content-Type', 'application/json');
        rm.setRequestBody(JSON.stringify({
            name:        params.name,
            description: params.description || ''
        }));
        try {
            var response = rm.execute();
            var status = response.getStatusCode();
            gs.info('CredoAI createUseCase [' + status + ']: ' + response.getBody());
            if (status === 200 || status === 201) return JSON.parse(response.getBody());
            gs.error('CredoAI createUseCase failed [' + status + ']: ' + response.getBody());
            return null;
        } catch (e) {
            gs.error('CredoAI createUseCase exception: ' + e.message);
            return null;
        }
    },

    type: 'CredoAIIntegration'
};
```

---

## Step 4: Create the Business Rule

Navigate to **System Definition → Business Rules** and create a new record:

- **Name:** `Send to Credo AI`
- **Table:** your AI intake table (e.g. `x_ai_intake`)
- **Advanced:** true
- **When to run:** Insert: checked

Substitute your column names from Step 2 into the two highlighted lines:

```javascript
(function executeRule(current, previous) {

    var credo = new CredoAIIntegration();

    var useCase = credo.createUseCase({
        name:        current.getValue('u_ai_system_name'),  // <-- your name column
        description: current.getValue('u_description')      // <-- your description column
    });

    if (!useCase) {
        current.setValue('u_credo_sync_status', 'failed');
        current.update();
        return;
    }

    current.setValue('u_credo_use_case_id', useCase.id);
    current.setValue('u_credo_sync_status', 'synced');
    current.update();

})(current, previous);
```

---

## Step 5: Add sync status fields

Add these two fields to your AI intake table to track sync state:

| Field Name | Type | Purpose |
|---|---|---|
| `u_credo_use_case_id` | String | Credo AI use case ID — populated on success |
| `u_credo_sync_status` | Choice | Values: `pending`, `synced`, `failed` |

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| 401 on token exchange | Wrong `api_key` or `tenant` | Check System Properties |
| 401 on use case creation | Token exchange failed silently | Check Application Logs for `getToken` errors |
| 404 Not Found | Wrong `integration_url` | Confirm base URL with Credo AI |
| 400 / 422 Bad Request | Malformed payload | Check Application Logs for request body |
| Use case name blank in Credo AI | Wrong column name | Verify column name in Step 2 mapping |
| Sync status field stays blank | Business Rule not firing | Verify table name and Insert checkbox |

Still stuck? Slack: `#credo-ai-integrations` | Support: support.credo.ai

---

## Full guide

[docs.credo.ai/integrations/servicenow/use-case-creation](https://docs.credo.ai/integrations/servicenow/use-case-creation)
