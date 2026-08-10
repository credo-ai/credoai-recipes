import os
import httpx
from fastapi import FastAPI, Request

app = FastAPI()

BASE_URL = os.environ["CREDO_BASE_URL"]
API_KEY  = os.environ["CREDO_API_KEY"]
TENANT   = os.environ["CREDO_TENANT"]


def get_token() -> str:
    response = httpx.post(
        f"{BASE_URL}/auth/token",
        headers={"X-API-Key": API_KEY, "X-Tenant": TENANT},
    )
    response.raise_for_status()
    return response.json()["access_token"]


def create_use_case(name: str, description: str) -> dict:
    token = get_token()
    response = httpx.post(
        f"{BASE_URL}/use_cases",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tenant": TENANT,
            "Content-Type": "application/json",
        },
        json={"name": name, "description": description},
    )
    response.raise_for_status()
    return response.json()


@app.post("/webhooks/jira")
async def jira_webhook(request: Request):
    payload = await request.json()

    if payload.get("webhookEvent") != "jira:issue_created":
        return {"message": "ignored"}

    issue = payload["issue"]
    name  = f"[{issue['key']}] {issue['fields']['summary']}"
    desc  = issue["fields"].get("description", "")

    use_case = create_use_case(name, desc)
    return {"use_case_id": use_case["id"]}
