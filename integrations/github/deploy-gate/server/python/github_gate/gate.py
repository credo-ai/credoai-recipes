"""Core gate orchestration — framework-free, dependencies injected.

Two entry points, one per inbound request:

- `handle_notify`: the GitHub workflow reported a PR awaiting approval. Creates (or reuses) the
  Credo AI use case and records the use case -> PR mapping.
- `handle_review_event`: a Credo AI `entity_review_status_updated` webhook arrived. On a
  terminal review status, flips the PR's commit status.
"""

from __future__ import annotations

import logging
from typing import Callable, Protocol

from github_gate.state import GateState

logger = logging.getLogger("github_gate.gate")

EVENT_TYPE = "entity_review_status_updated"

# The use case is named "<prefix>: <model_name>" so reviewers can find it in Credo AI.
USE_CASE_NAME_PREFIX = "Databricks"

APPROVED_STATUSES = {"approved"}
# The exact rejection string can differ per tenant — extend this set if your tenant uses another.
REJECTED_STATUSES = {"rejected", "denied"}

# Same shape as `github.set_commit_status`: (repo, sha, state, *, description) -> None
SetStatus = Callable[..., None]


class UseCaseCreator(Protocol):
    def create_use_case(self, name: str, description: str) -> str: ...


def handle_notify(info: dict, *, credo: UseCaseCreator, state: GateState) -> dict:
    """Register a PR awaiting approval; create or reuse its use case.

    A re-pushed PR re-runs the workflow with a new head SHA. If a non-terminal use case already
    gates this PR, its SHA is updated in place rather than creating a use case per push, so a later
    approval lands on the latest commit. Once a mapping is terminal, a new notify starts fresh.
    """
    repo, pr_number = info["repo"], info["pr_number"]
    name = f"{USE_CASE_NAME_PREFIX}: {info['model_name']}"
    entry_fields = {
        "name": name,
        "repo": repo,
        "pr_number": pr_number,
        "pr_title": info["pr_title"],
        "head_sha": info["head_sha"],
        "html_url": info["html_url"],
    }

    existing = state.find_open_for_pr(repo, pr_number)
    if existing is not None:
        use_case_id, _ = existing
        state.update_sha(use_case_id, info["head_sha"])
        logger.info(
            "PR %s#%s re-armed: reusing use case %s with new head %s",
            repo,
            pr_number,
            use_case_id,
            info["head_sha"][:12],
        )
        return {"use_case_id": use_case_id, "action": "sha_updated"}

    existing_id = state.find_by_name(name)
    if existing_id is not None:
        state.add_use_case(existing_id, **entry_fields)
        logger.info(
            "Reusing use case %s (name %r) for PR %s#%s",
            existing_id,
            name,
            repo,
            pr_number,
        )
        return {"use_case_id": existing_id, "action": "reused"}

    description = (
        f"Deployment gate for {info['html_url']} (head {info['head_sha']}). "
        "Approving this use case's review unblocks the deployment."
    )
    use_case_id = credo.create_use_case(name, description)
    state.add_use_case(use_case_id, **entry_fields)
    logger.info("Created use case %s for PR %s#%s", use_case_id, repo, pr_number)
    return {"use_case_id": use_case_id, "action": "created"}


def handle_review_event(body: dict, *, state: GateState, set_status: SetStatus) -> dict:
    """Map a review webhook back to its PR and flip the commit status.

    Returns an `{"action": ...}` dict. The caller turns every outcome into a 200 (so Credo AI does
    not retry handled-but-ignored deliveries) except a GitHub API failure, which propagates so the
    delivery is retried.
    """
    if body.get("type") != EVENT_TYPE:
        return {"action": "ignored_event_type", "type": body.get("type")}

    data = body.get("data") or {}
    status = (data.get("status") or "").lower()
    use_case_id = data.get("useCaseId")

    # The event also fires when a review is *created* (a non-terminal status).
    if status in APPROVED_STATUSES:
        gate_status, github_state = "approved", "success"
        description = "Approved via Credo AI use case review"
    elif status in REJECTED_STATUSES:
        gate_status, github_state = "rejected", "failure"
        description = "Rejected via Credo AI use case review"
    else:
        logger.info("Ignoring non-terminal review status %r", status)
        return {"action": "ignored_non_terminal", "status": status}

    entry = state.get(use_case_id) if use_case_id else None
    if entry is None:
        logger.warning("No tracked PR for use case %r — ignoring", use_case_id)
        return {"action": "unknown_use_case", "use_case_id": use_case_id}

    if entry["status"] == gate_status:
        logger.info("Duplicate %s delivery for use case %s", gate_status, use_case_id)
        return {"action": "duplicate", "use_case_id": use_case_id}

    set_status(entry["repo"], entry["head_sha"], github_state, description=description)
    state.mark(use_case_id, gate_status, data.get("reviewId"))
    logger.info(
        "Gate %s for PR %s#%s (%s)",
        gate_status,
        entry["repo"],
        entry["pr_number"],
        entry["head_sha"][:12],
    )
    return {
        "action": gate_status,
        "use_case_id": use_case_id,
        "pr_number": entry["pr_number"],
    }
