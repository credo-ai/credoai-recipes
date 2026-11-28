"""Custom field definitions. Integration API, plus an internal-API read of entity types (no Integration endpoint)."""

from kit.clients.base import APIError
from kit.loading import MissingReference, load_reference
from kit.tracking import load_extract, save_extract

ENTITY = "custom_fields"
FIELDS = ("name", "type", "element_type", "options", "multiple", "metadata")


def _entity_type_names(internal_client):
    """{entity type ID: name} for one tenant, e.g. {'D2no...': 'use_case'}."""
    return {et["id"]: et["attributes"]["name"] for et in internal_client.get_all_offset("entity_types")}


def field_names_for(ctx, entity_type_name):
    """Names of the target custom fields that apply to one entity type (e.g. 'vendor'): those with no entity types
    (meaning all) or with that type."""
    type_ids = {tid for tid, name in _entity_type_names(ctx.target_internal).items() if name == entity_type_name}
    names = set()
    for f in ctx.target.get_all("custom_fields"):
        # An empty list means "every entity type", so a missing key must not be read as empty
        if "entity_type_ids" not in f:
            raise APIError(
                f"{entity_type_name}s: target custom field {f.get('name')!r} has no entity_type_ids in the "
                f"response; stopping so it isn't taken to apply to {entity_type_name}s"
            )
        if not f["entity_type_ids"] or type_ids & set(f["entity_type_ids"]):
            names.add(f["name"])
    if ctx.dry_run:
        # Fields this dry run would have created earlier aren't on the target; count them so the dry run's results
        # match a live run's
        planned = {r["source_id"] for r in ctx.tracker(ENTITY).records if str(r["target_id"]).startswith("dry:")}
        if planned:
            for item in load_extract(ctx.data_dir, ENTITY):
                types = item.get("entity_type_names") or []
                if item["id"] in planned and (not types or entity_type_name in types):
                    names.add(item["name"])
    return names


def extract(ctx):
    names = _entity_type_names(ctx.source_internal)
    items = ctx.source.get_all("custom_fields")
    for item in items:
        # An empty list means "every entity type", so a missing key must not be read as empty
        if "entity_type_ids" not in item:
            raise APIError(
                f"custom_fields: {item.get('name')!r} has no entity_type_ids in the response; stopping "
                f"so fields aren't widened to every entity type"
            )
        item["entity_type_names"] = [names.get(i, f"unknown:{i}") for i in item["entity_type_ids"] or []]
    save_extract(ctx.data_dir, ENTITY, items)
    return len(items)


def load(ctx):
    target_ids = {name: tid for tid, name in _entity_type_names(ctx.target_internal).items()}

    def build_payload(item):
        missing = [n for n in item["entity_type_names"] if n not in target_ids]
        if missing:
            raise MissingReference(f"entity type(s) {missing} not on the target")
        payload = {k: item.get(k) for k in FIELDS}
        payload["entity_type_ids"] = [target_ids[n] for n in item["entity_type_names"]]
        return payload

    load_reference(
        ctx,
        ENTITY,
        items=load_extract(ctx.data_dir, ENTITY),
        target_items=ctx.target.get_all("custom_fields"),
        build_payload=build_payload,
        path="custom_fields",
        compare=("type", "element_type", "multiple", "options"),
    )
