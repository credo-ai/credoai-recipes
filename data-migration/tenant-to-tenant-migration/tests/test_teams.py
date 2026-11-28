import logging

import pytest

from kit.clients.base import APIError
from kit.internal import teams
from kit.tracking import load_extract, save_extract
from tests.fakes import FakeResponse, FakeTenant, make_ctx


def user(uid, email, deleted_at=None):
    return {"id": uid, "attributes": {"email": email, "deleted_at": deleted_at}}


def team(tid, name, description="About it"):
    return {"id": tid, "attributes": {"name": name, "description": description}}


def team_body(tid, memberships, listed=None):
    """teams/{id}?include=memberships. memberships: [(user ID, role)]. listed: memberships the team claims to have."""
    included = [
        {"id": f"m-{uid}", "type": "team_memberships", "attributes": {"member_id": uid, "role": role}}
        for uid, role in memberships
    ]
    count = len(memberships) if listed is None else listed
    return {
        "data": {
            "id": tid,
            "type": "teams",
            "relationships": {
                "memberships": {"data": [{"id": f"m{i}", "type": "team_memberships"} for i in range(count)]}
            },
        },
        "included": included,
    }


# Extract


def source_ctx(tmp_path, source_users, source_teams, bodies):
    client = FakeTenant({"users": source_users, "teams": source_teams}, dry_run=True, jsonapi=True, json_bodies=bodies)
    return make_ctx(tmp_path, source_internal=client)


def test_extract_reads_members_with_their_emails_and_roles(tmp_path):
    ctx = source_ctx(
        tmp_path,
        [user("u1", "zed@credo.ai"), user("u2", "Amy@credo.ai")],
        [team("t1", "Governance")],
        {"teams/t1": team_body("t1", [("u1", "owner"), ("u2", "member")])},
    )
    assert teams.extract(ctx) == 1
    assert ctx.source_internal.gets == [("teams/t1", {"include": "memberships"})]
    assert load_extract(tmp_path, "teams") == [
        {
            "id": "t1",
            "name": "Governance",
            "description": "About it",
            "members": [{"email": "Amy@credo.ai", "role": "member"}, {"email": "zed@credo.ai", "role": "owner"}],
        }
    ]


def test_extract_stops_when_memberships_were_not_included(tmp_path):
    ctx = source_ctx(
        tmp_path, [user("u1", "a@credo.ai")], [team("t1", "Governance")], {"teams/t1": team_body("t1", [], listed=2)}
    )
    with pytest.raises(APIError, match="memberships"):
        teams.extract(ctx)


def test_extract_skips_a_member_who_is_not_in_the_users_list(tmp_path, caplog):
    # System accounts are hidden from GET users but can still be team members
    ctx = source_ctx(
        tmp_path,
        [user("u1", "a@credo.ai")],
        [team("t1", "Governance")],
        {"teams/t1": team_body("t1", [("u9", "owner"), ("u1", "member")])},
    )
    with caplog.at_level(logging.WARNING, logger="kit.extract"):
        assert teams.extract(ctx) == 1
    assert load_extract(tmp_path, "teams")[0]["members"] == [{"email": "a@credo.ai", "role": "member"}]
    assert "'Governance'" in caplog.text and "u9" in caplog.text and "teams: " in caplog.text


# Load

KIT_USER = "t-kit"  # the API key's user, auto-added as owner of each new team


def item(sid, name, members=(), description="About it"):
    return {
        "id": sid,
        "name": name,
        "description": description,
        "members": [{"email": e, "role": r} for e, r in members],
    }


class Tenant(FakeTenant):
    """FakeTenant whose PATCH or DELETE can raise."""

    def __init__(self, *args, patch_error=None, delete_error=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.patch_error = patch_error
        self.delete_error = delete_error

    def patch(self, path, json):
        if self.patch_error and not self.dry_run:
            self.patches.append((path, json))
            raise self.patch_error
        return super().patch(path, json)

    def delete(self, path, json=None):
        if self.delete_error and not self.dry_run:
            self.deletes.append(path)
            raise self.delete_error
        return super().delete(path, json)


TARGET_USERS = [user(KIT_USER, "kit@credo.ai"), user("t-amy", "amy@credo.ai"), user("t-zed", "zed@credo.ai")]


def target_ctx(tmp_path, items, existing_teams=(), target_users=TARGET_USERS, bodies=None, dry_run=False, **kwargs):
    save_extract(tmp_path, "teams", items)
    client = Tenant(
        {"users": list(target_users), "teams": list(existing_teams)}, jsonapi=True, json_bodies=bodies or {}, **kwargs
    )
    return make_ctx(tmp_path, target_internal=client, dry_run=dry_run)


def after_add(*memberships):
    """The new team's memberships after the PATCH: the kit's user as owner plus the given ones."""
    return {"teams/new-1": team_body("new-1", [(KIT_USER, "owner"), *memberships])}


def test_load_creates_the_team_and_adds_members_by_email(tmp_path):
    ctx = target_ctx(
        tmp_path,
        [item("s1", "Governance", [("Amy@Credo.ai", "member"), ("zed@credo.ai", "owner")])],
        bodies=after_add(("t-amy", "member"), ("t-zed", "owner")),
    )
    teams.load(ctx)
    client = ctx.target_internal
    assert client.posts == [
        ("teams", {"data": {"type": "teams", "attributes": {"name": "Governance", "description": "About it"}}})
    ]
    assert client.patches == [
        (
            "teams/new-1/relationships/members",
            {
                "data": [
                    {"id": "t-amy", "type": "users", "role": "member"},
                    {"id": "t-zed", "type": "users", "role": "owner"},
                ]
            },
        )
    ]
    assert ctx.tracker("teams").target_id("s1") == "new-1"
    assert ctx.summary.to_dict()["teams"]["created"] == 1


def test_load_removes_the_auto_added_owner(tmp_path):
    ctx = target_ctx(
        tmp_path, [item("s1", "Governance", [("amy@credo.ai", "owner")])], bodies=after_add(("t-amy", "owner"))
    )
    teams.load(ctx)
    client = ctx.target_internal
    assert client.deletes == ["teams/new-1/relationships/members"]
    assert client.delete_bodies == [{"data": [{"id": KIT_USER, "type": "users"}]}]
    assert not ctx.summary.needs_attention


def test_kit_user_who_is_a_source_member_is_kept_with_the_source_role(tmp_path):
    ctx = target_ctx(
        tmp_path,
        [item("s1", "Governance", [("kit@credo.ai", "member")])],
        bodies={"teams/new-1": team_body("new-1", [(KIT_USER, "member")])},
    )
    teams.load(ctx)
    client = ctx.target_internal
    assert client.patches[0][1] == {"data": [{"id": KIT_USER, "type": "users", "role": "member"}]}
    assert client.deletes == []


@pytest.mark.parametrize(
    "kwargs",
    [{"patch_response": FakeResponse(422, {"errors": ["bad"]})}, {"patch_error": APIError("PATCH failed: timeout")}],
    ids=["422", "raises"],
)
def test_failed_add_removes_nothing_and_flags_the_team(tmp_path, caplog, kwargs):
    ctx = target_ctx(tmp_path, [item("s1", "Governance", [("amy@credo.ai", "member")])], bodies=after_add(), **kwargs)
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        teams.load(ctx)
    assert ctx.target_internal.deletes == []
    assert "teams: " in caplog.text and "'Governance'" in caplog.text
    assert ctx.summary.needs_attention
    assert ctx.summary.to_dict()["teams"]["created"] == 1


def test_unreadable_memberships_after_the_add_remove_nothing(tmp_path, caplog):
    ctx = target_ctx(
        tmp_path,
        [item("s1", "Governance", [("amy@credo.ai", "member")])],
        bodies={"teams/new-1": APIError("GET failed: 500", 500)},
    )
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        teams.load(ctx)
    assert len(ctx.target_internal.patches) == 1
    assert ctx.target_internal.deletes == []
    assert "'Governance'" in caplog.text
    assert ctx.summary.needs_attention


def test_member_missing_after_a_successful_add_is_uncertain_and_removes_nothing(tmp_path, caplog):
    ctx = target_ctx(tmp_path, [item("s1", "Governance", [("amy@credo.ai", "member")])], bodies=after_add())
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        teams.load(ctx)
    assert ctx.target_internal.deletes == []
    assert "amy@credo.ai" in caplog.text
    assert ctx.summary.needs_attention


@pytest.mark.parametrize(
    "kwargs",
    [{"delete_response": FakeResponse(500, {})}, {"delete_error": APIError("DELETE failed: timeout")}],
    ids=["500", "raises"],
)
def test_failed_removal_is_flagged(tmp_path, caplog, kwargs):
    ctx = target_ctx(
        tmp_path,
        [item("s1", "Governance", [("amy@credo.ai", "member")])],
        bodies=after_add(("t-amy", "member")),
        **kwargs,
    )
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        teams.load(ctx)
    assert "kit@credo.ai" in caplog.text and "'Governance'" in caplog.text
    assert ctx.summary.needs_attention


def test_team_without_members_is_not_patched_but_the_auto_added_owner_is_removed(tmp_path):
    ctx = target_ctx(tmp_path, [item("s1", "Empty")], bodies=after_add())
    teams.load(ctx)
    client = ctx.target_internal
    assert client.patches == []
    assert client.delete_bodies == [{"data": [{"id": KIT_USER, "type": "users"}]}]
    assert not ctx.summary.needs_attention


def test_team_with_no_members_on_the_target_still_removes_the_auto_added_owner(tmp_path, caplog):
    ctx = target_ctx(tmp_path, [item("s1", "Governance", [("ghost@credo.ai", "member")])], bodies=after_add())
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        teams.load(ctx)
    client = ctx.target_internal
    assert client.patches == []
    assert client.delete_bodies == [{"data": [{"id": KIT_USER, "type": "users"}]}]
    assert "ghost@credo.ai" in caplog.text


def test_team_without_members_and_unreadable_memberships_removes_nothing(tmp_path, caplog):
    ctx = target_ctx(tmp_path, [item("s1", "Empty")], bodies={"teams/new-1": APIError("GET failed: 500", 500)})
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        teams.load(ctx)
    assert ctx.target_internal.deletes == []
    assert ctx.summary.needs_attention


def test_member_left_out_of_the_users_extract_is_skipped_with_a_warning(tmp_path, caplog):
    save_extract(tmp_path, "users", [{"id": "u1", "email": "amy@credo.ai"}])
    ctx = target_ctx(
        tmp_path,
        [item("s1", "Governance", [("amy@credo.ai", "member"), ("ghost@other.com", "owner")])],
        bodies=after_add(("t-amy", "member")),
    )
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        teams.load(ctx)
    warning = next(r.getMessage() for r in caplog.records if "ghost@other.com" in r.getMessage())
    assert warning.startswith("teams: ") and "'Governance'" in warning
    assert "left out of the users extract" in warning and "by hand" in warning
    assert ctx.target_internal.patches[0][1] == {"data": [{"id": "t-amy", "type": "users", "role": "member"}]}
    assert ctx.summary.to_dict()["teams"]["created"] == 1
    assert not ctx.summary.needs_attention


def test_without_a_users_extract_missing_members_are_skipped_with_a_warning(tmp_path, caplog):
    ctx = target_ctx(tmp_path, [item("s1", "Governance", [("ghost@credo.ai", "owner")])], bodies=after_add())
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        teams.load(ctx)
    assert "ghost@credo.ai" in caplog.text and "left out of the users extract" in caplog.text
    assert ctx.summary.to_dict()["teams"]["created"] == 1


@pytest.mark.parametrize("dry_run", [False, True], ids=["live", "dry"])
def test_team_with_extracted_members_not_yet_on_the_target_is_not_created(tmp_path, caplog, dry_run):
    save_extract(
        tmp_path,
        "users",
        [
            {"id": "u1", "email": "New@credo.ai"},
            {"id": "u2", "email": "two@credo.ai"},
            {"id": "u3", "email": "amy@credo.ai"},
        ],
    )
    ctx = target_ctx(
        tmp_path,
        [
            item(
                "s1", "Governance", [("amy@credo.ai", "member"), ("new@credo.ai", "owner"), ("two@credo.ai", "member")]
            ),
            item("s2", "Solo", [("new@credo.ai", "owner")]),
        ],
        dry_run=dry_run,
    )
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        teams.load(ctx)
    client = ctx.target_internal
    assert client.posts == [] and client.patches == [] and client.deletes == []
    assert ctx.summary.to_dict()["teams"] == {"created": 0, "matched": 0, "skipped": 0, "failed": 2}
    assert ctx.tracker("teams").find("s1") is None
    assert (
        "teams: skipping 'Governance': 2 members aren't on the target yet (new@credo.ai, two@credo.ai). "
        "Load users first; if users already ran, fix the errors it logged for these emails, then re-run."
    ) in caplog.text
    assert "teams: skipping 'Solo': 1 member isn't on the target yet (new@credo.ai)." in caplog.text


def test_dry_run_after_a_dry_run_users_load_in_the_same_context_creates_the_team(tmp_path, caplog):
    from kit.internal import users

    save_extract(
        tmp_path,
        "users",
        [{"id": "u1", "email": "new@credo.ai", "given_name": "N", "family_name": "U", "role": "user"}],
    )
    ctx = target_ctx(tmp_path, [item("s1", "Governance", [("New@credo.ai", "member")])], dry_run=True)
    ctx.target_internal.collections["roles"] = [{"id": "user"}]
    ctx.target_internal.collections["entitlements"] = [{"id": "LOCAL_USERS", "attributes": {"value": False}}]
    users.load(ctx)
    with caplog.at_level(logging.INFO, logger="kit.load"):
        teams.load(ctx)
    assert ctx.summary.to_dict()["teams"]["created"] == 1
    assert "would add 1 member to 'Governance'" in caplog.text
    assert not ctx.summary.has_failures


def test_deleted_target_users_are_not_used_for_members(tmp_path, caplog):
    target_users = [
        user(KIT_USER, "kit@credo.ai"),
        user("t-old", "amy@credo.ai", deleted_at="2026-01-01T00:00:00Z"),
        user("t-amy", "amy@credo.ai"),
        user("t-gone", "zed@credo.ai", deleted_at="2026-01-01T00:00:00Z"),
    ]
    ctx = target_ctx(
        tmp_path,
        [item("s1", "Governance", [("amy@credo.ai", "member"), ("zed@credo.ai", "member")])],
        target_users=target_users,
        bodies=after_add(("t-amy", "member")),
    )
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        teams.load(ctx)
    assert ctx.target_internal.patches[0][1] == {"data": [{"id": "t-amy", "type": "users", "role": "member"}]}
    assert "zed@credo.ai" in caplog.text


def test_dry_run_counts_users_from_a_dry_run_users_load_as_present(tmp_path, caplog):
    save_extract(tmp_path, "users", [{"id": "u-new", "email": "New@credo.ai"}])
    ctx = target_ctx(tmp_path, [item("s1", "Governance", [("new@credo.ai", "member")])], dry_run=True)
    ctx.tracker("users").record("u-new", "dry:u-new", "New@credo.ai", "created")
    with caplog.at_level(logging.INFO, logger="kit.load"):
        teams.load(ctx)
    assert "isn't on the target" not in caplog.text and "left out" not in caplog.text
    assert "would add 1 member to" in caplog.text
    assert ctx.target_internal.posts == [] and ctx.target_internal.patches == []
    assert ctx.target_internal.deletes == []
    assert ctx.summary.to_dict()["teams"]["created"] == 1


def test_matched_team_is_not_changed_and_member_differences_warn(tmp_path, caplog):
    source = item(
        "s1", "Governance", [("AMY@credo.ai", "member"), ("ghost@credo.ai", "owner"), ("zed@credo.ai", "member")]
    )
    ctx = target_ctx(
        tmp_path,
        [source],
        existing_teams=[team("tt1", "Governance")],
        bodies={"teams/tt1": team_body("tt1", [("t-amy", "member"), ("t-zed", "owner"), (KIT_USER, "owner")])},
    )
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        teams.load(ctx)
    client = ctx.target_internal
    assert client.posts == [] and client.patches == [] and client.deletes == []
    assert ctx.tracker("teams").find("s1")["origin"] == "matched"
    warnings = [r.getMessage() for r in caplog.records if "member" in r.getMessage()]
    assert len(warnings) == 1
    text = warnings[0]
    assert text.startswith("teams: ") and "'Governance'" in text
    assert "ghost@credo.ai" in text and "kit@credo.ai" in text and "zed@credo.ai" in text
    assert "amy" not in text.lower()
    assert "\u2014" not in caplog.text


def test_matched_team_with_the_same_members_does_not_warn(tmp_path, caplog):
    ctx = target_ctx(
        tmp_path,
        [item("s1", "Governance", [("amy@credo.ai", "owner")])],
        existing_teams=[team("tt1", "Governance")],
        bodies={"teams/tt1": team_body("tt1", [("t-amy", "owner")])},
    )
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        teams.load(ctx)
    assert caplog.records == []


def test_unreadable_matched_team_warns_and_the_others_are_still_compared(tmp_path, caplog):
    ctx = target_ctx(
        tmp_path,
        [item("s1", "Broken", [("amy@credo.ai", "owner")]), item("s2", "Governance", [("amy@credo.ai", "owner")])],
        existing_teams=[team("tt0", "Broken"), team("tt1", "Governance")],
        bodies={"teams/tt0": APIError("GET failed: 500", 500), "teams/tt1": team_body("tt1", [("t-zed", "owner")])},
    )
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        teams.load(ctx)
    assert "'Broken'" in caplog.text
    assert "zed@credo.ai" in caplog.text
    assert not ctx.summary.has_failures


def test_removal_message_counts_members_in_the_singular(tmp_path, caplog):
    ctx = target_ctx(
        tmp_path, [item("s1", "Governance", [("amy@credo.ai", "owner")])], bodies=after_add(("t-amy", "owner"))
    )
    with caplog.at_level(logging.INFO, logger="kit.load"):
        teams.load(ctx)
    assert "removed 1 member the source team doesn't have" in caplog.text
