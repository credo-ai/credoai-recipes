import pytest

from kit.entities import custom_fields
from kit.tracking import load_extract
from tests.fakes import FakeTenant, make_ctx


def entity_types(prefix, names):
    return [{"id": f"{prefix}-{n}", "attributes": {"name": n}} for n in names]


def field(fid, name, type_ids):
    return {
        "id": fid,
        "name": name,
        "type": "string",
        "element_type": "select",
        "options": ["Yes", "No"],
        "multiple": False,
        "metadata": {"description": ""},
        "entity_type_ids": type_ids,
    }


def ctx_for(tmp_path, target_types=("use_case", "model")):
    source = FakeTenant(
        {
            "custom_fields": [
                field("f1", "Owner", ["src-use_case"]),
                field("f2", "Everywhere", []),
                field("f3", "Agent Only", ["src-agent"]),
            ]
        },
        dry_run=True,
    )
    return make_ctx(
        tmp_path,
        source=source,
        source_internal=FakeTenant({"entity_types": entity_types("src", ["use_case", "model", "agent"])}, dry_run=True),
        target_internal=FakeTenant({"entity_types": entity_types("tgt", target_types)}),
    )


def test_extract_stores_entity_type_names(tmp_path):
    ctx = ctx_for(tmp_path)
    assert custom_fields.extract(ctx) == 3
    names = {f["name"]: f["entity_type_names"] for f in load_extract(tmp_path, "custom_fields")}
    assert names == {"Owner": ["use_case"], "Everywhere": [], "Agent Only": ["agent"]}


def test_load_translates_entity_types_to_target_ids(tmp_path):
    ctx = ctx_for(tmp_path)
    custom_fields.extract(ctx)
    custom_fields.load(ctx)
    posted = {p[1]["name"]: p[1] for p in ctx.target.posts}
    assert posted["Owner"]["entity_type_ids"] == ["tgt-use_case"]
    assert posted["Owner"]["options"] == ["Yes", "No"]
    assert posted["Everywhere"]["entity_type_ids"] == []


def test_entity_type_missing_on_target_fails_the_field(tmp_path):
    ctx = ctx_for(tmp_path)
    custom_fields.extract(ctx)
    custom_fields.load(ctx)
    assert "Agent Only" not in {p[1]["name"] for p in ctx.target.posts}
    assert ctx.summary.to_dict()["custom_fields"]["failed"] == 1


def test_extract_stops_if_entity_type_ids_are_missing(tmp_path):
    from kit.clients.base import APIError

    source = FakeTenant({"custom_fields": [{"id": "f1", "name": "Owner", "type": "string"}]}, dry_run=True)
    ctx = make_ctx(tmp_path, source=source)
    with pytest.raises(APIError, match="entity_type_ids"):
        custom_fields.extract(ctx)


def field_names_ctx(tmp_path, fields):
    return make_ctx(
        tmp_path,
        target=FakeTenant({"custom_fields": fields}),
        target_internal=FakeTenant({"entity_types": entity_types("tgt", ["use_case", "vendor", "model"])}),
    )


@pytest.mark.parametrize(
    "type_name, expected",
    [
        ("vendor", {"Everywhere", "Vendor Only", "Vendor And Model"}),
        ("model", {"Everywhere", "Model Only", "Vendor And Model"}),
    ],
)
def test_field_names_for_an_entity_type(tmp_path, type_name, expected):
    ctx = field_names_ctx(
        tmp_path,
        [
            field("f1", "Everywhere", []),
            field("f2", "Vendor Only", ["tgt-vendor"]),
            field("f3", "Model Only", ["tgt-model"]),
            field("f4", "Vendor And Model", ["tgt-vendor", "tgt-model"]),
            field("f5", "Use Case Only", ["tgt-use_case"]),
        ],
    )
    assert custom_fields.field_names_for(ctx, type_name) == expected


def test_field_names_for_stops_on_a_field_without_entity_type_ids(tmp_path):
    from kit.clients.base import APIError

    ctx = field_names_ctx(tmp_path, [{"id": "f1", "name": "Tier"}])
    with pytest.raises(APIError, match=r"'Tier'.*entity_type_ids.*models"):
        custom_fields.field_names_for(ctx, "model")


def test_field_names_for_includes_fields_created_earlier_in_the_same_dry_run(tmp_path):
    from kit.tracking import save_extract

    save_extract(
        tmp_path,
        "custom_fields",
        [
            {"id": "s1", "name": "New Model Field", "entity_type_names": ["model"]},
            {"id": "s2", "name": "New Everywhere", "entity_type_names": []},
            {"id": "s3", "name": "New Vendor Field", "entity_type_names": ["vendor"]},
        ],
    )
    ctx = make_ctx(
        tmp_path,
        target=FakeTenant({"custom_fields": [field("f1", "Everywhere", [])]}),
        target_internal=FakeTenant({"entity_types": entity_types("tgt", ["vendor", "model"])}),
        dry_run=True,
    )
    tracker = ctx.tracker("custom_fields")
    for sid, name in (("s1", "New Model Field"), ("s2", "New Everywhere"), ("s3", "New Vendor Field")):
        tracker.record(sid, f"dry:{sid}", name, "created")
    assert custom_fields.field_names_for(ctx, "model") == {"Everywhere", "New Model Field", "New Everywhere"}


def test_field_names_for_ignores_tracked_fields_in_a_live_run(tmp_path):
    from kit.tracking import save_extract

    save_extract(tmp_path, "custom_fields", [{"id": "s1", "name": "Gone", "entity_type_names": []}])
    ctx = field_names_ctx(tmp_path, [])
    ctx.tracker("custom_fields").record("s1", "t1", "Gone", "created")
    assert custom_fields.field_names_for(ctx, "model") == set()
