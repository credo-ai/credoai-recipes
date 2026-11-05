"""Core Bedrock-to-Credo AI orchestration — framework-free, dependencies injected.

Two entry points, each called once per cycle: ``sync_models`` (the Bedrock
model inventory -> the Credo AI model registry) and ``sync_use_cases`` (the
AgentCore harnesses -> Credo AI Use Cases, one per agent). They are the same
upsert over two resource types, so they share ``_sync`` below rather than
carrying two copies of its invariants.

**Stateless.** Bedrock has no webhook or event feed for its model inventory,
so this is a poll: enumerate Bedrock, enumerate Credo AI, diff, write. Because
the cycle already has to read every Credo AI Model to do that diff, Credo AI
itself is the state store — there is no local state file to drift or corrupt,
and nothing to reset if a run is interrupted.

**Matched by name.** Credo AI Models and Use Cases are both name-unique (a
duplicate name comes back 422, verified live), so the name is the match key:
present in Credo AI -> PATCH, absent -> POST. For models that name is the
Bedrock one (``modelId`` for foundation models, ``modelName`` for custom and
imported ones); for use cases it is the harness name (see
``agentcore.AgentCoreResource``).

One failing record does not end the cycle: each upsert is isolated, counted in
the summary, and the caller exits non-zero if anything failed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from bedrock_sync.agentcore import AgentCoreResource
from bedrock_sync.bedrock import BedrockModel, resolve_model_key

logger = logging.getLogger("bedrock_sync.sync")

# `source` isn't free text on either resource — it's validated against a
# pre-existing Source record in the tenant, so an unknown value 422s on every
# create. Run `ensure_credo_source.py` once per tenant to create this one —
# see README "Step 3".
SOURCE = "Amazon Bedrock"


# These four Protocols are the contract this module needs from a Credo AI
# client, and nothing more. They are type declarations only — never
# instantiated, no runtime cost. Declaring them here is what keeps this
# module free of any HTTP dependency: the orchestration below never imports
# `CredoClient`, so it can be read, reasoned about and driven by any object
# that satisfies these signatures.
# The four Protocols below take the SDK's response objects as `Any`: they are
# `pycredoai` models, and naming them here would pull the SDK into this module,
# which is exactly the dependency these Protocols exist to avoid.
class ModelSink(Protocol):
    def list_models(self) -> list[Any]: ...
    def create_model(self, attrs: dict) -> Any: ...
    def patch_model(self, model_id: str, attrs: dict) -> Any: ...


class UseCaseSink(Protocol):
    def list_use_cases(self) -> list[Any]: ...
    def create_use_case(self, attrs: dict) -> Any: ...
    def patch_use_case(self, use_case_id: str, attrs: dict) -> Any: ...


class VendorSink(Protocol):
    def list_vendors(self) -> list[Any]: ...
    def create_vendor(self, name: str) -> Any: ...
    def list_vendor_models(self, vendor_id: str) -> list[Any]: ...
    def add_model_vendor(self, model_id: str, vendor_id: str) -> Any: ...


class UseCaseModelSink(Protocol):
    def list_use_case_models(self, use_case_id: str) -> list[Any]: ...
    def add_use_case_model(self, use_case_id: str, model_id: str) -> Any: ...


@dataclass
class SyncSummary:
    """Cycle counters for the summary log line.

    ``unchanged`` counts records already in Credo AI whose text matches what
    this cycle would write — no API call made. ``errors`` collects one message
    per failure so the cycle can continue and still exit non-zero.

    ``names`` is every name this cycle covered and ``ids`` maps the ones that
    exist in Credo AI to their id. They differ under ``--dry-run``, where a
    record that would be created has a name but no id yet; the link step below
    relies on exactly that distinction.
    """

    scanned: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: list[str] = field(default_factory=list)
    names: set[str] = field(default_factory=set)
    ids: dict[str, str] = field(default_factory=dict)

    def error(self, message: str) -> None:
        self.errors.append(message)

    def format(self) -> str:
        return (
            f"scanned={self.scanned} created={self.created} updated={self.updated} "
            f"unchanged={self.unchanged} errors={len(self.errors)}"
        )


@dataclass
class VendorSummary:
    """Counters for the model-to-vendor pass.

    ``skipped`` counts models with no provider to link — custom and imported
    models report a base model or an architecture instead, so only foundation
    models carry one. That is a fact about the model, not a failure.
    """

    providers: int = 0
    vendors_created: int = 0
    linked: int = 0
    already_linked: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def error(self, message: str) -> None:
        self.errors.append(message)

    def format(self) -> str:
        return (
            f"providers={self.providers} created={self.vendors_created} "
            f"linked={self.linked} already_linked={self.already_linked} "
            f"skipped={self.skipped} errors={len(self.errors)}"
        )


@dataclass
class LinkSummary:
    agents: int = 0
    linked: int = 0
    already_linked: int = 0
    unresolved: int = 0
    non_bedrock: int = 0
    errors: list[str] = field(default_factory=list)

    def error(self, message: str) -> None:
        self.errors.append(message)

    def format(self) -> str:
        return (
            f"agents={self.agents} linked={self.linked} "
            f"already_linked={self.already_linked} unresolved={self.unresolved} "
            f"non_bedrock={self.non_bedrock} errors={len(self.errors)}"
        )


def sync_models(
    models: list[BedrockModel],
    *,
    credo: ModelSink,
    dry_run: bool = False,
) -> SyncSummary:
    """Upsert every Bedrock model into the Credo AI model registry."""
    return _sync(
        [_Record(m.name, m.summary, m.kind) for m in models],
        _Target(
            noun="model",
            field="summary",
            list_items=credo.list_models,
            create=credo.create_model,
            patch=credo.patch_model,
        ),
        dry_run=dry_run,
    )


def sync_use_cases(
    resources: list[AgentCoreResource],
    *,
    credo: UseCaseSink,
    dry_run: bool = False,
) -> SyncSummary:
    """Upsert every AgentCore harness as a Credo AI Use Case — one per agent."""
    return _sync(
        [_Record(r.name, r.description, "agent") for r in resources],
        _Target(
            noun="use case",
            field="description",
            list_items=credo.list_use_cases,
            create=credo.create_use_case,
            patch=credo.patch_use_case,
        ),
        dry_run=dry_run,
    )


def sync_vendors(
    models: list[BedrockModel],
    *,
    synced: SyncSummary,
    credo: VendorSink,
    dry_run: bool = False,
) -> VendorSummary:
    by_provider: dict[str, list[BedrockModel]] = {}
    skipped = 0
    for model in models:
        if model.provider:
            by_provider.setdefault(model.provider, []).append(model)
        else:
            skipped += 1

    summary = VendorSummary(providers=len(by_provider), skipped=skipped)
    if not by_provider:
        # Nothing to do, and not a single API call made.
        return summary

    # Matched case-insensitively: vendor names are uniquely constrained server
    # side, so creating "Anthropic" where "anthropic" exists would 422.
    existing = {v.name.casefold(): v.id for v in credo.list_vendors()}

    for provider, provider_models in by_provider.items():
        try:
            _sync_vendor(
                provider,
                provider_models,
                existing=existing,
                synced=synced,
                credo=credo,
                dry_run=dry_run,
                summary=summary,
            )
        except Exception as exc:  # one bad provider must not end the cycle
            logger.exception("Failed to sync vendor %r", provider)
            summary.error(f"vendor {provider!r}: {exc}")
    return summary


def _sync_vendor(
    provider: str,
    models: list[BedrockModel],
    *,
    existing: dict[str, str],
    synced: SyncSummary,
    credo: VendorSink,
    dry_run: bool,
    summary: VendorSummary,
) -> None:
    vendor_id = existing.get(provider.casefold())
    fresh = False

    if vendor_id is None:
        if dry_run:
            logger.info("[dry-run] would create vendor %r", provider)
            summary.vendors_created += 1
            summary.linked += len(models)
            return
        vendor_id = credo.create_vendor(provider).id
        existing[provider.casefold()] = vendor_id
        summary.vendors_created += 1
        fresh = True
        logger.info("Created vendor %r (%s)", provider, vendor_id)

    # A vendor created a moment ago has no links, so reading them is a wasted
    # round trip.
    linked = set() if fresh else {m.id for m in credo.list_vendor_models(vendor_id)}

    for model in models:
        model_id = synced.ids.get(model.name)
        if model_id is None:
            if not dry_run:
                # Live run: the model failed to be created earlier this cycle,
                # so there is nothing to link. Counting it as linked would let a
                # partly-failed run read as clean.
                logger.error(
                    "Cannot link model %r to vendor %r — the model was not "
                    "created this cycle",
                    model.name,
                    provider,
                )
                summary.error(f"model {model.name!r}: not created, cannot link")
                continue
            # --dry-run: the model would be created this cycle, so it has no id
            # to link against yet.
            logger.info(
                "[dry-run] would link model %r to vendor %r", model.name, provider
            )
            summary.linked += 1
            continue
        if model_id in linked:
            summary.already_linked += 1
            continue
        if dry_run:
            logger.info(
                "[dry-run] would link model %r to vendor %r", model.name, provider
            )
        else:
            credo.add_model_vendor(model_id, vendor_id)
            logger.info("Linked model %r to vendor %r", model.name, provider)
        summary.linked += 1


def link_use_case_models(
    resources: list[AgentCoreResource],
    *,
    models: SyncSummary,
    use_cases: SyncSummary,
    credo: UseCaseModelSink,
    profiles: dict[str, str] | None = None,
    dry_run: bool = False,
) -> LinkSummary:
    summary = LinkSummary(agents=len(resources))

    for agent in resources:
        try:
            _link(
                agent,
                models=models,
                use_cases=use_cases,
                credo=credo,
                profiles=profiles or {},
                dry_run=dry_run,
                summary=summary,
            )
        except Exception as exc:  # one bad link must not end the cycle
            logger.exception("Failed to link use case %r to its model", agent.name)
            summary.error(f"link use case {agent.name!r}: {exc}")
    return summary


def _link(
    agent: AgentCoreResource,
    *,
    models: SyncSummary,
    use_cases: SyncSummary,
    credo: UseCaseModelSink,
    profiles: dict[str, str],
    dry_run: bool,
    summary: LinkSummary,
) -> None:
    if agent.model_provider != "bedrock" or not agent.model_id:
        logger.debug(
            "Use case %r runs on %s — no Bedrock model to link",
            agent.name,
            agent.model_provider or "an unreadable model config",
        )
        summary.non_bedrock += 1
        return

    model_key = resolve_model_key(agent.model_id, names=models.names, profiles=profiles)
    if model_key is None:
        logger.info(
            "No Credo AI model record for %r — not linking use case %r "
            "(foundation models may be disabled, or the inference profile is "
            "unknown)",
            agent.model_id,
            agent.name,
        )
        summary.unresolved += 1
        return
    if model_key != agent.model_id:
        logger.info(
            "Harness %r names inference profile %r — linking to model %r",
            agent.name,
            agent.model_id,
            model_key,
        )

    use_case_id = use_cases.ids.get(agent.name)
    model_id = models.ids.get(model_key)

    if use_case_id is None or model_id is None:
        if not dry_run:
            # Live run: an id is missing only because that record failed to be
            # created earlier this cycle. Reporting the link as made would let a
            # partly-failed run read as clean.
            logger.error(
                "Cannot link use case %r to model %r — one of them was not "
                "created this cycle",
                agent.name,
                model_key,
            )
            summary.error(f"link use case {agent.name!r}: record not created")
            return
        logger.info(
            "[dry-run] would link use case %r to model %r", agent.name, model_key
        )
        summary.linked += 1
        return

    if model_id in {m.id for m in credo.list_use_case_models(use_case_id)}:
        summary.already_linked += 1
        return

    if dry_run:
        logger.info(
            "[dry-run] would link use case %r to model %r", agent.name, model_key
        )
    else:
        credo.add_use_case_model(use_case_id, model_id)
        logger.info("Linked use case %r to model %r", agent.name, model_key)
    summary.linked += 1


@dataclass(frozen=True)
class _Record:
    """One thing to upsert: the Credo AI name, the text field that carries
    everything else, and a kind used only for logs and error messages."""

    name: str
    text: str
    kind: str


@dataclass(frozen=True)
class _Target:
    """The half of the upsert that differs between Models and Use Cases: which
    Credo AI text field holds the rendered detail, and the three sink calls."""

    noun: str  # "model" | "use case"
    field: str  # "summary" | "description"
    list_items: Callable[[], list[Any]]
    create: Callable[[dict], Any]
    patch: Callable[[str, dict], Any]


def _sync(
    records: list[_Record], target: _Target, *, dry_run: bool = False
) -> SyncSummary:
    summary = SyncSummary(scanned=len(records), names={r.name for r in records})
    if not records:
        # Nothing to compare against, so the listing would be wasted — and this
        # is exactly the case where it costs most: an AWS listing that failed
        # leaves no records, and paging an entire tenant to sync none
        # of them adds load to a cycle that is already in trouble.
        logger.info("No %ss in scope — skipping the Credo AI listing", target.noun)
        return summary

    # One listing per cycle, reused for every record — not one lookup each.
    existing = {item.name: item for item in target.list_items()}

    for record in records:
        try:
            _upsert(
                record,
                target,
                existing=existing,
                dry_run=dry_run,
                summary=summary,
            )
        except Exception as exc:  # one bad record must not end the cycle
            logger.exception(
                "Failed to upsert %s %s %r", record.kind, target.noun, record.name
            )
            summary.error(f"{record.kind} {target.noun} {record.name!r}: {exc}")
    return summary


def _upsert(
    record: _Record,
    target: _Target,
    *,
    existing: dict[str, Any],
    dry_run: bool,
    summary: SyncSummary,
) -> None:
    current = existing.get(record.name)

    if current is None:
        if dry_run:
            logger.info(
                "[dry-run] would create %s %s %r", record.kind, target.noun, record.name
            )
        else:
            created = target.create(
                {"name": record.name, target.field: record.text, "source": SOURCE}
            )
            # Two listings surfacing the same name in one cycle must not POST
            # twice into a name-unique registry — record the write here so the
            # second occurrence sees it.
            existing[record.name] = created
            summary.ids[record.name] = created.id
            logger.info("Created %s %r (%s)", target.noun, record.name, created.id)
        summary.created += 1
        return

    summary.ids[record.name] = current.id

    # Skip the write when nothing changed. The text field is absent from the
    # listing on any API version that doesn't return it, in which case this
    # comparison never matches and the cycle degrades to patching everything
    # every run — chatty, but never wrong. See README "Limitations".
    if getattr(current, target.field, None) == record.text:
        summary.unchanged += 1
        return

    if dry_run:
        logger.info(
            "[dry-run] would update %s %s %r", record.kind, target.noun, record.name
        )
    else:
        target.patch(current.id, {target.field: record.text})
        logger.info("Updated %s %r (%s)", target.noun, record.name, current.id)
    summary.updated += 1
