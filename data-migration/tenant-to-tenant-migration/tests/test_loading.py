from kit.clients.base import APIError
from kit.loading import MissingReference, data_id, load_reference
from tests.fakes import FakeResponse, FakeTenant, make_ctx

SOURCE_ITEMS = [{"id": "s1", "name": "Alpha"}, {"id": "s2", "name": "Beta"}]


def payload(item):
    return {"name": item["name"]}


def run(ctx, items=SOURCE_ITEMS, build=payload):
    load_reference(ctx, "widgets", items, ctx.target.get_all("widgets"), build, "widgets")


def test_creates_missing_items_and_tracks_them(tmp_path):
    ctx = make_ctx(tmp_path)
    run(ctx)
    assert [p[1] for p in ctx.target.posts] == [{"name": "Alpha"}, {"name": "Beta"}]
    assert ctx.tracker("widgets").find("s1")["origin"] == "created"
    assert ctx.summary.to_dict()["widgets"]["created"] == 2


def test_second_run_creates_nothing(tmp_path):
    target = FakeTenant()
    run(make_ctx(tmp_path, target=target))
    second = make_ctx(tmp_path, target=target)
    run(second)
    assert len(target.posts) == 2
    assert second.summary.to_dict()["widgets"] == {"created": 0, "matched": 0, "skipped": 2, "failed": 0}


def test_same_named_target_item_is_adopted_not_created(tmp_path):
    target = FakeTenant({"widgets": [{"id": "t-alpha", "name": "Alpha"}]})
    ctx = make_ctx(tmp_path, target=target)
    run(ctx)
    assert [p[1] for p in target.posts] == [{"name": "Beta"}]
    assert ctx.tracker("widgets").find("s1") == {
        "source_id": "s1",
        "target_id": "t-alpha",
        "name_or_key": "Alpha",
        "origin": "matched",
    }


def test_ambiguous_target_name_fails_without_writing(tmp_path):
    target = FakeTenant({"widgets": [{"id": "t1", "name": "Alpha"}, {"id": "t2", "name": "Alpha"}]})
    ctx = make_ctx(tmp_path, target=target)
    run(ctx, items=[SOURCE_ITEMS[0]])
    assert target.posts == []
    assert ctx.summary.has_failures


def test_missing_reference_fails_the_item(tmp_path):
    def build(item):
        raise MissingReference("entity type 'agent' is not on the target")

    ctx = make_ctx(tmp_path)
    run(ctx, items=[SOURCE_ITEMS[0]], build=build)
    assert ctx.target.posts == []
    assert ctx.summary.to_dict()["widgets"]["failed"] == 1


def test_failed_create_is_not_tracked(tmp_path):
    target = FakeTenant(post_response=FakeResponse(500, {"detail": "boom"}))
    ctx = make_ctx(tmp_path, target=target)
    run(ctx, items=[SOURCE_ITEMS[0]])
    assert ctx.tracker("widgets").find("s1") is None
    assert ctx.summary.has_failures


def test_422_already_exists_is_a_failure_not_a_success(tmp_path):
    target = FakeTenant(post_response=FakeResponse(422, {"detail": [{"msg": "name has already been taken"}]}))
    ctx = make_ctx(tmp_path, target=target)
    run(ctx, items=[SOURCE_ITEMS[0]])
    assert ctx.tracker("widgets").find("s1") is None
    assert ctx.summary.has_failures


def test_dry_run_predicts_creates_without_writing(tmp_path):
    ctx = make_ctx(tmp_path, dry_run=True)
    run(ctx)
    assert ctx.target.posts == []
    assert ctx.summary.to_dict()["widgets"]["created"] == 2
    assert ctx.tracker("widgets").target_id("s1") == "dry:s1"
    assert not (tmp_path / "loaded_widgets.json").exists()


def test_success_without_an_id_fails_the_item_and_continues(tmp_path):
    target = FakeTenant(post_response=FakeResponse(201, {"data": {"id": "x"}}))
    ctx = make_ctx(tmp_path, target=target)
    run(ctx)
    assert len(target.posts) == 2
    assert ctx.summary.to_dict()["widgets"]["failed"] == 2
    assert ctx.tracker("widgets").find("s1") is None


def test_success_with_non_json_body_fails_the_item(tmp_path):
    target = FakeTenant(post_response=FakeResponse(201, text=""))
    ctx = make_ctx(tmp_path, target=target)
    run(ctx, items=[SOURCE_ITEMS[0]])
    assert ctx.summary.to_dict()["widgets"]["failed"] == 1


def test_duplicate_source_names_warn(tmp_path, caplog):
    ctx = make_ctx(tmp_path)
    run(ctx, items=[{"id": "s1", "name": "Alpha"}, {"id": "s2", "name": "Alpha"}])
    assert len(ctx.target.posts) == 1
    assert "more than one source item named 'Alpha'" in caplog.text


def test_conflicting_adopted_item_warns(tmp_path, caplog):
    target = FakeTenant({"widgets": [{"id": "t1", "name": "Alpha", "type": "number"}]})
    ctx = make_ctx(tmp_path, target=target)
    load_reference(
        ctx,
        "widgets",
        [{"id": "s1", "name": "Alpha", "type": "string"}],
        target.get_all("widgets"),
        payload,
        "widgets",
        compare=("type",),
    )
    assert "differs from the source: type" in caplog.text


def test_load_reference_can_create_through_another_client(tmp_path):
    internal = FakeTenant(jsonapi=True)
    ctx = make_ctx(tmp_path, target_internal=internal)
    load_reference(
        ctx,
        "widgets",
        SOURCE_ITEMS,
        [],
        lambda i: {"data": {"attributes": {"name": i["name"]}}},
        "custom_widgets",
        client=internal,
        id_of=data_id,
    )
    assert [p[0] for p in internal.posts] == ["custom_widgets", "custom_widgets"]
    assert ctx.target.posts == []
    assert ctx.tracker("widgets").target_id("s1") == "new-1"


def test_connection_error_fails_only_that_item(tmp_path, caplog):
    class Flaky(FakeTenant):
        def post(self, path, json):
            if json["name"] == "Alpha":
                raise APIError("POST widgets failed: connection reset")
            return super().post(path, json)

    ctx = make_ctx(tmp_path, target=Flaky())
    run(ctx)
    assert ctx.summary.to_dict()["widgets"] == {"created": 1, "matched": 0, "skipped": 0, "failed": 1}
    assert ctx.tracker("widgets").find("s1") is None
    assert "'Alpha'" in caplog.text and "connection reset" in caplog.text


def test_missing_field_in_payload_fails_only_that_item(tmp_path, caplog):
    def build(item):
        return {"name": item["name"], "type": item["type"]}

    ctx = make_ctx(tmp_path)
    run(ctx, items=[{"id": "s1", "name": "Alpha"}, {"id": "s2", "name": "Beta", "type": "x"}], build=build)
    assert [p[1] for p in ctx.target.posts] == [{"name": "Beta", "type": "x"}]
    assert ctx.summary.to_dict()["widgets"]["failed"] == 1
    assert "skipping 'Alpha': a field the kit needs is missing ('type')" in caplog.text


def test_load_reference_calls_after_create_for_new_items_only(tmp_path):
    ctx = make_ctx(tmp_path, target=FakeTenant({"things": [{"id": "t1", "name": "Old"}]}))
    calls = []
    load_reference(
        ctx,
        "things",
        items=[{"id": "s1", "name": "Old"}, {"id": "s2", "name": "New"}],
        target_items=ctx.target.get_all("things"),
        build_payload=lambda i: {"name": i["name"]},
        path="things",
        after_create=lambda item, target_id: calls.append((item["id"], target_id)),
    )
    assert calls == [("s2", "new-1")]


def test_load_reference_calls_after_create_in_dry_run_with_the_placeholder(tmp_path):
    ctx = make_ctx(tmp_path, dry_run=True)
    calls = []
    load_reference(
        ctx,
        "things",
        items=[{"id": "s1", "name": "New"}],
        target_items=[],
        build_payload=lambda i: {"name": i["name"]},
        path="things",
        after_create=lambda item, target_id: calls.append(target_id),
    )
    assert calls == ["dry:s1"]
