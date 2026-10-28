import json

from github_gate import cli
from github_gate.state import GateState

from .conftest import FakeCredo


def seed(tmp_path, monkeypatch):
    state = GateState(path=tmp_path / "state.json")
    state.set_webhook("wh-1")
    state.add_use_case(
        "uc-1",
        name="Databricks: m",
        repo="a/b",
        pr_number=1,
        pr_title="t",
        head_sha="s",
        html_url="u",
    )
    monkeypatch.setattr(cli.GateState, "load", classmethod(lambda cls, *a, **k: state))
    return state


def test_status_prints_state(tmp_path, monkeypatch, capsys):
    seed(tmp_path, monkeypatch)

    assert cli.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out)["webhook_id"] == "wh-1"


def test_teardown_deletes_webhook_and_untracks(tmp_path, monkeypatch):
    state = seed(tmp_path, monkeypatch)
    fake = FakeCredo()
    fake.close = lambda: None
    monkeypatch.setattr("github_gate.credo.GateCredoClient", lambda settings: fake)

    assert cli.main(["teardown"]) == 0

    assert fake.deleted_webhooks == ["wh-1"]
    assert fake.deleted_use_cases == []
    assert state.webhook_id is None
    assert state.use_cases == {}


def test_teardown_delete_use_cases_flag(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch)
    fake = FakeCredo()
    fake.close = lambda: None
    monkeypatch.setattr("github_gate.credo.GateCredoClient", lambda settings: fake)

    assert cli.main(["teardown", "--delete-use-cases"]) == 0

    assert fake.deleted_use_cases == ["uc-1"]


def test_teardown_dry_run_changes_nothing(tmp_path, monkeypatch):
    state = seed(tmp_path, monkeypatch)

    assert cli.main(["teardown", "--dry-run"]) == 0

    assert state.webhook_id == "wh-1"
    assert "uc-1" in state.use_cases
