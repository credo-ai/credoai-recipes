"""The web server: accept a Credo AI webhook delivery, check it, store it, answer fast.

Request flow for `POST /webhook/<token>`:

1. Wrong token → 404, the same answer as for any other unknown path.
2. Bad, missing or stale (over 5 minutes) Svix signature → 401, nothing stored.
3. Not an event this server handles → 204, nothing stored.
4. Store the delivery in the outbox (deduplicated on `svix-id`) → 202.
5. The worker thread does the rest, after the response.

The signature is the real check. The long random token in the URL, the SDK
re-read before every email and the per-email idempotency keys are further
safeguards; see the README.
"""

from __future__ import annotations

import json
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from svix.webhooks import Webhook, WebhookVerificationError

from . import events, router
from .config import Settings, get_settings
from .credo import Credo
from .handlers import Context
from .mailer import Mailer
from .reminders.scheduler import Scheduler
from .stages import StageMessages
from .store import Store
from .worker import Worker

log = logging.getLogger("notifier.app")

MAX_BODY_BYTES = 256 * 1024


def build_context(settings: Settings, credo: Credo | None = None) -> Context:
    return Context(
        settings=settings,
        store=Store(settings.database_path),
        credo=credo or Credo(settings),
        mailer=Mailer(settings),
        stages=StageMessages(settings.stage_messages_path),
    )


def create_app(settings: Settings | None = None, credo: Credo | None = None) -> FastAPI:
    settings = settings or get_settings()
    token = settings.webhook_path_token
    verifier = Webhook(settings.webhook_signing_secret)
    _redact_token_in_access_logs(token)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        ctx = build_context(settings, credo)
        worker = Worker(ctx)
        worker.start()
        scheduler = None
        if settings.reminders_in_process:
            scheduler = Scheduler(settings, ctx.store, worker.notify)
            scheduler.start()
        app.state.store = ctx.store
        app.state.worker = worker
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.stop()
            worker.stop()
            ctx.store.close()

    # No /docs, /redoc or /openapi.json: they would advertise the webhook route.
    app = FastAPI(
        title="Credo AI governance notifications",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    # Also reports the worker, so a server that accepts deliveries but can't
    # send them shows as unhealthy.
    @app.get("/healthz")
    async def healthz(request: Request) -> JSONResponse:
        if not request.app.state.worker.healthy:
            return JSONResponse({"status": "worker unhealthy"}, status_code=503)
        return JSONResponse({"status": "ok"})

    @app.post("/webhook/{path_token}")
    async def webhook(path_token: str, request: Request) -> Response:
        if not secrets.compare_digest(path_token.encode(), token.encode()):
            return _not_found()

        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            return Response(status_code=413)
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            return Response(status_code=413)
        # Checked on the raw bytes, before anything is parsed or stored.
        try:
            verifier.verify(body, dict(request.headers))
        except WebhookVerificationError:
            log.warning(
                "rejected a delivery with a bad or missing signature; if every "
                "delivery is rejected, WEBHOOK_SIGNING_SECRET is wrong or was rotated"
            )
            return Response(status_code=401)
        try:
            payload = json.loads(body)
        except ValueError:
            return Response(status_code=400)
        event = events.parse(payload)
        if event is None:
            return Response(status_code=400)

        # Acknowledge anything this server doesn't handle, so a broader
        # subscription doesn't make Svix retry.
        if not router.accepts(event.subject):
            return Response(status_code=204)

        svix_id = request.headers.get("svix-id")
        if not svix_id:
            return Response(status_code=400)

        if not request.app.state.store.enqueue(svix_id, event.subject, payload):
            return Response(status_code=204)  # a redelivery of something already stored
        request.app.state.worker.notify()
        return Response(status_code=202)

    # Any other method on the webhook path answers 404 too, rather than 405,
    # which would confirm the route exists.
    @app.api_route(
        "/webhook/{path_token}", methods=["GET", "PUT", "PATCH", "DELETE", "OPTIONS"]
    )
    async def webhook_other_methods() -> Response:
        return _not_found()

    return app


def _not_found() -> Response:
    # Byte for byte what FastAPI answers for a path that doesn't exist.
    return JSONResponse({"detail": "Not Found"}, status_code=404)


class _RedactToken(logging.Filter):
    def __init__(self, token: str) -> None:
        super().__init__()
        self._token = token

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                a.replace(self._token, "<token>") if isinstance(a, str) else a
                for a in record.args
            )
        return True


def _redact_token_in_access_logs(token: str) -> None:
    # `main.py` turns uvicorn's access log off. If someone turns it back on, the
    # token is still not written to the log.
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _RedactToken) for f in access.filters):
        access.addFilter(_RedactToken(token))
