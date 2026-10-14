"""Core Agent 365 -> Credo AI orchestration — framework-free, dependencies injected.

Three entry points, each called once per cycle:

- ``select_agents``  — which catalog packages are in scope for this run.
- ``sync_use_cases`` — one Credo AI Use Case per agent, created or kept current.
- ``enforce``        — block or unblock each agent to match its Use Case.

**Stateless.** Agent 365 has no webhook or event feed, so this is a poll:
read the catalog, read Credo AI, diff, write. Credo AI itself is the state
store — there is no local state file to lose on an ephemeral CI runner, and
nothing to reset if a run is interrupted.

**Matched by asset ID, not by name.** An agent's name is not a key: the same
name can belong to several agents, while Credo AI Use Case names are unique (a
duplicate comes back 422). So each Use Case's description ends with a small
marker block naming the Agent 365 asset ID and the agent's last-modified time:

    Agent 365 asset ID: T_0a1b2c3d-4e5f-6789-abcd-ef0123456789
    Agent 365 last modified: 2026-09-24T15:50:07.1408663Z

The first line is the match key. The second is change detection: if it equals
what the catalog reports now, the agent is unchanged and no call is made.

**Cleared** means the Use Case has finished the *last* stage of its workflow —
the stage of type ``end``, at the step ``clear``. A Use Case at ``clear`` in an
earlier stage has only finished that stage. Anything else, including a rejected
Use Case, is not cleared, so ``--enforce`` keeps the agent blocked.

One failing record does not end the cycle: each agent is isolated, counted in
the summary, and the caller exits non-zero if anything failed.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger("agent365_sync.sync")

_ASSET_ID_RE = re.compile(r"^Agent 365 asset ID: (\S+)\s*$", re.MULTILINE)
_MODIFIED_RE = re.compile(r"^Agent 365 last modified: (\S+)\s*$", re.MULTILINE)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


# These Protocols are the contract this module needs from a Credo AI client and
# a Microsoft client, and nothing more. Type declarations only — never
# instantiated. They keep this module free of any HTTP or SDK import, so it can
# be read, reasoned about and driven by anything that satisfies the signatures.
class UseCaseSink(Protocol):
    def list_use_cases(self) -> list[Any]: ...
    def create_use_case(self, name: str, description: str) -> Any: ...
    def update_use_case(self, use_case_id: str, attrs: dict) -> Any: ...
    def attach_questionnaire(self, use_case_id: str, key: str, version: int) -> Any: ...
    def get_workflow(self, use_case_id: str) -> Any: ...


class Blocker(Protocol):
    def set_blocked(self, asset_id: str, blocked: bool) -> None: ...


@dataclass
class UseCaseSummary:
    """Counters for the Use Case pass.

    ``unchanged`` counts agents whose Use Case already matches the catalog — no
    API call made. ``attached`` counts intake questionnaires attached this run.
    ``errors`` collects one message per failure so the cycle can continue and
    still exit non-zero.
    """

    scanned: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    attached: int = 0
    errors: list[str] = field(default_factory=list)

    def error(self, message: str) -> None:
        self.errors.append(message)

    def format(self) -> str:
        return (
            f"scanned={self.scanned} created={self.created} updated={self.updated} "
            f"unchanged={self.unchanged} questionnaires_attached={self.attached} "
            f"errors={len(self.errors)}"
        )


@dataclass
class EnforceSummary:
    """Counters for the block/unblock pass. ``skipped`` counts agents whose Use
    Case could not be created or read this run: they are left exactly as they
    are rather than blocked on incomplete information."""

    scanned: int = 0
    blocked: int = 0
    unblocked: int = 0
    unchanged: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def error(self, message: str) -> None:
        self.errors.append(message)

    def format(self) -> str:
        return (
            f"scanned={self.scanned} blocked={self.blocked} unblocked={self.unblocked} "
            f"unchanged={self.unchanged} skipped={self.skipped} errors={len(self.errors)}"
        )


@dataclass
class Outcome:
    """What the Use Case pass learned about one agent's Use Case. ``use_case_id``
    is None under ``--dry-run`` for a Use Case that would be created."""

    use_case_id: str | None
    step: str | None


# -- selection ---------------------------------------------------------------


def select_agents(
    packages: list[dict],
    *,
    types: frozenset[str] | None,
    asset_ids: list[str] | tuple[str, ...] = (),
    limit: int | None = None,
) -> tuple[list[dict], list[str]]:
    """Pick the catalog packages this run covers, oldest first.

    ``asset_ids`` wins over ``types``: naming an agent explicitly is a clear
    instruction, so it is honoured whatever the agent's type. ``limit`` keeps
    only the newest N. Returns (selected, asset ids not found in the catalog).
    """
    by_id = {p["id"]: p for p in packages if p.get("id")}
    missing: list[str] = []
    if asset_ids:
        selected = []
        for asset_id in asset_ids:
            if asset_id in by_id:
                selected.append(by_id[asset_id])
            else:
                missing.append(asset_id)
    else:
        selected = [
            p
            for p in by_id.values()
            if types is None or (p.get("type") or "").lower() in types
        ]
    selected.sort(key=lambda p: p.get("createdDateTime") or "")
    if limit is not None:
        selected = selected[-limit:]
    return selected, missing


# -- the marker block --------------------------------------------------------


def asset_id_of(description: str | None) -> str | None:
    match = _ASSET_ID_RE.search(description or "")
    return match.group(1) if match else None


def modified_of(description: str | None) -> str | None:
    match = _MODIFIED_RE.search(description or "")
    return match.group(1) if match else None


def _plain_text(text: str | None) -> str:
    text = (text or "").strip()
    if "<" not in text:
        return text
    # Third-party store apps describe themselves in HTML; org-built agents use
    # plain text and keep their own line breaks.
    return _SPACE_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", text))).strip()


def build_description(pkg: dict) -> str:
    """The agent's own description, then facts a governance reviewer wants, then
    the marker block this cookbook matches on. Never empty: Copilot Studio and
    Foundry agents often have no description at all."""
    body = _plain_text(pkg.get("longDescription")) or _plain_text(
        pkg.get("shortDescription")
    )
    lines = []
    platform = (pkg.get("platform") or "").strip()
    if platform and platform != "Not Available":
        lines.append(f"Built with: {platform}")
    publisher = (pkg.get("publisher") or "").strip()
    if publisher:
        lines.append(f"Publisher: {publisher}")
    lines.append(f"Agent 365 asset ID: {pkg['id']}")
    lines.append(f"Agent 365 last modified: {pkg.get('lastModifiedDateTime') or ''}")
    return (body + "\n\n" if body else "") + "\n".join(lines)


# -- names -------------------------------------------------------------------


def _short_id(asset_id: str) -> str:
    return asset_id.split("-")[0] if "-" in asset_id else asset_id[-8:]


def name_candidates(display_name: str, asset_id: str) -> list[str]:
    """The clean name first, then progressively more specific ones, for when
    another Use Case already holds the clean name."""
    return [
        display_name,
        f"{display_name} ({_short_id(asset_id)})",
        f"{display_name} ({asset_id})",
    ]


def _pick_name(
    candidates: list[str], owners: dict[str, str], own_id: str | None
) -> str | None:
    for candidate in candidates:
        owner = owners.get(candidate.casefold())
        if owner is None or owner == own_id:
            return candidate
    return None


# -- Use Case pass -----------------------------------------------------------


def _step_of(use_case: Any) -> str | None:
    step = getattr(use_case, "workflow_stage_step", None)
    return getattr(step, "value", step)


def sync_use_cases(
    agents: list[dict],
    *,
    credo: UseCaseSink,
    use_cases: list[Any],
    questionnaire: tuple[str, int] | None,
    dry_run: bool,
) -> tuple[UseCaseSummary, dict[str, Outcome]]:
    """Create or refresh one Credo AI Use Case per agent.

    ``use_cases`` is every Use Case already in the tenant, read once by the
    caller: it is both the match index and the name-collision check.
    """
    summary = UseCaseSummary()
    outcomes: dict[str, Outcome] = {}

    by_asset: dict[str, Any] = {}
    owners: dict[str, str] = {}  # casefolded Use Case name -> its id
    for use_case in use_cases:
        owners[use_case.name.casefold()] = use_case.id
        asset_id = asset_id_of(use_case.description)
        if asset_id is None:
            continue
        if asset_id in by_asset:
            logger.warning(
                "Two Use Cases carry asset ID %s (%s, %s); using the first",
                asset_id,
                by_asset[asset_id].id,
                use_case.id,
            )
            continue
        by_asset[asset_id] = use_case

    wanted = f"{questionnaire[0]}+{questionnaire[1]}" if questionnaire else None
    verb = "[dry-run] would " if dry_run else ""

    for pkg in agents:
        summary.scanned += 1
        asset_id = pkg["id"]
        display_name = (pkg.get("displayName") or "").strip() or asset_id
        label = f"{display_name!r} ({asset_id})"
        existing = by_asset.get(asset_id)
        try:
            candidates = name_candidates(display_name, asset_id)
            if existing is None:
                name = _pick_name(candidates, owners, None)
                if name is None:
                    raise RuntimeError("every candidate name is already taken")
                logger.info("%screate Use Case %r for %s", verb, name, label)
                if dry_run:
                    owners[name.casefold()] = f"(new:{asset_id})"
                    use_case_id, step = None, None
                else:
                    created = credo.create_use_case(name, build_description(pkg))
                    owners[name.casefold()] = created.id
                    use_case_id, step = created.id, _step_of(created)
                summary.created += 1
                has_questionnaire = False
            else:
                use_case_id, step = existing.id, _step_of(existing)
                has_questionnaire = wanted in (existing.questionnaire_ids or [])
                changed = modified_of(existing.description) != (
                    pkg.get("lastModifiedDateTime") or ""
                )
                # Keep a name this cookbook already chose (clean or
                # disambiguated) so names never flip between runs; rename only
                # when the agent itself was renamed in Agent 365.
                keep_name = existing.name in candidates
                new_name = (
                    existing.name
                    if keep_name
                    else _pick_name(candidates, owners, existing.id)
                )
                if new_name is None:
                    raise RuntimeError("every candidate name is already taken")
                if not changed and keep_name:
                    summary.unchanged += 1
                    logger.debug("Use Case for %s is current", label)
                else:
                    attrs: dict = {"description": build_description(pkg)}
                    if new_name != existing.name:
                        attrs["name"] = new_name
                    logger.info(
                        "%supdate Use Case %r for %s", verb, existing.name, label
                    )
                    if not dry_run:
                        credo.update_use_case(existing.id, attrs)
                        if "name" in attrs:
                            owners.pop(existing.name.casefold(), None)
                            owners[new_name.casefold()] = existing.id
                    summary.updated += 1
            outcomes[asset_id] = Outcome(use_case_id, step)
        except Exception as exc:  # noqa: BLE001 — one bad agent must not end the cycle
            summary.error(f"{label}: {exc}")
            continue

        if questionnaire and not has_questionnaire:
            logger.info("%sattach questionnaire %s to %s", verb, wanted, label)
            try:
                if not dry_run:
                    credo.attach_questionnaire(use_case_id, *questionnaire)
                summary.attached += 1
            except Exception as exc:  # noqa: BLE001
                summary.error(
                    f"{label}: attaching questionnaire {wanted}: {exc} — check "
                    "CREDO_INTAKE_QUESTIONNAIRE names a questionnaire that exists "
                    "in your Credo AI tenant"
                )

    return summary, outcomes


# -- enforcement pass --------------------------------------------------------


def is_cleared(outcome: Outcome, credo: UseCaseSink) -> bool:
    """True once the Use Case has cleared the last stage of its workflow.

    ``clear`` is also the closing step of every earlier stage, so the step alone
    proves nothing; the stage's type has to be ``end``. The extra read only
    happens for a Use Case already at ``clear``, which is rare.
    """
    if outcome.use_case_id is None or outcome.step != "clear":
        return False
    workflow = credo.get_workflow(outcome.use_case_id)
    stage = getattr(workflow, "workflow_stage", None)
    stage_type = getattr(stage, "type", None)
    step = getattr(workflow, "workflow_stage_step", None)
    return getattr(stage_type, "value", stage_type) == "end" and (
        getattr(step, "value", step) == "clear"
    )


def enforce(
    agents: list[dict],
    outcomes: dict[str, Outcome],
    *,
    credo: UseCaseSink,
    agent365: Blocker,
    dry_run: bool,
) -> EnforceSummary:
    """Make every agent's blocked state match its Use Case.

    Only calls Microsoft when the state actually differs, so a run where
    nothing changed makes no block or unblock calls at all.
    """
    summary = EnforceSummary()
    verb = "[dry-run] would " if dry_run else ""

    for pkg in agents:
        summary.scanned += 1
        asset_id = pkg["id"]
        label = f"{(pkg.get('displayName') or asset_id)!r} ({asset_id})"
        outcome = outcomes.get(asset_id)
        if outcome is None:
            summary.skipped += 1
            logger.warning(
                "Skipping %s: its Use Case could not be synced this run", label
            )
            continue
        try:
            cleared = is_cleared(outcome, credo)
            want_blocked = not cleared
            if bool(pkg.get("isBlocked")) == want_blocked:
                summary.unchanged += 1
                logger.debug(
                    "%s is already %s",
                    label,
                    "blocked" if want_blocked else "available",
                )
                continue
            reason = (
                "its Use Case is cleared"
                if cleared
                else (
                    f"its Use Case is not cleared (step: {outcome.step or 'not started'})"
                )
            )
            logger.info(
                "%s%s %s: %s",
                verb,
                "block" if want_blocked else "unblock",
                label,
                reason,
            )
            if not dry_run:
                agent365.set_blocked(asset_id, want_blocked)
            if want_blocked:
                summary.blocked += 1
            else:
                summary.unblocked += 1
        except Exception as exc:  # noqa: BLE001 — one bad agent must not end the cycle
            summary.error(f"{label}: {exc}")

    return summary
