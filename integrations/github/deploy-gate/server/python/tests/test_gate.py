from github_gate import gate
from github_gate.state import GateState

from .conftest import notify_payload, review_event


def test_notify_creates_use_case_named_after_model(credo, state):
    result = gate.handle_notify(notify_payload(), credo=credo, state=state)

    assert result["action"] == "created"
    assert credo.created[0][0] == "Databricks: fraud-agent"
    entry = state.get(result["use_case_id"])
    assert entry["status"] == "pending"
    assert entry["pr_number"] == 42


def test_renotify_same_pr_updates_sha_without_new_use_case(credo, state):
    first = gate.handle_notify(notify_payload(), credo=credo, state=state)
    second = gate.handle_notify(
        notify_payload(head_sha="def456def456def456"), credo=credo, state=state
    )

    assert second["action"] == "sha_updated"
    assert second["use_case_id"] == first["use_case_id"]
    assert len(credo.created) == 1
    assert state.get(first["use_case_id"])["head_sha"] == "def456def456def456"


def test_second_pr_same_model_reuses_use_case(credo, state):
    first = gate.handle_notify(notify_payload(), credo=credo, state=state)
    second = gate.handle_notify(
        notify_payload(pr_number=43, head_sha="fff"), credo=credo, state=state
    )

    assert second["action"] == "reused"
    assert second["use_case_id"] == first["use_case_id"]
    assert len(credo.created) == 1
    # state entry now tracks the latest PR
    assert state.get(first["use_case_id"])["pr_number"] == 43


def test_different_models_get_separate_use_cases(credo, state):
    a = gate.handle_notify(notify_payload(), credo=credo, state=state)
    b = gate.handle_notify(
        notify_payload(pr_number=44, model_name="other-agent"), credo=credo, state=state
    )

    assert a["use_case_id"] != b["use_case_id"]
    assert len(credo.created) == 2


def test_approved_review_sets_success_on_head_sha(credo, github, state):
    uc = gate.handle_notify(notify_payload(), credo=credo, state=state)["use_case_id"]

    result = gate.handle_review_event(
        review_event(uc, "approved"), state=state, set_status=github
    )

    assert result["action"] == "approved"
    assert github.calls == [
        {
            "repo": "acme/ml-repo",
            "sha": "abc123abc123abc123",
            "state": "success",
            "description": "Approved via Credo AI use case review",
        }
    ]
    assert state.get(uc)["status"] == "approved"
    assert state.get(uc)["review_id"] == "rev-1"


def test_rejected_review_sets_failure(credo, github, state):
    uc = gate.handle_notify(notify_payload(), credo=credo, state=state)["use_case_id"]

    result = gate.handle_review_event(
        review_event(uc, "rejected"), state=state, set_status=github
    )

    assert result["action"] == "rejected"
    assert github.calls[0]["state"] == "failure"


def test_status_uses_latest_sha_after_repush(credo, github, state):
    uc = gate.handle_notify(notify_payload(), credo=credo, state=state)["use_case_id"]
    gate.handle_notify(notify_payload(head_sha="newsha"), credo=credo, state=state)

    gate.handle_review_event(
        review_event(uc, "approved"), state=state, set_status=github
    )

    assert github.calls[0]["sha"] == "newsha"


def test_non_terminal_status_is_ignored(credo, github, state):
    uc = gate.handle_notify(notify_payload(), credo=credo, state=state)["use_case_id"]

    result = gate.handle_review_event(
        review_event(uc, "in_progress"), state=state, set_status=github
    )

    assert result["action"] == "ignored_non_terminal"
    assert github.calls == []
    assert state.get(uc)["status"] == "pending"


def test_unknown_use_case_is_ignored(github, state):
    result = gate.handle_review_event(
        review_event("uc-nope", "approved"), state=state, set_status=github
    )

    assert result["action"] == "unknown_use_case"
    assert github.calls == []


def test_other_event_type_is_ignored(github, state):
    result = gate.handle_review_event(
        review_event("uc-1", "approved", type="something_else"),
        state=state,
        set_status=github,
    )

    assert result["action"] == "ignored_event_type"
    assert github.calls == []


def test_duplicate_delivery_does_not_repost(credo, github, state):
    uc = gate.handle_notify(notify_payload(), credo=credo, state=state)["use_case_id"]
    event = review_event(uc, "approved")

    gate.handle_review_event(event, state=state, set_status=github)
    result = gate.handle_review_event(event, state=state, set_status=github)

    assert result["action"] == "duplicate"
    assert len(github.calls) == 1


def test_github_failure_propagates_and_leaves_state_pending(credo, state):
    from .conftest import FakeGitHub

    uc = gate.handle_notify(notify_payload(), credo=credo, state=state)["use_case_id"]

    try:
        gate.handle_review_event(
            review_event(uc, "approved"), state=state, set_status=FakeGitHub(fail=True)
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError")

    assert state.get(uc)["status"] == "pending"


def test_new_notify_after_terminal_starts_fresh_cycle(credo, github, state):
    first = gate.handle_notify(notify_payload(), credo=credo, state=state)[
        "use_case_id"
    ]
    gate.handle_review_event(
        review_event(first, "rejected"), state=state, set_status=github
    )

    again = gate.handle_notify(
        notify_payload(head_sha="retry"), credo=credo, state=state
    )

    # terminal entry no longer gates the PR -> goes through the by-name reuse path and re-pends
    assert again["use_case_id"] == first
    assert state.get(first)["status"] == "pending"


def test_state_round_trips_through_disk(credo, tmp_path):
    path = tmp_path / "state.json"
    state = GateState(path=path)
    uc = gate.handle_notify(notify_payload(), credo=credo, state=state)["use_case_id"]
    state.set_webhook("wh-9")

    loaded = GateState.load(path)

    assert loaded.webhook_id == "wh-9"
    assert loaded.get(uc)["repo"] == "acme/ml-repo"


def test_corrupt_state_file_degrades_to_empty(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json")

    assert GateState.load(path).use_cases == {}
