"""Read the Bedrock AgentCore inventory via boto3 and flatten it to
``AgentCoreResource`` — the agents, as opposed to the models in ``bedrock.py``.

One ``bedrock-agentcore-control`` client. That is a *different* service from
the ``bedrock`` control plane the model sync uses (endpoint prefix
``bedrock-agentcore-control``, IAM signing name ``bedrock-agentcore``), so it
needs its own client and its own IAM grants.

**One agent is one harness is one Use Case.** ``list_harnesses`` gives the
declarative agents — a model, a system prompt, tools, skills and a memory
configuration — and those map to Credo AI **Use Cases**, not Models: an agent
is an application of AI, which is what a Use Case governs, whereas the Model
registry holds the models it calls.

**Runtimes are deliberately out of scope.** A runtime is the container
AgentCore hosts, and it holds nothing a reviewer assesses — no model, no
prompt, no tools, just a status and an ARN. Giving it its own Use Case doubled
the governance surface of every agent (two policy packs, two questionnaires,
two approvals) for a record with nothing in it, and the relationship runs *many
harnesses to one runtime*, so several agents would have pointed at the same
shared record. ``GetHarness`` still reports which runtime backs a harness, and
that name goes in the agent's description.

Shapes and pagination below were read out of botocore's own service model
(``botocore/data/bedrock-agentcore-control/2023-06-05/service-2.json``) rather
than from the docs. AgentCore is a young service, so its shapes move more than
the Bedrock control plane's — the unions below are read by whichever key is
present rather than by enumerating today's.

Per-listing failures are collected rather than raised, for the same reason as in
``bedrock.py``: an account that has never used AgentCore, or an IAM policy
without ``ListHarnesses``, must degrade to a partial sync rather than to no
sync at all.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import boto3

from bedrock_sync.bedrock import AWS_ERRORS, _day

logger = logging.getLogger("bedrock_sync.agentcore")

# ``ListHarnesses`` returns only identifiers, status and timestamps — nothing
# about what the agent actually does. ``GetHarness`` returns the model, system
# prompt, tools, skills and memory configuration, which is the whole point of a
# governance record, so each harness costs one extra call.

# The system prompt is the agent's instruction set and the most
# governance-relevant thing AgentCore holds, but it is unbounded, so the
# description carries it truncated. The Credo AI Use Case ``description`` has no
# length validation, so this ceiling is about keeping a record readable rather
# than about what the API accepts.
#
# Known limitation: a prompt edited only *past* this cut renders identically, so
# the upsert's text comparison sees no change and the edit is not recorded.
# Raising the limit narrows that window; removing truncation altogether would
# close it. See README "Limitations".
SYSTEM_PROMPT_LIMIT = 2500


@dataclass(frozen=True)
class AgentCoreResource:
    """One AgentCore resource, flattened to what a Credo AI Use Case holds.

    The Use Case resource this integration writes has ``name``, ``description``
    and ``source`` and nothing else usable here (no metadata, tags or version
    field), so everything AgentCore knows is rendered into ``description`` as
    text.

    ``name`` is the harness name unchanged. It used to carry an
    ``" (AgentCore harness)"`` suffix, which existed solely to keep it distinct
    from the runtime record sharing its name in a name-unique registry; with
    runtimes out of scope there is nothing to disambiguate against.

    ``model_id`` and ``model_provider`` carry the harness's model as *data*
    rather than only as rendered text, so ``sync.link_use_case_models`` can
    join this Use Case to the Credo AI Model record without re-parsing the
    description. Both are ``None`` for a harness whose model could not be read.
    """

    name: str
    description: str
    model_id: str | None = None
    model_provider: str | None = None


@dataclass
class AgentCoreInventory:
    resources: list[AgentCoreResource]
    errors: list[str]


def _normalised(text: str) -> str:
    return " ".join(text.split())


def _truncated(text: str, limit: int = SYSTEM_PROMPT_LIMIT) -> str:
    text = _normalised(text)
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _model_arm(model: dict | None) -> tuple[str | None, str | None]:
    """Read the set arm of the ``HarnessModelConfiguration`` union.

    It is a tagged union with one key per provider (``bedrockModelConfig``,
    ``openAiModelConfig``, ``geminiModelConfig``, ``liteLlmModelConfig``), and
    a future AgentCore release can add another — so read whichever key is
    present rather than enumerating them.

    Returns ``(provider, modelId)``, or ``(None, None)`` when no arm carries a
    model id.
    """
    if not model:
        return None, None
    for key, config in model.items():
        if isinstance(config, dict) and config.get("modelId"):
            return key.removesuffix("ModelConfig"), config["modelId"]
    return None, None


def _model_ref(model: dict | None) -> str:
    """Render the model union as ``provider modelId`` for the description."""
    provider, model_id = _model_arm(model)
    if not model_id:
        return "unknown"
    return f"{provider} {model_id}"


def _tools(tools: list[dict] | None) -> str:
    # An empty list is information ("this agent has no tools") and must not
    # read as "unknown" — only an absent key is genuinely unknown.
    if tools is None:
        return "unknown"
    if not tools:
        return "none"
    return ", ".join(
        f"{t['name']} ({t.get('type', 'unknown')})"
        if t.get("name")
        else t.get("type", "unknown")
        for t in tools
    )


def _system_prompt(blocks: list[dict] | None) -> str:
    """Flatten ``HarnessSystemPrompt`` — a *list* of content blocks, not a
    string. The obvious reading treats it as text and raises on every real
    harness; the service model is explicit that it is a list."""
    if not blocks:
        return ""
    return " ".join(b["text"] for b in blocks if b.get("text"))


def _runtime_environment(environment: dict | None) -> str:
    """The AgentCore Runtime a harness runs on, when the union says so.

    Worth recording because it is the only trace of deployment in the agent's
    record: runtimes are not synced, so this name is how a reviewer knows which
    container serves this agent.
    """
    runtime = (environment or {}).get("agentCoreRuntimeEnvironment") or {}
    return runtime.get("agentRuntimeName") or "unknown"


def _memory(memory: dict | None) -> str:
    """Which arm of the ``HarnessMemoryConfiguration`` union is set."""
    if not memory:
        return "unknown"
    if "disabled" in memory:
        return "disabled"
    for key in memory:
        return key.removesuffix("MemoryConfiguration").removesuffix("Configuration")
    return "unknown"


class AgentCoreReader:
    """Wraps the ``bedrock-agentcore-control`` client.

    Credentials come from boto3's default chain, exactly as in
    ``BedrockReader`` — this class takes a region and nothing else.
    """

    def __init__(self, region: str = "", client=None):
        self._region = region
        # `client` exists so a caller can supply its own — a pre-configured
        # session, a different endpoint, or a stub in a test. The CLI never
        # passes one: it lets boto3 build the client from the default
        # credential chain, which is the whole point of holding no AWS keys.
        self._client = client or boto3.client(
            "bedrock-agentcore-control", region_name=region or None
        )

    @property
    def region(self) -> str:
        return self._region or self._client.meta.region_name or "unknown"

    # -- listings --------------------------------------------------------------

    def list_harnesses(self) -> tuple[list[AgentCoreResource], list[str]]:
        """Every harness, each enriched with its ``GetHarness`` detail.

        A failed ``GetHarness`` is collected, not raised, and the harness still
        becomes a Use Case built from its listing fields alone: a harness that
        exists but cannot be described in full is still a harness worth
        governing.
        """
        resources: list[AgentCoreResource] = []
        errors: list[str] = []
        for summary in self._paginate("list_harnesses", "harnesses"):
            detail: dict = {}
            try:
                detail = self._client.get_harness(harnessId=summary["harnessId"])[
                    "harness"
                ]
            except AWS_ERRORS as exc:
                logger.warning(
                    "Failed to describe harness %s: %s", summary["harnessName"], exc
                )
                errors.append(f"describe harness {summary['harnessName']!r}: {exc}")
            resources.append(self._harness(summary, detail))
        return resources, errors

    def inventory(self) -> AgentCoreInventory:
        """Every agent in this region, plus per-listing errors.

        Harnesses only. Runtimes are not enumerated, so an account with agents
        needs ``ListHarnesses`` and ``GetHarness`` and nothing more.
        """
        resources: list[AgentCoreResource] = []
        errors: list[str] = []

        try:
            harnesses, harness_errors = self.list_harnesses()
        except AWS_ERRORS as exc:
            logger.warning("Failed to list harnesses: %s", exc)
            errors.append(f"list harnesses: {exc}")
        else:
            logger.info("Found %d harnesses in %s", len(harnesses), self.region)
            resources.extend(harnesses)
            errors.extend(harness_errors)

        return AgentCoreInventory(resources=resources, errors=errors)

    # -- plumbing --------------------------------------------------------------

    def _paginate(self, operation: str, result_key: str) -> list[dict]:
        # Both listings declare a paginator in botocore's paginators-1.json
        # (nextToken / maxResults), unlike list_foundation_models.
        return [
            item
            for page in self._client.get_paginator(operation).paginate()
            for item in page.get(result_key, [])
        ]

    # -- record builders -------------------------------------------------------

    def _harness(self, s: dict, detail: dict) -> AgentCoreResource:
        model_provider, model_id = _model_arm(detail.get("model"))
        parts = [
            "Amazon Bedrock AgentCore harness.",
            f"Region: {self.region}.",
            f"Harness ID: {s['harnessId']}.",
            f"Version: {s.get('harnessVersion') or 'unknown'}.",
            f"Status: {s['status']}.",
            f"Model: {_model_ref(detail.get('model'))}.",
            f"Tools: {_tools(detail.get('tools'))}.",
            f"Skills: {len(detail['skills']) if 'skills' in detail else 'unknown'}.",
            f"Memory: {_memory(detail.get('memory'))}.",
            f"Max iterations: {detail.get('maxIterations', 'unknown')}.",
            f"Runtime environment: {_runtime_environment(detail.get('environment'))}.",
            f"Created: {_day(s.get('createdAt'))}.",
            f"Updated: {_day(s.get('updatedAt'))}.",
            f"ARN: {s['arn']}.",
        ]
        # Unbounded, so it goes last and truncated.
        prompt = _system_prompt(detail.get("systemPrompt"))
        if prompt:
            parts.append(f"System prompt: {_truncated(prompt)}")
        return AgentCoreResource(
            name=s["harnessName"],
            description=" ".join(parts),
            model_id=model_id,
            model_provider=model_provider,
        )
