import logging

import pytest

from kit.clients.base import APIError
from kit.internal import policy_packs
from kit.tracking import load_extract, save_extract
from tests.fakes import FakeTenant, make_ctx

CONTROLS = [
    {
        "id": "P1.C1",
        "info": {"description": "Approval gate"},
        "evidence_requirements": [{"key": "P1.C1.R1", "question": "Approved?", "evidence_type": "yes_no"}],
    }
]


def base(key, ownership="custom", archived=False, name="Pack One", applies_to=()):
    return {
        "id": key,
        "attributes": {
            "ownership_type": ownership,
            "archived": archived,
            "applies_to": list(applies_to),
            "info": {"name": name, "description": "About it"},
        },
    }


def test_extract_keeps_live_custom_packs_with_base_info_and_latest_version(tmp_path):
    source_internal = FakeTenant(
        {
            "policy_pack_bases": [
                base("P1", applies_to=["use_case"]),
                base("P2", archived=True),
                base("EU", ownership="ootb"),
            ],
            "policy_pack_bases/P1/versions": [
                {"id": "P1+1", "attributes": {"version": 1, "draft": False, "info": {"source": "v1"}, "controls": []}},
                {
                    "id": "P1+2",
                    "attributes": {"version": 2, "draft": False, "info": {"source": "v2"}, "controls": CONTROLS},
                },
            ],
        },
        dry_run=True,
    )
    assert policy_packs.extract(make_ctx(tmp_path, source_internal=source_internal)) == 1
    [item] = load_extract(tmp_path, "policy_packs")
    assert item == {
        "id": "P1",
        "key": "P1",
        "version": 2,
        "draft": False,
        "base_info": {"name": "Pack One", "description": "About it"},
        "applies_to": ["use_case"],
        "version_info": {"source": "v2"},
        "controls": CONTROLS,
        "published_on_source": True,
    }


def item(key, name="Pack One"):
    return {
        "id": key,
        "key": key,
        "version": 2,
        "draft": False,
        "base_info": {"name": name},
        "version_info": {"source": "v2"},
        "controls": CONTROLS,
    }


def test_load_creates_base_with_info_and_version_with_controls(tmp_path):
    save_extract(tmp_path, "policy_packs", [item("P1")])
    ctx = make_ctx(tmp_path, target_internal=FakeTenant(jsonapi=True))
    policy_packs.load(ctx)
    (base_path, base_body), (version_path, version_body) = ctx.target_internal.posts
    assert base_path == "policy_pack_bases"
    assert base_body["data"]["attributes"] == {"id": "P1", "info": {"name": "Pack One"}}
    assert version_path == "policy_pack_bases/P1/versions"
    assert version_body["data"]["attributes"] == {"info": {"source": "v2"}, "controls": CONTROLS}
    assert ctx.target.posts == []


def test_pack_without_a_name_uses_its_key(tmp_path):
    save_extract(tmp_path, "policy_packs", [item("P1", name=None)])
    ctx = make_ctx(tmp_path, target_internal=FakeTenant(jsonapi=True))
    policy_packs.load(ctx)
    assert ctx.target_internal.posts[0][1]["data"]["attributes"]["info"]["name"] == "P1"


def test_existing_pack_key_is_matched_even_if_only_a_draft(tmp_path):
    save_extract(tmp_path, "policy_packs", [item("P1")])
    target_internal = FakeTenant(
        {
            "policy_pack_bases": [base("P1")],
            "policy_pack_bases/P1/versions": [{"id": "P1+1", "attributes": {"draft": True}}],
        },
        jsonapi=True,
    )
    ctx = make_ctx(tmp_path, target_internal=target_internal)
    policy_packs.load(ctx)
    assert target_internal.posts == []
    assert ctx.summary.to_dict()["policy_packs"]["matched"] == 1


def pack_version(n, **attrs):
    return {"id": f"P+{n}", "attributes": {"version": n, "draft": True, "info": {}, "controls": CONTROLS, **attrs}}


def test_extract_escapes_the_key_in_the_versions_path(tmp_path):
    source_internal = FakeTenant(
        {"policy_pack_bases": [base("A B/C")], "policy_pack_bases/A%20B%2FC/versions": [pack_version(1)]}, dry_run=True
    )
    assert policy_packs.extract(make_ctx(tmp_path, source_internal=source_internal)) == 1
    [extracted] = load_extract(tmp_path, "policy_packs")
    assert extracted["key"] == "A B/C"
    assert extracted["published_on_source"] is False


def test_extract_fails_loudly_when_controls_are_missing(tmp_path):
    v = pack_version(1)
    del v["attributes"]["controls"]
    source_internal = FakeTenant(
        {"policy_pack_bases": [base("P1")], "policy_pack_bases/P1/versions": [v]}, dry_run=True
    )
    with pytest.raises(APIError, match="'P1'.*controls"):
        policy_packs.extract(make_ctx(tmp_path, source_internal=source_internal))


def test_extract_names_the_key_when_reading_versions_fails(tmp_path):
    source_internal = FakeTenant({"policy_pack_bases": [base("P1")]}, dry_run=True)
    with pytest.raises(APIError, match="policy_packs: reading versions of 'P1' failed"):
        policy_packs.extract(make_ctx(tmp_path, source_internal=source_internal))


def test_dry_run_load_writes_nothing(tmp_path):
    save_extract(tmp_path, "policy_packs", [item("P1")])
    ctx = make_ctx(tmp_path, target_internal=FakeTenant(jsonapi=True), dry_run=True)
    policy_packs.load(ctx)
    assert ctx.target.posts == [] and ctx.target_internal.posts == []
    assert ctx.summary.to_dict()["policy_packs"]["created"] == 1


def test_extract_warns_about_risk_types_inside_pack_controls(tmp_path, caplog):
    controls = [
        dict(CONTROLS[0], risk_type_ids=["s-rt"]),
        dict(CONTROLS[0], id="P1.C2", risk_type_ids=[]),
        dict(CONTROLS[0], id="P1.C3", weight=2),
    ]
    source_internal = FakeTenant(
        {"policy_pack_bases": [base("P1")], "policy_pack_bases/P1/versions": [pack_version(1, controls=controls)]},
        dry_run=True,
    )
    with caplog.at_level(logging.WARNING, logger="kit.extract"):
        policy_packs.extract(make_ctx(tmp_path, source_internal=source_internal))
    assert "control 'P1.C1' in pack 'P1'" in caplog.text
    assert "risk type IDs inside pack controls aren't translated between tenants yet" in caplog.text
    assert "'P1.C2'" not in caplog.text
    assert "control 'P1.C3' in pack 'P1' has weight" in caplog.text


def test_load_sends_applies_to_for_a_targeted_pack(tmp_path):
    save_extract(tmp_path, "policy_packs", [dict(item("P1"), applies_to=["use_case"])])
    ctx = make_ctx(tmp_path, target_internal=FakeTenant(jsonapi=True))
    policy_packs.load(ctx)
    assert ctx.target_internal.posts[0][1]["data"]["attributes"]["applies_to"] == ["use_case"]


def test_extract_fails_loudly_when_applies_to_is_missing(tmp_path):
    b = base("P1")
    del b["attributes"]["applies_to"]
    source_internal = FakeTenant(
        {
            "policy_pack_bases": [b],
            "policy_pack_bases/P1/versions": [
                {"id": "P1+1", "attributes": {"version": 1, "draft": False, "info": {}, "controls": []}}
            ],
        },
        dry_run=True,
    )
    with pytest.raises(APIError, match="applies_to"):
        policy_packs.extract(make_ctx(tmp_path, source_internal=source_internal))
