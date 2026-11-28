"""Custom risk scenarios. Read through the Integration API; created through the internal API, since the
Integration API's /risk_scenarios is read-only."""

import logging

from kit import applies_to
from kit import risk_type_refs as risk_types
from kit.clients.base import APIError
from kit.loading import data_id, load_reference
from kit.ownership import keep_custom
from kit.remap import exact
from kit.tracking import load_extract, save_extract

ENTITY = "risk_scenarios"
FIELDS = ("name", "description", "author", "domains", "industries", "regions")
extract_log = logging.getLogger("kit.extract")
load_log = logging.getLogger("kit.load")


def extract(ctx):
    items = keep_custom(
        ENTITY,
        ctx.source.get_all("risk_scenarios"),
        ownership_of=lambda s: s.get("ownership_type"),
        archived_of=lambda s: s.get("archived"),
    )
    names = risk_types.names_by_id(ctx.source)
    type_names = applies_to.entity_type_names(ctx.source_internal) if items else {}
    for item in items:
        risk_types.add_risk_type_names(item, names)
        if "entity_type_ids" not in item:
            raise APIError(f"{ENTITY}: {item['name']!r} has no 'entity_type_ids' field")
        item["applies_to"] = applies_to.names_for(ENTITY, item["name"], item["entity_type_ids"], type_names)
    save_extract(ctx.data_dir, ENTITY, items)
    return len(items)


def load(ctx):
    on_target = ctx.target.get_all("risk_scenarios")
    built_in_names = {s["name"] for s in on_target if s.get("ownership_type") != "custom"}
    index = risk_types.target_index(ctx)
    items = load_extract(ctx.data_dir, ENTITY)
    custom = [s for s in on_target if s.get("ownership_type") == "custom"]
    names = {exact(i["name"]) for i in items}
    for s in custom:
        if s.get("archived") and exact(s["name"]) in names:
            load_log.warning(f"{ENTITY}: {s['name']!r} exists on the target but is archived; it will be matched as is")

    def build_payload(item):
        if item["name"] in built_in_names:
            load_log.info(
                f"{ENTITY}: a built-in scenario on the target is also named {item['name']!r}; creating a "
                f"separate custom one, as on the source"
            )
        attributes = {k: item.get(k) for k in FIELDS}
        # The server rejects risk_type_ids [] ("Invalid risk_type_ids") and null ("can't be blank"),
        # but accepts the create with the key omitted and stores []; so send it only when non-empty
        risk_type_ids = risk_types.target_ids(item["risk_type_names"], index, f"{ENTITY}: {item['name']!r}")
        if risk_type_ids:
            attributes["risk_type_ids"] = risk_type_ids
        return {"data": {"type": "risk_scenarios", "attributes": applies_to.add_to(attributes, item)}}

    load_reference(
        ctx,
        ENTITY,
        items=items,
        # Match only custom scenarios: a built-in one with the same name is a different scenario
        target_items=custom,
        build_payload=build_payload,
        path="custom_risk_scenarios",
        compare=("description",),
        client=ctx.target_internal,
        id_of=data_id,
    )
