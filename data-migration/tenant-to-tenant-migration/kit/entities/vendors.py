"""Custom vendors. Integration API for content and writes; the internal API says which vendors are built in (the
Integration API has no marker), reads custom field values and lists entity types."""

import logging

from kit.clients.base import APIError
from kit.entities.custom_fields import field_names_for
from kit.internal.questionnaires import published_version_id
from kit.loading import MissingReference, load_reference
from kit.tracking import load_extract, save_extract

ENTITY = "vendors"
VENDOR_TYPE = "vendor"
extract_log = logging.getLogger("kit.extract")
load_log = logging.getLogger("kit.load")


def _custom_field_values(internal, vendor_id):
    fields = internal.get_data(f"vendors/{vendor_id}/custom_fields")
    return [
        {"name": f["attributes"]["name"], "value": f["attributes"]["value"]}
        for f in fields
        if f["attributes"].get("value") not in (None, "", [])
    ]


def extract(ctx):
    internal = {v["id"]: v.get("attributes") or {} for v in ctx.source_internal.get_all_cursor("vendors")}
    items, built_in = [], 0
    for vendor in ctx.source.get_all("vendors"):
        attrs = internal.get(vendor["id"])
        if attrs is None:
            raise APIError(f"{ENTITY}: {vendor['name']!r} is in the Integration list but not the internal one")
        if not isinstance(attrs.get("custom"), bool):
            # Without the flag a built-in vendor can't be told apart; guessing could copy one
            raise APIError(f"{ENTITY}: {vendor['name']!r} has no 'custom' flag")
        if not attrs["custom"]:
            built_in += 1
            continue
        if vendor.get("status") not in (None, "none"):
            extract_log.warning(
                f"{ENTITY}: {vendor['name']!r} is {vendor['status']!r} on the source; the kit can't set "
                f"approval status, so set it on the target by hand"
            )
        items.append(
            {
                "id": vendor["id"],
                "name": vendor["name"],
                "description": vendor.get("description"),
                "questionnaire_keys": sorted({q.rsplit("+", 1)[0] for q in attrs.get("questionnaire_ids") or []}),
                "custom_fields": _custom_field_values(ctx.source_internal, vendor["id"]),
            }
        )
    extract_log.info(f"{ENTITY}: skipping {built_in} built-in vendors")
    save_extract(ctx.data_dir, ENTITY, items)
    return len(items)


def load(ctx):
    field_names = field_names_for(ctx, VENDOR_TYPE)
    versions = {}

    def build_payload(item):
        for key in item["questionnaire_keys"]:
            if key not in versions:
                versions[key] = published_version_id(ctx.target, key)
        unpublished = [k for k in item["questionnaire_keys"] if versions[k] is None]
        if unpublished:
            raise MissingReference(
                f"questionnaire(s) {unpublished} have no published version on the target; publish "
                f"it on the target, then re-run"
            )
        unknown = [f["name"] for f in item["custom_fields"] if f["name"] not in field_names]
        if unknown:
            raise MissingReference(
                f"custom field(s) {unknown} don't apply to vendors on the target; load custom_fields first"
            )
        # Values are set after the create: the server creates the vendor before checking them, so a bad value
        # would leave an untracked vendor behind an error response
        return {
            "name": item["name"],
            "description": item.get("description"),
            "questionnaire_ids": [versions[k] for k in item["questionnaire_keys"]],
        }

    def set_custom_fields(item, target_id):
        values = item["custom_fields"]
        if not values:
            return
        names = [f["name"] for f in values]
        if ctx.dry_run:
            load_log.info(f"{ENTITY}: would set custom field(s) {names} on {item['name']!r}")
            return
        try:
            resp = ctx.target.patch(f"vendors/{target_id}", {"custom_fields": values})
        except APIError as e:
            problem = str(e)
        else:
            # The status only: an error body can repeat a value
            problem = None if resp is None or 200 <= resp.status_code < 300 else resp.status_code
        if problem is None:
            load_log.debug(f"{ENTITY}: set custom field(s) {names} on {item['name']!r}")
            return
        message = (
            f"{ENTITY}: {item['name']!r} was created, but setting custom field(s) {names} failed ({problem}); "
            f"set them on the target by hand"
        )
        load_log.error(message)
        ctx.summary.flag(ENTITY, message)

    load_reference(
        ctx,
        ENTITY,
        items=load_extract(ctx.data_dir, ENTITY),
        target_items=ctx.target.get_all("vendors"),
        build_payload=build_payload,
        path="vendors",
        compare=("description",),
        after_create=set_custom_fields,
    )
