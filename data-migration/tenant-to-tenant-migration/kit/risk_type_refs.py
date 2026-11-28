"""Risk type references. IDs differ between tenants and names don't, so references are carried by name."""

import logging

from kit.clients.base import APIError
from kit.loading import MissingReference
from kit.remap import NameIndex

log = logging.getLogger("kit.load")


def names_by_id(client):
    """{risk type ID: name} for every risk type on a tenant, built-in and custom."""
    return {rt["id"]: rt["name"] for rt in client.get_all("risk_types")}


def add_risk_type_names(item, names):
    """Risk type IDs differ between tenants; names don't. Unknown IDs are kept visible as 'unknown:<id>'."""
    if "risk_type_ids" not in item:
        # Missing (not null) means the API shape changed; carrying on would drop every risk type reference
        raise APIError(f"{item.get('id')!r} has no 'risk_type_ids' field")
    item["risk_type_names"] = [names.get(i, f"unknown:{i}") for i in item["risk_type_ids"] or []]


def target_index(ctx):
    """Target risk types by name, plus any created earlier in this run (so a dry run sees them too)."""
    items = ctx.target.get_all("risk_types")
    index = NameIndex(items)
    on_target = {rt["id"] for rt in items}
    added = set()
    for record in ctx.tracker("risk_types").records:
        name, target_id = record["name_or_key"], record["target_id"]
        if target_id in on_target:
            continue
        if str(target_id).startswith("dry:"):
            # Two source risk types with one name share one placeholder; adding it twice would make it ambiguous
            if (name, target_id) not in added:
                index.add(name, target_id)
                added.add((name, target_id))
        else:
            log.warning(
                f"risk_types: {name!r} is tracked as {target_id} but isn't on the target any more; "
                f"references to it are matched by name only"
            )
    return index


def target_ids(names, index, label):
    """Target IDs for risk type names. Raises MissingReference for any name the target lacks.

    An 'unknown:<id>' reference points at a risk type the source itself no longer has, so the target won't
    have it either; it's dropped with a warning and the item (named by label) loads without it.
    """
    ids = []
    for name in names:
        if name.startswith("unknown:"):
            log.warning(f"{label}: risk type ID {name[8:]} doesn't exist on the source; loading without it")
            continue
        target_id = index.lookup(name)
        if target_id is None:
            raise MissingReference(f"risk type {name!r} not on the target")
        ids.append(target_id)
    return ids
