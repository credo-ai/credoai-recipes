"""Custom models, their owners and vendor links, and the model sources (AI provider names) they use. Integration API
for content and writes; the internal API reads custom field values and owners, sets owners (entity stakeholders), and
lists and creates sources (no Integration endpoint).

Content rules: a model whose name is already on the target is skipped with a warning, never adopted or changed. The
Integration API returns the same generic error for every rejected create, so each model is checked before it is
written: name free on the target, questionnaires published there, custom fields that apply to models, source present.
"""

import logging

from kit.clients.base import APIError
from kit.entities.custom_fields import field_names_for
from kit.internal.questionnaires import published_version_id
from kit.loading import MissingReference, create_target, data_id
from kit.people import People
from kit.tracking import load_extract, save_extract

ENTITY = "models"
SOURCES = "sources"
MODEL_TYPE = "model"
extract_log = logging.getLogger("kit.extract")
load_log = logging.getLogger("kit.load")


def _custom_field_values(internal, model_id):
    fields = internal.get_data(f"models/{model_id}/custom_fields")
    return [
        {"name": f["attributes"]["name"], "value": f["attributes"]["value"]}
        for f in fields
        if f["attributes"].get("value") not in (None, "", [])
    ]


def _vendors(client, model_id):
    linked = client.get_all(f"models/{model_id}/vendors")
    return {"vendor_ids": sorted(v["id"] for v in linked), "vendor_names": {v["id"]: v.get("name") for v in linked}}


def _names(values):
    return ", ".join(f["name"] for f in values)


def extract(ctx):
    items, built_in, owners, emails = [], 0, None, None
    for model in ctx.source.get_all("models"):
        if not isinstance(model.get("custom"), bool):
            # Without the flag a built-in model can't be told apart; guessing could copy one
            raise APIError(f"{ENTITY}: {model.get('name')!r} has no 'custom' flag")
        if not model["custom"]:
            built_in += 1
            continue
        if owners is None:
            # The owner is only on the internal API; kept for Phase 7 (owners are entity stakeholders)
            owners = {
                m["id"]: (m.get("attributes") or {}).get("user_id")
                for m in ctx.source_internal.get_all_cursor("models")
            }
            emails = {
                u["id"]: (u.get("attributes") or {}).get("email") for u in ctx.source_internal.get_all_cursor("users")
            }
        owner_email = emails.get(owners.get(model["id"]))
        if not owner_email:
            extract_log.warning(
                f"{ENTITY}: {model['name']!r}: its owner isn't in the source's users list (perhaps "
                f"a hidden system account); no owner email kept"
            )
        if model.get("status") not in (None, "none"):
            extract_log.warning(
                f"{ENTITY}: {model['name']!r} is {model['status']!r} on the source; the kit can't "
                f"set model status, so set it on the target by hand"
            )
        items.append(
            {
                "id": model["id"],
                "name": model["name"],
                "summary": model.get("summary"),
                "source": model.get("source"),
                "status": model.get("status"),
                "questionnaire_keys": sorted({q.rsplit("+", 1)[0] for q in model.get("questionnaire_ids") or []}),
                "custom_fields": _custom_field_values(ctx.source_internal, model["id"]),
                **_vendors(ctx.source, model["id"]),
                "owner_email": owner_email,
            }
        )
    extract_log.info(f"{ENTITY}: skipping {built_in} built-in model(s)")
    save_extract(ctx.data_dir, ENTITY, items)
    return len(items)


def _ok(resp):
    return resp is None or 200 <= resp.status_code < 300


def load(ctx):
    items = load_extract(ctx.data_dir, ENTITY)
    tracker = ctx.tracker(ENTITY)
    verb = "would create" if ctx.dry_run else "created"
    target_names = {m["name"] for m in ctx.target.get_all("models")}
    field_names = field_names_for(ctx, MODEL_TYPE)
    versions = {}

    def check(item):
        """Raises MissingReference for anything the Integration API would reject with its generic error."""
        if not item.get("source"):
            raise MissingReference(
                f"model {item['name']!r} has no source on the source tenant; set one there, then re-extract"
            )
        for key in item["questionnaire_keys"]:
            if key not in versions:
                versions[key] = published_version_id(ctx.target, key)
        unpublished = [k for k in item["questionnaire_keys"] if versions[k] is None]
        if unpublished:
            names = ", ".join(f"questionnaire {k}" for k in unpublished)
            has, it = ("has", "it") if len(unpublished) == 1 else ("have", "them")
            raise MissingReference(
                f"{names} {has} no published version on the target; publish {it} on the target, then re-run"
            )
        unknown = [f for f in item["custom_fields"] if f["name"] not in field_names]
        if unknown:
            raise MissingReference(
                f"custom field(s) {_names(unknown)} don't apply to models on the target; load custom_fields first"
            )

    links = _VendorLinks(ctx)
    owners = _Owners(ctx)
    # Pass 1: decide which models will be created, before any write
    to_create, seen = [], set()
    for item in items:
        name = item["name"]
        record = tracker.find(item["id"])
        if record:
            load_log.debug(f"{ENTITY}: {name!r} already tracked, skipping")
            ctx.summary.record(ENTITY, "skipped")
            seen.add(name)
            if record["origin"] == "created" and not ctx.dry_run:
                # An earlier run may have stopped between the create and these steps; both only add what's missing
                _repair_custom_fields(ctx, item, record["target_id"])
                links.add(item, record["target_id"], repair=True)
                owners.set(item, record["target_id"], repair=True)
            continue
        if name in seen:
            load_log.warning(
                f"{ENTITY}: more than one source model is named {name!r}; skipping this one "
                f"({item['id']}); to copy both, rename one on the source, then re-extract"
            )
            ctx.summary.record(ENTITY, "skipped")
            continue
        seen.add(name)
        if name in target_names:
            load_log.warning(
                f"{ENTITY}: a model named {name!r} is already on the target; skipping it (the kit "
                f"doesn't adopt or change existing models). If an earlier kit run created it and was "
                f"interrupted, delete it on the target and re-run."
            )
            ctx.summary.record(ENTITY, "skipped")
            continue
        try:
            check(item)
        except MissingReference as e:
            load_log.error(f"{ENTITY}: skipping {name!r}: {e}")
            ctx.summary.record(ENTITY, "failed")
            continue
        to_create.append(item)

    # Pass 2: sources the models to be created need. Only those, since sources can't be deleted.
    missing_sources = _ensure_sources(ctx, [i["source"] for i in to_create])

    for item in to_create:
        name = item["name"]
        if item.get("source") in missing_sources:
            load_log.error(
                f"{ENTITY}: skipping {name!r}: source {item['source']} isn't on the target and couldn't be created"
            )
            ctx.summary.record(ENTITY, "failed")
            continue
        payload = {
            "name": name,
            "summary": item.get("summary"),
            "source": item.get("source"),
            "questionnaire_ids": [versions[k] for k in item["questionnaire_keys"]],
        }
        try:
            target_id = create_target(ctx, ENTITY, "models", payload, item["id"], name)
        except APIError as e:
            if e.status_code is None:
                # A lost connection: the server may have created it, and a re-run would skip it by name
                load_log.error(
                    f"{ENTITY}: model {name!r} may have been created on the target before the connection "
                    f"was lost; check the target: if model {name!r} is there with no vendor links, delete "
                    f"it, then re-run ({e})"
                )
            else:
                load_log.error(f"{ENTITY}: creating {name!r} failed: {e}")
            target_id = None
        if target_id is None:
            ctx.summary.record(ENTITY, "failed")
            continue
        tracker.record(item["id"], target_id, name, "created")
        load_log.info(f"{ENTITY}: {verb} {name!r}")
        ctx.summary.record(ENTITY, "created")
        _set_custom_fields(ctx, item, target_id)
        links.add(item, target_id)
        owners.set(item, target_id)


def _ensure_sources(ctx, names):
    """Creates the source names not on the target (exact match). Returns the names that are still missing."""
    tracker = ctx.tracker(SOURCES)
    existing = {(s.get("attributes") or {}).get("name") for s in ctx.target_internal.get_all_cursor("sources")}
    folded = {n.casefold(): n for n in existing if n}
    missing = set()
    for name in dict.fromkeys(names):
        if name in existing:
            continue
        tracked = tracker.find(name)
        if tracked:
            load_log.warning(
                f"{SOURCES}: source {name} was created by an earlier run (loaded_sources.json) but "
                f"isn't on the target any more; creating it again"
            )
        if name.casefold() in folded:
            load_log.warning(
                f"{SOURCES}: the target has source {folded[name.casefold()]}, which differs from "
                f"source {name} only in case; creating {name} as a separate source"
            )
        try:
            payload = {"data": {"type": "sources", "attributes": {"name": name}}}
            target_id = create_target(
                ctx, SOURCES, "sources", payload, name, name, id_of=data_id, client=ctx.target_internal
            )
        except APIError as e:
            load_log.error(f"{SOURCES}: creating source {name} failed: {e}")
            target_id = None
        if target_id is None:
            ctx.summary.record(SOURCES, "failed")
            missing.add(name)
            continue
        if not tracked:
            tracker.record(name, target_id, name, "created")
        if ctx.dry_run:
            load_log.info(
                f"{SOURCES}: would create source {name} (sources can't be deleted, so it would stay on the target)"
            )
        else:
            load_log.info(f"{SOURCES}: created source {name} (sources can't be deleted, so it stays on the target)")
        ctx.summary.record(SOURCES, "created")
    return missing


def _set_custom_fields(ctx, item, target_id):
    values = item["custom_fields"]
    if not values:
        return
    names = _names(values)
    if ctx.dry_run:
        load_log.info(f"{ENTITY}: would set custom field(s) {names} on {item['name']!r}")
        return
    try:
        resp = ctx.target.patch(f"models/{target_id}", {"custom_fields": values})
    except APIError as e:
        problem = str(e)
    else:
        # The status only: an error body can repeat a value
        problem = None if _ok(resp) else resp.status_code
    if problem is None:
        load_log.debug(f"{ENTITY}: set custom field(s) {names} on {item['name']!r}")
        return
    message = (
        f"{ENTITY}: {item['name']!r} was created, but setting custom field(s) {names} failed ({problem}); "
        f"set them on the target by hand"
    )
    load_log.error(message)
    ctx.summary.flag(ENTITY, message)


def _norm(value):
    return sorted(value, key=str) if isinstance(value, list) else value


def _repair_custom_fields(ctx, item, target_id):
    """Sets the values of a tracked model that differ from the target's (a run stopped before its PATCH)."""
    if not item["custom_fields"]:
        return
    try:
        current = {
            f["attributes"]["name"]: f["attributes"].get("value")
            for f in ctx.target_internal.get_data(f"models/{target_id}/custom_fields")
        }
    except (APIError, KeyError, TypeError) as e:
        message = (
            f"{ENTITY}: couldn't read the custom field values of the tracked model {item['name']!r} to check "
            f"them ({e}); check custom field(s) {_names(item['custom_fields'])} on the target by hand"
        )
        load_log.error(message)
        ctx.summary.flag(ENTITY, message)
        return
    differing = [f for f in item["custom_fields"] if _norm(current.get(f["name"])) != _norm(f["value"])]
    if not differing:
        return
    load_log.info(
        f"{ENTITY}: tracked model {item['name']!r} is missing custom field value(s) {_names(differing)} "
        f"(perhaps an earlier run stopped); setting them"
    )
    _set_custom_fields(ctx, dict(item, custom_fields=differing), target_id)


class _VendorLinks:
    """Links a new model to its vendors, mapped through the vendors tracker. Built-in vendors keep their IDs, so an
    untracked vendor ID that is on the target is used as is."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.tracked = {r["source_id"]: (r["target_id"], r["name_or_key"]) for r in ctx.tracker("vendors").records}
        self._target_names = None

    def _target_vendor(self, vendor_id):
        if self._target_names is None:
            self._target_names = {v["id"]: v.get("name") for v in self.ctx.target.get_all("vendors")}
        if vendor_id in self._target_names:
            return vendor_id, self._target_names[vendor_id]
        return None

    def _fail(self, item, message):
        message = f"{ENTITY}: {item['name']!r} was created, but {message}"
        load_log.error(message)
        self.ctx.summary.flag(ENTITY, message)

    def add(self, item, model_id, repair=False):
        """repair: the model was tracked by an earlier run; only report links it adds."""
        wanted = {}
        for vendor_id in item["vendor_ids"]:
            found = self.tracked.get(vendor_id) or self._target_vendor(vendor_id)
            if found:
                wanted.setdefault(found[0], found[1])  # two source vendors can map to one target vendor
                continue
            vname = (item.get("vendor_names") or {}).get(vendor_id) or vendor_id
            self._fail(
                item,
                f"vendor {vname} ({vendor_id}) isn't tracked or on the target; load vendors first, then "
                f"link it on the target by hand",
            )
        wanted = list(wanted.items())
        if not wanted:
            return
        if self.ctx.dry_run:
            for _vid, vname in wanted:
                load_log.info(f"{ENTITY}: would link vendor {vname} to {item['name']!r}")
            return
        try:
            # Linking a vendor twice is rejected, so only the missing links are posted
            current = {v["id"] for v in self.ctx.target.get_all(f"models/{model_id}/vendors")}
        except APIError as e:
            self._fail(
                item,
                f"reading its vendor links failed ({e}); link vendor(s) "
                f"{', '.join(n for _v, n in wanted)} on the target by hand",
            )
            return
        for vid, vname in wanted:
            if vid in current:
                continue
            try:
                resp = self.ctx.target.post(f"models/{model_id}/vendors", {"id": vid})
                problem = None if _ok(resp) else f"{resp.status_code} {resp.text[:300]}"
            except APIError as e:
                problem = str(e)
            if problem:
                self._fail(item, f"linking vendor {vname} failed ({problem}); link it on the target by hand")
            elif repair:
                load_log.info(
                    f"{ENTITY}: linked vendor {vname} to the tracked model {item['name']!r} (perhaps an "
                    f"earlier run stopped before linking it)"
                )
            else:
                load_log.debug(f"{ENTITY}: linked vendor {vname} to {item['name']!r}")


class _Owners:
    """Makes the source owner (by email) the owner of a model the kit created. The Integration create makes the API
    key's user the owner, so the kit adds the mapped user as owner (an entity stakeholder, internal API), then removes
    the API key's user's owner row. A model whose owner someone else changed on the target is left alone."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.people = People(ctx)

    def _flag(self, item, message, level=logging.ERROR):
        message = f"{ENTITY}: {item['name']!r}: {message}"
        load_log.log(level, message)
        self.ctx.summary.flag(ENTITY, message)

    def _write(self, method, path, body=None):
        """None when the write worked, else what went wrong."""
        try:
            resp = (
                self.ctx.target_internal.post(path, body) if method == "post" else self.ctx.target_internal.delete(path)
            )
        except APIError as e:
            return str(e)
        return None if _ok(resp) else f"{resp.status_code} {resp.text[:300]}"

    def set(self, item, model_id, repair=False):
        """repair: the model was tracked by an earlier run; only report changes it makes."""
        email = item.get("owner_email")
        if not email:
            self._flag(
                item,
                "its owner isn't known on the source, so the API key's user owns it on the target; set "
                "the owner there by hand",
                logging.WARNING,
            )
            return
        found = self.people.lookup({"email": email})
        if found is None:
            self._flag(
                item,
                f"its owner user {email} isn't on the target, so the API key's user owns it; change the "
                f"owner on the target by hand",
                logging.WARNING,
            )
            return
        user_id = found[0]
        if self.ctx.dry_run:
            load_log.info(f"{ENTITY}: would make user {email} the owner of {item['name']!r}")
            return
        try:
            body = self.ctx.target_internal.get_json(f"models/{model_id}")
            attrs = (body.get("data") or {}).get("attributes") or {} if isinstance(body, dict) else {}
            entity_id, creator = attrs.get("entity_id"), attrs.get("user_id")
            if not entity_id:
                raise APIError(f"GET models/{model_id}: no entity_id in the response")
            rows = self.ctx.target_internal.get_data(f"entities/{entity_id}/stakeholders")
        except APIError as e:
            self._flag(
                item, f"reading its owner on the target failed ({e}); make user {email} the owner on the target by hand"
            )
            return
        owner_rows = [
            (r["id"], (r.get("attributes") or {}).get("user_id"))
            for r in rows
            if (r.get("attributes") or {}).get("role") == "owner"
        ]
        owner_ids = {uid for _rid, uid in owner_rows}
        if user_id not in owner_ids:
            if owner_ids and creator not in owner_ids:
                load_log.warning(
                    f"{ENTITY}: {item['name']!r} has an owner on the target other than user {email} and "
                    f"the API key's user (perhaps changed by hand); left the owner as is"
                )
                return
            path = f"entities/{entity_id}/stakeholders"
            problem = self._write(
                "post", path, {"data": {"type": "stakeholders", "attributes": {"user_id": user_id, "role": "owner"}}}
            )
            if problem:
                self._flag(
                    item,
                    f"making user {email} its owner failed ({problem}); the API key's user owns it, so "
                    f"change the owner on the target by hand",
                )
                return
            if repair:
                load_log.info(
                    f"{ENTITY}: made user {email} the owner of the tracked model {item['name']!r} (perhaps "
                    f"an earlier run stopped before setting it)"
                )
            else:
                load_log.debug(f"{ENTITY}: made user {email} the owner of {item['name']!r}")
        for row_id, uid in owner_rows:
            if uid != creator or uid == user_id:
                continue
            problem = self._write("delete", f"entities/{entity_id}/stakeholders/{row_id}")
            if problem:
                self._flag(
                    item,
                    f"user {email} is its owner now, but removing the API key's user as owner failed "
                    f"({problem}); remove it on the target by hand",
                )
