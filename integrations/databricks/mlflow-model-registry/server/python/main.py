"""
Databricks MLflow Model Registry -> Credo AI model sync.

Webhook receiver, plus a backfill that runs once at startup. Databricks
registry webhooks only tell you about changes from the moment they're
registered, so on every start this first walks the registry and upserts
everything already there — that's what makes a restart (or a first run
against a registry that predates this integration) true up rather than leave
old models missing. Steady state after that is webhook-driven.

Legacy Workspace Model Registry only: registry webhooks are not available for
Models in Unity Catalog. See ../../README.md.

Run: python main.py
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
import uvicorn
from databricks.sdk import WorkspaceClient
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request

load_dotenv()  # reads ../../.env (or cwd .env) into os.environ — must run
# before the required os.environ[...] reads below.

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [databricks-bridge] %(message)s",
)
log = logging.getLogger("databricks-bridge")

# ---- Credo AI ---------------------------------------------------------
CREDO_BASE_URL = os.environ["CREDO_BASE_URL"].rstrip("/")
CREDO_API_KEY = os.environ["CREDO_API_KEY"]
CREDO_TENANT = os.environ["CREDO_TENANT"]

# Not free text: `source` resolves against a Source record that must already
# exist in the tenant, so every model create 422s until setup_credo_source.py
# has been run once. See README.md Step 2.
SOURCE = "Databricks"

# ---- Databricks -------------------------------------------------------
# DATABRICKS_HOST / DATABRICKS_TOKEN are read by the SDK's unified auth, not
# here — this bridge never touches them directly.
DATABRICKS_WEBHOOK_SECRET = os.environ.get("DATABRICKS_WEBHOOK_SECRET", "")

# Events this bridge understands. Databricks supports several more
# (TRANSITION_REQUEST_CREATED, COMMENT_CREATED, ...); anything else is
# acknowledged and ignored. REGISTERED_MODEL_CREATED covers a model created
# with no versions yet, but Databricks only delivers it to registry-wide
# webhooks — see .env.example's DATABRICKS_MODEL_NAME note.
HANDLED_EVENTS = {
    "REGISTERED_MODEL_CREATED",
    "MODEL_VERSION_CREATED",
    "MODEL_VERSION_TRANSITIONED_STAGE",
}

# ---- Server -----------------------------------------------------------
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "5000"))
WEBHOOK_PATH = "/webhooks/databricks-mlflow"

# Credo AI model name -> model id. Seeded from Credo AI at startup (see
# `lifespan`), which is what keeps a restart from creating duplicates: the
# Credo AI side is the source of truth for what already exists, so no local
# state file has to survive between runs.
MODEL_IDS: dict[str, str] = {}


class CredoApiError(RuntimeError):
    pass


class CredoClient:
    """Credo AI public Integration API.

    Only three calls are needed: list every model once at startup, then
    create or patch one at a time.
    """

    def __init__(self, client: httpx.Client) -> None:
        self._client = client
        self._token: str | None = None

    def _get_token(self) -> str:
        resp = self._client.post(
            f"{CREDO_BASE_URL}/auth/token",
            headers={"X-API-Key": CREDO_API_KEY, "X-Tenant": CREDO_TENANT},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
    ) -> httpx.Response:
        refreshed = False
        for attempt in range(3):
            if self._token is None:
                self._token = self._get_token()
            resp = self._client.request(
                method,
                f"{CREDO_BASE_URL}{path}",
                json=json_body,
                params=params,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "X-Tenant": CREDO_TENANT,
                },
                timeout=30,
            )
            # Refresh once, then treat a second 401 as a real credential
            # problem rather than looping on it.
            if resp.status_code == 401 and not refreshed:
                log.info("token rejected; refreshing once")
                self._token = None
                refreshed = True
                continue
            if resp.status_code >= 500 and attempt < 2:
                log.warning("%s %s -> %s; backing off", method, path, resp.status_code)
                time.sleep(2**attempt)
                continue
            if resp.status_code >= 400:
                raise CredoApiError(
                    f"{method} {path} failed: {resp.status_code} {resp.text[:500]}"
                )
            return resp
        raise CredoApiError(f"{method} {path} failed after 3 attempts")

    def list_models(self) -> list[dict]:
        items: list[dict] = []
        params: dict[str, Any] = {"page_limit": 100}
        while True:
            data = self._request("GET", "/models", params=params).json()
            items.extend(data["items"])
            pagination = data.get("pagination") or {}
            cursor = pagination.get("next_cursor")
            if not pagination.get("has_more") or not cursor:
                return items
            params["page_after"] = cursor

    def create_model(self, attrs: dict) -> dict:
        return self._request("POST", "/models", json_body=attrs).json()

    def patch_model(self, model_id: str, attrs: dict) -> dict:
        return self._request("PATCH", f"/models/{model_id}", json_body=attrs).json()


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Databricks signs each delivery with HMAC-SHA256 of the raw body, hex
    encoded, keyed by the webhook's shared secret."""
    if not signature_header:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def build_summary(
    version: str | None = None,
    *,
    stage: str | None = None,
    from_stage: str | None = None,
) -> str:
    """Compose the Credo AI `summary`.

    Credo AI's Model has no version or stage field of its own, so everything
    Databricks tells us that's worth keeping has to live in this one string.
    """
    text = (
        f"Databricks MLflow model, version {version}."
        if version
        else "Databricks MLflow model."
    )
    if stage:
        if from_stage:
            text += f" Transitioned {from_stage} -> {stage}."
        else:
            text += f" Current stage: {stage}."
    return text


def upsert_model(credo: CredoClient, model_name: str, summary: str) -> tuple[str, str]:
    """Create the Credo AI Model, or update it if the name is already known.

    Returns (action, model_id). Credo AI Models have no version concept, so a
    second event for a name that already exists has to patch rather than
    create a duplicate.
    """
    existing_id = MODEL_IDS.get(model_name)
    if existing_id:
        credo.patch_model(existing_id, {"summary": summary})
        return "updated", existing_id

    created = credo.create_model(
        {"name": model_name, "summary": summary, "source": SOURCE}
    )
    model_id = created["id"]
    MODEL_IDS[model_name] = model_id
    return "created", model_id


def handle_event(payload: dict, credo: CredoClient) -> dict:
    """Upsert the Credo AI Model named by this event.

    Every outcome here is a 200 — an event we don't handle, or one without a
    model name, is not something Databricks should retry. Only a Credo AI API
    failure propagates, so a 500 always means "worth retrying".
    """
    event = payload.get("event", "")
    if event not in HANDLED_EVENTS:
        return {"action": "ignored_event_type", "event": event}

    model_name = payload.get("model_name") or ""
    if not model_name:
        log.warning("event %s has no model_name — ignoring", event)
        return {"action": "ignored_missing_model_name"}

    version = payload.get("version")
    summary = build_summary(
        str(version) if version else None,
        stage=payload.get("to_stage"),
        from_stage=payload.get("from_stage"),
    )
    action, model_id = upsert_model(credo, model_name, summary)
    log.info("%s Credo AI model %s (%s) from %s", action, model_id, model_name, event)
    return {
        "action": action,
        "model_id": model_id,
        "model_name": model_name,
        "version": version,
    }


def backfill(credo: CredoClient, workspace: WorkspaceClient) -> None:
    """One-time true-up: upsert every model already in the Databricks registry.

    Runs at every startup. Idempotent — anything already in Credo AI is
    patched, not duplicated — so restarting after downtime closes whatever
    gap the webhook missed. A single unreadable model is logged and skipped
    rather than aborting the run and leaving the rest unsynced.
    """
    registry = created = updated = failed = 0
    for model in workspace.model_registry.list_models():
        registry += 1
        model_name = model.name
        if not model_name:
            failed += 1
            continue
        try:
            # `latest_versions` holds one entry per stage; the highest version
            # number is the most recent registration.
            latest = max(
                (v for v in (model.latest_versions or []) if v.version),
                key=lambda v: int(v.version),
                default=None,
            )
            stage = latest.current_stage if latest else None
            summary = build_summary(latest.version if latest else None, stage=stage)
            action, _ = upsert_model(credo, model_name, summary)
            if action == "created":
                created += 1
            else:
                updated += 1
        except (CredoApiError, ValueError) as exc:
            failed += 1
            log.warning("backfill: skipping %s: %s", model_name, exc)

    log.info(
        "backfill: registry=%d created=%d updated=%d failed=%d",
        registry,
        created,
        updated,
        failed,
    )


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Clients are built here rather than at import time so the module can
        # be imported (and the pure functions tested) without credentials.
        with httpx.Client() as credo_http:
            credo = CredoClient(credo_http)
            app.state.credo = credo
            # WorkspaceClient() resolves DATABRICKS_HOST / DATABRICKS_TOKEN
            # itself via the SDK's unified auth.
            workspace = WorkspaceClient()

            # Learn what Credo AI already has before touching anything, so
            # both the backfill and the webhook handler patch existing models
            # instead of creating a second one with the same name.
            for model in credo.list_models():
                MODEL_IDS.setdefault(model["name"], model["id"])
            log.info("seeded %d existing Credo AI model(s)", len(MODEL_IDS))

            backfill(credo, workspace)
            yield

    app = FastAPI(title="databricks-mlflow-model-registry", lifespan=lifespan)

    @app.post(WEBHOOK_PATH)
    async def databricks_webhook(request: Request) -> dict:
        body = await request.body()
        if DATABRICKS_WEBHOOK_SECRET and not verify_signature(
            DATABRICKS_WEBHOOK_SECRET,
            body,
            request.headers.get("X-Databricks-Signature"),
        ):
            raise HTTPException(
                status_code=403, detail="Bad or missing X-Databricks-Signature"
            )
        payload = json.loads(body) if body else {}
        try:
            result = handle_event(payload, request.app.state.credo)
        except CredoApiError as exc:
            # 500 so Databricks can retry — the model genuinely didn't sync.
            log.error("failed to upsert Credo AI model: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"ok": True, **result}

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "tracked_models": len(MODEL_IDS)}

    return app


app = create_app()


def main() -> None:
    log.info("starting: tenant=%s port=%d", CREDO_TENANT, PORT)
    # Localhost only: put a tunnel or ingress in front of this rather than
    # exposing it directly. In a container, set HOST=0.0.0.0.
    uvicorn.run(app, host=HOST, port=PORT, log_config=None)


if __name__ == "__main__":
    main()
