"""
One-time setup: register the Databricks registry webhook that feeds main.py.

Registry webhooks are a legacy Workspace Model Registry feature — Databricks
does not deliver them for Models in Unity Catalog, so nothing here applies to
a UC-backed registry.

Registration is deliberately separate from the server: the webhook outlives
any single process, and tying it to startup would re-register it on every
restart. Re-run `register` whenever your public URL changes (free ngrok URLs
rotate on restart), and `delete` the stale one it replaced.

Run: python register_webhook.py register
     python register_webhook.py list
     python register_webhook.py delete <webhook-id>
"""

from __future__ import annotations

import argparse
import os
import sys

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.ml import HttpUrlSpec, RegistryWebhookEvent
from dotenv import load_dotenv

load_dotenv()

WEBHOOK_PATH = "/webhooks/databricks-mlflow"
DESCRIPTION = "Credo AI model registry sync"

# Keep in step with HANDLED_EVENTS in main.py. REGISTERED_MODEL_CREATED is
# registry-wide only — Databricks rejects it on a model-scoped webhook — so
# it's added conditionally below.
BASE_EVENTS = [
    RegistryWebhookEvent.MODEL_VERSION_CREATED,
    RegistryWebhookEvent.MODEL_VERSION_TRANSITIONED_STAGE,
]


def cmd_register(workspace: WorkspaceClient) -> int:
    public_base_url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if not public_base_url:
        print("PUBLIC_BASE_URL is required (see .env.example)", file=sys.stderr)
        return 1

    model_name = os.environ.get("DATABRICKS_MODEL_NAME", "").strip() or None
    secret = os.environ.get("DATABRICKS_WEBHOOK_SECRET", "").strip() or None
    if not secret:
        print(
            "warning: DATABRICKS_WEBHOOK_SECRET is unset — deliveries will be "
            "unsigned and main.py will accept them unverified.",
            file=sys.stderr,
        )

    events = list(BASE_EVENTS)
    if model_name:
        print(
            f"note: scoping to model {model_name!r}; REGISTERED_MODEL_CREATED is "
            "registry-wide only, so models with no versions yet won't sync."
        )
    else:
        events.append(RegistryWebhookEvent.REGISTERED_MODEL_CREATED)

    url = public_base_url + WEBHOOK_PATH
    created = workspace.model_registry.create_webhook(
        events=events,
        http_url_spec=HttpUrlSpec(url=url, secret=secret),
        model_name=model_name,
        description=DESCRIPTION,
    )
    webhook = created.webhook
    print(f"Registered webhook {webhook.id if webhook else '(unknown id)'} -> {url}")
    return 0


def cmd_list(workspace: WorkspaceClient) -> int:
    model_name = os.environ.get("DATABRICKS_MODEL_NAME", "").strip() or None
    found = 0
    for webhook in workspace.model_registry.list_webhooks(model_name=model_name):
        found += 1
        target = webhook.http_url_spec.url if webhook.http_url_spec else "(job spec)"
        print(f"{webhook.id}  {webhook.status}  {target}")
    if not found:
        print("No webhooks registered.")
    return 0


def cmd_delete(workspace: WorkspaceClient, webhook_id: str) -> int:
    workspace.model_registry.delete_webhook(id=webhook_id)
    print(f"Deleted webhook {webhook_id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("register", help="Register the webhook at PUBLIC_BASE_URL")
    sub.add_parser("list", help="List registered webhooks")
    delete = sub.add_parser("delete", help="Delete a webhook by id")
    delete.add_argument("webhook_id")
    args = parser.parse_args()

    workspace = WorkspaceClient()
    if args.command == "register":
        return cmd_register(workspace)
    if args.command == "list":
        return cmd_list(workspace)
    return cmd_delete(workspace, args.webhook_id)


if __name__ == "__main__":
    sys.exit(main())
