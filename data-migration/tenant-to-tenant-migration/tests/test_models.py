import logging

import pytest

from kit.clients.base import APIError
from kit.entities import models
from kit.tracking import load_extract, save_extract
from tests.fakes import FakeResponse, FakeTenant, make_ctx

# --- extract ---


def integration_model(mid, name, custom=True, status="none", source="Example Source", questionnaire_ids=()):
    return {
        "id": mid,
        "entity_id": f"e-{mid}",
        "name": name,
        "summary": f"About {name}",
        "source": source,
        "status": status,
        "custom": custom,
        "has_use_cases": False,
        "questionnaire_ids": list(questionnaire_ids),
    }


def cf_value(name, value):
    return {"id": f"cf-{name}", "attributes": {"name": name, "value": value}}


def source_ctx(tmp_path, integration, owners=None, values=None, vendor_links=None, users=None):
    collections = {"models": integration}
    for m in integration:
        collections[f"models/{m['id']}/vendors"] = [
            {"id": v, "name": f"Vendor {v}"} for v in (vendor_links or {}).get(m["id"], [])
        ]
    internal = {
        "models": [{"id": m["id"], "attributes": {"user_id": (owners or {}).get(m["id"])}} for m in integration],
        "users": users if users is not None else [{"id": "u1", "attributes": {"email": "owner@example.com"}}],
    }
    for m in integration:
        internal[f"models/{m['id']}/custom_fields"] = (values or {}).get(m["id"], [])
    return make_ctx(
        tmp_path,
        source=FakeTenant(collections, dry_run=True),
        source_internal=FakeTenant(internal, dry_run=True, jsonapi=True),
    )


def test_extract_keeps_custom_models_in_the_item_shape(tmp_path, caplog):
    ctx = source_ctx(
        tmp_path,
        [
            integration_model("m1", "Model A", questionnaire_ids=["CR-MODEL-SIMPLE+1", "MODELQ2+3"]),
            integration_model("m2", "Built in", custom=False),
        ],
        owners={"m1": "u1"},
        values={"m1": [cf_value("Tier", "Gold"), cf_value("Empty", ""), cf_value("None", None)]},
        vendor_links={"m1": ["v2", "v1"]},
    )
    with caplog.at_level(logging.INFO, logger="kit.extract"):
        assert models.extract(ctx) == 1
    assert load_extract(tmp_path, "models") == [
        {
            "id": "m1",
            "name": "Model A",
            "summary": "About Model A",
            "source": "Example Source",
            "status": "none",
            "questionnaire_keys": ["CR-MODEL-SIMPLE", "MODELQ2"],
            "custom_fields": [{"name": "Tier", "value": "Gold"}],
            "vendor_ids": ["v1", "v2"],
            "vendor_names": {"v1": "Vendor v1", "v2": "Vendor v2"},
            "owner_email": "owner@example.com",
        }
    ]
    assert "1 built-in model" in caplog.text


@pytest.mark.parametrize(
    "model", [{"id": "m1", "name": "A"}, {"id": "m1", "name": "A", "custom": "yes"}], ids=["missing", "not-bool"]
)
def test_extract_stops_when_custom_flag_is_missing(tmp_path, model):
    ctx = source_ctx(tmp_path, [model])
    with pytest.raises(APIError, match="custom"):
        models.extract(ctx)


def test_extract_leaves_owner_empty_when_the_user_is_not_listed(tmp_path, caplog):
    ctx = source_ctx(tmp_path, [integration_model("m1", "Model A")], owners={"m1": "hidden"})
    with caplog.at_level(logging.WARNING, logger="kit.extract"):
        models.extract(ctx)
    assert load_extract(tmp_path, "models")[0]["owner_email"] is None
    assert "owner" in caplog.text


def test_extract_warns_about_status(tmp_path, caplog):
    ctx = source_ctx(tmp_path, [integration_model("m1", "Model A", status="approved")])
    with caplog.at_level(logging.WARNING, logger="kit.extract"):
        models.extract(ctx)
    assert "'approved'" in caplog.text and "by hand" in caplog.text


# --- load ---


def item(mid, name, source="Example Source", keys=(), fields=(), vendor_ids=(), status="none"):
    return {
        "id": mid,
        "name": name,
        "summary": f"About {name}",
        "source": source,
        "status": status,
        "questionnaire_keys": list(keys),
        "custom_fields": list(fields),
        "vendor_ids": list(vendor_ids),
        "vendor_names": {v: f"Vendor {v}" for v in vendor_ids},
        "owner_email": "owner@example.com",
    }


def versions(*pairs):
    return [{"version": n, "draft": draft} for n, draft in pairs]


def field(name, type_ids=()):
    return {"id": f"f-{name}", "name": name, "entity_type_ids": list(type_ids)}


def source(name, sid=None):
    return {"id": sid or f"s-{name}", "attributes": {"name": name}}


API_USER = "api-user"  # the API key's user: it creates every model, and owns it until the kit changes the owner


class OwnerTenant(FakeTenant):
    """Internal fake for model owners. Each model reads back with entity_id e-<id> and the API key's user as its
    creator; an entity's stakeholders, unless given, are that user as the only owner. DELETE removes the row."""

    def get_json(self, path, params=None):
        parts = path.split("/")
        if len(parts) == 2 and parts[0] == "models" and path not in self.json_bodies:
            self.gets.append((path, params))
            return {"data": {"id": parts[1], "attributes": {"entity_id": f"e-{parts[1]}", "user_id": API_USER}}}
        return super().get_json(path, params)

    def get_data(self, path, params=None):
        if path.startswith("entities/") and path.endswith("/stakeholders") and path not in self.collections:
            self.collections[path] = [owner_row(f"sh-{path.split('/')[1]}", API_USER)]
        return super().get_data(path, params)

    def delete(self, path, json=None):
        resp = super().delete(path, json)
        base, _, row_id = path.rpartition("/")
        if resp is not None and resp.status_code < 300 and base in self.collections:
            self.collections[base] = [r for r in self.collections[base] if r["id"] != row_id]
        return resp


def owner_row(row_id, user_id, role="owner"):
    return {"id": row_id, "attributes": {"user_id": user_id, "team_id": None, "role": role}}


def target_ctx(
    tmp_path,
    existing=(),
    json_bodies=None,
    fields=(),
    sources=("Example Source",),
    vendors=(),
    links=None,
    dry_run=False,
    patch_response=None,
    post_responses=None,
    internal=None,
    internal_post_responses=None,
):
    collections = {"models": list(existing), "custom_fields": list(fields), "vendors": list(vendors)}
    collections.update(links or {})
    target = FakeTenant(
        collections, json_bodies=json_bodies, patch_response=patch_response, post_responses=post_responses
    )
    target_internal = OwnerTenant(
        {
            "entity_types": [
                {"id": "et-vendor", "attributes": {"name": "vendor"}},
                {"id": "et-model", "attributes": {"name": "model"}},
            ],
            "sources": [source(n) for n in sources],
            # The item() owner is the API key's user, so most tests need no owner change
            "users": [
                {"id": API_USER, "attributes": {"email": "owner@example.com"}},
                {"id": "tu-ana", "attributes": {"email": "Ana@example.com"}},
            ],
            **(internal or {}),
        },
        jsonapi=True,
        post_responses=internal_post_responses,
    )
    return make_ctx(tmp_path, target=target, target_internal=target_internal, dry_run=dry_run)


def model_posts(ctx):
    return [(p, b) for p, b in ctx.target.posts if p == "models"]


def test_load_creates_a_model_with_published_versions(tmp_path):
    save_extract(tmp_path, "models", [item("m1", "Model A", keys=["CR-MODEL-SIMPLE"], status="approved")])
    ctx = target_ctx(
        tmp_path, json_bodies={"questionnaires/CR-MODEL-SIMPLE/versions": versions((1, False), (2, False), (3, True))}
    )
    models.load(ctx)
    assert model_posts(ctx) == [
        (
            "models",
            {
                "name": "Model A",
                "summary": "About Model A",
                "source": "Example Source",
                "questionnaire_ids": ["CR-MODEL-SIMPLE+2"],
            },
        )
    ]
    record = ctx.tracker("models").find("m1")
    assert record["target_id"] == "new-1" and record["origin"] == "created"
    assert ctx.summary.to_dict()["models"]["created"] == 1


@pytest.mark.parametrize("body", [versions((1, True)), APIError("GET failed: 404", 404)])
def test_load_fails_the_model_without_a_published_version(tmp_path, caplog, body):
    save_extract(tmp_path, "models", [item("m1", "Model A", source="New source", keys=["MODELQ2"])])
    ctx = target_ctx(tmp_path, json_bodies={"questionnaires/MODELQ2/versions": body})
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        models.load(ctx)
    assert ctx.target.posts == [] and ctx.target_internal.posts == []
    assert ctx.summary.to_dict()["models"]["failed"] == 1
    assert "questionnaire MODELQ2" in caplog.text and "publish" in caplog.text


def test_load_fails_the_model_when_a_field_does_not_apply_to_models(tmp_path, caplog):
    save_extract(
        tmp_path, "models", [item("m1", "Model A", source="New source", fields=[{"name": "Tier", "value": "Gold"}])]
    )
    ctx = target_ctx(tmp_path, fields=[field("Tier", ["et-vendor"])])
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        models.load(ctx)
    assert ctx.target.posts == [] and ctx.target_internal.posts == []
    assert ctx.summary.to_dict()["models"]["failed"] == 1
    assert "Tier" in caplog.text


def test_values_are_patched_after_the_create(tmp_path):
    values = [{"name": "Tier", "value": "Gold"}, {"name": "Region", "value": "EU"}]
    save_extract(tmp_path, "models", [item("m1", "Model A", fields=values)])
    ctx = target_ctx(tmp_path, fields=[field("Tier", ["et-model"]), field("Region", [])])
    models.load(ctx)
    [(_, body)] = model_posts(ctx)
    assert "custom_fields" not in body
    assert ctx.target.patches == [("models/new-1", {"custom_fields": values})]
    assert not ctx.summary.needs_attention


@pytest.mark.parametrize("failure", ["status", "exception"])
def test_failed_patch_flags_but_keeps_the_model_tracked(tmp_path, caplog, failure):
    save_extract(tmp_path, "models", [item("m1", "Model A", fields=[{"name": "Tier", "value": "Gold"}])])
    ctx = target_ctx(tmp_path, fields=[field("Tier", [])], patch_response=FakeResponse(422, {"error": "bad"}))
    if failure == "exception":

        def boom(path, json):
            raise APIError("PATCH failed: timeout")

        ctx.target.patch = boom
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        models.load(ctx)
    assert ctx.tracker("models").target_id("m1") == "new-1"
    assert ctx.summary.to_dict()["models"]["created"] == 1
    assert ctx.summary.needs_attention
    assert "'Model A'" in caplog.text and "Tier" in caplog.text


def test_vendor_links_are_mapped_and_only_missing_ones_posted(tmp_path):
    save_extract(tmp_path, "models", [item("m1", "Model A", vendor_ids=["sv1", "sv2", "builtin"])])
    ctx = target_ctx(
        tmp_path,
        vendors=[{"id": "builtin", "name": "OpenAI"}],
        links={"models/new-1/vendors": [{"id": "tv2", "name": "Two"}]},
    )
    ctx.tracker("vendors").record("sv1", "tv1", "One", "created")
    ctx.tracker("vendors").record("sv2", "tv2", "Two", "matched")
    models.load(ctx)
    assert [(p, b) for p, b in ctx.target.posts if p != "models"] == [
        ("models/new-1/vendors", {"id": "tv1"}),
        ("models/new-1/vendors", {"id": "builtin"}),
    ]
    assert not ctx.summary.needs_attention


def test_unmapped_vendor_flags_but_keeps_the_model(tmp_path, caplog):
    save_extract(tmp_path, "models", [item("m1", "Model A", vendor_ids=["sv9"])])
    ctx = target_ctx(tmp_path)
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        models.load(ctx)
    assert ctx.tracker("models").target_id("m1") == "new-1"
    assert [p for p, _ in ctx.target.posts] == ["models"]
    assert ctx.summary.needs_attention
    assert "vendor Vendor sv9" in caplog.text and "load vendors first" in caplog.text


def test_failed_link_flags_but_keeps_the_model(tmp_path, caplog):
    save_extract(tmp_path, "models", [item("m1", "Model A", vendor_ids=["builtin"])])
    ctx = target_ctx(
        tmp_path,
        vendors=[{"id": "builtin", "name": "OpenAI"}],
        post_responses={"models/new-1/vendors": FakeResponse(422, {"detail": "error"})},
    )
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        models.load(ctx)
    assert ctx.summary.to_dict()["models"]["created"] == 1
    assert ctx.summary.needs_attention
    assert "vendor OpenAI" in caplog.text


def test_unreadable_links_flag_and_post_nothing(tmp_path, caplog):
    save_extract(tmp_path, "models", [item("m1", "Model A", vendor_ids=["builtin"])])
    ctx = target_ctx(tmp_path, vendors=[{"id": "builtin", "name": "OpenAI"}])

    def broken(path, params=None):
        if path.endswith("/vendors") and path.startswith("models/"):
            raise APIError("GET failed: 500", 500)
        return FakeTenant.get_all(ctx.target, path, params)

    ctx.target.get_all = broken
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        models.load(ctx)
    assert [p for p, _ in ctx.target.posts] == ["models"]
    assert ctx.summary.needs_attention


def test_missing_source_is_created_before_the_model(tmp_path, caplog):
    save_extract(tmp_path, "models", [item("m1", "Model A", source="Example Source")])
    ctx = target_ctx(tmp_path, sources=("Credo AI", "example source"))
    with caplog.at_level(logging.INFO, logger="kit.load"):
        models.load(ctx)
    assert ctx.target_internal.posts == [
        ("sources", {"data": {"type": "sources", "attributes": {"name": "Example Source"}}})
    ]
    record = ctx.tracker("sources").find("Example Source")
    assert record["origin"] == "created" and record["target_id"] == "new-1"
    assert (tmp_path / "loaded_sources.json").exists()
    assert "can't be deleted" in caplog.text
    assert ctx.summary.to_dict()["sources"]["created"] == 1
    assert model_posts(ctx)[0][1]["source"] == "Example Source"


def test_a_source_is_created_once_for_many_models(tmp_path):
    save_extract(tmp_path, "models", [item("m1", "Model A", source="New"), item("m2", "Model B", source="New")])
    ctx = target_ctx(tmp_path)
    models.load(ctx)
    assert len(ctx.target_internal.posts) == 1
    assert len(model_posts(ctx)) == 2


def test_no_source_is_created_for_a_model_that_is_skipped(tmp_path):
    save_extract(tmp_path, "models", [item("m1", "Model A", source="New")])
    ctx = target_ctx(tmp_path, existing=[{"id": "t1", "name": "Model A"}])
    models.load(ctx)
    assert ctx.target_internal.posts == []


def test_failed_source_create_fails_its_models(tmp_path, caplog):
    save_extract(
        tmp_path, "models", [item("m1", "Model A", source="New"), item("m2", "Model B", source="Example Source")]
    )
    ctx = target_ctx(tmp_path)
    ctx.target_internal.post_responses = {"sources": FakeResponse(422, {"errors": [{"detail": "bad"}]})}
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        models.load(ctx)
    assert [b["name"] for _, b in model_posts(ctx)] == ["Model B"]
    assert ctx.summary.to_dict()["models"] == {"created": 1, "matched": 0, "skipped": 0, "failed": 1}
    assert ctx.summary.to_dict()["sources"]["failed"] == 1
    assert "source New" in caplog.text


def test_dry_run_reports_a_source_as_would_create(tmp_path, caplog):
    save_extract(tmp_path, "models", [item("m1", "Model A", source="New")])
    ctx = target_ctx(tmp_path, dry_run=True)
    with caplog.at_level(logging.INFO, logger="kit.load"):
        models.load(ctx)
    assert ctx.target_internal.posts == [] and ctx.target.posts == []
    assert "would create source New" in caplog.text
    assert ctx.summary.to_dict()["sources"]["created"] == 1


def test_model_named_like_a_target_model_is_skipped_with_a_warning(tmp_path, caplog):
    save_extract(tmp_path, "models", [item("m1", "Model A", fields=[{"name": "Tier", "value": "Gold"}])])
    ctx = target_ctx(tmp_path, existing=[{"id": "t1", "name": "Model A"}], fields=[field("Tier", [])])
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        models.load(ctx)
    assert ctx.target.posts == [] and ctx.target.patches == []
    assert ctx.tracker("models").find("m1") is None
    assert ctx.summary.to_dict()["models"]["skipped"] == 1
    assert "'Model A'" in caplog.text and "already" in caplog.text
    assert "interrupted, delete it on the target and re-run" in caplog.text


def test_second_source_model_with_the_same_name_is_skipped(tmp_path, caplog):
    save_extract(tmp_path, "models", [item("m1", "Model A"), item("m2", "Model A")])
    ctx = target_ctx(tmp_path)
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        models.load(ctx)
    assert len(model_posts(ctx)) == 1
    assert ctx.tracker("models").find("m2") is None
    assert ctx.summary.to_dict()["models"] == {"created": 1, "matched": 0, "skipped": 1, "failed": 0}
    assert "more than one source model" in caplog.text and "rename one on the source, then re-extract" in caplog.text


def full_run(tmp_path, dry_run=False):
    save_extract(
        tmp_path,
        "models",
        [
            item(
                "m1",
                "Model A",
                source="New",
                keys=["CR-MODEL-SIMPLE"],
                fields=[{"name": "Tier", "value": "Gold"}],
                vendor_ids=["builtin"],
            ),
            item("m2", "Model B"),
        ],
    )
    ctx = target_ctx(
        tmp_path,
        json_bodies={"questionnaires/CR-MODEL-SIMPLE/versions": versions((1, False))},
        fields=[field("Tier", [])],
        vendors=[{"id": "builtin", "name": "OpenAI"}],
        dry_run=dry_run,
    )
    models.load(ctx)
    return ctx


def test_second_run_skips_everything(tmp_path):
    first = full_run(tmp_path)
    assert first.summary.to_dict()["models"]["created"] == 2
    ctx = target_ctx(
        tmp_path,
        existing=first.target.collections["models"],
        sources=("Example Source", "New"),
        json_bodies={"questionnaires/CR-MODEL-SIMPLE/versions": versions((1, False))},
        fields=[field("Tier", [])],
        vendors=[{"id": "builtin", "name": "OpenAI"}],
        links={"models/new-1/vendors": [{"id": "builtin", "name": "OpenAI"}]},
        internal={"models/new-1/custom_fields": [cf_value("Tier", "Gold")], "models/new-2/custom_fields": []},
    )
    models.load(ctx)
    assert ctx.target.posts == [] and ctx.target.patches == [] and ctx.target_internal.posts == []
    assert ctx.summary.to_dict()["models"] == {"created": 0, "matched": 0, "skipped": 2, "failed": 0}
    assert not ctx.summary.needs_attention


def test_dry_run_writes_nothing_and_counts_like_a_live_run(tmp_path, caplog):
    live = full_run(tmp_path / "live")
    with caplog.at_level(logging.INFO, logger="kit.load"):
        dry = full_run(tmp_path / "dry", dry_run=True)
    assert dry.target.posts == [] and dry.target.patches == [] and dry.target_internal.posts == []
    assert dry.summary.to_dict() == live.summary.to_dict()
    assert not (tmp_path / "dry" / "loaded_models.json").exists()
    assert "would set" in caplog.text and "would link vendor OpenAI" in caplog.text


def test_lost_connection_on_create_says_the_model_may_exist(tmp_path, caplog):
    save_extract(tmp_path, "models", [item("m1", "Model A")])
    ctx = target_ctx(tmp_path)

    def lost(path, json):
        raise APIError("POST models lost its connection: reset. The item may or may not have been created")

    ctx.target.post = lost
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        models.load(ctx)
    assert ctx.tracker("models").find("m1") is None
    assert ctx.summary.to_dict()["models"]["failed"] == 1
    assert (
        "model 'Model A' may have been created on the target before the connection was lost; check the "
        "target: if model 'Model A' is there with no vendor links, delete it, then re-run"
    ) in caplog.text


def test_interrupted_run_repairs_links_and_custom_fields_of_tracked_models(tmp_path, caplog):
    save_extract(
        tmp_path,
        "models",
        [
            item(
                "m1",
                "Model A",
                fields=[{"name": "Tier", "value": "Gold"}, {"name": "Region", "value": "EU"}],
                vendor_ids=["builtin", "other"],
            )
        ],
    )
    ctx = target_ctx(
        tmp_path,
        existing=[{"id": "t1", "name": "Model A"}],
        fields=[field("Tier", []), field("Region", [])],
        vendors=[{"id": "builtin", "name": "OpenAI"}, {"id": "other", "name": "Anthropic"}],
        links={"models/t1/vendors": [{"id": "builtin", "name": "OpenAI"}]},
        internal={"models/t1/custom_fields": [cf_value("Tier", "Gold"), cf_value("Region", None)]},
    )
    ctx.tracker("models").record("m1", "t1", "Model A", "created")
    with caplog.at_level(logging.INFO, logger="kit.load"):
        models.load(ctx)
    assert ctx.target.posts == [("models/t1/vendors", {"id": "other"})]
    assert ctx.target.patches == [("models/t1", {"custom_fields": [{"name": "Region", "value": "EU"}]})]
    assert ctx.summary.to_dict()["models"]["skipped"] == 1
    assert "vendor Anthropic" in caplog.text and "Region" in caplog.text


def test_dry_run_does_not_recheck_tracked_models(tmp_path):
    save_extract(tmp_path, "models", [item("m1", "Model A", vendor_ids=["builtin"])])
    ctx = target_ctx(
        tmp_path,
        existing=[{"id": "t1", "name": "Model A"}],
        vendors=[{"id": "builtin", "name": "OpenAI"}],
        dry_run=True,
    )
    ctx.tracker("models").record("m1", "t1", "Model A", "created")
    models.load(ctx)
    assert ctx.summary.to_dict()["models"]["skipped"] == 1
    assert not ctx.summary.needs_attention


def test_unreadable_custom_fields_of_a_tracked_model_flag(tmp_path):
    save_extract(tmp_path, "models", [item("m1", "Model A", fields=[{"name": "Tier", "value": "Gold"}])])
    ctx = target_ctx(tmp_path, existing=[{"id": "t1", "name": "Model A"}], fields=[field("Tier", [])])
    ctx.tracker("models").record("m1", "t1", "Model A", "created")
    models.load(ctx)
    assert ctx.target.patches == []
    assert ctx.summary.needs_attention


def test_dry_run_accepts_custom_fields_created_earlier_in_the_run(tmp_path):
    save_extract(tmp_path, "custom_fields", [{"id": "s1", "name": "Tier", "entity_type_names": ["model"]}])
    save_extract(tmp_path, "models", [item("m1", "Model A", fields=[{"name": "Tier", "value": "Gold"}])])
    ctx = target_ctx(tmp_path, dry_run=True)
    ctx.tracker("custom_fields").record("s1", "dry:s1", "Tier", "created")
    models.load(ctx)
    assert ctx.summary.to_dict()["models"]["created"] == 1


@pytest.mark.parametrize("value", [None, ""])
def test_model_without_a_source_fails_before_any_write(tmp_path, caplog, value):
    save_extract(tmp_path, "models", [item("m1", "Model A", source=value)])
    ctx = target_ctx(tmp_path)
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        models.load(ctx)
    assert ctx.target.posts == [] and ctx.target_internal.posts == []
    assert ctx.summary.to_dict()["models"]["failed"] == 1
    assert "model 'Model A' has no source on the source tenant; set one there, then re-extract" in caplog.text


def test_tracked_source_missing_on_the_target_is_created_again(tmp_path, caplog):
    save_extract(tmp_path, "models", [item("m1", "Model A", source="New")])
    ctx = target_ctx(tmp_path)
    ctx.tracker("sources").record("New", "s-old", "New", "created")
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        models.load(ctx)
    assert ctx.target_internal.posts == [("sources", {"data": {"type": "sources", "attributes": {"name": "New"}}})]
    assert len(model_posts(ctx)) == 1
    assert "source New" in caplog.text and "isn't on the target" in caplog.text


def test_two_source_vendors_mapping_to_one_target_vendor_link_once(tmp_path):
    save_extract(tmp_path, "models", [item("m1", "Model A", vendor_ids=["sv1", "sv2"])])
    ctx = target_ctx(tmp_path)
    ctx.tracker("vendors").record("sv1", "tv1", "Acme", "created")
    ctx.tracker("vendors").record("sv2", "tv1", "Acme", "matched")
    models.load(ctx)
    assert [(p, b) for p, b in ctx.target.posts if p != "models"] == [("models/new-1/vendors", {"id": "tv1"})]


def test_case_only_source_difference_is_warned(tmp_path, caplog):
    save_extract(tmp_path, "models", [item("m1", "Model A", source="Example Source")])
    ctx = target_ctx(tmp_path, sources=("example source",))
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        models.load(ctx)
    assert (
        "the target has source example source, which differs from source Example Source only in case; creating "
        "Example Source as a separate source"
    ) in caplog.text


def test_custom_field_names_are_listed_as_plain_text(tmp_path, caplog):
    save_extract(
        tmp_path,
        "models",
        [item("m1", "Model A", fields=[{"name": "Tier", "value": "Gold"}, {"name": "Region", "value": "EU"}])],
    )
    ctx = target_ctx(tmp_path)
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        models.load(ctx)
    assert "custom field(s) Tier, Region don't apply" in caplog.text


# --- owners ---


def owner_writes(ctx):
    posts = [(p, b) for p, b in ctx.target_internal.posts if p.startswith("entities/")]
    return posts, list(ctx.target_internal.deletes)


def stakeholder_post(entity_id, user_id):
    return (
        f"entities/{entity_id}/stakeholders",
        {"data": {"type": "stakeholders", "attributes": {"user_id": user_id, "role": "owner"}}},
    )


def test_owner_of_a_created_model_is_set_then_the_api_key_user_removed(tmp_path):
    save_extract(tmp_path, "models", [dict(item("m1", "Model A"), owner_email="ana@example.com")])
    ctx = target_ctx(tmp_path)
    models.load(ctx)
    assert owner_writes(ctx) == ([stakeholder_post("e-new-1", "tu-ana")], ["entities/e-new-1/stakeholders/sh-e-new-1"])
    assert ("models/new-1", None) in ctx.target_internal.gets
    assert not ctx.summary.needs_attention


def test_owner_maps_through_the_users_tracker_first(tmp_path):
    save_extract(tmp_path, "models", [dict(item("m1", "Model A"), owner_email="ana@example.com")])
    ctx = target_ctx(tmp_path)
    ctx.tracker("users").record("su-ana", "tu-new", "ana@example.com", "created")
    models.load(ctx)
    assert owner_writes(ctx)[0] == [stakeholder_post("e-new-1", "tu-new")]


def test_owner_that_is_the_api_key_user_needs_no_write(tmp_path):
    save_extract(tmp_path, "models", [item("m1", "Model A")])
    ctx = target_ctx(tmp_path)
    models.load(ctx)
    assert owner_writes(ctx) == ([], [])
    assert not ctx.summary.needs_attention


@pytest.mark.parametrize("email", [None, "nobody@example.com"], ids=["no-email", "not-on-target"])
def test_unmapped_owner_is_reported_and_flagged(tmp_path, caplog, email):
    save_extract(tmp_path, "models", [dict(item("m1", "Model A"), owner_email=email)])
    ctx = target_ctx(tmp_path)
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        models.load(ctx)
    assert owner_writes(ctx) == ([], [])
    assert ctx.summary.to_dict()["models"]["created"] == 1
    assert ctx.summary.needs_attention
    assert "'Model A'" in caplog.text and "the API key's user owns it" in caplog.text


def test_failed_owner_post_flags_and_keeps_the_api_key_user(tmp_path, caplog):
    save_extract(tmp_path, "models", [dict(item("m1", "Model A"), owner_email="ana@example.com")])
    ctx = target_ctx(
        tmp_path, internal_post_responses={"entities/e-new-1/stakeholders": FakeResponse(422, {"detail": "error"})}
    )
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        models.load(ctx)
    assert ctx.target_internal.deletes == []
    assert ctx.summary.needs_attention
    assert "user ana@example.com" in caplog.text and "by hand" in caplog.text


def test_failed_owner_delete_flags(tmp_path, caplog):
    save_extract(tmp_path, "models", [dict(item("m1", "Model A"), owner_email="ana@example.com")])
    ctx = target_ctx(tmp_path)
    ctx.target_internal.delete_response = FakeResponse(500, text="boom")
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        models.load(ctx)
    assert ctx.summary.needs_attention
    assert "the API key's user" in caplog.text and "by hand" in caplog.text


def test_unreadable_model_flags_the_owner(tmp_path, caplog):
    save_extract(tmp_path, "models", [dict(item("m1", "Model A"), owner_email="ana@example.com")])
    ctx = target_ctx(tmp_path)
    ctx.target_internal.json_bodies["models/new-1"] = APIError("GET models/new-1 failed: 500", 500)
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        models.load(ctx)
    assert owner_writes(ctx) == ([], [])
    assert ctx.summary.needs_attention
    assert "make user ana@example.com the owner" in caplog.text


def test_rerun_repairs_an_owner_an_earlier_run_did_not_set(tmp_path, caplog):
    save_extract(tmp_path, "models", [dict(item("m1", "Model A"), owner_email="ana@example.com")])
    ctx = target_ctx(tmp_path, existing=[{"id": "t1", "name": "Model A"}])
    ctx.tracker("models").record("m1", "t1", "Model A", "created")
    with caplog.at_level(logging.INFO, logger="kit.load"):
        models.load(ctx)
    assert owner_writes(ctx) == ([stakeholder_post("e-t1", "tu-ana")], ["entities/e-t1/stakeholders/sh-e-t1"])
    assert "perhaps an earlier run stopped" in caplog.text


def test_rerun_removes_the_api_key_user_left_as_a_second_owner(tmp_path):
    save_extract(tmp_path, "models", [dict(item("m1", "Model A"), owner_email="ana@example.com")])
    ctx = target_ctx(
        tmp_path,
        existing=[{"id": "t1", "name": "Model A"}],
        internal={"entities/e-t1/stakeholders": [owner_row("k1", API_USER), owner_row("k2", "tu-ana")]},
    )
    ctx.tracker("models").record("m1", "t1", "Model A", "created")
    models.load(ctx)
    assert owner_writes(ctx) == ([], ["entities/e-t1/stakeholders/k1"])


def test_rerun_with_the_owner_in_place_writes_nothing(tmp_path):
    save_extract(tmp_path, "models", [dict(item("m1", "Model A"), owner_email="ana@example.com")])
    ctx = target_ctx(
        tmp_path,
        existing=[{"id": "t1", "name": "Model A"}],
        internal={"entities/e-t1/stakeholders": [owner_row("k2", "tu-ana")]},
    )
    ctx.tracker("models").record("m1", "t1", "Model A", "created")
    models.load(ctx)
    assert owner_writes(ctx) == ([], [])
    assert not ctx.summary.needs_attention


def test_owner_changed_by_hand_on_the_target_is_left_alone(tmp_path, caplog):
    save_extract(tmp_path, "models", [dict(item("m1", "Model A"), owner_email="ana@example.com")])
    ctx = target_ctx(
        tmp_path,
        existing=[{"id": "t1", "name": "Model A"}],
        internal={"entities/e-t1/stakeholders": [owner_row("k3", "someone-else")]},
    )
    ctx.tracker("models").record("m1", "t1", "Model A", "created")
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        models.load(ctx)
    assert owner_writes(ctx) == ([], [])
    assert "left the owner as is" in caplog.text


def test_dry_run_reports_the_owner_and_writes_nothing(tmp_path, caplog):
    save_extract(tmp_path, "models", [dict(item("m1", "Model A"), owner_email="ana@example.com")])
    ctx = target_ctx(tmp_path, dry_run=True)
    with caplog.at_level(logging.INFO, logger="kit.load"):
        models.load(ctx)
    assert owner_writes(ctx) == ([], [])
    assert ctx.target_internal.gets == []
    assert "would make user ana@example.com the owner of 'Model A'" in caplog.text


def test_failed_custom_field_patch_logs_the_status_only(tmp_path, caplog):
    save_extract(tmp_path, "models", [item("m1", "Model A", fields=[{"name": "Tier", "value": "Gold"}])])
    ctx = target_ctx(
        tmp_path, fields=[field("Tier", [])], patch_response=FakeResponse(422, text='{"value": "Gold is not allowed"}')
    )
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        models.load(ctx)
    assert "(422)" in caplog.text and "Gold" not in caplog.text
