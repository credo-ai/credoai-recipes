"""
Databricks Unity Catalog model registry -> Credo AI model sync.

Unity Catalog has no webhooks -- Databricks does not deliver registry webhook
events for UC models, so there is nothing to subscribe to. This polls instead:
a full sync of every model on start, then an interval poll that only re-syncs
models whose `updated_at` moved, so steady-state cycles stay cheap.

See README.md for the real-time options that do exist (Lakeflow Jobs model
update triggers) and why none of them is a webhook.

Run: python main.py
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.catalog import RegisteredModelInfo
from dotenv import load_dotenv

load_dotenv()  # reads ../../.env (or cwd .env) into os.environ -- must run
# before the required os.environ[...] reads below.

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [databricks-uc-poller] %(message)s",
)
log = logging.getLogger("databricks-uc-poller")

# ---- Credo AI ---------------------------------------------------------
CREDO_BASE_URL = os.environ["CREDO_BASE_URL"].rstrip("/")
CREDO_API_KEY = os.environ["CREDO_API_KEY"]
CREDO_TENANT = os.environ["CREDO_TENANT"]

# Not free text: `source` resolves against a Source record that must already
# exist in the tenant, so every model create 422s until setup_credo_source.py
# has been run once. Same "Databricks" source the legacy Workspace Model
# Registry cookbook uses -- one source covers both. See README.md Step 2.
SOURCE = "Databricks"

# ---- Databricks -------------------------------------------------------
# DATABRICKS_HOST / DATABRICKS_TOKEN are read by the SDK's unified auth, not
# here -- this poller never touches them directly.

# A metastore-wide list returns Databricks' own built-in foundation models,
# and there are a lot of them: they can outnumber a workspace's own models many
# times over. Syncing those would bury a tenant's real models under dozens of
# records nobody governs, so the system catalogs are skipped by default.
EXCLUDED_CATALOGS = {"system", "__databricks_internal"}

# Optional narrowing. The UC API rejects a catalog without a schema
# ("Cannot have an empty schema if the catalog is set"), so these are set
# together or not at all -- there is no way to scope to a whole catalog.
DATABRICKS_CATALOG = os.environ.get("DATABRICKS_CATALOG", "").strip()
DATABRICKS_SCHEMA = os.environ.get("DATABRICKS_SCHEMA", "").strip()
if bool(DATABRICKS_CATALOG) != bool(DATABRICKS_SCHEMA):
    raise SystemExit(
        "DATABRICKS_CATALOG and DATABRICKS_SCHEMA must both be set or both be "
        "blank -- Unity Catalog rejects a catalog without a schema."
    )

# ---- Tuning -----------------------------------------------------------
# Deliberately long: a cycle costs at least two Unity Catalog calls per
# *changed* model, and UC rate-limits per account
# (REQUEST_LIMIT_EXCEEDED / UC_REQUEST_LIMIT_EXCEEDED).
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "300"))

# Every Nth cycle re-syncs everything, ignoring the watermark. Unity Catalog
# does not document what mutates a registered model's `updated_at` -- observed
# behavior is that creating a version bumps the parent, but that is not a
# contract. This bounds how stale a model can get if that ever stops holding.
FULL_SWEEP_EVERY_N_CYCLES = max(
    1, int(os.environ.get("FULL_SWEEP_EVERY_N_CYCLES", "20"))
)

# A model that fails this many syncs in a row stops holding the watermark back.
# Without a cap, one permanently failing model keeps the watermark below every
# other model's `updated_at`, so every cycle re-syncs the whole registry. It is
# still retried on each full sweep, which ignores the watermark.
MAX_FAILED_SYNCS = 3

# Pause between the per-model detail calls. Small, but it is what keeps a
# large metastore from tripping the UC rate limit on a full sweep.
REQUEST_DELAY_SECONDS = float(os.environ.get("REQUEST_DELAY_SECONDS", "0.1"))

# Credo AI model name -> model id. Seeded from Credo AI at startup (see
# `main`), which is what keeps a restart from creating duplicates: the Credo AI
# side is the source of truth for what already exists, so no local state file
# has to survive between runs.
MODEL_IDS: dict[str, str] = {}


@dataclass
class SyncState:
    """What has to survive between cycles of a single run.

    Deliberately in memory. Every start does a full sweep, so a watermark
    persisted to disk would be loaded and then immediately ignored.
    """

    watermark_ms: int = 0
    cycles: int = 0
    failures: dict[str, int] = field(default_factory=dict)


class CredoApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class CredoClient:
    """Credo AI public Integration API.

    Only three calls are needed: list every model once at startup, then
    create or patch one at a time.
    """

    def __init__(self, client: httpx.Client) -> None:
        self._client = client
        self._token: str | None = None

    def _get_token(self) -> str:
        # Wrapped so callers only ever have to catch CredoApiError, including
        # for network failures and a rejected token exchange.
        try:
            resp = self._client.post(
                f"{CREDO_BASE_URL}/auth/token",
                headers={"X-API-Key": CREDO_API_KEY, "X-Tenant": CREDO_TENANT},
                timeout=30,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise CredoApiError(f"token exchange failed: {exc}") from exc
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
            try:
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
            except httpx.RequestError as exc:
                raise CredoApiError(f"{method} {path} failed: {exc}") from exc
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
                    f"{method} {path} failed: {resp.status_code} {resp.text[:500]}",
                    status_code=resp.status_code,
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


def build_summary(version: int | None, aliases: list | None) -> str:
    """Compose the Credo AI `summary`.

    Credo AI's Model has no version field of its own, so everything worth
    keeping has to live in this one string. Unity Catalog replaced the legacy
    registry's stages with aliases, so there is no stage transition to record
    here -- the alias-to-version mapping is the closest equivalent.
    """
    text = (
        f"Databricks Unity Catalog model, version {version}."
        if version
        else "Databricks Unity Catalog model."
    )
    named = [a for a in (aliases or []) if getattr(a, "alias_name", None)]
    if named:
        pairs = ", ".join(f"{a.alias_name}={a.version_num}" for a in named)
        text += f" Aliases: {pairs}."
    return text


def upsert_model(credo: CredoClient, model_name: str, summary: str) -> tuple[str, str]:
    """Create the Credo AI Model, or update it if the name is already known.

    Returns (action, model_id). Credo AI Models have no version concept, so a
    later sync of a name that already exists has to patch rather than create a
    duplicate.
    """
    existing_id = MODEL_IDS.get(model_name)
    if existing_id:
        try:
            credo.patch_model(existing_id, {"summary": summary})
            return "updated", existing_id
        except CredoApiError as exc:
            if exc.status_code != 404:
                raise
            # Deleted in Credo AI after we cached its id. Forget it and fall
            # through to create, otherwise every later sync 404s forever.
            log.warning(
                "Credo AI model %s (%s) is gone; recreating", model_name, existing_id
            )
            MODEL_IDS.pop(model_name, None)

    created = credo.create_model(
        {"name": model_name, "summary": summary, "source": SOURCE}
    )
    model_id = created["id"]
    MODEL_IDS[model_name] = model_id
    return "created", model_id


def list_models_scoped(workspace: WorkspaceClient) -> Iterator[RegisteredModelInfo]:
    """Every registered model this poller is configured to care about.

    The SDK's `list` returns a lazy auto-paginating iterator, which matters:
    the UC API can hand back a page with zero results and still supply a
    next_page_token, so hand-rolled paging that stops on an empty page
    silently truncates the registry.
    """
    if DATABRICKS_CATALOG:
        models = workspace.registered_models.list(
            catalog_name=DATABRICKS_CATALOG, schema_name=DATABRICKS_SCHEMA
        )
    else:
        models = workspace.registered_models.list()

    for model in models:
        if not model.full_name:
            continue
        # Only applies to the metastore-wide listing -- an explicitly
        # configured catalog is synced even if someone points it at `system`.
        if not DATABRICKS_CATALOG and model.catalog_name in EXCLUDED_CATALOGS:
            continue
        yield model


def sync_model(
    credo: CredoClient, workspace: WorkspaceClient, model: RegisteredModelInfo
) -> str:
    """Upsert one Unity Catalog model into Credo AI. Returns the action taken.

    Two kinds of UC call, and both are needed: `registered_models.list` omits
    aliases, and `model_versions.list` documents that its results "will not
    contain any aliases or tags" -- only
    `registered_models.get(include_aliases=True)` has them. The latest version
    number has no home on the registered model at all, so it takes the version
    listing. That listing has no guaranteed order, so every page is read; it
    asks for the largest page the server allows to keep that to few calls.
    """
    full_name = model.full_name or ""
    detail = workspace.registered_models.get(full_name, include_aliases=True)
    time.sleep(REQUEST_DELAY_SECONDS)
    versions = workspace.model_versions.list(full_name, max_results=1000)
    latest = max(
        (v.version for v in versions if v.version is not None),
        default=None,
    )
    summary = build_summary(latest, detail.aliases)
    action, model_id = upsert_model(credo, full_name, summary)
    log.info("%s Credo AI model %s (%s)", action, model_id, full_name)
    return action


def run_cycle(credo: CredoClient, workspace: WorkspaceClient, state: SyncState) -> None:
    """One poll: scan the registry, sync what changed, advance the watermark."""
    full_sweep = state.cycles % FULL_SWEEP_EVERY_N_CYCLES == 0
    state.cycles += 1

    scanned = changed = created = updated = failed = 0
    highest_synced = state.watermark_ms
    lowest_failed: int | None = None

    for model in list_models_scoped(workspace):
        scanned += 1
        updated_at = model.updated_at or 0
        # The whole point of the watermark: skip the two per-model detail
        # calls for anything that has not moved since the last cycle.
        # A model that failed before is retried even when its `updated_at` is
        # at or below the watermark -- which is where it sits after a failure
        # on a full sweep, since the sweep ignores the watermark.
        name = model.full_name or ""
        retrying = 0 < state.failures.get(name, 0) <= MAX_FAILED_SYNCS
        if not full_sweep and updated_at <= state.watermark_ms and not retrying:
            continue
        changed += 1
        try:
            action = sync_model(credo, workspace, model)
        # Broader than CredoApiError/DatabricksError on purpose: the SDK lets
        # TimeoutError and raw `requests` errors through, and the Credo AI
        # client can raise on a malformed response body. One model failing
        # any of those must not abort the cycle.
        except Exception as exc:
            failed += 1
            state.failures[name] = state.failures.get(name, 0) + 1
            if state.failures[name] <= MAX_FAILED_SYNCS:
                lowest_failed = (
                    updated_at
                    if lowest_failed is None
                    else min(lowest_failed, updated_at)
                )
                log.warning("skipping %s: %r", name, exc)
            else:
                log.warning(
                    "skipping %s: %r (failed %d times; retrying on full sweeps only)",
                    name,
                    exc,
                    state.failures[name],
                )
            continue
        state.failures.pop(model.full_name or "", None)
        if action == "created":
            created += 1
        else:
            updated += 1
        highest_synced = max(highest_synced, updated_at)
        time.sleep(REQUEST_DELAY_SECONDS)

    # Never advance past work that did not land. Anything at or after the
    # earliest failure is left below the watermark so the next cycle retries
    # it, rather than skipping it forever. Models over MAX_FAILED_SYNCS are
    # excluded from that, so they can't pin the watermark indefinitely. A
    # failure at or below the watermark can't be held back this way; the
    # `retrying` check above is what picks those up.
    if lowest_failed is not None:
        highest_synced = min(highest_synced, lowest_failed - 1)
    state.watermark_ms = max(state.watermark_ms, highest_synced)

    log.info(
        "cycle: scanned=%d changed=%d created=%d updated=%d failed=%d full_sweep=%s",
        scanned,
        changed,
        created,
        updated,
        failed,
        full_sweep,
    )


def main() -> None:
    log.info(
        "starting poll loop: tenant=%s interval=%ds scope=%s",
        CREDO_TENANT,
        POLL_INTERVAL_SECONDS,
        f"{DATABRICKS_CATALOG}.{DATABRICKS_SCHEMA}"
        if DATABRICKS_CATALOG
        else f"metastore-wide, excluding {sorted(EXCLUDED_CATALOGS)}",
    )
    # WorkspaceClient() resolves DATABRICKS_HOST / DATABRICKS_TOKEN itself via
    # the SDK's unified auth.
    workspace = WorkspaceClient()
    state = SyncState()

    with httpx.Client() as credo_http:
        credo = CredoClient(credo_http)

        # Learn what Credo AI already has before touching anything, so the
        # first sync patches existing models instead of creating a second one
        # with the same name.
        # A model another source already owns under the same name is left
        # alone, or its summary would be overwritten. Only a source that is
        # visibly a different string is skipped: if the list response shapes
        # `source` some other way, this falls back to name-only.
        foreign = 0
        for model in credo.list_models():
            source = model.get("source")
            if isinstance(source, str) and source != SOURCE:
                foreign += 1
                continue
            MODEL_IDS.setdefault(model["name"], model["id"])
        log.info(
            "seeded %d existing Credo AI model(s); ignored %d from other sources",
            len(MODEL_IDS),
            foreign,
        )

        # state.cycles == 0, so the first cycle is a full sweep: every model
        # in the registry syncs on start, not just whatever changed while this
        # process happened to be running.
        while True:
            try:
                run_cycle(credo, workspace, state)
            except Exception:
                # Listing the registry is the one thing run_cycle doesn't
                # guard per model. Whatever it raises, the poller outlives it
                # and tries again next interval instead of exiting.
                log.exception("cycle failed; retrying in %ds", POLL_INTERVAL_SECONDS)
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
