import logging

import pytest

from kit.internal import users
from kit.loading import LoadStopped
from kit.tracking import load_extract, save_extract
from tests.fakes import FakeTenant, make_ctx


@pytest.fixture(autouse=True)
def no_domain_settings(monkeypatch):
    monkeypatch.delenv("USER_EMAIL_DOMAINS", raising=False)
    monkeypatch.delenv("SKIP_USER_EMAIL_DOMAINS", raising=False)


def user(uid, email, role="user", deleted_at=None, given="Ada", family="Lovelace"):
    return {
        "id": uid,
        "attributes": {
            "email": email,
            "given_name": given,
            "family_name": family,
            "role": role,
            "deleted_at": deleted_at,
            "is_local": False,
        },
    }


def source_ctx(tmp_path, source_users):
    return make_ctx(tmp_path, source_internal=FakeTenant({"users": source_users}, dry_run=True, jsonapi=True))


def test_extract_keeps_live_users_with_an_email(tmp_path):
    ctx = source_ctx(
        tmp_path,
        [
            user("u1", " a@credo.ai ", role="admin"),
            user("u2", ""),
            user("u3", "gone@credo.ai", deleted_at="2026-01-01T00:00:00Z"),
        ],
    )
    assert users.extract(ctx) == 1
    assert load_extract(tmp_path, "users") == [
        {"id": "u1", "email": "a@credo.ai", "given_name": "Ada", "family_name": "Lovelace", "role": "admin"}
    ]


def test_extract_applies_the_domain_settings_and_counts_what_it_left_out(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("USER_EMAIL_DOMAINS", "credo.ai, test.com")
    monkeypatch.setenv("SKIP_USER_EMAIL_DOMAINS", "test.com")
    ctx = source_ctx(
        tmp_path,
        [
            user("u1", "a@credo.ai"),
            user("u2", "X@Credo.AI"),
            user("u3", "b@partner.com"),
            user("u4", "c@partner.com"),
            user("u5", "d@test.com"),
            user("u6", "e@sub.credo.ai"),
        ],
    )
    with caplog.at_level(logging.INFO, logger="kit.extract"):
        assert users.extract(ctx) == 2
    assert [u["email"] for u in load_extract(tmp_path, "users")] == ["a@credo.ai", "X@Credo.AI"]
    assert "users: left out 2 @partner.com, 1 @sub.credo.ai, 1 @test.com" in caplog.text


def test_extract_with_no_domain_settings_keeps_every_domain(tmp_path, caplog):
    ctx = source_ctx(tmp_path, [user("u1", "a@credo.ai"), user("u2", "b@partner.com")])
    with caplog.at_level(logging.INFO, logger="kit.extract"):
        assert users.extract(ctx) == 2
    assert "left out" not in caplog.text
    assert "users: no email-domain filter set; keeping every domain" in caplog.text


def test_extract_logs_the_active_domain_settings(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("USER_EMAIL_DOMAINS", "credo.ai")
    monkeypatch.setenv("SKIP_USER_EMAIL_DOMAINS", "@x.com")
    ctx = source_ctx(tmp_path, [user("u1", "a@credo.ai")])
    with caplog.at_level(logging.INFO, logger="kit.extract"):
        users.extract(ctx)
    assert "users: keeping only @credo.ai" in caplog.text
    assert "users: leaving out @x.com" in caplog.text
    assert "no email-domain filter" not in caplog.text


def item(uid, email, role="user"):
    return {"id": uid, "email": email, "given_name": "Ada", "family_name": "Lovelace", "role": role}


def entitlement(value):
    return {"id": "LOCAL_USERS", "attributes": {"id": "LOCAL_USERS", "value": value}}


ROLES = [{"id": r, "attributes": {}} for r in ("admin", "reviewer", "vendor", "read_only_admin", "user")]


def target_ctx(
    tmp_path, existing=(), entitlements=(entitlement(False),), dry_run=False, allow_invites=False, raise_roles=False
):
    collections = {"users": list(existing), "roles": ROLES}
    if entitlements is not None:
        collections["entitlements"] = list(entitlements)
    ctx = make_ctx(tmp_path, target_internal=FakeTenant(collections, jsonapi=True), dry_run=dry_run)
    ctx.allow_invites = allow_invites
    ctx.raise_roles = raise_roles
    return ctx


@pytest.mark.parametrize("entitlements", [[entitlement(True)], [], None], ids=["on", "absent", "unreadable"])
def test_live_load_refuses_when_invites_may_be_sent(tmp_path, caplog, entitlements):
    save_extract(tmp_path, "users", [item("u1", "a@credo.ai")])
    ctx = target_ctx(tmp_path, entitlements=entitlements)
    with caplog.at_level(logging.ERROR, logger="kit.load"), pytest.raises(LoadStopped):
        users.load(ctx)
    assert ctx.target_internal.posts == [] and ctx.target_internal.patches == []
    assert "LOCAL_USERS" in caplog.text and "--allow-invites" in caplog.text
    assert ctx.summary.needs_attention
    assert ctx.tracker("users").find("u1") is None


def test_unreadable_entitlements_are_named_in_the_message(tmp_path, caplog):
    save_extract(tmp_path, "users", [item("u1", "a@credo.ai")])
    ctx = target_ctx(tmp_path, entitlements=None)
    with caplog.at_level(logging.ERROR, logger="kit.load"), pytest.raises(LoadStopped):
        users.load(ctx)
    assert "couldn't be read" in caplog.text


def test_live_load_with_allow_invites_creates_users(tmp_path):
    save_extract(tmp_path, "users", [item("u1", "a@credo.ai")])
    ctx = target_ctx(tmp_path, entitlements=[entitlement(True)], allow_invites=True)
    users.load(ctx)
    assert len(ctx.target_internal.posts) == 1
    assert ctx.summary.to_dict()["users"]["created"] == 1


def test_dry_run_warns_about_invites_but_reports_what_it_would_create(tmp_path, caplog):
    save_extract(tmp_path, "users", [item("u1", "a@credo.ai")])
    ctx = target_ctx(tmp_path, entitlements=[entitlement(True)], dry_run=True)
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        users.load(ctx)
    assert ctx.target_internal.posts == []
    assert "LOCAL_USERS" in caplog.text
    assert ctx.summary.to_dict()["users"]["created"] == 1


def test_load_creates_the_user_through_the_internal_api(tmp_path):
    save_extract(tmp_path, "users", [item("u1", "a@credo.ai", role="admin")])
    ctx = target_ctx(tmp_path)
    users.load(ctx)
    assert ctx.target.posts == []
    assert ctx.target_internal.posts == [
        (
            "users",
            {
                "data": {
                    "type": "users",
                    "attributes": {
                        "email": "a@credo.ai",
                        "given_name": "Ada",
                        "family_name": "Lovelace",
                        "role": "admin",
                    },
                }
            },
        )
    ]
    assert ctx.tracker("users").target_id("u1") == "new-1"
    assert not ctx.summary.needs_attention


def test_user_matched_by_email_ignoring_case_and_role_difference_warned(tmp_path, caplog):
    save_extract(tmp_path, "users", [item("u1", "Ada@Credo.ai", role="admin")])
    ctx = target_ctx(tmp_path, existing=[user("t1", "ada@credo.ai", role="user")])
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        users.load(ctx)
    assert ctx.target_internal.posts == []
    assert ctx.tracker("users").find("u1")["origin"] == "matched"
    assert ctx.tracker("users").target_id("u1") == "t1"
    assert "role" in caplog.text


def test_role_missing_on_the_target_fails_only_that_user(tmp_path, caplog):
    save_extract(tmp_path, "users", [item("u1", "a@credo.ai", role="custom_auditor"), item("u2", "b@credo.ai")])
    ctx = target_ctx(tmp_path)
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        users.load(ctx)
    assert ctx.summary.to_dict()["users"] == {"created": 1, "matched": 0, "skipped": 0, "failed": 1}
    assert "custom_auditor" in caplog.text
    assert [body["data"]["attributes"]["email"] for _, body in ctx.target_internal.posts] == ["b@credo.ai"]


@pytest.mark.parametrize("state, entitlements", [("on", [entitlement(True)]), ("absent", []), ("unreadable", None)])
def test_invite_flag_text_reads_cleanly(tmp_path, state, entitlements):
    save_extract(tmp_path, "users", [item("u1", "a@credo.ai")])
    ctx = target_ctx(tmp_path, entitlements=entitlements)
    with pytest.raises(LoadStopped):
        users.load(ctx)
    [flag] = ctx.summary.attention
    assert "on on" not in flag["message"] and "LOCAL_USERS" in flag["message"]
    assert "--allow-invites" in flag["message"]


VENDOR_MESSAGE = (
    "vendor-portal users are always emailed an invitation when created; add them by hand, or re-run "
    "with --allow-invites to send the invitations"
)


@pytest.mark.parametrize("dry_run", [False, True], ids=["live", "dry"])
def test_vendor_role_user_fails_without_allow_invites(tmp_path, caplog, dry_run):
    save_extract(tmp_path, "users", [item("u1", "v@acme.com", role="vendor"), item("u2", "b@credo.ai")])
    ctx = target_ctx(tmp_path, dry_run=dry_run)
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        users.load(ctx)
    assert ctx.summary.to_dict()["users"] == {"created": 1, "matched": 0, "skipped": 0, "failed": 1}
    assert VENDOR_MESSAGE in caplog.text and "v@acme.com" in caplog.text
    posted = [b["data"]["attributes"]["email"] for _, b in ctx.target_internal.posts]
    assert posted == ([] if dry_run else ["b@credo.ai"])
    assert ctx.tracker("users").find("u1") is None


def test_vendor_role_user_is_created_with_allow_invites(tmp_path):
    save_extract(tmp_path, "users", [item("u1", "v@acme.com", role="vendor")])
    ctx = target_ctx(tmp_path, allow_invites=True)
    users.load(ctx)
    assert ctx.summary.to_dict()["users"]["created"] == 1
    assert len(ctx.target_internal.posts) == 1


def test_existing_vendor_role_user_is_still_matched(tmp_path):
    save_extract(tmp_path, "users", [item("u1", "v@acme.com", role="vendor")])
    ctx = target_ctx(tmp_path, existing=[user("t1", "v@acme.com", role="vendor")])
    users.load(ctx)
    assert ctx.summary.to_dict()["users"]["matched"] == 1


def raise_body(target_id, role):
    return (f"users/{target_id}", {"data": {"id": target_id, "type": "users", "attributes": {"role": role}}})


def test_raise_roles_raises_matched_users_whose_target_role_is_lower(tmp_path, caplog):
    save_extract(
        tmp_path,
        "users",
        [
            item("u1", "a@x.com", role="admin"),
            item("u2", "b@x.com", role="reviewer"),
            item("u3", "c@x.com", role="reviewer"),
            item("u4", "d@x.com", role="user"),
        ],
    )
    existing = [
        user("t1", "a@x.com", role="user"),
        user("t2", "b@x.com", role="user"),
        user("t3", "c@x.com", role="admin"),
        user("t4", "d@x.com", role="reviewer"),
    ]
    ctx = target_ctx(tmp_path, existing=existing, raise_roles=True)
    with caplog.at_level(logging.INFO, logger="kit.load"):
        users.load(ctx)
    assert ctx.target_internal.patches == [raise_body("t1", "admin"), raise_body("t2", "reviewer")]
    assert "raised 'a@x.com' from user to admin" in caplog.text
    assert ctx.summary.to_dict()["users"]["matched"] == 4 and not ctx.summary.needs_attention


@pytest.mark.parametrize(
    "source_role, target_role",
    [("admin", "read_only_admin"), ("read_only_admin", "user"), ("admin", "vendor"), ("custom_auditor", "user")],
)
def test_raise_roles_leaves_roles_outside_the_built_in_order_alone(tmp_path, source_role, target_role):
    save_extract(tmp_path, "users", [item("u1", "a@x.com", role=source_role)])
    ctx = target_ctx(tmp_path, existing=[user("t1", "a@x.com", role=target_role)], raise_roles=True)
    users.load(ctx)
    assert ctx.target_internal.patches == []


def test_raise_roles_never_touches_users_the_kit_created(tmp_path):
    save_extract(tmp_path, "users", [item("u1", "a@x.com", role="admin")])
    ctx = target_ctx(tmp_path, raise_roles=True)
    users.load(ctx)
    assert ctx.target_internal.patches == []


def test_without_raise_roles_lower_roles_are_listed_not_changed(tmp_path, caplog):
    save_extract(tmp_path, "users", [item("u1", "a@x.com", role="admin")])
    ctx = target_ctx(tmp_path, existing=[user("t1", "a@x.com", role="user")])
    with caplog.at_level(logging.INFO, logger="kit.load"):
        users.load(ctx)
    assert ctx.target_internal.patches == []
    assert (
        "1 matched user(s) have a lower role on the target than on the source (a@x.com); re-run with "
        "--raise-roles" in caplog.text
    )


def test_raise_roles_dry_run_writes_nothing(tmp_path, caplog):
    save_extract(tmp_path, "users", [item("u1", "a@x.com", role="admin")])
    ctx = target_ctx(tmp_path, existing=[user("t1", "a@x.com", role="user")], dry_run=True, raise_roles=True)
    with caplog.at_level(logging.INFO, logger="kit.load"):
        users.load(ctx)
    assert ctx.target_internal.patches == []
    assert "would raise 'a@x.com' from user to admin" in caplog.text


def test_raise_roles_failure_is_flagged(tmp_path, caplog):
    from tests.fakes import FakeResponse

    save_extract(tmp_path, "users", [item("u1", "a@x.com", role="admin")])
    ctx = target_ctx(tmp_path, existing=[user("t1", "a@x.com", role="user")], raise_roles=True)
    ctx.target_internal.patch_response = FakeResponse(403, text="forbidden")
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        users.load(ctx)
    assert "raising 'a@x.com' from user to admin failed (403 forbidden)" in caplog.text
    [flag] = ctx.summary.attention
    assert flag["message"] == "a@x.com: raising the role from user to admin failed; set it by hand"
