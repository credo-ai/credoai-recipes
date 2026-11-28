"""Load steps shared by entity modules."""

import logging
import re
from urllib.parse import quote

from kit.clients.base import APIError
from kit.remap import AmbiguousMatch, NameIndex, exact

log = logging.getLogger("kit.load")

_ALREADY_EXISTS = re.compile(r"already|taken|unique|exists", re.IGNORECASE)


def data_id(body):
    """The ID in an internal API response: {"data": {"id": ...}}."""
    return body["data"]["id"]


class MissingReference(Exception):
    """An item points at something that doesn't exist on the target, such as an entity type."""


class LoadStopped(Exception):
    """A load refused to go on, so later entities must not run (for example, users the target would email). The
    entity logs and flags the reason before raising."""


def create_target(ctx, entity, path, payload, source_id, label, id_of=lambda body: body["id"], client=None):
    """POST one new item. Returns its target ID (a dry: placeholder in dry run), or None after logging the failure."""
    resp = (client or ctx.target).post(path, payload)
    if resp is None:
        return f"dry:{source_id}"
    if 200 <= resp.status_code < 300:
        # Parsed leniently: a caller that already knows the ID doesn't need a body
        try:
            body = resp.json()
        except ValueError:
            body = None
        try:
            return id_of(body)
        except (KeyError, TypeError):
            log.error(
                f"{entity}: {label!r} was probably created on the target, but the response had no ID, so it "
                f"isn't tracked. Check the target before re-running. Response: {resp.status_code} {resp.text[:300]}"
            )
            return None
    if resp.status_code == 422 and _ALREADY_EXISTS.search(resp.text):
        log.error(
            f"{entity}: {label!r} already exists on the target but wasn't found by name or key first; "
            f"check for a near-duplicate. Response: {resp.text[:300]}"
        )
    else:
        log.error(f"{entity}: creating {label!r} failed: {resp.status_code} {resp.text[:300]}")
    return None


def _differences(source_item, target_item, fields):
    def norm(value):
        return sorted(value) if isinstance(value, list) else value

    return [f for f in fields if norm(source_item.get(f)) != norm(target_item.get(f))]


def load_reference(
    ctx,
    entity,
    items,
    target_items,
    build_payload,
    path,
    name_of=lambda i: i["name"],
    normalize=exact,
    compare=(),
    client=None,
    id_of=lambda body: body["id"],
    after_create=None,
    label_of=None,
):
    """Shared reference data: skip tracked items, adopt same-named target items as 'matched', create the rest.

    name_of gives what items are matched and tracked by (the name, unless the entity matches by something else, such
    as an ID); label_of gives what the logs call an item (default: the same as name_of).

    compare: fields to check on adopted items; differences are logged as warnings, since later values may not fit.
    after_create(item, target_id): extra work for a newly created item (in dry run, target_id is the dry:
    placeholder). It must handle its own failures.
    """
    tracker = ctx.tracker(entity)
    index = NameIndex(target_items, name_of=name_of, normalize=normalize)
    target_by_id = {t["id"]: t for t in target_items}
    created_names = set()
    verb = "would create" if ctx.dry_run else "created"
    for item in items:
        name = name_of(item)
        label = label_of(item) if label_of else name
        if tracker.find(item["id"]):
            log.debug(f"{entity}: {label!r} already tracked, skipping")
            ctx.summary.record(entity, "skipped")
            continue
        if normalize(name) in created_names:
            log.warning(
                f"{entity}: more than one source item named {label!r}; mapping this one to the item created "
                f"for the first"
            )
        try:
            existing = index.lookup(name)
            payload = None if existing else build_payload(item)
        except (AmbiguousMatch, MissingReference) as e:
            log.error(f"{entity}: skipping {label!r}: {e}")
            ctx.summary.record(entity, "failed")
            continue
        except KeyError as e:
            log.error(f"{entity}: skipping {label!r}: a field the kit needs is missing ({e})")
            ctx.summary.record(entity, "failed")
            continue
        if existing:
            tracker.record(item["id"], existing, name, "matched")
            log.info(f"{entity}: matched existing {label!r}")
            differences = (
                _differences(item, target_by_id.get(existing, {}), compare) if existing in target_by_id else []
            )
            if differences:
                log.warning(f"{entity}: existing target {label!r} differs from the source: {', '.join(differences)}")
            ctx.summary.record(entity, "matched")
            continue
        try:
            target_id = create_target(ctx, entity, path, payload, item["id"], label, id_of=id_of, client=client)
        except APIError as e:
            log.error(f"{entity}: creating {label!r} failed: {e}")
            target_id = None
        if target_id is None:
            ctx.summary.record(entity, "failed")
            continue
        tracker.record(item["id"], target_id, name, "created")
        index.add(name, target_id)
        created_names.add(normalize(name))
        log.info(f"{entity}: {verb} {label!r}")
        ctx.summary.record(entity, "created")
        if after_create:
            after_create(item, target_id)


def _remove_base(entity, client, path, key):
    """Deletes a base whose version failed, so a re-run doesn't mistake it for an existing item."""
    try:
        resp = client.delete(path)
    except APIError as e:
        resp, problem = None, str(e)
    else:
        ok = resp is None or 200 <= resp.status_code < 300 or resp.status_code == 404
        problem = None if ok else resp.status_code
    if problem is not None:
        log.error(
            f"{entity}: {key!r} may have been left on the target, and removing it failed ({problem}). Check it "
            f"on the target before re-running: if it has no version, delete it."
        )
    elif resp is not None and resp.status_code == 404:
        log.error(f"{entity}: the new base of {key!r} was already gone after its version failed")
    else:
        log.error(f"{entity}: removed the new base of {key!r} because its version failed")


def _has_version(entity, client, path, key):
    """True if the existing target item at path has at least one version. Logs why not otherwise."""
    try:
        versions = client.get_data(path)
    except APIError as e:
        log.error(f"{entity}: {key!r} exists on the target, but its versions couldn't be read ({e}); skipping it")
        return False
    if not versions:
        log.error(
            f"{entity}: {key!r} exists on the target but has no version (a half-made item, perhaps from an "
            f"interrupted run). Delete it on the target, or add a version there, then re-run."
        )
        return False
    return True


def load_versioned(ctx, entity, items, existing_keys, client, base_path, base_payload, version_path, version_payload):
    """Keyed content made of a base and one version, such as controls and policy packs. Returns the created keys.

    Skips tracked items. A key already on the target with a version is recorded as 'matched' and left unchanged; one
    without a version fails. Otherwise creates the base, then the version; if the version fails, deletes the new base
    so a re-run doesn't mistake a half-made item for an existing one.
    version_path takes the key escaped for use in a URL; payloads and tracking use the raw key.
    """
    tracker = ctx.tracker(entity)
    verb = "would create" if ctx.dry_run else "created"
    created = []
    for item in items:
        key = item["key"]
        path_key = quote(key, safe="")
        if tracker.find(key):
            log.debug(f"{entity}: {key!r} already tracked, skipping")
            ctx.summary.record(entity, "skipped")
            continue
        # Keyed content is tracked with the key as both source and target ID.
        if key in existing_keys:
            if not _has_version(entity, client, version_path(path_key), key):
                ctx.summary.record(entity, "failed")
                continue
            tracker.record(key, key, key, "matched")
            log.warning(f"{entity}: {key!r} already exists on the target; using it as is (the kit doesn't change it)")
            ctx.summary.record(entity, "matched")
            continue
        try:
            base = base_payload(item)
            version = version_payload(item)
        except (AmbiguousMatch, MissingReference) as e:
            log.error(f"{entity}: skipping {key!r}: {e}")
            ctx.summary.record(entity, "failed")
            continue
        except KeyError as e:
            log.error(f"{entity}: skipping {key!r}: a field the kit needs is missing ({e})")
            ctx.summary.record(entity, "failed")
            continue
        try:
            base_id = create_target(ctx, entity, base_path, base, key, key, id_of=lambda _body: key, client=client)
        except APIError as e:
            log.error(f"{entity}: creating {key!r} failed: {e}")
            base_id = None
        if base_id is None:
            ctx.summary.record(entity, "failed")
            continue
        try:
            # The version's ID isn't used, so any 2xx is success: a missing one mustn't delete a complete item
            version_id = create_target(
                ctx,
                entity,
                version_path(path_key),
                version,
                key,
                f"{key} version",
                id_of=lambda _body: key,
                client=client,
            )
        except APIError as e:
            log.error(f"{entity}: creating {key + ' version'!r} failed: {e}")
            version_id = None
        if version_id is None:
            _remove_base(entity, client, f"{base_path}/{path_key}", key)
            ctx.summary.record(entity, "failed")
            continue
        # base_id is the key, or its dry: placeholder in a dry run, which later steps read as "would be created"
        tracker.record(key, base_id, key, "created")
        created.append(key)
        log.info(f"{entity}: {verb} {key!r} as a draft")
        ctx.summary.record(entity, "created")
    return created


def _listing(keys, shown=20):
    """': K1, K2' with at most `shown` keys, then 'and N more'."""
    text = ": " + ", ".join(keys[:shown])
    if len(keys) > shown:
        text += f" and {len(keys) - shown} more"
    return text


def remind_to_publish(ctx, entity, items, created_keys):
    """The kit creates drafts. Lists the new items that were published on the source.

    An item whose latest source version is a draft over an earlier published one is listed separately: publishing
    the copy would release the source's unpublished changes.
    """
    if not created_keys:
        return
    created = set(created_keys)
    new = [i for i in items if i["key"] in created]
    draft_over = [i["key"] for i in new if i.get("draft") and i.get("published_on_source")]
    published = [
        i["key"] for i in new if i["key"] not in draft_over and i.get("published_on_source", not i.get("draft"))
    ]
    if ctx.dry_run:
        message = (
            f"{entity}: {len(created)} new items would be created as drafts; {len(published)} of them were "
            f"published on the source and would need publishing on the target"
        )
    else:
        message = (
            f"{entity}: new items are drafts. {len(published)} of {len(created)} were published on the "
            f"source; publish them on the target when ready"
        )
    if published:
        message += _listing(published)
    log.info(message)
    if draft_over:
        are = "would be" if ctx.dry_run else "are"
        log.warning(
            f"{entity}: {len(draft_over)} new items {are} copies of the source's unpublished draft, which sits "
            f"over an earlier published version; review them before publishing{_listing(draft_over)}"
        )
