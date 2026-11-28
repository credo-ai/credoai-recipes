import logging

import pytest

from kit.clients.base import APIError
from kit.internal import trigger_mode
from tests.fakes import FakeResponse, FakeTenant, make_ctx

PAUSE_BODY = {"data": {"type": "entities", "attributes": {"trigger_mode": "hidden_questions_only"}}}


def test_read_mode_returns_the_entity_trigger_mode():
    internal = FakeTenant(
        json_bodies={"use_cases/uc1": {"data": {"id": "uc1", "attributes": {"trigger_mode": "full"}}}}
    )
    assert trigger_mode.read_mode(internal, "use_cases/uc1") == "full"


def test_read_mode_raises_on_an_unexpected_shape():
    internal = FakeTenant(json_bodies={"use_cases/uc1": {"items": []}})
    with pytest.raises(APIError, match="trigger mode"):
        trigger_mode.read_mode(internal, "use_cases/uc1")


def test_pause_for_load_patches_partial_pause(tmp_path):
    ctx = make_ctx(tmp_path)
    assert trigger_mode.pause_for_load(ctx, "e1", "use case 'A'") == "paused"
    assert ctx.target_internal.patches == [("entities/e1/trigger_mode", PAUSE_BODY)]
    assert not ctx.summary.needs_attention


def test_pause_for_load_404_means_not_available(tmp_path, caplog):
    target_internal = FakeTenant(
        patch_response=FakeResponse(404, text='{"errors":[{"reason":"feature_flag_disabled"}]}')
    )
    ctx = make_ctx(tmp_path, target_internal=target_internal)
    with caplog.at_level(logging.DEBUG, logger="kit.load"):
        assert trigger_mode.pause_for_load(ctx, "e1", "model 'M'") == "not_available"
    assert [r.levelno for r in caplog.records] == [logging.DEBUG]
    assert not ctx.summary.needs_attention


@pytest.mark.parametrize(
    "failure", [FakeResponse(500, text="boom"), APIError("lost its connection")], ids=["status", "exception"]
)
def test_pause_for_load_other_failure_returns_none(tmp_path, caplog, failure):
    target_internal = FakeTenant()
    if isinstance(failure, Exception):

        def patch(path, json):
            raise failure

        target_internal.patch = patch
    else:
        target_internal.patch_response = failure
    ctx = make_ctx(tmp_path, target_internal=target_internal)
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        assert trigger_mode.pause_for_load(ctx, "e1", "use case 'A'") is None
    assert "use case 'A'" in caplog.text and "no answers will be written" in caplog.text


def test_pause_for_load_dry_run_writes_nothing(tmp_path):
    ctx = make_ctx(tmp_path, dry_run=True)
    assert trigger_mode.pause_for_load(ctx, "e1", "use case 'A'") == "paused"
    assert ctx.target_internal.patches == []


def test_summary_note_tells_the_operator_how_to_turn_triggers_back_on(tmp_path, caplog):
    ctx = make_ctx(tmp_path)
    with caplog.at_level(logging.INFO, logger="kit.load"):
        trigger_mode.summary_note(ctx, 3)
    assert "Triggers are paused on 3 migrated use cases, so the copies match the source" in caplog.text
    assert "'Triggers and actions settings'" in caplog.text and "choose Enabled" in caplog.text
    assert "previews the changes before applying them" in caplog.text
    [note] = ctx.summary.notes  # repeated after the summary table, at the end of the run's output
    assert note.startswith("Triggers are paused on 3 migrated use cases") and "choose Enabled" in note
    assert not ctx.summary.needs_attention


def test_summary_note_is_silent_with_nothing_paused(tmp_path, caplog):
    ctx = make_ctx(tmp_path)
    with caplog.at_level(logging.INFO, logger="kit.load"):
        trigger_mode.summary_note(ctx, 0)
    assert caplog.text == ""
    assert ctx.summary.notes == []


def test_summary_note_in_dry_run_says_would(tmp_path, caplog):
    ctx = make_ctx(tmp_path, dry_run=True)
    with caplog.at_level(logging.INFO, logger="kit.load"):
        trigger_mode.summary_note(ctx, 1)
    assert "Triggers would be paused on 1 migrated use case," in caplog.text
