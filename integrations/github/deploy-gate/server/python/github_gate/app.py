"""FastAPI app: thin routes over `gate.py`, plus the webhook lifecycle.

The Credo AI webhook is registered on startup and deleted on shutdown (lifespan), because the URL
it points at is this service's public URL, which can change between runs (a tunnel, a redeploy). The
webhook id is also persisted to the state file so `teardown` can clean up after a crash.

Dependencies (Credo client, state, status setter) live on `app.state` so tests can build the app
with fakes via `create_app(...)`; lifespan only runs when the TestClient is used as a context
manager, so tests skip the real webhook registration.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from github_gate import gate
from github_gate.config import Settings, get_settings
from github_gate.github import set_commit_status
from github_gate.state import GateState

logger = logging.getLogger("github_gate.app")

WEBHOOK_PATH = "/webhooks/credo"
WEBHOOK_DESCRIPTION = "GitHub deploy gate"


class GateNotify(BaseModel):
    repo: str
    pr_number: int
    pr_title: str
    model_name: str
    head_sha: str
    html_url: str


def create_app(
    settings: Settings | None = None,
    *,
    credo=None,
    state: GateState | None = None,
    set_status=None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not settings.gate_public_base_url:
            raise RuntimeError(
                "GATE_PUBLIC_BASE_URL is required to register the Credo AI webhook "
                "(start a tunnel, e.g. `ngrok http 8321`, and set it in .env)."
            )
        url = settings.gate_public_base_url.rstrip("/") + WEBHOOK_PATH
        webhook = app.state.credo.create_webhook(
            description=WEBHOOK_DESCRIPTION,
            event_types=[gate.EVENT_TYPE],
            url=url,
        )
        app.state.gate_state.set_webhook(webhook["id"])
        logger.info("Registered webhook %s -> %s", webhook["id"], url)
        try:
            yield
        finally:
            try:
                app.state.credo.delete_webhook(webhook["id"])
                app.state.gate_state.clear_webhook()
                logger.info("Deleted webhook %s", webhook["id"])
            except Exception as exc:
                logger.warning(
                    "Failed to delete webhook %s (run `python main.py teardown`): %s",
                    webhook["id"],
                    exc,
                )
            close = getattr(app.state.credo, "close", None)
            if close:
                close()

    app = FastAPI(title="github-gate", lifespan=lifespan)

    if credo is None:
        from github_gate.credo import GateCredoClient

        credo = GateCredoClient(settings)
    app.state.credo = credo
    app.state.gate_state = state if state is not None else GateState.load()
    app.state.set_status = set_status or partial(
        set_commit_status, token=settings.github_token
    )

    @app.post("/gate/notify")
    def notify(payload: GateNotify, request: Request) -> dict:
        secret = settings.gate_shared_secret
        if secret and request.headers.get("X-Gate-Secret") != secret:
            raise HTTPException(status_code=403, detail="Bad or missing X-Gate-Secret")
        return gate.handle_notify(
            payload.model_dump(),
            credo=request.app.state.credo,
            state=request.app.state.gate_state,
        )

    @app.post(WEBHOOK_PATH)
    async def credo_webhook(request: Request) -> dict:
        body = await request.json()
        try:
            result = gate.handle_review_event(
                body,
                state=request.app.state.gate_state,
                set_status=request.app.state.set_status,
            )
        except RuntimeError as exc:
            # GitHub API failure: the one case where a retry of the delivery is wanted.
            logger.error("Failed to update commit status: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"ok": True, **result}

    @app.get("/gate/state")
    def gate_state(request: Request) -> dict:
        return request.app.state.gate_state.snapshot()

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app
