import logging

import pytest

from kit.clients.base import APIError
from kit.internal import controls
from kit.tracking import load_extract, save_extract
from tests.fakes import FakeTenant, make_ctx

REQUIREMENT = {"key": "b5a5", "question": "Run the evaluator?", "evidence_type": "metric", "code_template": "x=1"}


def version(key, n, draft, risk_type_ids=()):
    return {
        "id": f"{key}+{n}",
        "attributes": {
            "version": n,
            "draft": draft,
            "info": {"description": f"{key} v{n}"},
            "evidence_requirements": [REQUIREMENT],
            "risk_type_ids": list(risk_type_ids),
        },
    }


def internal_base(key, applies_to=()):
    return {"id": key, "attributes": {"ownership_type": "custom", "applies_to": list(applies_to)}}


def source_ctx(tmp_path):
    bases = [
        {"id": "C1", "ownership_type": "custom", "archived": False},
        {"id": "C2", "ownership_type": "custom", "archived": True},
        {"id": "OOTB1", "ownership_type": "ootb", "archived": False},
        {"id": "OLD1", "ownership_type": "transferred", "archived": True},
    ]
    source = FakeTenant(
        {"policy_control_bases": bases, "risk_types": [{"id": "s-rt", "name": "Privacy"}]}, dry_run=True
    )
    source_internal = FakeTenant(
        {
            "policy_control_bases": [internal_base("C1", ["use_case"])],
            "policy_control_bases/C1/versions": [version("C1", 1, False), version("C1", 2, True, ["s-rt"])],
        },
        dry_run=True,
    )
    return make_ctx(tmp_path, source=source, source_internal=source_internal)


def test_extract_keeps_live_custom_controls_at_their_latest_version(tmp_path):
    assert controls.extract(source_ctx(tmp_path)) == 1
    [item] = load_extract(tmp_path, "controls")
    assert item == {
        "id": "C1",
        "key": "C1",
        "version": 2,
        "draft": True,
        "info": {"description": "C1 v2"},
        "evidence_requirements": [REQUIREMENT],
        "risk_type_ids": ["s-rt"],
        "risk_type_names": ["Privacy"],
        "applies_to": ["use_case"],
        "published_on_source": True,
    }


def item(key, names=("Privacy",), draft=False):
    return {
        "id": key,
        "key": key,
        "version": 1,
        "draft": draft,
        "info": {"description": key},
        "evidence_requirements": [REQUIREMENT],
        "risk_type_ids": ["s-rt"],
        "risk_type_names": list(names),
    }


def target_ctx(tmp_path, bases=(), archived=(), dry_run=False):
    target = FakeTenant(
        {
            "risk_types": [{"id": "t-rt", "name": "Privacy"}],
            "policy_control_bases": [{"id": b, "archived": b in archived} for b in bases],
        }
    )
    # An existing key is adopted only if it has a version (load_versioned's half-made check)
    versions = {f"policy_control_bases/{b}/versions": [{"id": f"{b}+1", "attributes": {}}] for b in bases}
    return make_ctx(tmp_path, target=target, target_internal=FakeTenant(versions, jsonapi=True), dry_run=dry_run)


def test_load_creates_base_and_version_through_the_internal_api(tmp_path):
    save_extract(tmp_path, "controls", [item("C1")])
    ctx = target_ctx(tmp_path)
    controls.load(ctx)
    (base_path, base), (version_path, version) = ctx.target_internal.posts
    assert (base_path, base) == (
        "policy_control_bases",
        {"data": {"type": "policy_control_bases", "attributes": {"id": "C1"}}},
    )
    assert version_path == "policy_control_bases/C1/versions"
    assert version["data"]["attributes"] == {
        "info": {"description": "C1"},
        "evidence_requirements": [REQUIREMENT],
        "risk_type_ids": ["t-rt"],
    }
    assert ctx.target.posts == []


def test_control_whose_risk_type_is_missing_fails(tmp_path):
    save_extract(tmp_path, "controls", [item("C1", names=["Not There"])])
    ctx = target_ctx(tmp_path)
    controls.load(ctx)
    assert ctx.target_internal.posts == []
    assert ctx.summary.to_dict()["controls"]["failed"] == 1


def test_existing_control_key_is_matched(tmp_path):
    save_extract(tmp_path, "controls", [item("C1")])
    ctx = target_ctx(tmp_path, bases=["C1"])
    controls.load(ctx)
    assert ctx.target_internal.posts == []
    assert ctx.summary.to_dict()["controls"]["matched"] == 1


def one_base_ctx(tmp_path, key, versions):
    source = FakeTenant(
        {"policy_control_bases": [{"id": key, "ownership_type": "custom", "archived": False}], "risk_types": []},
        dry_run=True,
    )
    versions = {**versions, "policy_control_bases": [internal_base(key)]}
    return make_ctx(tmp_path, source=source, source_internal=FakeTenant(versions, dry_run=True))


def test_extract_escapes_the_key_in_the_versions_path(tmp_path):
    ctx = one_base_ctx(tmp_path, "A B/C", {"policy_control_bases/A%20B%2FC/versions": [version("A B/C", 1, False)]})
    assert controls.extract(ctx) == 1
    assert load_extract(tmp_path, "controls")[0]["key"] == "A B/C"


def test_extract_fails_loudly_when_requirements_are_missing(tmp_path):
    v = version("C1", 1, False)
    del v["attributes"]["evidence_requirements"]
    ctx = one_base_ctx(tmp_path, "C1", {"policy_control_bases/C1/versions": [v]})
    with pytest.raises(APIError, match="'C1'.*evidence_requirements"):
        controls.extract(ctx)


def test_extract_treats_null_requirements_as_none(tmp_path):
    v = version("C1", 1, False)
    v["attributes"]["evidence_requirements"] = None
    ctx = one_base_ctx(tmp_path, "C1", {"policy_control_bases/C1/versions": [v]})
    controls.extract(ctx)
    assert load_extract(tmp_path, "controls")[0]["evidence_requirements"] == []


def test_extract_names_the_key_when_reading_versions_fails(tmp_path):
    ctx = one_base_ctx(tmp_path, "C1", {})  # the fake returns 404 for an unseeded path
    with pytest.raises(APIError, match="controls: reading versions of 'C1' failed") as e:
        controls.extract(ctx)
    assert e.value.status_code == 404


def test_extract_reports_published_on_source_from_any_version(tmp_path):
    ctx = one_base_ctx(
        tmp_path, "C1", {"policy_control_bases/C1/versions": [version("C1", 1, True), version("C1", 2, True)]}
    )
    controls.extract(ctx)
    assert load_extract(tmp_path, "controls")[0]["published_on_source"] is False


def test_archived_existing_control_is_matched_with_a_warning(tmp_path, caplog):
    save_extract(tmp_path, "controls", [item("C1"), item("C2")])
    ctx = target_ctx(tmp_path, bases=["C1", "C2", "C3"], archived=["C1", "C3"])
    with caplog.at_level(logging.WARNING):
        controls.load(ctx)
    assert "controls: 'C1' exists on the target but is archived; it will be matched as is" in caplog.text
    assert "'C2' exists on the target but is archived" not in caplog.text
    assert "'C3'" not in caplog.text
    assert ctx.summary.to_dict()["controls"]["matched"] == 2


def test_dry_run_load_writes_nothing(tmp_path):
    save_extract(tmp_path, "controls", [item("C1")])
    ctx = target_ctx(tmp_path, dry_run=True)
    controls.load(ctx)
    assert ctx.target.posts == [] and ctx.target_internal.posts == []
    assert ctx.summary.to_dict()["controls"]["created"] == 1


def test_extract_fails_loudly_when_risk_type_ids_are_missing(tmp_path):
    v = version("C1", 1, False)
    del v["attributes"]["risk_type_ids"]
    ctx = one_base_ctx(tmp_path, "C1", {"policy_control_bases/C1/versions": [v]})
    with pytest.raises(APIError, match="'C1'.*risk_type_ids"):
        controls.extract(ctx)


def test_extract_treats_null_risk_type_ids_as_none(tmp_path):
    v = version("C1", 1, False)
    v["attributes"]["risk_type_ids"] = None
    ctx = one_base_ctx(tmp_path, "C1", {"policy_control_bases/C1/versions": [v]})
    controls.extract(ctx)
    [extracted] = load_extract(tmp_path, "controls")
    assert extracted["risk_type_ids"] == [] and extracted["risk_type_names"] == []


def test_archived_warning_logs_under_kit_load(tmp_path, caplog):
    save_extract(tmp_path, "controls", [item("C1")])
    ctx = target_ctx(tmp_path, bases=["C1"], archived=["C1"])
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        controls.load(ctx)
    assert [r.name for r in caplog.records if "archived" in r.getMessage()] == ["kit.load"]


def test_load_sends_applies_to_on_the_base_of_a_targeted_control(tmp_path):
    save_extract(tmp_path, "controls", [dict(item("C1"), applies_to=["use_case", "model"])])
    ctx = target_ctx(tmp_path)
    controls.load(ctx)
    assert ctx.target_internal.posts[0] == (
        "policy_control_bases",
        {"data": {"type": "policy_control_bases", "attributes": {"id": "C1", "applies_to": ["use_case", "model"]}}},
    )
