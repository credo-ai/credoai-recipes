"""Risk type weighting: how much each risk type counts toward the residual risk score behind the risk scoring
classification (Risk configuration, then "Scoring classifications", then "Risk type weighting" in the UI).

Internal API only: the Integration API has no risk scoring settings. The weights are keyed by risk type ID, which
differs between tenants, so they are extracted by risk type name and remapped on load. Only the tenant-wide weights
are copied; the scoring bands and the on/off switch on the same page are not.

The load sets every active risk type on the target: the source's weight where the source has that type, 0 where it
doesn't (a zero weight may be left out, which the server treats the same). Unlike other entities it replaces what the target has, since the target always has some weighting and the
point of the step is to make it match the source. The log lists the target's previous non-zero weights.
"""

import logging
from decimal import Decimal, InvalidOperation

from kit import risk_type_refs
from kit.clients.base import APIError
from kit.remap import AmbiguousMatch
from kit.tracking import load_extract, save_extract

ENTITY = "risk_type_weights"
PATH = "risk_scoring_config"
extract_log = logging.getLogger("kit.extract")
load_log = logging.getLogger("kit.load")


def _number(value):
    try:
        return Decimal(str(value))
    except InvalidOperation as e:
        raise APIError(f"{ENTITY}: weight {value!r} is not a number") from e


def _read_weights(client):
    """{risk type ID: weight entry} from a tenant's scoring configuration, or None when it has none (404).

    The server names each weight's risk type ({"name": ..., "value": ...}); a bare value is a weight for a risk type
    that is hidden or deleted.
    """
    try:
        body = client.get_json(PATH)
    except APIError as e:
        if e.status_code == 404:
            return None
        raise
    try:
        weights = body["data"]["attributes"]["formula_config"]["weights"]
    except (KeyError, TypeError) as e:
        raise APIError(f"GET {PATH}: no weights in the response", body=str(body)[:300]) from e
    if not isinstance(weights, dict):
        raise APIError(f"GET {PATH}: weights are not a map", body=str(body)[:300])
    return weights


def _value(entry):
    return entry["value"] if isinstance(entry, dict) else entry


def _percent(value):
    return f"{_number(value).quantize(Decimal('0.0001')).normalize():f}%"


def _rescaled(items):
    """The items' weights scaled to total 100%, or the items unchanged when they already do (within the server's
    rounding tolerance). Weights on risk types hidden or deleted on the source aren't extracted, which can leave the
    rest short of 100%; the server rejects that, but scores only depend on the weights' ratios, since the server divides
    each weight by the total of the weights in use. Returns None when nothing is left to scale."""
    total = sum(_number(i["weight"]) for i in items)
    if abs(total - 100) <= Decimal("0.01"):
        return items
    if not total:
        return None
    step = Decimal("0.0001")
    scaled = [dict(i, weight=str((_number(i["weight"]) * 100 / total).quantize(step))) for i in items]
    # Rounding can leave the total a little off; the largest weight takes the difference so it's exactly 100
    largest = max(scaled, key=lambda i: _number(i["weight"]))
    largest["weight"] = str(_number(largest["weight"]) + 100 - sum(_number(i["weight"]) for i in scaled))
    load_log.warning(
        f"{ENTITY}: the source's weights for active risk types total {_percent(total)} (the rest are on "
        f"risk types hidden or deleted there), so they are scaled to total 100%, keeping their ratios. "
        f"Scores are unchanged unless a use case still has scenarios of those hidden risk types"
    )
    return scaled


def extract(ctx):
    weights = _read_weights(ctx.source_internal)
    if weights is None:
        extract_log.info(f"{ENTITY}: the source has no risk scoring configuration; nothing to copy")
        weights = {}
    items = []
    for risk_type_id, entry in weights.items():
        if not isinstance(entry, dict):
            if _number(entry):
                extract_log.warning(
                    f"{ENTITY}: weight {_percent(entry)} is on risk type {risk_type_id}, which is "
                    f"hidden or deleted on the source; it isn't copied"
                )
            continue
        items.append({"id": risk_type_id, "risk_type": entry["name"], "weight": str(entry["value"])})
    items.sort(key=lambda i: i["risk_type"])
    save_extract(ctx.data_dir, ENTITY, items)
    return len(items)


def _target_weights(ctx, items):
    """{target risk type ID: weight} for every active risk type on the target, or None after logging what stops it."""
    index = risk_type_refs.target_index(ctx)
    on_target = ctx.target.get_all("risk_types")
    hidden = {rt["id"] for rt in on_target if rt.get("hidden")}
    weights, problems = {}, []
    for item in items:
        name, weight = item["risk_type"], item["weight"]
        try:
            target_id = index.lookup(name)
        except AmbiguousMatch as e:
            problems.append(str(e))
            continue
        if target_id is None or target_id in hidden:
            where = "is hidden on" if target_id else "isn't on"
            if _number(weight):
                problems.append(f"risk type {name!r} (weight {_percent(weight)}) {where} the target")
            else:
                load_log.debug(f"{ENTITY}: risk type {name!r} {where} the target; its weight is 0, so it's left out")
            continue
        weights[target_id] = weight
    for message in problems:
        load_log.error(f"{ENTITY}: {message}")
    if problems:
        load_log.error(
            f"{ENTITY}: not loaded, since the weights must total 100%. Add or unhide the risk type on "
            f"the target (or load risk_types first), then re-run"
        )
        return None
    extra = sorted(rt["name"] for rt in on_target if not rt.get("hidden") and rt["id"] not in weights)
    for rt in on_target:
        if not rt.get("hidden"):
            weights.setdefault(rt["id"], "0")
    if extra:
        load_log.info(
            f"{ENTITY}: {len(extra)} risk type(s) on the target aren't on the source and get weight 0: "
            f"{', '.join(repr(n) for n in extra)}"
        )
    # The server allows a rounding error in the total (equal weights like 18 x 5.5556) only when 100 doesn't divide
    # evenly by the number of weights sent, so the same weights can fail with 20 or 25 risk types. A zero weight
    # counts the same as a missing one, so zeros are left out until the count allows it.
    total = sum(_number(w) for w in weights.values())
    zeros = [i for i, w in weights.items() if not _number(w)]
    while total != 100 and 100 % len(weights) == 0 and zeros:
        load_log.debug(f"{ENTITY}: leaving out a zero weight so the server accepts the rounded total of {total}%")
        del weights[zeros.pop()]
    return weights


def _same(wanted, current):
    ids = set(wanted) | set(current)
    return all(_number(wanted.get(i, 0)) == _number(_value(current.get(i, 0))) for i in ids)


def _non_zero(weights):
    """'name 60%, name 40%' for the non-zero weights of a tenant's configuration, as the server returns it."""
    shown = [
        f"{entry['name'] if isinstance(entry, dict) else risk_type_id} {_percent(_value(entry))}"
        for risk_type_id, entry in weights.items()
        if _number(_value(entry))
    ]
    return ", ".join(sorted(shown)) or "none"


def load(ctx):
    items = load_extract(ctx.data_dir, ENTITY)
    if not items:
        load_log.info(f"{ENTITY}: the source has no weights; nothing to load")
        return
    items = _rescaled(items)
    if items is None:
        load_log.error(
            f"{ENTITY}: every non-zero weight on the source is on a risk type hidden or deleted there, so "
            f"there is nothing to copy; set the weighting on the target by hand"
        )
        ctx.summary.record(ENTITY, "failed")
        return
    wanted = _target_weights(ctx, items)
    if wanted is None:
        ctx.summary.record(ENTITY, "failed")
        return
    current = _read_weights(ctx.target_internal) or {}
    source_weights = ", ".join(f"{i['risk_type']} {_percent(i['weight'])}" for i in items if _number(i["weight"]))
    if _same(wanted, current):
        load_log.info(f"{ENTITY}: the target's weights already match the source ({source_weights})")
        ctx.summary.record(ENTITY, "skipped")
        return
    body = {
        "data": {"type": "risk_score_calculation_configuration", "attributes": {"formula_config": {"weights": wanted}}}
    }
    try:
        resp = ctx.target_internal.patch(PATH, body)
    except APIError as e:
        load_log.error(f"{ENTITY}: setting the weights failed: {e}")
        ctx.summary.record(ENTITY, "failed")
        return
    if resp is not None and not 200 <= resp.status_code < 300:
        load_log.error(f"{ENTITY}: setting the weights failed: {resp.status_code} {resp.text[:300]}")
        ctx.summary.record(ENTITY, "failed")
        return
    verb = "would set" if ctx.dry_run else "set"
    load_log.info(
        f"{ENTITY}: {verb} the target's weights to match the source ({source_weights}); the target's "
        f"non-zero weights were: {_non_zero(current)}"
    )
    ctx.summary.record(ENTITY, "created")
