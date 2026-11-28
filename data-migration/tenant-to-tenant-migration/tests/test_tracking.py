import json

import pytest

from kit.tracking import Tracker, load_extract, save_extract


def test_live_record_is_saved_and_reloaded(tmp_path):
    Tracker(tmp_path, "widgets", dry_run=False).record("s1", "t1", "Widget", "created")
    reloaded = Tracker(tmp_path, "widgets", dry_run=False)
    assert reloaded.target_id("s1") == "t1"
    assert json.loads((tmp_path / "loaded_widgets.json").read_text()) == [
        {"source_id": "s1", "target_id": "t1", "name_or_key": "Widget", "origin": "created"}
    ]


def test_dry_run_keeps_records_in_memory_only(tmp_path):
    t = Tracker(tmp_path, "widgets", dry_run=True)
    t.record("s1", "dry:s1", "Widget", "created")
    assert t.target_id("s1") == "dry:s1"
    assert not (tmp_path / "loaded_widgets.json").exists()


def test_recording_the_same_source_twice_is_an_error(tmp_path):
    t = Tracker(tmp_path, "widgets", dry_run=False)
    t.record("s1", "t1", "Widget", "matched")
    with pytest.raises(ValueError):
        t.record("s1", "t2", "Widget", "created")


def test_unknown_origin_is_an_error(tmp_path):
    with pytest.raises(ValueError):
        Tracker(tmp_path, "widgets", dry_run=False).record("s1", "t1", "Widget", "guessed")


def test_id_map(tmp_path):
    t = Tracker(tmp_path, "widgets", dry_run=False)
    t.record("s1", "t1", "A", "created")
    t.record("s2", "t2", "B", "matched")
    assert t.id_map() == {"s1": "t1", "s2": "t2"}


def test_extract_round_trip_and_missing_file_hint(tmp_path):
    save_extract(tmp_path, "widgets", [{"id": "s1"}])
    assert load_extract(tmp_path, "widgets") == [{"id": "s1"}]
    with pytest.raises(FileNotFoundError, match="python migrate.py extract gadgets"):
        load_extract(tmp_path, "gadgets")


def test_live_tracker_refuses_dry_run_placeholders(tmp_path):
    with pytest.raises(ValueError, match="dry-run placeholder"):
        Tracker(tmp_path, "widgets", dry_run=False).record("s1", "dry:s1", "Widget", "created")
