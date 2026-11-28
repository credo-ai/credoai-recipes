"""Teams and their members. Internal API only: the Integration API has no teams endpoints."""

import logging
from pathlib import Path

from kit.clients.base import APIError
from kit.loading import MissingReference, data_id, load_reference
from kit.remap import AmbiguousMatch, NameIndex, casefold
from kit.tracking import load_extract, save_extract

ENTITY = "teams"
extract_log = logging.getLogger("kit.extract")
load_log = logging.getLogger("kit.load")


def _members(n):
    return f"{n} member" if n == 1 else f"{n} members"


def _memberships(client, team_id):
    """[(user ID, role)] from teams/{id}?include=memberships."""
    body = client.get_json(f"teams/{team_id}", {"include": "memberships"}) or {}
    expected = len((((body.get("data") or {}).get("relationships") or {}).get("memberships") or {}).get("data") or [])
    found = [
        ((m.get("attributes") or {}).get("member_id"), (m.get("attributes") or {}).get("role"))
        for m in body.get("included") or []
        if m.get("type") == "team_memberships"
    ]
    if len(found) != expected:
        raise APIError(f"{ENTITY}: team {team_id!r} lists {expected} memberships but returned {len(found)}")
    return found


def extract(ctx):
    emails = {u["id"]: (u.get("attributes") or {}).get("email") for u in ctx.source_internal.get_all_cursor("users")}
    items = []
    for team in ctx.source_internal.get_all_cursor("teams"):
        members = []
        for user_id, role in _memberships(ctx.source_internal, team["id"]):
            if not emails.get(user_id):
                # System accounts are hidden from GET users but can still be team members
                extract_log.warning(
                    f"{ENTITY}: {team['attributes']['name']!r}: member {user_id} isn't in the "
                    f"source's users list (perhaps a hidden system account); leaving it out"
                )
                continue
            members.append({"email": emails[user_id], "role": role})
        items.append(
            {
                "id": team["id"],
                "name": team["attributes"]["name"],
                "description": team["attributes"].get("description"),
                "members": sorted(members, key=lambda m: m["email"].lower()),
            }
        )
    save_extract(ctx.data_dir, ENTITY, items)
    return len(items)


def _ok(resp):
    return resp is None or 200 <= resp.status_code < 300


def load(ctx):
    client = ctx.target_internal
    all_users = [
        {
            "id": u["id"],
            "email": (u.get("attributes") or {}).get("email") or "",
            "deleted": bool((u.get("attributes") or {}).get("deleted_at")),
        }
        for u in client.get_all_cursor("users")
    ]
    # Deleted accounts are left out of the index so a member never maps to one
    users = NameIndex([u for u in all_users if not u["deleted"]], name_of=lambda u: u["email"], normalize=casefold)
    for record in ctx.tracker("users").records:  # this run's dry-run users, and anything tracked but not yet listed
        try:
            known = users.lookup(record["name_or_key"])
        except AmbiguousMatch:
            known = True
        if known is None:
            users.add(record["name_or_key"], record["target_id"])
    email_of = {u["id"]: u["email"] for u in all_users if not u["deleted"]}
    for u in all_users:
        email_of.setdefault(u["id"], u["email"])

    def names(user_ids):
        return ", ".join(email_of.get(uid) or uid for uid in user_ids)

    # Members the users load is meant to bring over. One of these not yet on the target stops its team being
    # created; any other missing member was left out of the users extract and is skipped with a warning.
    users_file = Path(ctx.data_dir) / "users.json"
    extracted = {casefold(u["email"]) for u in load_extract(ctx.data_dir, "users")} if users_file.exists() else set()

    def absent(item):
        """Emails of the item's members with no target user (a dry-run users record counts as present)."""
        missing = []
        for member in item["members"]:
            try:
                found = users.lookup(member["email"])
            except AmbiguousMatch:
                continue  # reported when the members are added
            if not found:
                missing.append(member["email"])
        return missing

    def build_payload(item):
        pending = [e for e in absent(item) if casefold(e) in extracted]
        if pending:
            verb = "isn't" if len(pending) == 1 else "aren't"
            raise MissingReference(
                f"{_members(len(pending))} {verb} on the target yet ({', '.join(pending)}). "
                f"Load users first; if users already ran, fix the errors it logged for these emails, then re-run."
            )
        return {"data": {"type": "teams", "attributes": {"name": item["name"], "description": item.get("description")}}}

    def add_members(item, team_id):
        name = item["name"]
        wanted, missing = [], []
        for member in item["members"]:
            try:
                user_id = users.lookup(member["email"])
            except AmbiguousMatch as e:
                load_log.error(f"{ENTITY}: {name!r}: member {member['email']!r} skipped: {e}")
                ctx.summary.flag(
                    ENTITY, f"{name!r}: member {member['email']!r} not added (more than one target user has that email)"
                )
                continue
            if user_id:
                wanted.append({"id": user_id, "type": "users", "role": member["role"]})
            else:
                missing.append(member["email"])
        if missing:
            # build_payload already refused any team missing an extracted user, so these were left out of the extract
            load_log.warning(
                f"{ENTITY}: {name!r}: {_members(len(missing))} not added because they were left out of "
                f"the users extract (by the email-domain settings, or no users were extracted): "
                f"{', '.join(missing)}. Add them to the team on the target by hand if wanted."
            )
        if ctx.dry_run:
            load_log.info(
                f"{ENTITY}: would add {_members(len(wanted))} to {name!r} and remove anyone else the server adds"
            )
            return
        if wanted:
            try:
                resp = client.patch(f"teams/{team_id}/relationships/members", {"data": wanted})
                problem = None if _ok(resp) else f"{resp.status_code} {resp.text[:300]}"
            except APIError as e:
                problem = str(e)
            if problem:
                load_log.error(
                    f"{ENTITY}: adding members to {name!r} failed: {problem}. Nothing was removed from "
                    f"the team; check its members on the target and fix them by hand."
                )
                ctx.summary.flag(ENTITY, f"{name!r}: members not added")
                return
        # The server makes the API key's user an owner of each new team; remove anyone the source team doesn't have.
        try:
            current = _memberships(client, team_id)
        except APIError as e:
            load_log.error(
                f"{ENTITY}: reading the members of {name!r} after the add failed ({e}). Nothing was "
                f"removed; the API key's user may still be an owner. Check its members on the target."
            )
            ctx.summary.flag(ENTITY, f"{name!r}: members not checked; extra members may remain")
            return
        present = {uid for uid, _role in current}
        not_added = [w["id"] for w in wanted if w["id"] not in present]
        if not_added:
            load_log.error(
                f"{ENTITY}: {name!r}: the add succeeded but these members aren't on the team: "
                f"{names(not_added)}. Nothing was removed; check its members on the target."
            )
            ctx.summary.flag(ENTITY, f"{name!r}: members missing after the add; extra members may remain")
            return
        keep = {w["id"] for w in wanted}
        extra = [uid for uid, _role in current if uid not in keep]
        if not extra:
            return
        try:
            resp = client.delete(
                f"teams/{team_id}/relationships/members", json={"data": [{"id": uid, "type": "users"} for uid in extra]}
            )
            problem = None if _ok(resp) else f"{resp.status_code} {resp.text[:300]}"
        except APIError as e:
            problem = str(e)
        if problem:
            load_log.error(
                f"{ENTITY}: {name!r} has members the source team doesn't ({names(extra)}); removing "
                f"them failed: {problem}. Remove them on the target by hand."
            )
            ctx.summary.flag(ENTITY, f"{name!r}: extra members not removed")
        else:
            load_log.info(
                f"{ENTITY}: removed {_members(len(extra))} the source team doesn't have from {name!r}: {names(extra)}"
            )

    items = load_extract(ctx.data_dir, ENTITY)
    target_teams = [{"id": t["id"], **(t.get("attributes") or {})} for t in client.get_all_cursor("teams")]
    load_reference(
        ctx,
        ENTITY,
        items=items,
        target_items=target_teams,
        build_payload=build_payload,
        path="teams",
        compare=("description",),
        client=client,
        id_of=data_id,
        after_create=add_members,
    )
    _warn_member_differences(ctx, items, email_of)


def _warn_member_differences(ctx, items, email_of):
    """Read-only: for each matched team, one warning listing members missing on the target, extra there, and role
    differences. The kit never changes a matched team."""
    client = ctx.target_internal
    tracker = ctx.tracker(ENTITY)
    for item in items:
        record = tracker.find(item["id"])
        if not record or record["origin"] != "matched":
            continue
        name = item["name"]
        try:
            current = _memberships(client, record["target_id"])
        except APIError as e:
            load_log.warning(
                f"{ENTITY}: couldn't read the members of the existing target team {name!r} to compare "
                f"them with the source ({e})"
            )
            continue
        source_roles = {casefold(m["email"]): (m["email"], m["role"]) for m in item["members"]}
        target_roles = {}
        for uid, role in current:
            email = email_of.get(uid) or uid
            target_roles[casefold(email)] = (email, role)
        missing = [source_roles[k][0] for k in sorted(source_roles) if k not in target_roles]
        extra = [target_roles[k][0] for k in sorted(target_roles) if k not in source_roles]
        roles = [
            f"{source_roles[k][0]} ({source_roles[k][1]} on the source, {target_roles[k][1]} on the target)"
            for k in sorted(source_roles)
            if k in target_roles and source_roles[k][1] != target_roles[k][1]
        ]
        parts = []
        if missing:
            parts.append(f"not on the target team: {', '.join(missing)}")
        if extra:
            parts.append(f"only on the target team: {', '.join(extra)}")
        if roles:
            parts.append(f"different roles: {', '.join(roles)}")
        if parts:
            load_log.warning(
                f"{ENTITY}: existing target team {name!r} has different members from the source (the "
                f"kit doesn't change it); {'; '.join(parts)}"
            )
