# Credo AI Integration Recipes

Runnable integration examples for connecting your systems to the Credo AI platform.

Each recipe is self-contained — clone, configure, and run.

---

## Available integrations

| Target System | Integration Pattern | Delivery Type | Status |
|---|---|---|---|
| [JIRA](./jira/use-case-creation) | Use Case Intake | Cookbook (run your own server) | ✅ Available |
| [ServiceNow](./servicenow/use-case-creation) | Use Case Intake | Native Integration (runs inside ServiceNow, no server) | ✅ Available |

More systems and patterns are on the [request board](https://github.com/credo-ai/credoai-recipes/issues/new?template=integration_request.md) — see [Request an integration](#request-an-integration) below.

---

## How to use a recipe

**Cookbook-delivery integrations** (you run a server):

```bash
git clone https://github.com/credo-ai/credoai-recipes.git
cd credoai-recipes/integrations/jira/use-case-creation
cp .env.example .env
# Edit .env with your credentials
cd server/python
pip install -r requirements.txt
uvicorn main:app --port 5000
```

**Native-integration-delivery integrations** (no server — you paste config/script into the target system):

```bash
git clone https://github.com/credo-ai/credoai-recipes.git
cd credoai-recipes/integrations/servicenow/use-case-creation
```

Then follow the README — it walks through the System Properties, Script Include, and Business Rule you create directly inside ServiceNow.

---

## Request an integration

Don't see what you need? [Open an issue](https://github.com/credo-ai/credoai-recipes/issues/new?template=integration_request.md) using the Integration Request template. If enough customers ask for the same thing, it becomes the next cookbook.

---

## Resources

- Docs: [sdk.credo.ai](https://sdk.credo.ai)
- Developer Slack: `#credo-ai-integrations`
- Support: support.credo.ai
