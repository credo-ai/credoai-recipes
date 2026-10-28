"""Shared fakes. Tests are fully offline: no Credo AI, no GitHub, no network."""

import itertools

import pytest
from github_gate.state import GateState


class FakeCredo:
    """Stands in for GateCredoClient."""

    def __init__(self):
        self._ids = itertools.count(1)
        self.created: list[tuple[str, str]] = []
        self.deleted_use_cases: list[str] = []
        self.webhooks: dict[str, dict] = {}
        self.deleted_webhooks: list[str] = []

    def create_use_case(self, name: str, description: str) -> str:
        self.created.append((name, description))
        return f"uc-{next(self._ids)}"

    def delete_use_case(self, use_case_id: str) -> None:
        self.deleted_use_cases.append(use_case_id)

    def create_webhook(self, description, event_types, url) -> dict:
        webhook = {"id": f"wh-{len(self.webhooks) + 1}", "url": url}
        self.webhooks[webhook["id"]] = webhook
        return webhook

    def delete_webhook(self, webhook_id: str) -> None:
        self.deleted_webhooks.append(webhook_id)
        self.webhooks.pop(webhook_id, None)


class FakeGitHub:
    """Records every commit status the service sets."""

    def __init__(self, fail: bool = False):
        self.calls: list[dict] = []
        self.fail = fail

    def __call__(self, repo, sha, state, *, description=""):
        if self.fail:
            raise RuntimeError("GitHub API error 500")
        self.calls.append(
            {"repo": repo, "sha": sha, "state": state, "description": description}
        )


@pytest.fixture
def credo():
    return FakeCredo()


@pytest.fixture
def github():
    return FakeGitHub()


@pytest.fixture
def state(tmp_path):
    return GateState(path=tmp_path / "state.json")


def notify_payload(**overrides) -> dict:
    payload = {
        "repo": "acme/ml-repo",
        "pr_number": 42,
        "pr_title": "Update agent",
        "model_name": "fraud-agent",
        "head_sha": "abc123abc123abc123",
        "html_url": "https://github.com/acme/ml-repo/pull/42",
    }
    payload.update(overrides)
    return payload


def review_event(use_case_id: str, status: str, **overrides) -> dict:
    body = {
        "type": "entity_review_status_updated",
        "data": {"useCaseId": use_case_id, "status": status, "reviewId": "rev-1"},
    }
    body.update(overrides)
    return body
