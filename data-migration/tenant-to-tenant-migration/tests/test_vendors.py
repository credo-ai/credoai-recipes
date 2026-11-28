import logging

import pytest

from kit.clients.base import APIError
from kit.entities import vendors
from kit.tracking import load_extract, save_extract
from tests.fakes import FakeResponse, FakeTenant, make_ctx


def integration_vendor(vid, name, status="none"):
    return {"id": vid, "entity_id": f"e-{vid}", "name": name, "description": f"About {name}", "status": status}


def internal_vendor(vid, custom=True, questionnaire_ids=()):
    return {"id": vid, "attributes": {"custom": custom, "questionnaire_ids": list(questionnaire_ids)}}


def source_ctx(tmp_path, integration, internal, values=None):
    collections = {"vendors": internal}
    for v in internal:
        collections[f"vendors/{v['id']}/custom_fields"] = (values or {}).get(v["id"], [])
    return make_ctx(
        tmp_path,
        source=FakeTenant({"vendors": integration}, dry_run=True),
        source_internal=FakeTenant(collections, dry_run=True, jsonapi=True),
    )


def cf_value(name, value):
    return {"id": f"cf-{name}", "attributes": {"name": name, "value": value}}


def test_extract_keeps_custom_vendors_in_the_item_shape(tmp_path):
    ctx = source_ctx(
        tmp_path,
        [integration_vendor("v1", "Acme"), integration_vendor("v2", "OpenAI")],
        [internal_vendor("v1", questionnaire_ids=["CR-VENDOR+1"]), internal_vendor("v2", custom=False)],
        values={"v1": [cf_value("Tier", "Gold"), cf_value("Empty", ""), cf_value("None", None), cf_value("List", [])]},
    )
    assert vendors.extract(ctx) == 1
    assert load_extract(tmp_path, "vendors") == [
        {
            "id": "v1",
            "name": "Acme",
            "description": "About Acme",
            "questionnaire_keys": ["CR-VENDOR"],
            "custom_fields": [{"name": "Tier", "value": "Gold"}],
        }
    ]


def test_extract_stops_when_custom_flag_is_missing(tmp_path):
    ctx = source_ctx(tmp_path, [integration_vendor("v1", "Acme")], [{"id": "v1", "attributes": {}}])
    with pytest.raises(APIError, match="custom"):
        vendors.extract(ctx)


def test_extract_stops_when_a_vendor_is_missing_from_the_internal_list(tmp_path):
    ctx = source_ctx(tmp_path, [integration_vendor("v1", "Acme")], [])
    with pytest.raises(APIError, match="internal"):
        vendors.extract(ctx)


def test_extract_warns_about_approval_status(tmp_path, caplog):
    ctx = source_ctx(tmp_path, [integration_vendor("v1", "Acme", status="approved")], [internal_vendor("v1")])
    with caplog.at_level(logging.WARNING, logger="kit.extract"):
        vendors.extract(ctx)
    assert "can't set approval status" in caplog.text


def item(vid, name, keys=(), fields=()):
    return {
        "id": vid,
        "name": name,
        "description": f"About {name}",
        "questionnaire_keys": list(keys),
        "custom_fields": list(fields),
    }


def versions(*pairs):
    return [{"version": n, "draft": draft} for n, draft in pairs]


def field(name, type_ids=()):
    return {"id": f"f-{name}", "name": name, "entity_type_ids": list(type_ids)}


def target_ctx(tmp_path, existing=(), json_bodies=None, fields=(), dry_run=False, patch_response=None):
    target = FakeTenant(
        {"vendors": list(existing), "custom_fields": list(fields)},
        json_bodies=json_bodies,
        patch_response=patch_response,
    )
    target_internal = FakeTenant(
        {
            "entity_types": [
                {"id": "et-vendor", "attributes": {"name": "vendor"}},
                {"id": "et-model", "attributes": {"name": "model"}},
            ]
        },
        jsonapi=True,
    )
    return make_ctx(tmp_path, target=target, target_internal=target_internal, dry_run=dry_run)


def test_load_attaches_the_latest_published_version(tmp_path):
    save_extract(tmp_path, "vendors", [item("v1", "Acme", keys=["CR-VENDOR"])])
    ctx = target_ctx(
        tmp_path, json_bodies={"questionnaires/CR-VENDOR/versions": versions((1, False), (2, False), (3, True))}
    )
    vendors.load(ctx)
    [(path, body)] = ctx.target.posts
    assert path == "vendors"
    assert body == {"name": "Acme", "description": "About Acme", "questionnaire_ids": ["CR-VENDOR+2"]}
    assert ctx.tracker("vendors").target_id("v1") == "new-1"


def test_load_attaches_version_one_when_it_is_the_only_published(tmp_path):
    save_extract(tmp_path, "vendors", [item("v1", "Acme", keys=["CR-VENDOR"])])
    ctx = target_ctx(tmp_path, json_bodies={"questionnaires/CR-VENDOR/versions": versions((1, False))})
    vendors.load(ctx)
    assert ctx.target.posts[0][1]["questionnaire_ids"] == ["CR-VENDOR+1"]


@pytest.mark.parametrize("body", [versions((1, True)), APIError("GET failed: 404", 404)])
def test_load_fails_the_vendor_without_a_published_version(tmp_path, caplog, body):
    save_extract(tmp_path, "vendors", [item("v1", "Acme", keys=["CR-VENDOR"])])
    ctx = target_ctx(tmp_path, json_bodies={"questionnaires/CR-VENDOR/versions": body})
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        vendors.load(ctx)
    assert ctx.target.posts == []
    assert ctx.summary.to_dict()["vendors"]["failed"] == 1
    assert "publish it on the target" in caplog.text


def test_load_fails_the_vendor_when_a_field_does_not_apply_to_vendors(tmp_path, caplog):
    save_extract(tmp_path, "vendors", [item("v1", "Acme", fields=[{"name": "Tier", "value": "Gold"}])])
    ctx = target_ctx(tmp_path, fields=[field("Tier", ["et-model"])])
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        vendors.load(ctx)
    assert ctx.target.posts == []
    assert ctx.summary.to_dict()["vendors"]["failed"] == 1
    assert "Tier" in caplog.text


def test_load_fails_the_vendor_when_a_field_is_not_on_the_target(tmp_path):
    save_extract(tmp_path, "vendors", [item("v1", "Acme", fields=[{"name": "Tier", "value": "Gold"}])])
    ctx = target_ctx(tmp_path)
    vendors.load(ctx)
    assert ctx.target.posts == []
    assert ctx.summary.to_dict()["vendors"]["failed"] == 1


def test_values_are_patched_after_the_create(tmp_path):
    values = [{"name": "Tier", "value": "Gold"}, {"name": "Region", "value": "EU"}]
    save_extract(tmp_path, "vendors", [item("v1", "Acme", fields=values)])
    ctx = target_ctx(tmp_path, fields=[field("Tier", ["et-vendor"]), field("Region", [])])
    vendors.load(ctx)
    [(_, body)] = ctx.target.posts
    assert "custom_fields" not in body
    assert ctx.target.patches == [("vendors/new-1", {"custom_fields": values})]
    assert ctx.summary.to_dict()["vendors"]["created"] == 1
    assert not ctx.summary.needs_attention


def test_no_patch_when_the_vendor_has_no_values(tmp_path):
    save_extract(tmp_path, "vendors", [item("v1", "Acme")])
    ctx = target_ctx(tmp_path)
    vendors.load(ctx)
    assert ctx.target.patches == []


@pytest.mark.parametrize("failure", ["status", "exception"])
def test_failed_patch_flags_but_keeps_the_vendor_tracked(tmp_path, caplog, failure):
    save_extract(tmp_path, "vendors", [item("v1", "Acme", fields=[{"name": "Tier", "value": "Gold"}])])
    ctx = target_ctx(tmp_path, fields=[field("Tier", [])], patch_response=FakeResponse(422, {"error": "bad value"}))
    if failure == "exception":

        def boom(path, json):
            raise APIError("PATCH failed: timeout")

        ctx.target.patch = boom
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        vendors.load(ctx)
    assert ctx.tracker("vendors").target_id("v1") == "new-1"
    assert ctx.summary.to_dict()["vendors"]["created"] == 1
    assert ctx.summary.needs_attention
    assert "'Acme'" in caplog.text and "Tier" in caplog.text


def test_matched_vendor_is_adopted_and_description_difference_warned(tmp_path, caplog):
    save_extract(tmp_path, "vendors", [item("v1", "Acme", fields=[{"name": "Tier", "value": "Gold"}])])
    existing = dict(integration_vendor("t1", "Acme"), description="Something else")
    ctx = target_ctx(tmp_path, existing=[existing], fields=[field("Tier", [])])
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        vendors.load(ctx)
    assert ctx.target.posts == [] and ctx.target.patches == []
    assert ctx.tracker("vendors").find("v1")["origin"] == "matched"
    assert "description" in caplog.text


def test_dry_run_writes_nothing_and_reports_created(tmp_path, caplog):
    save_extract(
        tmp_path, "vendors", [item("v1", "Acme", keys=["CR-VENDOR"], fields=[{"name": "Tier", "value": "Gold"}])]
    )
    ctx = target_ctx(
        tmp_path,
        json_bodies={"questionnaires/CR-VENDOR/versions": versions((1, False))},
        fields=[field("Tier", [])],
        dry_run=True,
    )
    with caplog.at_level(logging.INFO, logger="kit.load"):
        vendors.load(ctx)
    assert ctx.target.posts == [] and ctx.target.patches == []
    assert ctx.summary.to_dict()["vendors"]["created"] == 1
    assert "would set" in caplog.text and "Tier" in caplog.text


@pytest.mark.parametrize(
    "body",
    [{"versions": []}, [{"version": "1", "draft": False}], [{"version": 1}]],
    ids=["not-a-list", "string-version", "no-draft-key"],
)
def test_unexpected_versions_shape_raises_instead_of_reading_as_unpublished(tmp_path, body):
    save_extract(tmp_path, "vendors", [item("v1", "Acme", keys=["CR-VENDOR"])])
    ctx = target_ctx(tmp_path, json_bodies={"questionnaires/CR-VENDOR/versions": body})
    with pytest.raises(APIError, match="unexpected shape"):
        vendors.load(ctx)
    assert ctx.target.posts == []


def test_target_field_without_entity_type_ids_raises(tmp_path):
    save_extract(tmp_path, "vendors", [item("v1", "Acme")])
    ctx = target_ctx(tmp_path, fields=[{"id": "f-Tier", "name": "Tier"}])
    with pytest.raises(APIError, match="entity_type_ids"):
        vendors.load(ctx)
    assert ctx.target.posts == []


def test_failed_custom_field_patch_logs_the_status_only(tmp_path, caplog):
    save_extract(tmp_path, "vendors", [item("v1", "Acme", fields=[{"name": "Tier", "value": "Gold"}])])
    ctx = target_ctx(
        tmp_path, fields=[field("Tier", [])], patch_response=FakeResponse(422, text='{"value": "Gold is not allowed"}')
    )
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        vendors.load(ctx)
    assert "(422)" in caplog.text and "Gold" not in caplog.text
