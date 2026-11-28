import logging

import pytest

from kit.clients.base import APIError
from kit.internal import risk_type_weights
from kit.tracking import load_extract, save_extract
from tests.fakes import FakeResponse, FakeTenant, make_ctx


def config(weights):
    return {
        "data": {
            "id": "cfg1",
            "type": "risk_score_calculation_configuration",
            "attributes": {"enabled": True, "formula_type": "weighted_average", "formula_config": {"weights": weights}},
        }
    }


def body(weights):
    return {
        "data": {"type": "risk_score_calculation_configuration", "attributes": {"formula_config": {"weights": weights}}}
    }


SOURCE_WEIGHTS = {
    "s-priv": {"name": "Privacy", "value": "0.0000"},
    "s-ev": {"name": "Policy Evidence", "value": "100.0000"},
}
EXTRACTED = [
    {"id": "s-ev", "risk_type": "Policy Evidence", "weight": "100.0000"},
    {"id": "s-priv", "risk_type": "Privacy", "weight": "0.0000"},
]
TARGET_TYPES = [
    {"id": "t-priv", "name": "Privacy", "hidden": False},
    {"id": "t-ev", "name": "Policy Evidence", "hidden": False},
]


def target_ctx(tmp_path, types=TARGET_TYPES, current=None, dry_run=False, patch_response=None, items=EXTRACTED):
    target = FakeTenant({"risk_types": types})
    bodies = (
        {"risk_scoring_config": config(current)}
        if current is not None
        else {"risk_scoring_config": APIError("GET risk_scoring_config failed: 404", 404)}
    )
    target_internal = FakeTenant(json_bodies=bodies, patch_response=patch_response)
    ctx = make_ctx(tmp_path, target=target, target_internal=target_internal, dry_run=dry_run)
    save_extract(tmp_path, "risk_type_weights", items)
    return ctx


def test_extract_saves_weights_by_risk_type_name(tmp_path):
    source_internal = FakeTenant(json_bodies={"risk_scoring_config": config(SOURCE_WEIGHTS)}, dry_run=True)
    ctx = make_ctx(tmp_path, source_internal=source_internal, dry_run=True)
    assert risk_type_weights.extract(ctx) == 2
    assert load_extract(tmp_path, "risk_type_weights") == EXTRACTED


def test_extract_skips_a_hidden_or_deleted_risk_type_and_warns_when_it_has_weight(tmp_path, caplog):
    weights = {**SOURCE_WEIGHTS, "gone0": "0.0000", "gone5": "5"}
    source_internal = FakeTenant(json_bodies={"risk_scoring_config": config(weights)}, dry_run=True)
    ctx = make_ctx(tmp_path, source_internal=source_internal, dry_run=True)
    with caplog.at_level(logging.WARNING, logger="kit.extract"):
        assert risk_type_weights.extract(ctx) == 2
    assert "weight 5% is on risk type gone5" in caplog.text and "gone0" not in caplog.text


def test_extract_with_no_source_configuration_saves_nothing(tmp_path):
    source_internal = FakeTenant(json_bodies={"risk_scoring_config": APIError("404", 404)}, dry_run=True)
    ctx = make_ctx(tmp_path, source_internal=source_internal, dry_run=True)
    assert risk_type_weights.extract(ctx) == 0
    assert load_extract(tmp_path, "risk_type_weights") == []


def test_extract_raises_on_an_unexpected_shape(tmp_path):
    source_internal = FakeTenant(json_bodies={"risk_scoring_config": {"data": {"attributes": {}}}}, dry_run=True)
    with pytest.raises(APIError, match="no weights"):
        risk_type_weights.extract(make_ctx(tmp_path, source_internal=source_internal, dry_run=True))


def test_load_remaps_weights_and_sets_target_only_types_to_zero(tmp_path):
    types = TARGET_TYPES + [
        {"id": "t-new", "name": "Target Only", "hidden": False},
        {"id": "t-hid", "name": "Hidden One", "hidden": True},
    ]
    current = {"t-priv": {"name": "Privacy", "value": "50"}, "t-ev": {"name": "Policy Evidence", "value": "50"}}
    ctx = target_ctx(tmp_path, types=types, current=current)
    risk_type_weights.load(ctx)
    assert ctx.target_internal.patches == [
        ("risk_scoring_config", body({"t-ev": "100.0000", "t-priv": "0.0000", "t-new": "0"}))
    ]
    assert ctx.summary.to_dict()["risk_type_weights"]["created"] == 1


def test_load_leaves_out_a_zero_weight_when_the_count_would_make_a_rounded_total_fail(tmp_path):
    # 18 x 5.5556 = 100.0008: fine with 19 weights, rejected with 20 (100 divides evenly, so no tolerance)
    items = [{"id": f"s{n}", "risk_type": f"Type {n}", "weight": "5.5556"} for n in range(18)]
    types = [{"id": f"t{n}", "name": f"Type {n}", "hidden": False} for n in range(20)]
    ctx = target_ctx(tmp_path, types=types, current={}, items=items)
    risk_type_weights.load(ctx)
    [(_, sent)] = ctx.target_internal.patches
    weights = sent["data"]["attributes"]["formula_config"]["weights"]
    assert len(weights) == 19 and weights["t18"] == "0" and "t19" not in weights


def test_load_keeps_every_zero_weight_when_the_total_is_exact(tmp_path):
    items = [{"id": f"s{n}", "risk_type": f"Type {n}", "weight": "25"} for n in range(4)]
    types = [{"id": f"t{n}", "name": f"Type {n}", "hidden": False} for n in range(5)]
    ctx = target_ctx(tmp_path, types=types, current={}, items=items)
    risk_type_weights.load(ctx)
    [(_, sent)] = ctx.target_internal.patches
    assert sent["data"]["attributes"]["formula_config"]["weights"]["t4"] == "0"


def test_load_logs_the_weights_it_replaces(tmp_path, caplog):
    current = {"t-priv": {"name": "Privacy", "value": "50.0000"}, "t-ev": {"name": "Policy Evidence", "value": "50"}}
    ctx = target_ctx(tmp_path, current=current)
    with caplog.at_level(logging.INFO, logger="kit.load"):
        risk_type_weights.load(ctx)
    assert "set the target's weights to match the source (Policy Evidence 100%)" in caplog.text
    assert "non-zero weights were: Policy Evidence 50%, Privacy 50%" in caplog.text


def test_load_skips_when_the_target_already_matches(tmp_path):
    current = {"t-priv": {"name": "Privacy", "value": "0"}, "t-ev": {"name": "Policy Evidence", "value": "100"}}
    ctx = target_ctx(tmp_path, current=current)
    risk_type_weights.load(ctx)
    assert ctx.target_internal.patches == []
    assert ctx.summary.to_dict()["risk_type_weights"]["skipped"] == 1


def test_load_writes_when_the_target_has_no_configuration(tmp_path):
    ctx = target_ctx(tmp_path)
    risk_type_weights.load(ctx)
    assert ctx.target_internal.patches == [("risk_scoring_config", body({"t-ev": "100.0000", "t-priv": "0.0000"}))]


@pytest.mark.parametrize(
    "types, problem",
    [
        ([TARGET_TYPES[0]], "'Policy Evidence' (weight 100%) isn't on the target"),
        (
            [TARGET_TYPES[0], dict(TARGET_TYPES[1], hidden=True)],
            "'Policy Evidence' (weight 100%) is hidden on the target",
        ),
        (TARGET_TYPES + [{"id": "t-ev2", "name": "Policy Evidence", "hidden": False}], "2 target items named"),
    ],
    ids=["missing", "hidden", "ambiguous"],
)
def test_load_stops_when_a_weighted_risk_type_cant_be_matched(tmp_path, caplog, types, problem):
    ctx = target_ctx(tmp_path, types=types, current={})
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        risk_type_weights.load(ctx)
    assert problem in caplog.text and "must total 100%" in caplog.text
    assert ctx.target_internal.patches == []
    assert ctx.summary.to_dict()["risk_type_weights"]["failed"] == 1


def test_load_leaves_out_a_missing_risk_type_with_zero_weight(tmp_path):
    ctx = target_ctx(tmp_path, types=[TARGET_TYPES[1]], current={})
    risk_type_weights.load(ctx)
    assert ctx.target_internal.patches == [("risk_scoring_config", body({"t-ev": "100.0000"}))]


def test_load_uses_risk_types_created_earlier_in_a_dry_run(tmp_path, caplog):
    ctx = target_ctx(tmp_path, types=[TARGET_TYPES[0]], current={}, dry_run=True)
    ctx.tracker("risk_types").record("s-ev", "dry:s-ev", "Policy Evidence", "created")
    with caplog.at_level(logging.INFO, logger="kit.load"):
        risk_type_weights.load(ctx)
    assert "would set the target's weights" in caplog.text
    assert ctx.target_internal.patches == []


def test_load_reports_a_rejected_write(tmp_path, caplog):
    ctx = target_ctx(tmp_path, current={}, patch_response=FakeResponse(422, text='{"errors": "weights must total"}'))
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        risk_type_weights.load(ctx)
    assert "setting the weights failed: 422" in caplog.text
    assert ctx.summary.to_dict()["risk_type_weights"]["failed"] == 1


def test_load_with_nothing_extracted_does_nothing(tmp_path):
    ctx = target_ctx(tmp_path, current={}, items=[])
    risk_type_weights.load(ctx)
    assert ctx.target_internal.patches == [] and ctx.summary.to_dict() == {}


def test_load_scales_weights_left_short_by_hidden_source_risk_types(tmp_path, caplog):
    # 11 equal weights on the source, 7 of them on hidden risk types: the 4 kept total 36.36...%
    items = [{"id": f"s{n}", "risk_type": f"Type {n}", "weight": "9.0909"} for n in range(4)]
    types = [{"id": f"t{n}", "name": f"Type {n}", "hidden": False} for n in range(4)]
    ctx = target_ctx(tmp_path, types=types, current={}, items=items)
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        risk_type_weights.load(ctx)
    [(_, sent)] = ctx.target_internal.patches
    weights = sent["data"]["attributes"]["formula_config"]["weights"]
    assert sorted(weights.values()) == ["25.0000"] * 4
    assert "total 36.3636%" in caplog.text and "scaled to total 100%" in caplog.text


def test_load_scaling_rounds_to_an_exact_total(tmp_path):
    items = [{"id": f"s{n}", "risk_type": f"Type {n}", "weight": "10"} for n in range(3)]
    types = [{"id": f"t{n}", "name": f"Type {n}", "hidden": False} for n in range(3)]
    ctx = target_ctx(tmp_path, types=types, current={}, items=items)
    risk_type_weights.load(ctx)
    [(_, sent)] = ctx.target_internal.patches
    from decimal import Decimal

    weights = sent["data"]["attributes"]["formula_config"]["weights"]
    assert sum(Decimal(w) for w in weights.values()) == 100


def test_load_with_only_zero_weights_left_fails(tmp_path, caplog):
    ctx = target_ctx(tmp_path, current={}, items=[{"id": "s1", "risk_type": "Privacy", "weight": "0"}])
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        risk_type_weights.load(ctx)
    assert ctx.target_internal.patches == []
    assert "set the weighting on the target by hand" in caplog.text
    assert ctx.summary.to_dict()["risk_type_weights"]["failed"] == 1
