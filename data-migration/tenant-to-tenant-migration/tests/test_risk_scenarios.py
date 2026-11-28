import logging

from kit.internal import risk_scenarios
from kit.tracking import load_extract, save_extract
from tests.fakes import FakeTenant, make_ctx


def scenario(sid, name, ownership="custom", archived=False, risk_type_ids=()):
    return {
        "id": sid,
        "name": name,
        "description": f"About {name}",
        "author": None,
        "domains": [],
        "industries": [],
        "regions": [],
        "risk_type_ids": list(risk_type_ids),
        "ownership_type": ownership,
        "archived": archived,
        "deprecated": False,
        "entity_type_ids": [],
    }


def test_extract_keeps_live_custom_scenarios_with_risk_type_names(tmp_path):
    source = FakeTenant(
        {
            "risk_scenarios": [
                dict(scenario("s1", "Toxic content", risk_type_ids=["rt1"]), entity_type_ids=["et-uc"]),
                scenario("s2", "Old", archived=True),
                scenario("s3", "Bias", ownership="ootb"),
            ],
            "risk_types": [{"id": "rt1", "name": "Safety"}],
        },
        dry_run=True,
    )
    source_internal = FakeTenant({"entity_types": [{"id": "et-uc", "attributes": {"name": "use_case"}}]}, dry_run=True)
    assert risk_scenarios.extract(make_ctx(tmp_path, source=source, source_internal=source_internal)) == 1
    [item] = load_extract(tmp_path, "risk_scenarios")
    assert item["name"] == "Toxic content" and item["risk_type_names"] == ["Safety"]
    assert item["applies_to"] == ["use_case"]


def item(sid, name, names=("Safety",)):
    return dict(scenario(sid, name), risk_type_names=list(names))


def target_ctx(tmp_path, existing=(), dry_run=False):
    target = FakeTenant({"risk_types": [{"id": "t-rt", "name": "Safety"}], "risk_scenarios": list(existing)})
    return make_ctx(tmp_path, target=target, target_internal=FakeTenant(jsonapi=True), dry_run=dry_run)


def test_creates_custom_scenario_even_when_a_built_in_has_the_same_name(tmp_path, caplog):
    save_extract(tmp_path, "risk_scenarios", [item("s1", "Toxic content")])
    ctx = target_ctx(tmp_path, existing=[scenario("b1", "Toxic content", ownership="ootb")])
    with caplog.at_level(logging.INFO, logger="kit.load"):
        risk_scenarios.load(ctx)
    assert "a built-in scenario on the target is also named 'Toxic content'" in caplog.text
    assert ctx.target.posts == []
    [(path, body)] = ctx.target_internal.posts
    assert path == "custom_risk_scenarios"
    assert body["data"]["attributes"] == {
        "name": "Toxic content",
        "description": "About Toxic content",
        "author": None,
        "domains": [],
        "industries": [],
        "regions": [],
        "risk_type_ids": ["t-rt"],
    }
    assert ctx.tracker("risk_scenarios").target_id("s1") == "new-1"


def test_scenario_without_risk_types_omits_risk_type_ids(tmp_path):
    save_extract(tmp_path, "risk_scenarios", [item("s1", "Toxic content", names=())])
    ctx = target_ctx(tmp_path)
    risk_scenarios.load(ctx)
    [(path, body)] = ctx.target_internal.posts
    assert path == "custom_risk_scenarios"
    assert body["data"]["attributes"] == {
        "name": "Toxic content",
        "description": "About Toxic content",
        "author": None,
        "domains": [],
        "industries": [],
        "regions": [],
    }
    assert ctx.tracker("risk_scenarios").target_id("s1") == "new-1"


def test_same_named_custom_scenario_on_target_is_adopted(tmp_path):
    save_extract(tmp_path, "risk_scenarios", [item("s1", "Toxic content")])
    ctx = target_ctx(tmp_path, existing=[scenario("c1", "Toxic content")])
    risk_scenarios.load(ctx)
    assert ctx.target_internal.posts == []
    assert ctx.tracker("risk_scenarios").find("s1")["origin"] == "matched"


def test_missing_risk_type_fails_the_scenario(tmp_path):
    save_extract(tmp_path, "risk_scenarios", [item("s1", "Toxic content", names=["Nope"])])
    ctx = target_ctx(tmp_path)
    risk_scenarios.load(ctx)
    assert ctx.summary.to_dict()["risk_scenarios"]["failed"] == 1


def test_archived_custom_scenario_on_target_is_adopted_with_a_warning(tmp_path, caplog):
    save_extract(tmp_path, "risk_scenarios", [item("s1", "Toxic content")])
    ctx = target_ctx(
        tmp_path, existing=[scenario("c1", "Toxic content", archived=True), scenario("c2", "Other", archived=True)]
    )
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        risk_scenarios.load(ctx)
    assert (
        "risk_scenarios: 'Toxic content' exists on the target but is archived; it will be matched as is" in caplog.text
    )
    assert "'Other'" not in caplog.text
    assert ctx.tracker("risk_scenarios").find("s1")["origin"] == "matched"


def test_dry_run_load_writes_nothing(tmp_path):
    save_extract(tmp_path, "risk_scenarios", [item("s1", "Toxic content")])
    ctx = target_ctx(tmp_path, dry_run=True)
    risk_scenarios.load(ctx)
    assert ctx.target.posts == [] and ctx.target_internal.posts == []
    assert ctx.summary.to_dict()["risk_scenarios"]["created"] == 1


def test_second_run_skips_tracked_scenarios(tmp_path):
    save_extract(tmp_path, "risk_scenarios", [item("s1", "Toxic content")])
    risk_scenarios.load(target_ctx(tmp_path))
    ctx = target_ctx(tmp_path)
    risk_scenarios.load(ctx)
    assert ctx.target.posts == [] and ctx.target_internal.posts == []
    assert ctx.summary.to_dict()["risk_scenarios"]["skipped"] == 1


def test_load_sends_applies_to_for_a_targeted_scenario(tmp_path):
    save_extract(tmp_path, "risk_scenarios", [dict(item("s1", "Toxic content"), applies_to=["use_case"])])
    ctx = target_ctx(tmp_path)
    risk_scenarios.load(ctx)
    [(_, body)] = ctx.target_internal.posts
    assert body["data"]["attributes"]["applies_to"] == ["use_case"]


def test_extract_fails_on_an_entity_type_the_source_doesnt_list(tmp_path):
    import pytest

    from kit.clients.base import APIError

    source = FakeTenant(
        {"risk_scenarios": [dict(scenario("s1", "Toxic content"), entity_type_ids=["gone"])], "risk_types": []},
        dry_run=True,
    )
    source_internal = FakeTenant({"entity_types": []}, dry_run=True)
    with pytest.raises(APIError, match="doesn't list"):
        risk_scenarios.extract(make_ctx(tmp_path, source=source, source_internal=source_internal))
