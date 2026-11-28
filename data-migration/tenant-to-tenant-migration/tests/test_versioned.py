import logging

from kit.clients.base import APIError
from kit.loading import MissingReference, load_versioned, remind_to_publish
from tests.fakes import FakeResponse, FakeTenant, make_ctx

ITEMS = [{"id": "K1", "key": "K1", "body": "one"}, {"id": "K2", "key": "K2", "body": "two"}]


def run(ctx, items=ITEMS, existing=(), version_payload=None):
    return load_versioned(
        ctx,
        "things",
        items,
        set(existing),
        client=ctx.target_internal,
        base_path="thing_bases",
        base_payload=lambda i: {"data": {"attributes": {"id": i["key"]}}},
        version_path=lambda key: f"thing_bases/{key}/versions",
        version_payload=version_payload or (lambda i: {"data": {"attributes": {"body": i["body"]}}}),
    )


def ctx_for(tmp_path, dry_run=False, **internal):
    return make_ctx(tmp_path, target_internal=FakeTenant(jsonapi=True, dry_run=dry_run, **internal), dry_run=dry_run)


def test_creates_base_then_version_and_tracks_the_key(tmp_path):
    ctx = ctx_for(tmp_path)
    assert run(ctx) == ["K1", "K2"]
    assert [p[0] for p in ctx.target_internal.posts] == [
        "thing_bases",
        "thing_bases/K1/versions",
        "thing_bases",
        "thing_bases/K2/versions",
    ]
    assert ctx.tracker("things").find("K1") == {
        "source_id": "K1",
        "target_id": "K1",
        "name_or_key": "K1",
        "origin": "created",
    }
    assert ctx.summary.to_dict()["things"]["created"] == 2


def test_second_run_skips_tracked_items(tmp_path):
    run(ctx_for(tmp_path))
    ctx = ctx_for(tmp_path)
    assert run(ctx) == []
    assert ctx.target_internal.posts == []
    assert ctx.summary.to_dict()["things"]["skipped"] == 2


def test_existing_key_is_matched_and_left_alone(tmp_path, caplog):
    ctx = ctx_for(tmp_path, collections={"thing_bases/K1/versions": [{"id": "v1"}]})
    with caplog.at_level(logging.WARNING):
        run(ctx, existing={"K1"})
    assert [p[0] for p in ctx.target_internal.posts] == ["thing_bases", "thing_bases/K2/versions"]
    assert ctx.tracker("things").find("K1")["origin"] == "matched"
    assert "already exists on the target" in caplog.text


def test_failed_version_deletes_the_new_base_and_fails_the_item(tmp_path):
    ctx = ctx_for(tmp_path, post_responses={"thing_bases/K1/versions": FakeResponse(500, {"error": "boom"})})
    assert run(ctx) == ["K2"]
    assert ctx.target_internal.deletes == ["thing_bases/K1"]
    assert ctx.tracker("things").find("K1") is None
    assert ctx.summary.to_dict()["things"]["failed"] == 1


def test_base_left_behind_is_reported(tmp_path, caplog):
    ctx = ctx_for(
        tmp_path,
        post_responses={"thing_bases/K1/versions": FakeResponse(500, {})},
        delete_response=FakeResponse(422, {}),
    )
    with caplog.at_level(logging.ERROR):
        run(ctx)
    assert "may have been left on the target, and removing it failed (422)" in caplog.text


def test_failed_base_creates_no_version(tmp_path):
    ctx = ctx_for(tmp_path, post_responses={"thing_bases": FakeResponse(500, {})})
    run(ctx)
    assert all(p[0] == "thing_bases" for p in ctx.target_internal.posts)
    assert ctx.summary.to_dict()["things"]["failed"] == 2


def test_missing_reference_fails_before_any_write(tmp_path):
    def broken(_item):
        raise MissingReference("risk type 'X' not on the target")

    ctx = ctx_for(tmp_path)
    run(ctx, version_payload=broken)
    assert ctx.target_internal.posts == []
    assert ctx.summary.to_dict()["things"]["failed"] == 2


def test_dry_run_predicts_without_writing(tmp_path):
    ctx = ctx_for(tmp_path, dry_run=True)
    assert run(ctx) == ["K1", "K2"]
    assert ctx.target_internal.posts == []
    assert ctx.target_internal.deletes == []
    assert not (tmp_path / "loaded_things.json").exists()
    assert ctx.summary.to_dict()["things"]["created"] == 2
    # Later steps (triggers) read a dry: placeholder as "would be created in this run"
    assert ctx.tracker("things").target_id("K1") == "dry:K1"


class RaisingTenant(FakeTenant):
    """A FakeTenant whose POST or DELETE to the given paths raises APIError, as on a lost connection."""

    def __init__(self, raise_on=(), raise_on_delete=False, **kwargs):
        super().__init__(**kwargs)
        self.raise_on = set(raise_on)
        self.raise_on_delete = raise_on_delete

    def post(self, path, json):
        if path in self.raise_on and not self.dry_run:
            self.posts.append((path, json))
            raise APIError(f"POST {path} failed: connection reset")
        return super().post(path, json)

    def delete(self, path):
        if self.raise_on_delete and not self.dry_run:
            self.deletes.append(path)
            raise APIError(f"DELETE {path} failed: timeout")
        return super().delete(path)


def raising_ctx(tmp_path, **kwargs):
    return make_ctx(tmp_path, target_internal=RaisingTenant(jsonapi=True, **kwargs))


def test_base_id_comes_from_the_key_not_the_response(tmp_path):
    ctx = ctx_for(tmp_path, post_responses={"thing_bases": FakeResponse(201, {"unexpected": "shape"})})
    assert run(ctx, items=ITEMS[:1]) == ["K1"]
    assert [p[0] for p in ctx.target_internal.posts] == ["thing_bases", "thing_bases/K1/versions"]


def test_connection_error_on_base_fails_the_item_and_continues(tmp_path, caplog):
    ctx = raising_ctx(tmp_path, raise_on={"thing_bases"})
    with caplog.at_level(logging.ERROR):
        assert run(ctx) == []
    assert ctx.target_internal.deletes == []
    assert ctx.summary.to_dict()["things"]["failed"] == 2
    assert "connection reset" in caplog.text


def test_connection_error_on_version_removes_the_base_and_continues(tmp_path):
    ctx = raising_ctx(tmp_path, raise_on={"thing_bases/K1/versions"})
    assert run(ctx) == ["K2"]
    assert ctx.target_internal.deletes == ["thing_bases/K1"]
    assert ctx.tracker("things").find("K1") is None
    assert ctx.summary.to_dict()["things"]["failed"] == 1


def test_connection_error_on_delete_is_reported(tmp_path, caplog):
    ctx = raising_ctx(tmp_path, raise_on={"thing_bases/K1/versions"}, raise_on_delete=True)
    with caplog.at_level(logging.ERROR):
        assert run(ctx) == ["K2"]
    assert "may have been left on the target, and removing it failed" in caplog.text


def test_base_already_gone_is_reported_as_gone(tmp_path, caplog):
    ctx = ctx_for(
        tmp_path,
        post_responses={"thing_bases/K1/versions": FakeResponse(500, {})},
        delete_response=FakeResponse(404, {}),
    )
    with caplog.at_level(logging.ERROR):
        run(ctx)
    assert "was already gone" in caplog.text
    assert "removed the new base" not in caplog.text


def test_existing_key_without_a_version_is_a_half_made_item(tmp_path, caplog):
    ctx = ctx_for(tmp_path, collections={"thing_bases/K1/versions": []})
    with caplog.at_level(logging.ERROR):
        assert run(ctx, existing={"K1"}) == ["K2"]
    assert ctx.tracker("things").find("K1") is None
    assert [p[0] for p in ctx.target_internal.posts] == ["thing_bases", "thing_bases/K2/versions"]
    assert ctx.summary.to_dict()["things"]["failed"] == 1
    assert "has no version" in caplog.text


def test_keys_are_escaped_in_paths_but_not_in_bodies_or_tracking(tmp_path):
    key = "A/B?c"
    ctx = ctx_for(tmp_path, post_responses={"thing_bases/A%2FB%3Fc/versions": FakeResponse(500, {})})
    item = {"id": key, "key": key, "body": "x"}
    run(ctx, items=[item])
    assert ctx.target_internal.posts[0] == ("thing_bases", {"data": {"attributes": {"id": key}}})
    assert ctx.target_internal.posts[1][0] == "thing_bases/A%2FB%3Fc/versions"
    assert ctx.target_internal.deletes == ["thing_bases/A%2FB%3Fc"]

    ctx = ctx_for(tmp_path)
    assert run(ctx, items=[item]) == [key]
    assert ctx.tracker("things").find(key)["target_id"] == key


def test_escaped_key_is_used_for_the_half_made_check(tmp_path):
    key = "A/B?c"
    ctx = ctx_for(tmp_path, collections={"thing_bases/A%2FB%3Fc/versions": [{"id": "v1"}]})
    run(ctx, items=[{"id": key, "key": key, "body": "x"}], existing={key})
    assert ctx.tracker("things").find(key)["origin"] == "matched"


def test_bad_base_payload_fails_only_that_item(tmp_path):
    ctx = ctx_for(tmp_path)
    items = [{"id": "K1", "body": "one", "key": "K1", "broken": True}, ITEMS[1]]

    def base(i):
        if i.get("broken"):
            raise KeyError("info")
        return {"data": {"attributes": {"id": i["key"]}}}

    created = load_versioned(
        ctx,
        "things",
        items,
        set(),
        client=ctx.target_internal,
        base_path="thing_bases",
        base_payload=base,
        version_path=lambda key: f"thing_bases/{key}/versions",
        version_payload=lambda i: {"data": {"attributes": {"body": i["body"]}}},
    )
    assert created == ["K2"]
    assert [p[0] for p in ctx.target_internal.posts] == ["thing_bases", "thing_bases/K2/versions"]
    assert ctx.summary.to_dict()["things"]["failed"] == 1


PUBLISH_ITEMS = [{"key": f"K{n}", "draft": n % 2 == 0} for n in range(1, 5)]


def test_remind_to_publish_lists_published_items(tmp_path, caplog):
    ctx = make_ctx(tmp_path)
    with caplog.at_level(logging.INFO):
        remind_to_publish(ctx, "things", PUBLISH_ITEMS, ["K1", "K2", "K3"])
    assert "2 of 3 were published on the source" in caplog.text
    assert "K1, K3" in caplog.text


def test_remind_to_publish_dry_run_wording(tmp_path, caplog):
    ctx = make_ctx(tmp_path, dry_run=True)
    with caplog.at_level(logging.INFO):
        remind_to_publish(ctx, "things", PUBLISH_ITEMS, ["K1", "K2", "K3"])
    assert (
        "things: 3 new items would be created as drafts; 2 of them were published on the source and would "
        "need publishing on the target"
    ) in caplog.text


def test_remind_to_publish_none_published(tmp_path, caplog):
    ctx = make_ctx(tmp_path)
    with caplog.at_level(logging.INFO):
        remind_to_publish(ctx, "things", PUBLISH_ITEMS, ["K2", "K4"])
    assert "0 of 2 were published on the source" in caplog.text
    assert "K2" not in caplog.text


def test_remind_to_publish_more_than_twenty(tmp_path, caplog):
    items = [{"key": f"K{n}"} for n in range(25)]
    ctx = make_ctx(tmp_path)
    with caplog.at_level(logging.INFO):
        remind_to_publish(ctx, "things", items, [i["key"] for i in items])
    assert "K19 and 5 more" in caplog.text
    assert "K20" not in caplog.text
    assert "..." not in caplog.text


def test_remind_to_publish_prefers_published_on_source(tmp_path, caplog):
    items = [
        {"key": "K1", "draft": False, "published_on_source": True},
        {"key": "K2", "draft": True, "published_on_source": False},
    ]
    ctx = make_ctx(tmp_path)
    with caplog.at_level(logging.INFO):
        remind_to_publish(ctx, "things", items, ["K1", "K2"])
    assert "1 of 2 were published on the source; publish them on the target when ready: K1" in caplog.text


DRAFT_OVER = [
    {"key": "K1", "draft": True, "published_on_source": True},
    {"key": "K2", "draft": False, "published_on_source": True},
    {"key": "K3", "draft": True, "published_on_source": False},
]


def test_remind_to_publish_warns_about_drafts_over_published_versions(tmp_path, caplog):
    # Publishing a copy of the source's unpublished draft would release changes the source hasn't published
    ctx = make_ctx(tmp_path)
    with caplog.at_level(logging.INFO):
        remind_to_publish(ctx, "things", DRAFT_OVER, ["K1", "K2", "K3"])
    assert "1 of 3 were published on the source; publish them on the target when ready: K2" in caplog.text
    [warning] = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warning == (
        "things: 1 new items are copies of the source's unpublished draft, which sits over an earlier "
        "published version; review them before publishing: K1"
    )


def test_remind_to_publish_draft_over_published_dry_run_wording(tmp_path, caplog):
    ctx = make_ctx(tmp_path, dry_run=True)
    with caplog.at_level(logging.INFO):
        remind_to_publish(ctx, "things", DRAFT_OVER, ["K1", "K2", "K3"])
    assert "1 of them were published on the source and would need publishing on the target: K2" in caplog.text
    assert (
        "things: 1 new items would be copies of the source's unpublished draft, which sits over an earlier "
        "published version; review them before publishing: K1"
    ) in caplog.text


def test_remind_to_publish_draft_over_published_more_than_twenty(tmp_path, caplog):
    items = [{"key": f"K{n}", "draft": True, "published_on_source": True} for n in range(25)]
    ctx = make_ctx(tmp_path)
    with caplog.at_level(logging.WARNING):
        remind_to_publish(ctx, "things", items, [i["key"] for i in items])
    assert "review them before publishing: K0" in caplog.text and "K19 and 5 more" in caplog.text
    assert "K20" not in caplog.text


def test_remind_to_publish_no_draft_over_published_warning_without_any(tmp_path, caplog):
    ctx = make_ctx(tmp_path)
    with caplog.at_level(logging.WARNING):
        remind_to_publish(ctx, "things", PUBLISH_ITEMS, ["K1", "K2", "K3"])
    assert caplog.records == []


def test_version_success_without_an_id_is_still_created(tmp_path):
    # The version's ID isn't used, so any 2xx is success; deleting the base here would remove a complete item
    ctx = ctx_for(tmp_path, post_responses={"thing_bases/K1/versions": FakeResponse(201, {})})
    assert run(ctx, items=ITEMS[:1]) == ["K1"]
    assert ctx.target_internal.deletes == []
    assert ctx.summary.to_dict()["things"]["created"] == 1


def test_version_success_with_no_body_is_still_created(tmp_path):
    ctx = ctx_for(tmp_path, post_responses={"thing_bases/K1/versions": FakeResponse(204)})
    assert run(ctx, items=ITEMS[:1]) == ["K1"]
    assert ctx.target_internal.deletes == []
    assert ctx.summary.to_dict()["things"]["created"] == 1
