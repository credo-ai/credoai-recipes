"""Persistent state: the registered webhook id and use case -> PR mappings.

The state file lives at the cookbook root (not the cwd) so `serve`, `status` and `teardown` always
see the same file. Writes are atomic (tempfile + os.replace) so a crash during a save cannot corrupt
it; a corrupt or missing file degrades to empty state.

The webhook id is kept here so `teardown` can delete the webhook after a crash that skipped the
shutdown cleanup.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from github_gate.config import STATE_FILE

# A use case whose review reached one of these no longer gates its PR; a new notify for the same PR
# starts a fresh use case (a new review cycle).
TERMINAL_STATUSES = {"approved", "rejected"}


@dataclass
class GateState:
    webhook_id: str | None = None
    use_cases: dict[str, dict] = field(default_factory=dict)
    path: Path = STATE_FILE

    @classmethod
    def load(cls, path: Path = STATE_FILE) -> GateState:
        if not path.exists():
            return cls(path=path)
        try:
            data = json.loads(path.read_text())
            return cls(
                webhook_id=data.get("webhook_id"),
                use_cases=data.get("use_cases", {}),
                path=path,
            )
        except (json.JSONDecodeError, OSError):
            return cls(path=path)

    def save(self) -> None:
        payload = json.dumps(self.snapshot(), indent=2)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(payload)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def snapshot(self) -> dict:
        return {"webhook_id": self.webhook_id, "use_cases": self.use_cases}

    # -- webhook -----------------------------------------------------------------

    def set_webhook(self, webhook_id: str) -> None:
        self.webhook_id = webhook_id
        self.save()

    def clear_webhook(self) -> None:
        self.webhook_id = None
        self.save()

    # -- use case -> PR mappings ---------------------------------------------------

    def add_use_case(
        self,
        use_case_id: str,
        *,
        name: str,
        repo: str,
        pr_number: int,
        pr_title: str,
        head_sha: str,
        html_url: str,
    ) -> None:
        self.use_cases[use_case_id] = {
            "name": name,
            "repo": repo,
            "pr_number": pr_number,
            "pr_title": pr_title,
            "head_sha": head_sha,
            "html_url": html_url,
            "status": "pending",
            "created_at": _utcnow(),
            "resolved_at": None,
            "review_id": None,
        }
        self.save()

    def get(self, use_case_id: str) -> dict | None:
        return self.use_cases.get(use_case_id)

    def find_open_for_pr(self, repo: str, pr_number: int) -> tuple[str, dict] | None:
        """The (use_case_id, entry) still gating this PR, if any."""
        for use_case_id, entry in self.use_cases.items():
            if (
                entry["repo"] == repo
                and entry["pr_number"] == pr_number
                and entry["status"] not in TERMINAL_STATUSES
            ):
                return use_case_id, entry
        return None

    def find_by_name(self, name: str) -> str | None:
        """The use_case_id of any tracked use case with this name, or None."""
        for use_case_id, entry in self.use_cases.items():
            if entry.get("name") == name:
                return use_case_id
        return None

    def update_sha(self, use_case_id: str, head_sha: str) -> None:
        self.use_cases[use_case_id]["head_sha"] = head_sha
        self.save()

    def mark(self, use_case_id: str, status: str, review_id: str | None) -> None:
        entry = self.use_cases[use_case_id]
        entry["status"] = status
        entry["review_id"] = review_id
        entry["resolved_at"] = _utcnow()
        self.save()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
