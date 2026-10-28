from fastapi.testclient import TestClient
from github_gate.app import WEBHOOK_PATH, create_app
from github_gate.config import Settings

from .conftest import notify_payload, review_event


def make_client(credo, github, state, **settings):
    app = create_app(Settings(**settings), credo=credo, state=state, set_status=github)
    return TestClient(app)


def test_notify_then_approve_flips_status(credo, github, state):
    client = make_client(credo, github, state)

    created = client.post("/gate/notify", json=notify_payload()).json()
    resp = client.post(
        WEBHOOK_PATH, json=review_event(created["use_case_id"], "approved")
    )

    assert resp.status_code == 200
    assert resp.json()["action"] == "approved"
    assert github.calls[0]["state"] == "success"


def test_notify_requires_secret_when_configured(credo, github, state):
    client = make_client(credo, github, state, gate_shared_secret="s3cret")

    assert client.post("/gate/notify", json=notify_payload()).status_code == 403
    bad = client.post(
        "/gate/notify", json=notify_payload(), headers={"X-Gate-Secret": "wrong"}
    )
    assert bad.status_code == 403
    ok = client.post(
        "/gate/notify", json=notify_payload(), headers={"X-Gate-Secret": "s3cret"}
    )
    assert ok.status_code == 200


def test_notify_open_when_no_secret_configured(credo, github, state):
    client = make_client(credo, github, state)

    assert client.post("/gate/notify", json=notify_payload()).status_code == 200


def test_notify_rejects_incomplete_payload(credo, github, state):
    client = make_client(credo, github, state)

    assert client.post("/gate/notify", json={"repo": "acme/ml-repo"}).status_code == 422


def test_webhook_ignored_events_return_200(credo, github, state):
    client = make_client(credo, github, state)

    resp = client.post(WEBHOOK_PATH, json=review_event("uc-nope", "approved"))

    assert resp.status_code == 200
    assert resp.json()["action"] == "unknown_use_case"


def test_webhook_returns_500_when_github_fails(credo, state):
    from .conftest import FakeGitHub

    client = make_client(credo, FakeGitHub(fail=True), state)
    uc = client.post("/gate/notify", json=notify_payload()).json()["use_case_id"]

    resp = client.post(WEBHOOK_PATH, json=review_event(uc, "approved"))

    assert resp.status_code == 500


def test_state_endpoint_lists_tracked_pr(credo, github, state):
    client = make_client(credo, github, state)
    uc = client.post("/gate/notify", json=notify_payload()).json()["use_case_id"]

    snapshot = client.get("/gate/state").json()

    assert snapshot["use_cases"][uc]["pr_number"] == 42


def test_lifespan_registers_and_deletes_webhook(credo, github, state):
    app = create_app(
        Settings(gate_public_base_url="https://gate.example.com/"),
        credo=credo,
        state=state,
        set_status=github,
    )

    with TestClient(app):
        assert state.webhook_id == "wh-1"
        assert (
            credo.webhooks["wh-1"]["url"] == "https://gate.example.com/webhooks/credo"
        )

    assert credo.deleted_webhooks == ["wh-1"]
    assert state.webhook_id is None
