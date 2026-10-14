"""The web server: accept a Credo AI webhook delivery, store it, answer fast.

⚠️ SIGNATURE VERIFICATION IS OFF. Credo AI doesn't currently give customers the
signing secret for their webhooks, so this server cannot prove a request came
from Credo AI. Anyone who learns the URL can send it fake events. The secret
path token, the domain allow-list, the SDK re-read before every email and the
one-email-per-model rule limit what a fake event can do; see the README.

Request flow for `POST /webhook/<token>`:

1. Wrong token → 404, the same answer as for any other unknown path.
2. Not an event this server handles → 204, nothing stored.
3. Store the delivery in the outbox (deduplicated on `svix-id`) → 202.
4. The worker thread does the rest, after the response.
"""

from __future__ import annotations

import json
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from . import events
from .config import Settings, get_settings
from .credo import Credo, CredoUnavailable
from .handlers import Context, model_added_key
from .mailer import Mailer
from .store import Store
from .worker import Worker

log = logging.getLogger("notifier.app")

MAX_BODY_BYTES = 256 * 1024
_SEEDED = "seeded_existing_models"


def create_app(settings: Settings | None = None, credo: Credo | None = None) -> FastAPI:
    settings = settings or get_settings()
    token = settings.webhook_path_token
    _redact_token_in_access_logs(token)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store = Store(settings.database_path)
        ctx = Context(
            settings=settings,
            store=store,
            credo=credo or Credo(settings),
            mailer=Mailer(settings),
        )
        if settings.seed_existing_models:
            try:
                _seed_existing_models(ctx)
            except CredoUnavailable:
                log.error(
                    "First start needs the list of existing models from Credo AI and couldn't get it. "
                    "Check CREDO_API_KEY, CREDO_TENANT and CREDO_API_BASE_URL. Not starting."
                )
                store.close()
                raise
        worker = Worker(ctx)
        worker.start()
        app.state.store = store
        app.state.worker = worker
        log.warning(
            "Webhook signature verification is OFF: anyone who knows the webhook URL can send "
            "events. Keep WEBHOOK_PATH_TOKEN secret."
        )
        try:
            yield
        finally:
            worker.stop()
            store.close()

    # No /docs, /redoc or /openapi.json: they would advertise the webhook route.
    app = FastAPI(
        title="Credo AI governance notifications",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

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
        try:
            payload = json.loads(body)
        except ValueError:
            return Response(status_code=400)
        event = events.parse(payload)
        if event is None:
            return Response(status_code=400)

        # Only model creations are handled today; acknowledge everything else so
        # a broader subscription doesn't make Svix retry.
        if not events.is_model_created(event):
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


def _seed_existing_models(ctx: Context) -> None:
    """On the first start only, mark every existing model as already emailed.

    Without this, someone holding the URL could send one email per model that
    existed before the server was installed. Later starts don't re-seed, so a
    model created while the server was down still gets its email when Svix
    redelivers the event. Models referenced by deliveries still waiting in the
    outbox are left alone so a crash doesn't swallow their emails.
    """
    if ctx.store.get_meta(_SEEDED):
        return
    waiting = {
        (p.get("data") or {}).get("modelId")
        for p in ctx.store.pending_payloads()
        if isinstance(p, dict)
    }
    keys = [model_added_key(mid) for mid in ctx.credo.model_ids() if mid not in waiting]
    count = ctx.store.seed(keys)
    ctx.store.set_meta(_SEEDED, "1")
    log.info("first start: marked %d existing models as already emailed", count)


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
