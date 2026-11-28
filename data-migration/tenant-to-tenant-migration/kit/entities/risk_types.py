"""Custom risk types. Integration API only."""

from kit.loading import load_reference
from kit.tracking import load_extract, save_extract

ENTITY = "risk_types"


def extract(ctx):
    items = [rt for rt in ctx.source.get_all("risk_types") if rt.get("custom")]
    save_extract(ctx.data_dir, ENTITY, items)
    return len(items)


def _payload(rt):
    # The create rejects a null description (422 "Input should be a valid string") but defaults a missing one to ""
    payload = {"name": rt["name"]}
    if rt.get("description") is not None:
        payload["description"] = rt["description"]
    return payload


def load(ctx):
    load_reference(
        ctx,
        ENTITY,
        items=load_extract(ctx.data_dir, ENTITY),
        target_items=ctx.target.get_all("risk_types"),
        build_payload=_payload,
        path="risk_types",
    )
