"""Matching source items to target items across tenants. One place, so every entity matches the same way."""

from collections import defaultdict


class AmbiguousMatch(Exception):
    pass


def exact(value):
    return (value or "").strip()


def casefold(value):
    return (value or "").strip().casefold()


class NameIndex:
    """Target IDs by name (or email). Two target items with the same name raise AmbiguousMatch: never guess."""

    def __init__(self, items=(), name_of=lambda i: i["name"], id_of=lambda i: i["id"], normalize=exact):
        self._normalize = normalize
        self._ids = defaultdict(list)
        for item in items:
            self.add(name_of(item), id_of(item))

    def add(self, name, target_id):
        self._ids[self._normalize(name)].append(target_id)

    def lookup(self, name):
        ids = self._ids.get(self._normalize(name), [])
        if len(ids) > 1:
            raise AmbiguousMatch(f"{len(ids)} target items named {name!r}: {ids}")
        return ids[0] if ids else None
