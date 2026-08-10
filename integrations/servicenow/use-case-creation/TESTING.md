# Testing — ServiceNow Use Case Creation

## Step 1: Verify System Properties

In ServiceNow, navigate to **System Properties** and confirm all three properties exist:
- `x_credo_ai.integration_url`
- `x_credo_ai.api_key`
- `x_credo_ai.tenant`

## Step 2: Test the Script Include directly

Open **System Definition → Scripts - Background** and run:

```javascript
var credo = new CredoAIIntegration();
var token = credo.getToken();
gs.info('Token: ' + token);
```

Expected: a JWT token printed in the output. If null, check Application Logs for the error.

## Step 3: Test use case creation

```javascript
var credo = new CredoAIIntegration();
var result = credo.createUseCase({
    name: 'Test Use Case from ServiceNow',
    description: 'Created via background script test'
});
gs.info('Result: ' + JSON.stringify(result));
```

Expected: a JSON object with an `id` field. Log into Credo AI and confirm the use case appears.

## Step 4: End-to-end test

Insert a new record into your AI intake table manually. Check:
1. Application Logs for `CredoAI createUseCase [201]`
2. The record's `u_credo_sync_status` field shows `synced`
3. The record's `u_credo_use_case_id` field is populated
4. Use case appears in Credo AI workspace
