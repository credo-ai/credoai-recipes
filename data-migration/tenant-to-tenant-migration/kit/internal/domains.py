"""Custom domains. Internal API only: the Integration API has no domains endpoints.

A domain's ID is a slug (built-in ones too, for example 'anomaly_detection'), and the create takes it, so the source
ID is kept and domains are matched by ID, not name. A target domain with the same ID is adopted as 'matched' and left
unchanged.
"""

import logging

from kit.clients.base import APIError
from kit.loading import data_id, load_reference
from kit.tracking import load_extract, save_extract

ENTITY = "domains"
extract_log = logging.getLogger("kit.extract")


def _label(item):
    return f"{item['name']} ({item['id']})"


def extract(ctx):
    items, built_in = [], 0
    for d in ctx.source_internal.get_data("domains"):  # not paged: the server returns every domain
        attrs = d.get("attributes") or {}
        if not isinstance(attrs.get("custom"), bool):
            # Without the flag a built-in domain can't be told apart; guessing could copy one
            raise APIError(f"{ENTITY}: {d['id']!r} has no 'custom' flag")
        if not attrs["custom"]:
            built_in += 1
            continue
        items.append({"id": d["id"], "name": attrs.get("name"), "description": attrs.get("description")})
    extract_log.info(f"{ENTITY}: skipping {built_in} built-in domain(s)")
    save_extract(ctx.data_dir, ENTITY, items)
    return len(items)


def load(ctx):
    client = ctx.target_internal
    target_domains = [{"id": d["id"], **(d.get("attributes") or {})} for d in client.get_data("domains")]

    def build_payload(item):
        # The ID goes inside attributes; a top-level data.id gets 422 "id can't be blank"
        return {
            "data": {
                "type": "domains",
                "attributes": {"id": item["id"], "name": item["name"], "description": item.get("description")},
            }
        }

    load_reference(
        ctx,
        ENTITY,
        items=load_extract(ctx.data_dir, ENTITY),
        target_items=target_domains,
        build_payload=build_payload,
        path="domains",
        name_of=lambda i: i["id"],
        label_of=_label,
        compare=("name", "description"),
        client=client,
        id_of=data_id,
    )
