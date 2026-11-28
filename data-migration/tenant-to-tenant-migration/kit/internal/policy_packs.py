"""Custom policy packs, with the controls they contain.

Internal API: the Integration API's create makes only the pack's base (no version, so no controls), and it can't
see packs whose only version is a draft, which is how the kit creates them. The server gives each control inside a
new pack a new ID; evidence requirement keys are kept.
"""

import logging
from urllib.parse import quote

from kit import applies_to
from kit.clients.base import APIError
from kit.loading import load_versioned, remind_to_publish
from kit.ownership import keep_custom
from kit.tracking import load_extract, save_extract

ENTITY = "policy_packs"
extract_log = logging.getLogger("kit.extract")
load_log = logging.getLogger("kit.load")
# Real pack controls have only these fields; anything else is copied as is, untranslated
CONTROL_FIELDS = {"id", "info", "evidence_requirements"}


def extract(ctx):
    bases = keep_custom(
        ENTITY,
        ctx.source_internal.get_all_cursor("policy_pack_bases"),
        ownership_of=lambda b: b["attributes"].get("ownership_type"),
        archived_of=lambda b: b["attributes"].get("archived"),
    )
    items = []
    for base in bases:
        key = base["id"]
        try:
            versions = ctx.source_internal.get_data(f"policy_pack_bases/{quote(key, safe='')}/versions")
        except APIError as e:
            raise APIError(f"{ENTITY}: reading versions of {key!r} failed: {e}", e.status_code, e.body) from e
        if not versions:
            extract_log.warning(f"{ENTITY}: {key!r} has no versions on the source; skipping it")
            continue
        latest = max(versions, key=lambda v: v["attributes"]["version"])["attributes"]
        if "controls" not in latest:
            # Missing (not null) means the API shape changed; carrying on would create packs with no controls
            raise APIError(f"{ENTITY}: the latest version of {key!r} has no 'controls' field")
        _warn_untranslated(key, latest["controls"] or [])
        items.append(
            {
                "id": key,
                "key": key,
                "version": latest["version"],
                "draft": bool(latest.get("draft")),
                "base_info": base["attributes"].get("info") or {},
                "applies_to": applies_to.from_base(ENTITY, key, base["attributes"]),
                "version_info": latest.get("info") or {},
                "controls": latest["controls"] or [],
                # The latest may be a draft over an earlier published version, which still needs publishing
                "published_on_source": any(not v["attributes"].get("draft") for v in versions),
            }
        )
    save_extract(ctx.data_dir, ENTITY, items)
    return len(items)


def _warn_untranslated(key, controls):
    for control in controls:
        extra = sorted(f for f in control if f not in CONTROL_FIELDS and (f != "risk_type_ids" or control[f]))
        if extra:
            extract_log.warning(
                f"{ENTITY}: control {control.get('id')!r} in pack {key!r} has {', '.join(extra)}, which the kit copies "
                f"as is: risk type IDs inside pack controls aren't translated between tenants yet. Check it on the "
                f"target after loading."
            )


def _base_payload(item):
    info = dict(item["base_info"])
    info["name"] = info.get("name") or item["key"]  # the API requires a name
    return {
        "data": {"type": "policy_pack_bases", "attributes": applies_to.add_to({"id": item["key"], "info": info}, item)}
    }


def load(ctx):
    items = load_extract(ctx.data_dir, ENTITY)
    # Internal list, since the Integration API doesn't show packs that only have a draft
    target_bases = ctx.target_internal.get_all_cursor("policy_pack_bases")
    keys = {i["key"] for i in items}
    for b in target_bases:
        if b["attributes"].get("archived") and b["id"] in keys:
            load_log.warning(f"{ENTITY}: {b['id']!r} exists on the target but is archived; it will be matched as is")
    created = load_versioned(
        ctx,
        ENTITY,
        items,
        existing_keys={b["id"] for b in target_bases},
        client=ctx.target_internal,
        base_path="policy_pack_bases",
        base_payload=_base_payload,
        version_path=lambda key: f"policy_pack_bases/{key}/versions",
        version_payload=lambda i: {"data": {"attributes": {"info": i["version_info"], "controls": i["controls"]}}},
    )
    remind_to_publish(ctx, ENTITY, items, created)
