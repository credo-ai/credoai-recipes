"""Custom controls (standalone, not the copies inside policy packs).

Internal API for content and writes: the Integration API lists control bases, but reading a control's versions
returns 500, and its create schema keeps only type, description and required for each evidence requirement,
losing the requirement key, question, evidence type and code template.
"""

import logging
from urllib.parse import quote

from kit import applies_to
from kit import risk_type_refs as risk_types
from kit.clients.base import APIError
from kit.loading import load_versioned, remind_to_publish
from kit.ownership import keep_custom
from kit.tracking import load_extract, save_extract

ENTITY = "controls"
extract_log = logging.getLogger("kit.extract")
load_log = logging.getLogger("kit.load")


def extract(ctx):
    bases = keep_custom(
        ENTITY,
        ctx.source.get_all("policy_control_bases"),
        ownership_of=lambda b: b.get("ownership_type"),
        archived_of=lambda b: b.get("archived"),
    )
    names = risk_types.names_by_id(ctx.source)
    # The Integration API's bases don't say which entity types a control applies to; the internal ones do
    targeting = {b["id"]: b.get("attributes") or {} for b in ctx.source_internal.get_all_offset("policy_control_bases")}
    items = []
    for n, base in enumerate(bases, 1):
        if n % 50 == 0:
            extract_log.info(f"{ENTITY}: read {n} of {len(bases)}")
        key = base["id"]
        try:
            versions = ctx.source_internal.get_data(f"policy_control_bases/{quote(key, safe='')}/versions")
        except APIError as e:
            raise APIError(f"{ENTITY}: reading versions of {key!r} failed: {e}", e.status_code, e.body) from e
        if not versions:
            extract_log.warning(f"{ENTITY}: {key!r} has no versions on the source; skipping it")
            continue
        latest = max(versions, key=lambda v: v["attributes"]["version"])["attributes"]
        for field in ("evidence_requirements", "risk_type_ids"):
            if field not in latest:
                # Missing (not null) means the API shape changed; carrying on would create controls without them
                raise APIError(f"{ENTITY}: the latest version of {key!r} has no {field!r} field")
        item = {
            "id": key,
            "key": key,
            "version": latest["version"],
            "draft": bool(latest.get("draft")),
            "info": latest.get("info") or {},
            "evidence_requirements": latest["evidence_requirements"] or [],
            "risk_type_ids": latest["risk_type_ids"] or [],
        }
        risk_types.add_risk_type_names(item, names)
        item["applies_to"] = applies_to.from_base(ENTITY, key, targeting.get(key, {}))
        # The latest version may be a draft over an earlier published one; that still needs publishing on the target
        item["published_on_source"] = any(not v["attributes"].get("draft") for v in versions)
        items.append(item)
    save_extract(ctx.data_dir, ENTITY, items)
    return len(items)


def load(ctx):
    items = load_extract(ctx.data_dir, ENTITY)
    index = risk_types.target_index(ctx)
    target_bases = ctx.target.get_all("policy_control_bases")
    keys = {i["key"] for i in items}
    for b in target_bases:
        if b.get("archived") and b["id"] in keys:
            load_log.warning(f"{ENTITY}: {b['id']!r} exists on the target but is archived; it will be matched as is")
    created = load_versioned(
        ctx,
        ENTITY,
        items,
        existing_keys={b["id"] for b in target_bases},
        client=ctx.target_internal,
        base_path="policy_control_bases",
        base_payload=lambda i: {
            "data": {"type": "policy_control_bases", "attributes": applies_to.add_to({"id": i["key"]}, i)}
        },
        version_path=lambda key: f"policy_control_bases/{key}/versions",
        version_payload=lambda i: {
            "data": {
                "type": "policy_control_versions",
                "attributes": {
                    "info": i["info"],
                    "evidence_requirements": i["evidence_requirements"],
                    "risk_type_ids": risk_types.target_ids(i["risk_type_names"], index, f"{ENTITY}: {i['key']!r}"),
                },
            }
        },
    )
    remind_to_publish(ctx, ENTITY, items, created)
