import logging

import pytest

from kit.clients.base import APIError
from kit.internal import questionnaires
from kit.tracking import load_extract, save_extract
from tests.fakes import FakeResponse, FakeTenant, make_ctx

SECTIONS = [
    {
        "id": "s1",
        "title": "General",
        "description": None,
        "questions": [
            {
                "id": "q1",
                "question": "Owner?",
                "description": None,
                "evidence_type": "user",
                "required": True,
                "hidden": False,
                "multiple": False,
                "select_options": None,
                "alert_triggers": [],
            }
        ],
    }
]
INFO = {"description": "About it", "introduction": "Start here", "logo_url": "https://example.com/logo.png"}


def base(key, custom=True, archived=False, show_on_create=False, target="use_case", name="Intake"):
    return {
        "id": key,
        "attributes": {
            "custom": custom,
            "archived": archived,
            "name": name,
            "target": target,
            "show_on_create": show_on_create,
            "info": dict(INFO),
        },
    }


def version(n, draft=False):
    return {"id": f"K+{n}", "questionnaire_id": "K", "version": n, "draft": draft, "sections": []}


def source_ctx(tmp_path, bases, json_bodies):
    return make_ctx(
        tmp_path,
        source=FakeTenant(json_bodies=json_bodies, dry_run=True),
        source_internal=FakeTenant({"questionnaire_bases": bases}, dry_run=True),
    )


def test_extract_keeps_live_custom_questionnaires_with_latest_version_content(tmp_path):
    ctx = source_ctx(
        tmp_path,
        [base("K"), base("CR-MODEL", custom=False), base("OLD", archived=True)],
        {
            "questionnaires/K/versions": [version(1), version(2, draft=True)],
            "questionnaires/K/spec": {"key": "K", "info": None, "sections": SECTIONS},
        },
    )
    assert questionnaires.extract(ctx) == 1
    [item] = load_extract(tmp_path, "questionnaires")
    assert item == {
        "id": "K",
        "key": "K",
        "name": "Intake",
        "target": "use_case",
        "info": INFO,
        "show_on_create": False,
        "version": 2,
        "draft": True,
        "published_on_source": True,
        "sections": SECTIONS,
    }
    assert ("questionnaires/K/spec", {"version": 2}) in ctx.source.gets


def test_extract_skips_a_questionnaire_with_no_versions(tmp_path, caplog):
    ctx = source_ctx(tmp_path, [base("K")], {"questionnaires/K/versions": []})
    with caplog.at_level(logging.WARNING):
        assert questionnaires.extract(ctx) == 0
    assert "'K' has no versions" in caplog.text


def test_extract_escapes_the_key(tmp_path):
    ctx = source_ctx(
        tmp_path,
        [base("A B/C")],
        {"questionnaires/A%20B%2FC/versions": [version(1)], "questionnaires/A%20B%2FC/spec": {"sections": SECTIONS}},
    )
    assert questionnaires.extract(ctx) == 1
    assert load_extract(tmp_path, "questionnaires")[0]["key"] == "A B/C"


def test_extract_stops_when_the_spec_has_no_sections_field(tmp_path):
    ctx = source_ctx(
        tmp_path, [base("K")], {"questionnaires/K/versions": [version(1)], "questionnaires/K/spec": {"key": "K"}}
    )
    with pytest.raises(APIError, match="'K'.*sections"):
        questionnaires.extract(ctx)


@pytest.mark.parametrize("sections, expected", [(SECTIONS, SECTIONS), (None, [])])
def test_read_sections_reads_one_version(sections, expected):
    client = FakeTenant(json_bodies={"questionnaires/A%20B/spec": {"sections": sections}})
    assert questionnaires.read_sections(client, "A B", 3) == expected
    assert client.gets == [("questionnaires/A%20B/spec", {"version": 3})]


def test_extract_stops_when_a_base_has_no_custom_flag(tmp_path):
    no_flag = base("K")
    del no_flag["attributes"]["custom"]
    ctx = source_ctx(tmp_path, [no_flag], {})
    with pytest.raises(APIError, match="'K'.*custom"):
        questionnaires.extract(ctx)


def test_extract_names_the_questionnaire_when_a_read_fails(tmp_path):
    ctx = source_ctx(
        tmp_path, [base("K")], {"questionnaires/K/versions": APIError("GET questionnaires/K/versions failed: 500", 500)}
    )
    with pytest.raises(APIError, match="reading 'K' failed"):
        questionnaires.extract(ctx)


def test_extract_info_defaults_when_the_base_has_none(tmp_path):
    bare = base("K")
    bare["attributes"]["info"] = None
    ctx = source_ctx(
        tmp_path, [bare], {"questionnaires/K/versions": [version(1)], "questionnaires/K/spec": {"sections": SECTIONS}}
    )
    questionnaires.extract(ctx)
    assert load_extract(tmp_path, "questionnaires")[0]["info"] == {
        "description": None,
        "introduction": None,
        "logo_url": None,
    }


@pytest.mark.parametrize("missing", ["version", "draft"])
def test_extract_stops_on_a_malformed_version(tmp_path, missing):
    bad = version(1)
    del bad[missing]
    ctx = source_ctx(
        tmp_path, [base("K")], {"questionnaires/K/versions": [bad], "questionnaires/K/spec": {"sections": SECTIONS}}
    )
    with pytest.raises(APIError, match="reading 'K' failed.*unexpected shape"):
        questionnaires.extract(ctx)


def test_extract_stops_on_a_version_that_is_not_a_dict(tmp_path):
    ctx = source_ctx(
        tmp_path, [base("K")], {"questionnaires/K/versions": ["K+1"], "questionnaires/K/spec": {"sections": SECTIONS}}
    )
    with pytest.raises(APIError, match="reading 'K' failed.*unexpected shape"):
        questionnaires.extract(ctx)


def test_extract_stops_when_a_base_has_no_attributes(tmp_path):
    ctx = source_ctx(tmp_path, [{"id": "K"}], {})
    with pytest.raises(APIError, match="'K'.*custom"):
        questionnaires.extract(ctx)


@pytest.mark.parametrize("value", ["missing", None, "false"])
def test_extract_stops_when_a_base_has_no_show_on_create(tmp_path, value):
    no_flag = base("K")
    if value == "missing":
        del no_flag["attributes"]["show_on_create"]
    else:
        no_flag["attributes"]["show_on_create"] = value
    ctx = source_ctx(
        tmp_path,
        [no_flag],
        {"questionnaires/K/versions": [version(1)], "questionnaires/K/spec": {"sections": SECTIONS}},
    )
    with pytest.raises(APIError, match="'K'.*show_on_create"):
        questionnaires.extract(ctx)


def item(key="K", show_on_create=False):
    return {
        "id": key,
        "key": key,
        "name": "Intake",
        "target": "use_case",
        "info": dict(INFO),
        "show_on_create": show_on_create,
        "version": 2,
        "draft": False,
        "published_on_source": True,
        "sections": SECTIONS,
    }


def target_ctx(tmp_path, items, bases=(), json_bodies=None, dry_run=False, **target_kwargs):
    save_extract(tmp_path, "questionnaires", items)
    return make_ctx(
        tmp_path,
        target=FakeTenant(json_bodies=json_bodies, **target_kwargs),
        target_internal=FakeTenant({"questionnaire_bases": list(bases)}, jsonapi=True),
        dry_run=dry_run,
    )


def test_load_creates_a_draft_with_content_and_info(tmp_path):
    ctx = target_ctx(tmp_path, [item(show_on_create=True)])
    questionnaires.load(ctx)
    [(path, body)] = ctx.target.posts
    assert path == "questionnaires/with_content"
    assert body == {
        "publish": False,
        "spec": {
            "key": "K",
            "name": "Intake",
            "target": "use_case",
            "info": dict(INFO, show_on_create=True),
            "sections": SECTIONS,
        },
    }
    assert ctx.target.patches == [] and ctx.target_internal.posts == []
    assert ctx.tracker("questionnaires").find("K")["origin"] == "created"
    assert ctx.summary.to_dict()["questionnaires"]["created"] == 1


def test_show_on_create_false_is_set_after_the_create(tmp_path):
    ctx = target_ctx(tmp_path, [item("A B", show_on_create=False)])
    questionnaires.load(ctx)
    assert ctx.target.patches == [("questionnaires/A%20B", {"show_on_create": False})]


def test_failed_show_on_create_patch_still_counts_as_created(tmp_path, caplog):
    ctx = target_ctx(tmp_path, [item()], patch_response=FakeResponse(500, text="boom"))
    with caplog.at_level(logging.ERROR):
        questionnaires.load(ctx)
    assert ctx.summary.to_dict()["questionnaires"]["created"] == 1
    assert "'K' was created" in caplog.text and "show_on_create" in caplog.text
    assert "In the target's Questionnaires list, set its Visibility to Not visible" in caplog.text


def test_failed_create_is_not_tracked(tmp_path):
    ctx = target_ctx(tmp_path, [item()], post_responses={"questionnaires/with_content": FakeResponse(422, text="bad")})
    questionnaires.load(ctx)
    assert ctx.tracker("questionnaires").find("K") is None
    assert ctx.summary.to_dict()["questionnaires"]["failed"] == 1
    assert ctx.target.patches == []


def test_tracked_questionnaire_is_skipped(tmp_path):
    ctx = target_ctx(tmp_path, [item()])
    ctx.tracker("questionnaires").record("K", "K", "K", "created")
    questionnaires.load(ctx)
    assert ctx.target.posts == []
    assert ctx.summary.to_dict()["questionnaires"]["skipped"] == 1


@pytest.mark.parametrize("target_sections, wording", [(SECTIONS, "same sections and questions"), ([], "differs")])
def test_existing_key_is_matched_and_compared(tmp_path, caplog, target_sections, wording):
    ctx = target_ctx(
        tmp_path,
        [item()],
        bases=[base("K")],
        json_bodies={
            "questionnaires/K/versions": [version(1), version(3, draft=True)],
            "questionnaires/K/spec": {"sections": target_sections},
        },
    )
    with caplog.at_level(logging.WARNING):
        questionnaires.load(ctx)
    assert ctx.target.posts == []
    assert ctx.tracker("questionnaires").find("K")["origin"] == "matched"
    assert ("questionnaires/K/spec", {"version": 3}) in ctx.target.gets
    assert "already exists on the target" in caplog.text and wording in caplog.text


def test_existing_key_without_versions_fails(tmp_path, caplog):
    ctx = target_ctx(tmp_path, [item()], bases=[base("K")], json_bodies={"questionnaires/K/versions": []})
    with caplog.at_level(logging.ERROR):
        questionnaires.load(ctx)
    assert ctx.summary.to_dict()["questionnaires"]["failed"] == 1
    assert ctx.tracker("questionnaires").find("K") is None
    assert "no version" in caplog.text


def test_existing_key_that_cant_be_read_fails(tmp_path):
    ctx = target_ctx(
        tmp_path, [item()], bases=[base("K")], json_bodies={"questionnaires/K/versions": APIError("failed: 500", 500)}
    )
    questionnaires.load(ctx)
    assert ctx.summary.to_dict()["questionnaires"]["failed"] == 1


def test_archived_match_is_warned_about_in_the_match_warning(tmp_path, caplog):
    ctx = target_ctx(
        tmp_path,
        [item()],
        bases=[base("K", archived=True)],
        json_bodies={"questionnaires/K/versions": [version(1)], "questionnaires/K/spec": {"sections": SECTIONS}},
    )
    with caplog.at_level(logging.WARNING):
        questionnaires.load(ctx)
    [warning] = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert "already exists on the target" in warning
    assert (
        "It's archived on the target; unarchive it there before loading triggers or use cases that use it." in warning
    )


def test_unarchived_match_has_no_archive_advice(tmp_path, caplog):
    ctx = target_ctx(
        tmp_path,
        [item()],
        bases=[base("K")],
        json_bodies={"questionnaires/K/versions": [version(1)], "questionnaires/K/spec": {"sections": SECTIONS}},
    )
    with caplog.at_level(logging.WARNING):
        questionnaires.load(ctx)
    assert "archived" not in caplog.text


def test_dry_run_writes_nothing_and_predicts_the_create(tmp_path, caplog):
    ctx = target_ctx(tmp_path, [item()], dry_run=True)
    with caplog.at_level(logging.INFO):
        questionnaires.load(ctx)
    assert ctx.target.posts == [] and ctx.target.patches == []
    assert ctx.summary.to_dict()["questionnaires"]["created"] == 1
    assert "would create 'K' as a draft" in caplog.text
    assert "would need publishing" in caplog.text
    # The triggers step reads a dry: placeholder as "created in this dry run" and takes the questions from the extract
    assert ctx.tracker("questionnaires").target_id("K") == "dry:K"


def test_matched_base_fields_are_compared_and_show_on_create_advice_given(tmp_path, caplog):
    ctx = target_ctx(
        tmp_path,
        [item(show_on_create=False)],
        bases=[base("K", show_on_create=True)],
        json_bodies={"questionnaires/K/versions": [version(1)], "questionnaires/K/spec": {"sections": SECTIONS}},
    )
    with caplog.at_level(logging.WARNING):
        questionnaires.load(ctx)
    assert "same sections and questions" in caplog.text
    assert "These fields differ from the source: show_on_create." in caplog.text
    assert (
        "If an earlier kit run made it, set its Visibility to Not visible in the target's Questionnaires list (show_on_create: false)."
        in caplog.text
    )
    assert ctx.target.patches == []
    assert ctx.tracker("questionnaires").find("K")["origin"] == "matched"


def test_matched_base_fields_list_every_difference(tmp_path, caplog):
    target_base = base("K", name="Other", target="model")
    target_base["attributes"]["info"] = dict(INFO, introduction="Changed")
    ctx = target_ctx(
        tmp_path,
        [item()],
        bases=[target_base],
        json_bodies={"questionnaires/K/versions": [version(1)], "questionnaires/K/spec": {"sections": SECTIONS}},
    )
    with caplog.at_level(logging.WARNING):
        questionnaires.load(ctx)
    assert "These fields differ from the source: name, target, introduction." in caplog.text
    assert "by hand" not in caplog.text


def test_matched_base_with_identical_fields_has_no_difference_wording(tmp_path, caplog):
    ctx = target_ctx(
        tmp_path,
        [item()],
        bases=[base("K")],
        json_bodies={"questionnaires/K/versions": [version(1)], "questionnaires/K/spec": {"sections": SECTIONS}},
    )
    with caplog.at_level(logging.WARNING):
        questionnaires.load(ctx)
    assert "same sections and questions" in caplog.text
    assert "differ" not in caplog.text and "by hand" not in caplog.text


def test_matched_base_without_attributes_is_compared_not_crashed(tmp_path, caplog):
    ctx = target_ctx(
        tmp_path,
        [item()],
        bases=[{"id": "K"}],
        json_bodies={"questionnaires/K/versions": [version(1)], "questionnaires/K/spec": {"sections": SECTIONS}},
    )
    with caplog.at_level(logging.WARNING):
        questionnaires.load(ctx)
    assert ctx.summary.to_dict()["questionnaires"]["matched"] == 1
    assert "differ from the source" in caplog.text


def test_matched_nameless_base_compares_its_key_as_the_name(tmp_path, caplog):
    # A base with no name is extracted with its key as the name, so that isn't a difference
    nameless = base("K", name=None)
    source_item = dict(item(), name="K")
    ctx = target_ctx(
        tmp_path,
        [source_item],
        bases=[nameless],
        json_bodies={"questionnaires/K/versions": [version(1)], "questionnaires/K/spec": {"sections": SECTIONS}},
    )
    with caplog.at_level(logging.WARNING):
        questionnaires.load(ctx)
    assert ctx.summary.to_dict()["questionnaires"]["matched"] == 1
    assert "differ" not in caplog.text


def test_failed_show_on_create_patches_are_listed_at_the_end(tmp_path, caplog):
    ctx = target_ctx(
        tmp_path, [item("A"), item("B"), item("C", show_on_create=True)], patch_response=FakeResponse(500, text="boom")
    )
    with caplog.at_level(logging.ERROR):
        questionnaires.load(ctx)
    assert ctx.summary.to_dict()["questionnaires"]["created"] == 3
    last = caplog.records[-1]
    assert last.levelno == logging.ERROR
    assert last.getMessage() == (
        "questionnaires: 2 new questionnaires are visible on the target but hidden on the source (show_on_create "
        "couldn't be set to false). In the target's Questionnaires list, set their Visibility to Not visible: A, B"
    )
    assert [a["entity"] for a in ctx.summary.attention] == ["questionnaires", "questionnaires"]
    assert "'A'" in ctx.summary.attention[0]["message"]


class RaisingPatchTenant(FakeTenant):
    def patch(self, path, json):
        self.patches.append((path, json))
        raise APIError(f"PATCH {path} lost its connection")


def test_show_on_create_patch_that_raises_is_handled_the_same_way(tmp_path, caplog):
    save_extract(tmp_path, "questionnaires", [item()])
    ctx = make_ctx(
        tmp_path, target=RaisingPatchTenant(), target_internal=FakeTenant({"questionnaire_bases": []}, jsonapi=True)
    )
    with caplog.at_level(logging.ERROR):
        questionnaires.load(ctx)
    assert ctx.summary.to_dict()["questionnaires"]["created"] == 1
    assert ctx.tracker("questionnaires").find("K")["origin"] == "created"
    assert "lost its connection" in caplog.text
    assert "1 new questionnaires are visible on the target" in caplog.text and "Not visible: K" in caplog.text
    assert len(ctx.summary.attention) == 1


def test_dry_run_sends_no_show_on_create_patch_and_logs_no_error(tmp_path, caplog):
    ctx = target_ctx(tmp_path, [item(show_on_create=False)], dry_run=True)
    with caplog.at_level(logging.INFO):
        questionnaires.load(ctx)
    assert ctx.target.patches == []
    assert "show_on_create" not in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_differing_sections_on_a_match_say_how_to_get_a_fresh_copy(tmp_path, caplog):
    ctx = target_ctx(
        tmp_path,
        [item()],
        bases=[base("K")],
        json_bodies={"questionnaires/K/versions": [version(1)], "questionnaires/K/spec": {"sections": []}},
    )
    with caplog.at_level(logging.WARNING):
        questionnaires.load(ctx)
    assert (
        "If an earlier kit run made it and you want a fresh copy, delete it on the target, remove its entry "
        "from data/loaded_questionnaires.json, and re-run."
    ) in caplog.text


def test_same_sections_on_a_match_give_no_fresh_copy_advice(tmp_path, caplog):
    ctx = target_ctx(
        tmp_path,
        [item()],
        bases=[base("K")],
        json_bodies={"questionnaires/K/versions": [version(1)], "questionnaires/K/spec": {"sections": SECTIONS}},
    )
    with caplog.at_level(logging.WARNING):
        questionnaires.load(ctx)
    assert "fresh copy" not in caplog.text


class PatchRecordsTracking(FakeTenant):
    """Records whether the questionnaire was already tracked when the show_on_create PATCH went out."""

    def __init__(self, data_dir, **kwargs):
        super().__init__(**kwargs)
        self.data_dir = data_dir
        self.tracked_at_patch = None

    def patch(self, path, json):
        self.tracked_at_patch = "K" in (self.data_dir / "loaded_questionnaires.json").read_text()
        raise APIError(f"PATCH {path} lost its connection")


def test_created_questionnaire_is_tracked_before_the_patch(tmp_path):
    save_extract(tmp_path, "questionnaires", [item()])
    target = PatchRecordsTracking(tmp_path)
    ctx = make_ctx(tmp_path, target=target, target_internal=FakeTenant({"questionnaire_bases": []}, jsonapi=True))
    questionnaires.load(ctx)
    assert target.tracked_at_patch is True


def test_failed_create_hints_at_a_letter_case_clash(tmp_path, caplog):
    ctx = target_ctx(
        tmp_path,
        [item("Intake")],
        bases=[base("INTAKE")],
        post_responses={"questionnaires/with_content": FakeResponse(422, text="bad")},
    )
    with caplog.at_level(logging.ERROR):
        questionnaires.load(ctx)
    assert ctx.summary.to_dict()["questionnaires"]["failed"] == 1
    assert "'Intake'" in caplog.text and "'INTAKE'" in caplog.text and "letter case" in caplog.text


def test_failed_create_without_a_case_clash_has_no_hint(tmp_path, caplog):
    ctx = target_ctx(
        tmp_path,
        [item("K")],
        bases=[base("OTHER")],
        post_responses={"questionnaires/with_content": FakeResponse(422, text="bad")},
    )
    with caplog.at_level(logging.ERROR):
        questionnaires.load(ctx)
    assert "letter case" not in caplog.text


def test_extract_log_names_the_skipped_questionnaires(tmp_path, caplog):
    ctx = source_ctx(tmp_path, [base("CR-MODEL", custom=False), base("OLD", archived=True)], {})
    with caplog.at_level(logging.INFO):
        questionnaires.extract(ctx)
    assert "questionnaires: skipping 1 built-in and 1 archived custom questionnaires" in caplog.text


@pytest.mark.parametrize("target", ["use_case", "vendor", "model"])
def test_extract_accepts_known_targets(tmp_path, target):
    ctx = source_ctx(
        tmp_path,
        [base("K", target=target)],
        {"questionnaires/K/versions": [version(1)], "questionnaires/K/spec": {"sections": SECTIONS}},
    )
    assert questionnaires.extract(ctx) == 1


@pytest.mark.parametrize("target", ["policy", None, "missing"])
def test_extract_stops_on_an_unknown_target(tmp_path, target):
    odd = base("K", target=target)
    if target == "missing":
        del odd["attributes"]["target"]
    ctx = source_ctx(
        tmp_path, [odd], {"questionnaires/K/versions": [version(1)], "questionnaires/K/spec": {"sections": SECTIONS}}
    )
    with pytest.raises(APIError, match="'K'.*target"):
        questionnaires.extract(ctx)


@pytest.mark.parametrize(
    "versions, expected",
    [
        ([version(1), version(2), version(3, draft=True)], "K+2"),
        ([version(1)], "K+1"),
        ([version(1, draft=True)], None),
        ([], None),
    ],
)
def test_published_version_id_is_the_latest_published(versions, expected):
    client = FakeTenant(json_bodies={"questionnaires/K/versions": versions})
    assert questionnaires.published_version_id(client, "K") == expected


def test_published_version_id_is_none_for_a_404():
    client = FakeTenant(json_bodies={"questionnaires/K/versions": APIError("GET failed: 404", 404)})
    assert questionnaires.published_version_id(client, "K") is None


def test_published_version_id_raises_on_other_errors():
    client = FakeTenant(json_bodies={"questionnaires/K/versions": APIError("GET failed: 500", 500)})
    with pytest.raises(APIError, match="500"):
        questionnaires.published_version_id(client, "K")
