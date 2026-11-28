import json
import logging

import pytest

from kit.logger import RunSummary, setup_logging


def test_log_file_redacts_tokens_and_keys(tmp_path):
    log, path = setup_logging("load_x", log_dir=tmp_path, secrets=["secret-key"])
    logging.getLogger("kit.http").debug("headers Authorization: Bearer abc.def-123 key=secret-key")
    for h in log.handlers:
        h.flush()
    text = path.read_text()
    assert "abc.def-123" not in text
    assert "secret-key" not in text
    assert text.count("[REDACTED]") == 2


def test_setup_logging_twice_does_not_duplicate_handlers(tmp_path):
    setup_logging("a", log_dir=tmp_path)
    log, _ = setup_logging("b", log_dir=tmp_path)
    assert len(log.handlers) == 2


def test_summary_counts_table_and_failures(tmp_path):
    s = RunSummary()
    s.record("risk_types", "created")
    s.record("risk_types", "matched")
    assert not s.has_failures
    s.record("custom_fields", "failed")
    assert s.has_failures
    assert "risk_types" in s.table() and "failed" in s.table()
    s.save(tmp_path / "summary.json")
    saved = json.loads((tmp_path / "summary.json").read_text())
    assert saved["risk_types"] == {"created": 1, "matched": 1, "skipped": 0, "failed": 0}


def test_summary_rejects_unknown_outcome():
    with pytest.raises(ValueError):
        RunSummary().record("risk_types", "exploded")


def test_summary_attention_counts_as_a_problem_and_is_shown_and_saved(tmp_path):
    s = RunSummary()
    s.record("questionnaires", "created")
    assert not s.needs_attention
    s.flag("questionnaires", "'K': set its Visibility to Not visible")
    assert s.needs_attention and not s.has_failures
    assert s.to_dict()["questionnaires"]["created"] == 1
    table = s.table()
    assert table.index("Needs attention") > table.index("questionnaires")
    assert "questionnaires: 'K': set its Visibility to Not visible" in table
    s.save(tmp_path / "summary.json")
    saved = json.loads((tmp_path / "summary.json").read_text())
    assert saved["needs_attention"] == [
        {"entity": "questionnaires", "message": "'K': set its Visibility to Not visible"}
    ]
    assert saved["questionnaires"]["created"] == 1


def test_summary_without_attention_saves_no_attention_key(tmp_path):
    s = RunSummary()
    s.record("questionnaires", "created")
    assert "Needs attention" not in s.table()
    s.save(tmp_path / "summary.json")
    assert "needs_attention" not in json.loads((tmp_path / "summary.json").read_text())


def test_summary_notes_are_shown_after_the_table_and_saved_but_are_not_problems(tmp_path):
    s = RunSummary()
    s.record("use_cases", "created")
    s.note("Triggers are paused on 1 migrated use case")
    assert not s.needs_attention and not s.has_failures
    table = s.table()
    assert table.index("Notes:") > table.index("use_cases")
    assert "  Triggers are paused on 1 migrated use case" in table
    s.save(tmp_path / "summary.json")
    assert json.loads((tmp_path / "summary.json").read_text())["notes"] == [
        "Triggers are paused on 1 migrated use case"
    ]


def test_summary_without_notes_shows_and_saves_none(tmp_path):
    s = RunSummary()
    s.record("use_cases", "created")
    assert "Notes:" not in s.table()
    s.save(tmp_path / "summary.json")
    assert "notes" not in json.loads((tmp_path / "summary.json").read_text())


def test_flag_drops_a_repeated_entity_prefix():
    from kit.logger import RunSummary

    s = RunSummary()
    s.flag("use_cases", "use_cases: 'A': something to fix")
    s.flag("use_cases", "'B': something else")
    assert [a["message"] for a in s.attention] == ["'A': something to fix", "'B': something else"]
