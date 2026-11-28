import logging

import pytest

from kit.clients.base import APIError
from kit.loading import MissingReference
from kit.risk_type_refs import add_risk_type_names, target_ids, target_index
from tests.fakes import FakeTenant, make_ctx


def test_names_follow_ids_and_unknown_ids_are_marked():
    item = {"risk_type_ids": ["s1", "zz"]}
    add_risk_type_names(item, {"s1": "Privacy"})
    assert item["risk_type_names"] == ["Privacy", "unknown:zz"]


def test_target_ids_by_name_including_items_created_earlier_in_the_run(tmp_path):
    ctx = make_ctx(
        tmp_path, target=FakeTenant({"risk_types": [{"id": "t1", "name": "Privacy"}]}, dry_run=True), dry_run=True
    )
    ctx.tracker("risk_types").record("s9", "dry:s9", "Agent Risk", "created")
    assert target_ids(["Privacy", "Agent Risk"], target_index(ctx), "x") == ["t1", "dry:s9"]


def test_missing_risk_type_raises(tmp_path):
    ctx = make_ctx(tmp_path)
    with pytest.raises(MissingReference, match="'Privacy'"):
        target_ids(["Privacy"], target_index(ctx), "x")


def test_live_record_gone_from_target_matches_by_name(tmp_path, caplog):
    ctx = make_ctx(tmp_path, target=FakeTenant({"risk_types": [{"id": "t2", "name": "Agent Risk"}]}))
    ctx.tracker("risk_types").record("s9", "t-old", "Agent Risk", "created")
    with caplog.at_level(logging.WARNING):
        assert target_ids(["Agent Risk"], target_index(ctx), "x") == ["t2"]
    assert "isn't on the target any more" in caplog.text


def test_live_record_gone_from_target_without_a_namesake_is_missing(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.tracker("risk_types").record("s9", "t-old", "Agent Risk", "created")
    with pytest.raises(MissingReference, match="'Agent Risk'"):
        target_ids(["Agent Risk"], target_index(ctx), "x")


def test_risk_type_missing_on_the_source_is_dropped_with_a_warning(tmp_path, caplog):
    ctx = make_ctx(tmp_path, target=FakeTenant({"risk_types": [{"id": "t1", "name": "Privacy"}]}))
    with caplog.at_level(logging.WARNING):
        assert target_ids(["unknown:zz", "Privacy"], target_index(ctx), "controls: 'c1'") == ["t1"]
    assert "controls: 'c1'" in caplog.text and "zz" in caplog.text and "without it" in caplog.text


def test_missing_risk_type_ids_key_raises():
    with pytest.raises(APIError, match="'x1'.*risk_type_ids"):
        add_risk_type_names({"id": "x1"}, {})


def test_null_risk_type_ids_mean_none():
    item = {"id": "x1", "risk_type_ids": None}
    add_risk_type_names(item, {})
    assert item["risk_type_names"] == []


def test_two_source_risk_types_with_one_name_share_a_dry_placeholder(tmp_path):
    ctx = make_ctx(tmp_path, dry_run=True)
    ctx.tracker("risk_types").record("s1", "dry:s1", "Agent Risk", "created")
    ctx.tracker("risk_types").record("s2", "dry:s1", "Agent Risk", "matched")
    assert target_index(ctx).lookup("Agent Risk") == "dry:s1"
