"""Which entity types an item applies to ("Applies to" in the UI), for risk scenarios, controls and policy packs.

Entity type IDs differ between tenants and names (use_case, model, vendor, ...) don't, and the internal creates take
names as applies_to, so items carry names. An empty list means every entity type. Targeting can't be narrowed after
a create, so it has to be right when the item is created.
"""

from kit.clients.base import APIError


def entity_type_names(internal):
    """{entity type ID: name} on a tenant."""
    return {e["id"]: e["attributes"]["name"] for e in internal.get_data("entity_types")}


def names_for(entity, label, entity_type_ids, names):
    """Sorted entity type names for IDs from the same tenant. An unknown ID raises: dropping it would widen the item
    to every entity type on the target."""
    unknown = [i for i in entity_type_ids or [] if i not in names]
    if unknown:
        raise APIError(f"{entity}: {label!r} applies to entity type(s) {unknown} the source doesn't list")
    return sorted(names[i] for i in entity_type_ids or [])


def from_base(entity, label, attrs):
    """applies_to names from an internal base's attributes."""
    if "applies_to" not in attrs:
        # Missing (not empty) means the API shape changed; carrying on would widen targeted items to every type
        raise APIError(f"{entity}: {label!r} has no 'applies_to' field")
    return sorted(attrs["applies_to"] or [])


def add_to(attributes, item):
    """Adds applies_to to a create's attributes when the item is targeted; left out, the item applies to every type."""
    if item.get("applies_to"):
        attributes["applies_to"] = item["applies_to"]
    return attributes
