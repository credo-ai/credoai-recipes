"""Custom questionnaires: sections and questions, with their IDs kept.

Content is read and written through the Integration API, which copies questionnaires exactly. The internal API only
lists bases and supplies their info: the Integration API's list hides questionnaires whose only version is a draft
(how the kit creates them), its include_unpublished option returns 422, and its specs carry no info.
"""

import logging
from urllib.parse import quote

from kit.clients.base import APIError
from kit.loading import create_target, remind_to_publish
from kit.tracking import load_extract, save_extract

ENTITY = "questionnaires"
extract_log = logging.getLogger("kit.extract")
load_log = logging.getLogger("kit.load")
INFO_FIELDS = ("description", "introduction", "logo_url")
TARGETS = ("use_case", "vendor", "model")
HIDE_BY_HAND = "set its Visibility to Not visible in the target's Questionnaires list (show_on_create: false)"
PROGRESS_EVERY = 10


def _path(key):
    return f"questionnaires/{quote(key, safe='')}"


def read_versions(client, key):
    """[{"version": int, "draft": bool, ...}] for a questionnaire. Raises APIError on any other shape, so a changed
    response can't read as "no published version"."""
    versions = client.get_json(f"{_path(key)}/versions")
    if not isinstance(versions, list):
        raise APIError(f"{ENTITY}: the versions of {key!r} came back in an unexpected shape", body=str(versions)[:300])
    for v in versions:
        if not isinstance(v, dict) or not isinstance(v.get("version"), int) or "draft" not in v:
            raise APIError(f"{ENTITY}: a version of {key!r} came back in an unexpected shape", body=str(v)[:300])
    return versions


def published_version_id(client, key):
    """'KEY+N' for the latest published version of a questionnaire, or None if it has none or isn't there."""
    try:
        versions = read_versions(client, key)
    except APIError as e:
        if e.status_code == 404:
            return None
        raise
    published = [v["version"] for v in versions if v["draft"] is False]
    return f"{key}+{max(published)}" if published else None


def read_sections(client, key, number):
    """The sections (with their questions) of one version of a questionnaire."""
    spec = client.get_json(f"{_path(key)}/spec", {"version": number})
    if not isinstance(spec, dict) or "sections" not in spec:
        # Missing (not empty) means the API shape changed; carrying on would create empty questionnaires
        raise APIError(f"{ENTITY}: version {number} of {key!r} has no 'sections' field", body=str(spec)[:300])
    return spec["sections"] or []


def _live_custom(bases):
    kept, built_in, archived = [], 0, 0
    for base in bases:
        attrs = base.get("attributes") or {}
        if "custom" not in attrs:
            # Without the flag a built-in questionnaire can't be told apart; guessing could copy one
            raise APIError(f"{ENTITY}: base {base['id']!r} has no 'custom' field")
        if not attrs["custom"]:
            built_in += 1
        elif attrs.get("archived"):
            archived += 1
        else:
            kept.append(base)
    extract_log.info(f"{ENTITY}: skipping {built_in} built-in and {archived} archived custom questionnaires")
    return kept


def extract(ctx):
    # Cursor paging: this endpoint ignores page[size] and page[number] and returns 30 at a time
    bases = _live_custom(ctx.source_internal.get_all_cursor("questionnaire_bases"))
    items = []
    for n, base in enumerate(bases, 1):
        key, attrs = base["id"], base["attributes"]
        if not isinstance(attrs.get("show_on_create"), bool):
            # Defaulting would leave a hidden questionnaire visible on the target, since the create stores true
            raise APIError(f"{ENTITY}: base {key!r} has no valid 'show_on_create' field")
        if attrs.get("target") not in TARGETS:
            raise APIError(
                f"{ENTITY}: base {key!r} has an unexpected target {attrs.get('target')!r}; the kit knows "
                f"{', '.join(TARGETS)}"
            )
        try:
            versions = read_versions(ctx.source, key)
            if not versions:
                extract_log.warning(f"{ENTITY}: {key!r} has no versions on the source; skipping it")
                continue
            latest = max(versions, key=lambda v: v["version"])
            sections = read_sections(ctx.source, key, latest["version"])
        except APIError as e:
            raise APIError(f"{ENTITY}: reading {key!r} failed: {e}", e.status_code, e.body) from e
        info = attrs.get("info") or {}
        items.append(
            {
                "id": key,
                "key": key,
                "name": attrs.get("name") or key,
                "target": attrs.get("target"),
                "info": {f: info.get(f) for f in INFO_FIELDS},
                "show_on_create": attrs["show_on_create"],
                "version": latest["version"],
                "draft": bool(latest.get("draft")),
                # The latest may be a draft over an earlier published version, which still needs publishing
                "published_on_source": any(not v.get("draft") for v in versions),
                "sections": sections,
            }
        )
        if n % PROGRESS_EVERY == 0:
            extract_log.info(f"{ENTITY}: read {n} of {len(bases)}")
    save_extract(ctx.data_dir, ENTITY, items)
    return len(items)


def _spec_payload(item):
    info = dict(item["info"], show_on_create=item["show_on_create"])
    return {
        "publish": False,
        "spec": {
            "key": item["key"],
            "name": item["name"],
            "target": item["target"],
            "info": info,
            "sections": item["sections"],
        },
    }


def _hide_from_create(ctx, key):
    """with_content ignores show_on_create: false and stores true, so it's set afterward. True if that worked."""
    try:
        resp = ctx.target.patch(_path(key), {"show_on_create": False})
    except APIError as e:
        problem = str(e)
    else:
        problem = None if resp is None or 200 <= resp.status_code < 300 else f"{resp.status_code} {resp.text[:200]}"
    if problem:
        load_log.error(
            f"{ENTITY}: {key!r} was created, but setting show_on_create to false failed ({problem}), so "
            f"it's visible on the target. In the target's Questionnaires list, set its Visibility to Not visible (show_on_create: false)."
        )
        return False
    return True


def _base_differences(item, attrs):
    """Names of the base fields where the target's base (its internal attributes) differs from the source item."""
    info = attrs.get("info") or {}
    # A nameless base is extracted with its key as its name
    differences = ["name"] if (attrs.get("name") or item["key"]) != item["name"] else []
    differences += [f for f in ("target", "show_on_create") if attrs.get(f) != item[f]]
    return differences + [f for f in INFO_FIELDS if info.get(f) != item["info"].get(f)]


def _match_existing(ctx, item, attrs):
    """True if the target's questionnaire with this key can be used as is. Logs how it compares with the source.

    attrs: the target base's internal attributes, whose name, target and info are compared with the source's.
    """
    key = item["key"]
    try:
        versions = read_versions(ctx.target, key)
        if not versions:
            load_log.error(
                f"{ENTITY}: {key!r} exists on the target but has no version. Delete it on the target, or "
                f"add a version there, then re-run."
            )
            return False
        latest = max(versions, key=lambda v: v["version"])
        same = read_sections(ctx.target, key, latest["version"]) == item["sections"]
    except APIError as e:
        load_log.error(f"{ENTITY}: {key!r} exists on the target, but it couldn't be read ({e}); skipping it")
        return False
    comparison = (
        "has the same sections and questions as the source"
        if same
        else "differs from the source. If an earlier kit run made it and you want a fresh copy, delete it "
        "on the target, remove its entry from data/loaded_questionnaires.json, and re-run"
    )
    message = (
        f"{ENTITY}: {key!r} already exists on the target; using it as is (the kit doesn't change it). "
        f"Its latest version, {latest['version']}, {comparison}."
    )
    differences = _base_differences(item, attrs)
    if differences:
        message += f" These fields differ from the source: {', '.join(differences)}."
    if item["show_on_create"] is False and attrs.get("show_on_create") is True:
        # A create whose response was lost never got its PATCH, so the target still shows it on create
        message += f" If an earlier kit run made it, {HIDE_BY_HAND}."
    if attrs.get("archived"):
        message += " It's archived on the target; unarchive it there before loading triggers or use cases that use it."
    load_log.warning(message)
    return True


def load(ctx):
    items = load_extract(ctx.data_dir, ENTITY)
    tracker = ctx.tracker(ENTITY)
    # Internal list: the Integration API's list doesn't show questionnaires whose only version is a draft
    target_bases = {b["id"]: b for b in ctx.target_internal.get_all_cursor("questionnaire_bases")}
    verb = "would create" if ctx.dry_run else "created"
    created, still_shown = [], []
    for item in items:
        key = item["key"]
        if tracker.find(key):
            load_log.debug(f"{ENTITY}: {key!r} already tracked, skipping")
            ctx.summary.record(ENTITY, "skipped")
            continue
        if key in target_bases:
            attrs = target_bases[key].get("attributes") or {}
            if _match_existing(ctx, item, attrs):
                tracker.record(key, key, key, "matched")
                ctx.summary.record(ENTITY, "matched")
            else:
                ctx.summary.record(ENTITY, "failed")
            continue
        try:
            payload = _spec_payload(item)
        except KeyError as e:
            load_log.error(f"{ENTITY}: skipping {key!r}: a field the kit needs is missing ({e})")
            ctx.summary.record(ENTITY, "failed")
            continue
        try:
            # Keyed content: the key is the target ID, so any 2xx is success
            target_id = create_target(
                ctx,
                ENTITY,
                "questionnaires/with_content",
                payload,
                key,
                key,
                id_of=lambda _body: key,
                client=ctx.target,
            )
        except APIError as e:
            load_log.error(f"{ENTITY}: creating {key!r} failed: {e}")
            target_id = None
        if target_id is None:
            clash = next((k for k in target_bases if k.lower() == key.lower()), None)
            if clash:
                load_log.error(
                    f"{ENTITY}: {key!r}: the failure may be because the target has "
                    f"{clash!r}, which differs only in letter case."
                )
            ctx.summary.record(ENTITY, "failed")
            continue
        # Tracked before the PATCH: the questionnaire exists now, whatever the PATCH does. In a dry run target_id is
        # the dry: placeholder, which tells the triggers step to read the questions from the extract
        tracker.record(key, target_id, key, "created")
        created.append(key)
        if item["show_on_create"] is False and not _hide_from_create(ctx, key):
            still_shown.append(key)
            ctx.summary.flag(ENTITY, f"{key!r} is visible on the target but hidden on the source: {HIDE_BY_HAND}")
        load_log.info(f"{ENTITY}: {verb} {key!r} as a draft")
        ctx.summary.record(ENTITY, "created")
    remind_to_publish(ctx, ENTITY, items, created)
    if still_shown:
        load_log.error(
            f"{ENTITY}: {len(still_shown)} new questionnaires are visible on the target but hidden on the "
            f"source (show_on_create couldn't be set to false). In the target's Questionnaires list, set "
            f"their Visibility to Not visible: {', '.join(still_shown)}"
        )
