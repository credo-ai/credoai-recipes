"""Compare locally authored content against what the server returns.

Shared by `controls_sync.py` and `questionnaire_sync.py` so both skip the write when nothing
changed. The server adds fields we never author (ids, timestamps, nulls) and may not return some
fields we do author, so a plain `==` would never match. See `project` and `restrict`.
"""

from __future__ import annotations

import json


def normalize(value, *, ordered: bool = False):
    """Recursively sort lists so reordering (at any nesting depth — e.g. a reordered
    `governance_bounds` list inside an evidence requirement) doesn't register as a content
    difference. Dict key order never matters for `==`, so only lists need this. Pass
    `ordered=True` where list order is meaningful (e.g. questionnaire sections and questions)."""
    if isinstance(value, list):
        normalized_items = [normalize(v, ordered=ordered) for v in value]
        if ordered:
            return normalized_items
        return sorted(normalized_items, key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(value, dict):
        # A null value and an absent key mean the same thing here: the server returns every
        # unset field as null, while the yaml simply omits it.
        return {
            k: normalize(v, ordered=ordered) for k, v in value.items() if v is not None
        }
    return value


def union(a, b):
    """Merge two values' key sets recursively: dicts merge by key, lists concatenate, anything
    else keeps `a`. Used to get one combined "shape" out of a list of items."""
    if isinstance(a, dict) and isinstance(b, dict):
        merged = dict(a)
        for key, value in b.items():
            merged[key] = union(a[key], value) if key in a else value
        return merged
    if isinstance(a, list) and isinstance(b, list):
        return a + b
    return a


def project(value, template, *, ordered: bool = False):
    """Keep only the keys/shape present in `template`, recursively.

    The server may return fields our local yaml doesn't define (ids, timestamps, etc. added at
    any nesting depth). Comparing raw equality against that would never match. Projecting the
    server's response down to our template's own shape before comparing means only fields we
    actually author can cause a mismatch.

    List items are projected onto the merged shape of all template items, since their order may
    differ from the server's. With `ordered=True` (order is meaningful) each item is projected onto
    the template item at the same position instead, so a field set on one template item doesn't
    drag the server's default for it into every other item.
    """
    if isinstance(template, dict):
        if not isinstance(value, dict):
            return value
        return {
            k: project(value.get(k), template[k], ordered=ordered) for k in template
        }
    if isinstance(template, list):
        if not isinstance(value, list):
            return value
        if not template:
            return value
        if ordered and len(value) == len(template):
            return [project(v, t, ordered=True) for v, t in zip(value, template)]
        shape = template[0]
        for item in template[1:]:
            shape = union(shape, item)
        return [project(v, shape, ordered=ordered) for v in value]
    return value


def restrict(desired, server, unreturned: set[str], path: str = ""):
    """Keep only the parts of `desired` that the server also returns, recursively.

    The reverse of `project`. If the server never echoes a field we author (it accepts the write
    but doesn't return it on read), that field can never compare equal, so every run would look
    like a change and post a new version. Dropping it from the comparison — and noting it in
    `unreturned` — means only fields we can actually read back decide whether content differs.
    """
    if isinstance(desired, dict):
        if not isinstance(server, dict):
            return desired
        restricted = {}
        for key, value in desired.items():
            if value is None:
                # Unset locally: the server is free to fill in its own default (e.g.
                # `multiple: false`), so there is nothing to compare.
                continue
            if key in server:
                restricted[key] = restrict(
                    value, server[key], unreturned, f"{path}.{key}"
                )
            else:
                unreturned.add(f"{path}.{key}".lstrip("."))
        return restricted
    if isinstance(desired, list):
        if not isinstance(server, list) or not server:
            return desired
        shape = server[0]
        for item in server[1:]:
            shape = union(shape, item)
        return [restrict(d, shape, unreturned, f"{path}[]") for d in desired]
    return desired
