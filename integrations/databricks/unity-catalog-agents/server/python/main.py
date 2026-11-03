"""
Databricks agents -> Credo AI sync. Two kinds of Databricks agent are synced:

1. Deployed agents. Unity Catalog has no "agent" object: an agent is a registered
   model that was deployed with the Databricks Agent Framework (`agents.deploy`).
   The deployment list is therefore what separates agents from the plain models
   that `unity-catalog-registry` syncs, so this polls that list and upserts one
   Credo AI use case per deployed agent (public Integration API). Legacy
   Workspace Model Registry locations are not read.

2. Genie agents (the Databricks API still calls them "Genie spaces"). Each one is
   upserted as a Credo AI Agent entity. The public Integration API has no agent
   resource yet, so these go through the private v2 API instead.

Unity Catalog has no webhooks, so this polls on an interval. Neither list carries
a change timestamp, so every cycle re-reads every agent and only writes to
Credo AI when the rendered description differs.

Run: python main.py
"""

from __future__ import annotations

import logging
import os
import time
from collections import Counter
from collections.abc import Callable
from typing import Any

import httpx
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from dotenv import load_dotenv

load_dotenv()  # reads ../../.env (or cwd .env) into os.environ -- must run
# before the required os.environ[...] reads below.

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [databricks-uc-agent-poller] %(message)s",
)
log = logging.getLogger("databricks-uc-agent-poller")

# ---- Credo AI ---------------------------------------------------------
CREDO_BASE_URL = os.environ["CREDO_BASE_URL"].rstrip("/")
CREDO_API_KEY = os.environ["CREDO_API_KEY"]
CREDO_TENANT = os.environ["CREDO_TENANT"]

# Private v2 API host, used only for Genie agents (Credo AI Agent entities have no
# public Integration API route yet). Optional so a deployed-agents-only setup
# keeps working: without it, Genie agents are skipped with a warning.
CREDO_BACKEND_BASE_URL = os.environ.get("CREDO_BACKEND_BASE_URL", "").rstrip("/")

# Not free text: `source` resolves against a Source record that must already
# exist in the tenant, so every use case create 422s until
# setup_credo_source.py has been run once. Same "Databricks" source the other
# Databricks cookbooks use -- one source covers all of them. See README.md Step 2.
SOURCE = "Databricks"

# ---- Databricks -------------------------------------------------------
# DATABRICKS_HOST / DATABRICKS_TOKEN are read by the SDK's unified auth, not
# here -- this poller never touches them directly.

# Optional narrowing. The deployment list is workspace-wide, so unlike the
# model sync this is a client-side prefix filter and a catalog alone is valid.
DATABRICKS_CATALOG = os.environ.get("DATABRICKS_CATALOG", "").strip()
DATABRICKS_SCHEMA = os.environ.get("DATABRICKS_SCHEMA", "").strip()
if DATABRICKS_SCHEMA and not DATABRICKS_CATALOG:
    raise SystemExit("DATABRICKS_SCHEMA requires DATABRICKS_CATALOG to be set too.")

# ---- Tuning -----------------------------------------------------------
# Deliberately long: every cycle makes two Unity Catalog calls per deployed
# agent, and UC rate-limits per account (REQUEST_LIMIT_EXCEEDED).
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "300"))

# Pause between the per-agent detail calls. Small, but it is what keeps a large
# number of agents from tripping the UC rate limit.
REQUEST_DELAY_SECONDS = float(os.environ.get("REQUEST_DELAY_SECONDS", "0.1"))

# Credo AI use case name -> {"id", "description"}. Seeded from Credo AI at
# startup (see `main`), which is what keeps a restart from creating duplicates:
# the Credo AI side is the source of truth for what already exists, so no local
# state file has to survive between runs.
USE_CASES: dict[str, dict[str, Any]] = {}

# Same idea for Genie agents, which live in Credo AI as Agent entities. Entity
# names are unique per entity type, so this cache is independent of USE_CASES.
AGENTS: dict[str, dict[str, Any]] = {}


class CredoApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class CredoClient:
    """Credo AI public Integration API.

    Only three calls are needed: list every use case once at startup, then
    create or patch one at a time.
    """

    # Overridden by CredoAgentClient, which talks to a different API on the same
    # retry/refresh machinery.
    _base_url = CREDO_BASE_URL

    def __init__(self, client: httpx.Client) -> None:
        self._client = client
        self._token: str | None = None

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "X-Tenant": CREDO_TENANT}

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
                    f"{self._base_url}{path}",
                    json=json_body,
                    params=params,
                    headers=self._auth_headers(),
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

    def list_use_cases(self) -> list[dict]:
        items: list[dict] = []
        params: dict[str, Any] = {"page_limit": 100}
        while True:
            data = self._request("GET", "/use_cases", params=params).json()
            items.extend(data["items"])
            pagination = data.get("pagination") or {}
            cursor = pagination.get("next_cursor")
            if not pagination.get("has_more") or not cursor:
                return items
            params["page_after"] = cursor

    def create_use_case(self, attrs: dict) -> dict:
        return self._request("POST", "/use_cases", json_body=attrs).json()

    def patch_use_case(self, use_case_id: str, attrs: dict) -> dict:
        return self._request(
            "PATCH", f"/use_cases/{use_case_id}", json_body=attrs
        ).json()


class CredoAgentClient(CredoClient):
    """Credo AI private v2 API (JSON:API), used for Agent entities only.

    The public Integration API has no agent resource yet, so Genie agents are
    written here instead. Auth is the `/auth/exchange` flow that
    setup_credo_source.py uses, not the Integration API's `/auth/token`. Drop this
    class and use CredoClient once the public API supports agents.
    """

    _base_url = f"{CREDO_BACKEND_BASE_URL}/api/v2/{CREDO_TENANT}"
    _JSONAPI = "application/vnd.api+json"

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": self._JSONAPI,
            "Content-Type": self._JSONAPI,
        }

    def _get_token(self) -> str:
        try:
            resp = self._client.post(
                f"{CREDO_BACKEND_BASE_URL}/auth/exchange",
                json={"api_token": CREDO_API_KEY, "tenant": CREDO_TENANT},
                timeout=30,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise CredoApiError(f"token exchange failed: {exc}") from exc
        return resp.json()["access_token"]

    def _list(self, path: str, filters: dict[str, str] | None = None) -> list[dict]:
        items: list[dict] = []
        params: dict[str, Any] = {"page[limit]": 1000, **(filters or {})}
        while True:
            body = self._request("GET", path, params=params).json()
            items.extend(body["data"])
            after = (body.get("meta") or {}).get("after")
            if not after:
                return items
            params["page[after]"] = after

    def agent_type_id(self) -> str:
        # Entity type ids are per tenant, so resolve "agent" by name.
        for entity_type in self._list("/entity_types"):
            if entity_type["attributes"]["name"] == "agent":
                return entity_type["id"]
        raise CredoApiError("this tenant has no 'agent' entity type")

    def list_agents(self) -> list[dict]:
        return self._list("/entities", {"filter[type]": "agent"})

    def create_agent(self, type_id: str, name: str, description: str) -> dict:
        body = {
            "data": {
                "type": "entities",
                "attributes": {
                    "entity_type_id": type_id,
                    "name": name,
                    "description": description,
                },
            }
        }
        return self._request("POST", "/entities", json_body=body).json()["data"]

    def patch_agent(self, agent_id: str, description: str) -> dict:
        body = {
            "data": {
                "type": "entities",
                "id": agent_id,
                "attributes": {"description": description},
            }
        }
        return self._request("PATCH", f"/entities/{agent_id}", json_body=body).json()[
            "data"
        ]


def in_scope(full_name: str) -> bool:
    """True if `catalog.schema.model` falls inside the configured scope."""
    parts = full_name.split(".")
    if DATABRICKS_CATALOG and parts[0] != DATABRICKS_CATALOG:
        return False
    if DATABRICKS_SCHEMA and (len(parts) < 3 or parts[1] != DATABRICKS_SCHEMA):
        return False
    return True


def list_deployed_agents(workspace: WorkspaceClient) -> dict[str, list[dict]]:
    """Deployed agents in scope, keyed by Unity Catalog model name.

    This is the REST call behind `databricks.agents.list_deployments()`. It is
    made directly so the cookbook does not take on the databricks-agents
    package and its dependencies (mlflow-skinny, pandas, numpy, boto3, ...).
    The route is not paginated. A model can have several deployments (one per
    endpoint or version), so they are grouped and ordered for a stable
    description.
    """
    response = workspace.api_client.do("GET", "/api/2.0/agents/deployments")
    grouped: dict[str, list[dict]] = {}
    for deployment in response.get("chain_deployments") or []:
        name = deployment.get("model_name")
        if name and in_scope(name):
            grouped.setdefault(name, []).append(deployment)
    for deployments in grouped.values():
        deployments.sort(
            key=lambda d: (
                d.get("endpoint_name") or "",
                int(d["model_version"]) if str(d.get("model_version")).isdigit() else 0,
            )
        )
    return grouped


def build_description(
    full_name: str,
    comment: str | None,
    owner: str | None,
    deployments: list[dict],
    latest_version: int | None,
    aliases: list | None,
    host: str,
) -> str:
    """Compose the Credo AI use case `description`.

    Everything worth keeping has to live in this one string. It is also what
    the next cycle compares against, so it must be deterministic: nothing
    time-dependent goes in.
    """
    lines = ["Databricks Unity Catalog agent.", f"Model: {full_name}"]
    if comment:
        lines.append(f"Description: {comment}")
    if owner:
        lines.append(f"Owner: {owner}")
    for d in deployments:
        endpoint = d.get("endpoint_name")
        lines.append(
            f"Deployment: endpoint {endpoint}, version {d.get('model_version')} "
            f"({host}/ml/endpoints/{endpoint})"
        )
    if latest_version is not None:
        lines.append(f"Latest registered version: {latest_version}")
    named = [a for a in (aliases or []) if getattr(a, "alias_name", None)]
    if named:
        pairs = ", ".join(f"{a.alias_name}={a.version_num}" for a in named)
        lines.append(f"Aliases: {pairs}")
    return "\n".join(lines)


def upsert(
    cache: dict[str, dict[str, Any]],
    name: str,
    description: str,
    create: Callable[[str, str], str],
    patch: Callable[[str, str], Any],
) -> tuple[str, str]:
    """Create the Credo AI record, or update it if the name is already known.

    `create(name, description)` returns the new id; `patch(id, description)`
    updates it. Returns (action, id). Names are unique, so a later sync of a name
    that already exists has to patch rather than create a duplicate. An unchanged
    description makes no call at all.
    """
    existing = cache.get(name)
    if existing:
        if (existing["description"] or "").strip() == description.strip():
            return "unchanged", existing["id"]
        try:
            patch(existing["id"], description)
            existing["description"] = description
            return "updated", existing["id"]
        except CredoApiError as exc:
            if exc.status_code != 404:
                raise
            # Deleted in Credo AI after we cached its id. Forget it and fall
            # through to create, otherwise every later sync 404s forever.
            log.warning(
                "Credo AI record %s (%s) is gone; recreating", name, existing["id"]
            )
            cache.pop(name, None)

    new_id = create(name, description)
    cache[name] = {"id": new_id, "description": description}
    return "created", new_id


def upsert_use_case(credo: CredoClient, name: str, description: str) -> tuple[str, str]:
    """Create or update the Credo AI use case for a deployed agent."""
    return upsert(
        USE_CASES,
        name,
        description,
        lambda n, d: credo.create_use_case(
            {"name": n, "description": d, "source": SOURCE}
        )["id"],
        lambda i, d: credo.patch_use_case(i, {"description": d}),
    )


def list_genie_spaces(workspace: WorkspaceClient) -> list[Any]:
    """Every Genie agent the token can see, across all pages.

    Spaces have no catalog or schema, so DATABRICKS_CATALOG / DATABRICKS_SCHEMA do
    not apply. The list response omits `parent_path`; `sync_genie_space` fetches it.
    """
    spaces: list[Any] = []
    token: str | None = None
    while True:
        page = workspace.genie.list_spaces(page_size=100, page_token=token)
        spaces.extend(page.spaces or [])
        token = page.next_page_token
        if not token:
            return spaces


def build_genie_description(space: Any, host: str) -> str:
    """Compose the Credo AI Agent `description` for a Genie agent.

    Same rule as `build_description`: deterministic, since the next cycle compares
    it with what Credo AI holds. Agent entities have no `source` attribute, so
    provenance lives here.
    """
    lines = ["Databricks Genie agent.", f"Space ID: {space.space_id}"]
    if space.description:
        lines.append(f"Description: {space.description}")
    if space.parent_path:
        lines.append(f"Location: {space.parent_path}")
    if space.warehouse_id:
        lines.append(f"SQL warehouse: {space.warehouse_id}")
    if space.update_time:
        # The SDK returns a protobuf Timestamp, not a string.
        lines.append(f"Last updated: {space.update_time.ToJsonString()}")
    lines.append(f"Link: {host}/genie/rooms/{space.space_id}")
    return "\n".join(lines)


def sync_genie_space(
    credo: CredoAgentClient,
    workspace: WorkspaceClient,
    type_id: str,
    space: Any,
    seen: set[str],
) -> str:
    """Upsert one Genie agent into Credo AI as an Agent. Returns the action taken."""
    # Upsert is keyed by name, so two spaces sharing a title would patch one
    # Credo AI agent back and forth every cycle. Keep the first, skip the rest.
    if space.title in seen:
        log.warning(
            "skipping Genie agent %s: title %r already synced this cycle",
            space.space_id,
            space.title,
        )
        return "skipped"
    seen.add(space.title)

    try:
        # Not include_serialized_space: that needs CAN EDIT, and nothing here uses it.
        detail = workspace.genie.get_space(space.space_id)
    except NotFound:
        log.warning("skipping Genie agent %s: deleted mid-cycle", space.space_id)
        return "skipped"

    description = build_genie_description(
        detail, (workspace.config.host or "").rstrip("/")
    )
    action, agent_id = upsert(
        AGENTS,
        detail.title,
        description,
        lambda n, d: credo.create_agent(type_id, n, d)["id"],
        credo.patch_agent,
    )
    log.info("%s Credo AI agent %s (%s)", action, agent_id, detail.title)
    return action


def sync_agent(
    credo: CredoClient,
    workspace: WorkspaceClient,
    full_name: str,
    deployments: list[dict],
) -> str:
    """Upsert one deployed agent into Credo AI. Returns the action taken.

    Two kinds of UC call, and both are needed: aliases only come back from
    `registered_models.get(include_aliases=True)`, and the latest version number
    has no home on the registered model at all, so it takes the version listing.
    That listing has no guaranteed order, so every page is read; it asks for the
    largest page the server allows to keep that to few calls.
    """
    try:
        detail = workspace.registered_models.get(full_name, include_aliases=True)
        time.sleep(REQUEST_DELAY_SECONDS)
        versions = workspace.model_versions.list(full_name, max_results=1000)
        latest = max(
            (v.version for v in versions if v.version is not None),
            default=None,
        )
    except NotFound:
        # A deployment can outlive its model: renaming or deleting the
        # registered model leaves the serving endpoint pointing at the old name.
        log.warning("skipping %s: deployed, but no longer in Unity Catalog", full_name)
        return "skipped"

    description = build_description(
        full_name,
        detail.comment,
        detail.owner,
        deployments,
        latest,
        detail.aliases,
        (workspace.config.host or "").rstrip("/"),
    )
    action, use_case_id = upsert_use_case(credo, full_name, description)
    log.info("%s Credo AI use case %s (%s)", action, use_case_id, full_name)
    return action


def log_counts(kind: str, scanned: int, counts: Counter[str]) -> None:
    log.info(
        "%s: scanned=%d created=%d updated=%d unchanged=%d skipped=%d failed=%d",
        kind,
        scanned,
        counts["created"],
        counts["updated"],
        counts["unchanged"],
        counts["skipped"],
        counts["failed"],
    )


def run_cycle(credo: CredoClient, workspace: WorkspaceClient) -> None:
    """One poll: list deployed agents and sync each."""
    agents = list_deployed_agents(workspace)
    counts: Counter[str] = Counter()

    for full_name, deployments in agents.items():
        try:
            counts[sync_agent(credo, workspace, full_name, deployments)] += 1
        # Broader than CredoApiError/DatabricksError on purpose: the SDK lets
        # TimeoutError and raw `requests` errors through, and the Credo AI
        # client can raise on a malformed response body. One agent failing
        # any of those must not abort the cycle.
        except Exception as exc:
            counts["failed"] += 1
            log.warning("skipping %s: %r", full_name, exc)
        time.sleep(REQUEST_DELAY_SECONDS)

    log_counts("cycle", len(agents), counts)


def run_genie_cycle(
    credo: CredoAgentClient, workspace: WorkspaceClient, type_id: str
) -> None:
    """One poll of Genie agents. Kept apart from run_cycle so that a failure here
    (Genie not enabled, no permission, v2 API down) never costs the deployed-agent
    sync, and the reverse."""
    spaces = list_genie_spaces(workspace)
    counts: Counter[str] = Counter()
    seen: set[str] = set()

    for space in spaces:
        try:
            counts[sync_genie_space(credo, workspace, type_id, space, seen)] += 1
        except Exception as exc:  # see run_cycle: one space must not abort the rest
            counts["failed"] += 1
            log.warning("skipping Genie agent %s: %r", space.space_id, exc)
        time.sleep(REQUEST_DELAY_SECONDS)

    log_counts("genie cycle", len(spaces), counts)


def setup_genie_sync(http: httpx.Client) -> tuple[CredoAgentClient, str] | None:
    """Connect to the v2 API and seed AGENTS. None (and a warning) if Genie agents
    can't be synced; deployed agents still are."""
    if not CREDO_BACKEND_BASE_URL:
        log.warning(
            "CREDO_BACKEND_BASE_URL is not set; Genie agents will not be synced"
        )
        return None
    credo = CredoAgentClient(http)
    try:
        type_id = credo.agent_type_id()
        for agent in credo.list_agents():
            AGENTS.setdefault(
                agent["attributes"]["name"],
                {
                    "id": agent["id"],
                    "description": agent["attributes"].get("description"),
                },
            )
    except CredoApiError as exc:
        log.warning("Genie agents will not be synced: %s", exc)
        return None
    log.info("seeded %d existing Credo AI agent(s)", len(AGENTS))
    return credo, type_id


def main() -> None:
    log.info(
        "starting poll loop: tenant=%s interval=%ds scope=%s",
        CREDO_TENANT,
        POLL_INTERVAL_SECONDS,
        ".".join(p for p in (DATABRICKS_CATALOG, DATABRICKS_SCHEMA) if p)
        or "workspace-wide",
    )
    # WorkspaceClient() resolves DATABRICKS_HOST / DATABRICKS_TOKEN itself via
    # the SDK's unified auth.
    workspace = WorkspaceClient()

    with httpx.Client() as credo_http:
        credo = CredoClient(credo_http)

        # Learn what Credo AI already has before touching anything, so the
        # first sync patches existing use cases instead of creating a second
        # one with the same name. Matching is by name alone: unlike models, the
        # use case API does not return `source`, so a use case another system
        # created under the same name cannot be told apart and is adopted.
        for use_case in credo.list_use_cases():
            USE_CASES.setdefault(
                use_case["name"],
                {"id": use_case["id"], "description": use_case.get("description")},
            )
        log.info("seeded %d existing Credo AI use case(s)", len(USE_CASES))

        genie = setup_genie_sync(credo_http)

        while True:
            try:
                run_cycle(credo, workspace)
            except Exception:
                # Listing deployments is the one thing run_cycle doesn't guard
                # per agent. Whatever it raises, the poller outlives it and
                # tries again next interval instead of exiting.
                log.exception("cycle failed; retrying in %ds", POLL_INTERVAL_SECONDS)
            if genie:
                agent_client, agent_type_id = genie
                try:
                    run_genie_cycle(agent_client, workspace, agent_type_id)
                except Exception:
                    log.exception("genie cycle failed; retrying next interval")
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
