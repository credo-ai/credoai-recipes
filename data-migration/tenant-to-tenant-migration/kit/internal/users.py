"""Users. Internal API only: the Integration API's /users isn't available on production (2026-11-01)."""

import logging
from collections import Counter

from kit.clients.base import APIError
from kit.config import email_domains
from kit.loading import LoadStopped, MissingReference, data_id, load_reference
from kit.remap import casefold
from kit.tracking import load_extract, save_extract

ENTITY = "users"
FIELDS = ("email", "given_name", "family_name", "role")
# The server emails a vendor-portal invitation whenever it creates a user with this role, whatever LOCAL_USERS says
VENDOR_ROLE = "vendor"
# Built-in roles from least to most access. --raise-roles moves a matched user up this list, never down or off it
RAISABLE = ("user", "reviewer", "admin")
VENDOR_INVITE = (
    "vendor-portal users are always emailed an invitation when created; add them by hand, or re-run "
    "with --allow-invites to send the invitations"
)
extract_log = logging.getLogger("kit.extract")
load_log = logging.getLogger("kit.load")


def extract(ctx):
    """Live users with an email. USER_EMAIL_DOMAINS (only these) and SKIP_USER_EMAIL_DOMAINS (never these) match the
    whole domain after the @, ignoring case: credo.ai doesn't match sub.credo.ai."""
    only, skip = email_domains("USER_EMAIL_DOMAINS"), email_domains("SKIP_USER_EMAIL_DOMAINS")
    if only:
        extract_log.info(f"{ENTITY}: keeping only {', '.join(f'@{d}' for d in sorted(only))}")
    if skip:
        extract_log.info(f"{ENTITY}: leaving out {', '.join(f'@{d}' for d in sorted(skip))}")
    if not only and not skip:
        extract_log.info(f"{ENTITY}: no email-domain filter set; keeping every domain")
    items, left_out = [], Counter()
    for user in ctx.source_internal.get_all_cursor("users"):
        attrs = user.get("attributes") or {}
        email = (attrs.get("email") or "").strip()
        if not email:
            left_out["without an email"] += 1
            continue
        if attrs.get("deleted_at"):
            left_out["deleted"] += 1
            continue
        domain = email.rsplit("@", 1)[-1].lower()
        if (only and domain not in only) or domain in skip:
            left_out[f"@{domain}"] += 1
            continue
        items.append({"id": user["id"], **{k: attrs.get(k) for k in FIELDS}, "email": email})
    if left_out:
        extract_log.info(f"{ENTITY}: left out {', '.join(f'{n} {what}' for what, n in sorted(left_out.items()))}")
    save_extract(ctx.data_dir, ENTITY, items)
    return len(items)


def local_users_state(ctx):
    """'on', 'off', 'missing' (not listed) or 'unreadable' for the target's LOCAL_USERS entitlement."""
    try:
        entitlements = ctx.target_internal.get_data("entitlements")
    except APIError as e:
        load_log.debug(f"{ENTITY}: reading the target's entitlements failed: {e}")
        return "unreadable"
    for entitlement in entitlements:
        if entitlement.get("id") == "LOCAL_USERS":
            return "off" if (entitlement.get("attributes") or {}).get("value") is False else "on"
    return "missing"


STATE_TEXT = {
    "on": "is on",
    "missing": "isn't in the target's entitlements list, so the kit assumes it is on",
    "unreadable": "couldn't be read, so the kit assumes it is on",
}


def _invite_warning(state):
    return (
        f"{ENTITY}: the target's LOCAL_USERS entitlement {STATE_TEXT[state]}. While it is on, Credo AI emails an "
        f"invitation to each user the kit creates. To load users without sending invitations, turn LOCAL_USERS "
        f"off on the target (ask your Credo AI contact if you can't), run the load, then turn it back on if the tenant "
        f"uses it"
    )


def load(ctx):
    state = local_users_state(ctx)
    if state != "off" and not ctx.allow_invites:
        if not ctx.dry_run:
            load_log.error(
                f"{_invite_warning(state)}. Nothing was loaded. To send the invitations on purpose, re-run "
                f"with --allow-invites."
            )
            ctx.summary.flag(
                ENTITY,
                (
                    f"not loaded: the target's LOCAL_USERS entitlement {STATE_TEXT[state]} (turn "
                    f"it off, or re-run with --allow-invites)"
                ),
            )
            raise LoadStopped(
                f"{ENTITY}: not loaded because the target may email invitations (see above); nothing "
                f"after users was loaded either"
            )
        load_log.warning(f"{_invite_warning(state)}. A live run would stop here unless given --allow-invites.")
    roles = {r["id"] for r in ctx.target_internal.get_data("roles")}

    def build_payload(item):
        if item.get("role") == VENDOR_ROLE and not ctx.allow_invites:
            raise MissingReference(VENDOR_INVITE)
        if item.get("role") not in roles:
            raise MissingReference(
                f"role {item.get('role')!r} isn't on the target (roles there: {', '.join(sorted(roles))})"
            )
        return {"data": {"type": "users", "attributes": {k: item.get(k) for k in FIELDS}}}

    target_users = [{"id": u["id"], **(u.get("attributes") or {})} for u in ctx.target_internal.get_all_cursor("users")]
    items = load_extract(ctx.data_dir, ENTITY)
    load_reference(
        ctx,
        ENTITY,
        items=items,
        target_items=target_users,
        build_payload=build_payload,
        path="users",
        name_of=lambda u: u["email"],
        normalize=casefold,
        compare=("role",),
        client=ctx.target_internal,
        id_of=data_id,
    )
    raise_roles(ctx, items, target_users)


def raise_roles(ctx, items, target_users):
    """Matched users whose built-in role on the target is below their source role. Listed for the operator, and
    raised to the source role with --raise-roles. Custom, vendor and read-only admin roles are never changed."""
    tracker = ctx.tracker(ENTITY)
    role_of = {u["id"]: u.get("role") for u in target_users}
    lower = []
    for item in items:
        record = tracker.find(item["id"])
        if not record or record["origin"] != "matched":
            continue
        current, wanted = role_of.get(record["target_id"]), item.get("role")
        if current in RAISABLE and wanted in RAISABLE and RAISABLE.index(wanted) > RAISABLE.index(current):
            lower.append((item["email"], record["target_id"], current, wanted))
    if not ctx.raise_roles:
        if lower:
            load_log.info(
                f"{ENTITY}: {len(lower)} matched user(s) have a lower role on the target than on the source "
                f"({', '.join(email for email, *_ in lower)}); re-run with --raise-roles to raise them"
            )
        return
    verb = "would raise" if ctx.dry_run else "raised"
    for email, target_id, current, wanted in lower:
        body = {"data": {"id": target_id, "type": "users", "attributes": {"role": wanted}}}
        try:
            resp = ctx.target_internal.patch(f"users/{target_id}", body)
        except APIError as e:
            problem = str(e)
        else:
            if resp is None or 200 <= resp.status_code < 300:
                load_log.info(f"{ENTITY}: {verb} {email!r} from {current} to {wanted}")
                continue
            problem = f"{resp.status_code} {resp.text[:300]}"
        load_log.error(f"{ENTITY}: raising {email!r} from {current} to {wanted} failed ({problem})")
        ctx.summary.flag(ENTITY, f"{email}: raising the role from {current} to {wanted} failed; set it by hand")
