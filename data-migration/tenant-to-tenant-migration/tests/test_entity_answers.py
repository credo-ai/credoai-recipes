import logging

import pytest

from kit.clients.base import APIError
from kit.entities import entity_answers
from kit.tracking import load_extract, save_extract
from tests.fakes import FakeResponse, FakeTenant, make_ctx
from tests.test_answers import SpecTenant, evidence, question, section

PAUSE = {"data": {"type": "entities", "attributes": {"trigger_mode": "hidden_questions_only"}}}
SOURCE_MQ_1 = [section("s1", "About", question("q1", "Purpose?"), question("q2", "Region?"))]
TARGET_MQ_2 = [section("ts1", "About", question("q1", "Purpose?"), question("q2", "Region?"))]
SPECS = {
    "models": (entity_answers.model_answers, "model"),
    "vendors": (entity_answers.vendor_answers, "vendor"),
}


# --- extract ---


def source_ctx(tmp_path, kind, listing, evidences, attachments):
    """listing: [(id, name, custom)]. Models carry the custom flag on the Integration list, vendors on the internal
    one."""
    if kind == "models":
        integration = [{"id": i, "name": n, "custom": c} for i, n, c in listing]
        internal = {}
    else:
        integration = [{"id": i, "name": n} for i, n, _c in listing]
        internal = {"vendors": [{"id": i, "attributes": {"custom": c}} for i, _n, c in listing]}
    collections = {kind: integration}
    for i, _n, _c in listing:
        collections[f"{kind}/{i}/questionnaires"] = attachments.get(i, [])
        internal[f"{kind}/{i}/evidences"] = evidences.get(i, [])
    source = SpecTenant(collections, specs={("MQ", 1): SOURCE_MQ_1}, dry_run=True)
    return make_ctx(tmp_path, source=source, source_internal=FakeTenant(internal, dry_run=True, jsonapi=True))


@pytest.mark.parametrize("kind", ["models", "vendors"])
def test_extract_keeps_custom_entities_with_answers_and_counts_built_in_answers(tmp_path, caplog, kind):
    module, noun = SPECS[kind]
    ctx = source_ctx(
        tmp_path,
        kind,
        listing=[
            ("c1", "Answered", True),
            ("c2", "Silent", True),
            ("b1", "Catalog one", False),
            ("b2", "Catalog two", False),
        ],
        evidences={
            "c1": [evidence("e1", "q1", "s1", "Triage")],
            "b1": [
                evidence("e2", "q1", "s1", "x"),
                evidence("e3", "q2", "s1", "y"),
                {"id": "e4", "attributes": {"data": {"value": "z"}, "metadata": {}}},
            ],
            "b2": [evidence("e5", "q1", "s1", "x")],
        },
        attachments={"c1": [{"id": "a1", "key": "MQ", "version": 1}]},
    )
    with caplog.at_level(logging.INFO, logger="kit.extract"):
        assert module.extract(ctx) == 1
    assert load_extract(tmp_path, module.ENTITY) == [
        {
            "id": "c1",
            "name": "Answered",
            "attachments": [{"key": "MQ", "version": 1}],
            "answers": [
                {
                    "key": "MQ",
                    "version": 1,
                    "section_title": "About",
                    "question_id": "q1",
                    "question_text": "Purpose?",
                    "evidence_type": "text",
                    "multiple": False,
                    "value": "Triage",
                }
            ],
            "file_answers": 0,
        }
    ]
    assert (
        f"{module.ENTITY}: 3 answer(s) on 2 built-in {noun}(s) aren't copied: the target can't take answers on "
        f"built-in entities, whose answers come with Credo's catalog or with the integration that synced them"
    ) in caplog.text
    assert "Triage" not in caplog.text


def test_extract_stops_when_a_model_has_no_custom_flag(tmp_path):
    ctx = source_ctx(tmp_path, "models", [("m1", "A", None)], {}, {})
    with pytest.raises(APIError, match="custom"):
        entity_answers.model_answers.extract(ctx)


def test_extract_stops_when_a_vendor_is_missing_from_the_internal_list(tmp_path):
    ctx = source_ctx(tmp_path, "vendors", [("v1", "A", True)], {}, {})
    ctx.source_internal.collections["vendors"] = []
    with pytest.raises(APIError, match="internal"):
        entity_answers.vendor_answers.extract(ctx)


# --- load ---


def src_answer(qid, text, value):
    return {
        "key": "MQ",
        "version": 1,
        "section_title": "About",
        "question_id": qid,
        "question_text": text,
        "evidence_type": "text",
        "multiple": False,
        "value": value,
    }


def load_item(attachments=("MQ",)):
    return {
        "id": "s1",
        "name": "Answered",
        "attachments": [{"key": k, "version": 1} for k in attachments],
        "answers": [src_answer("q1", "Purpose?", "Triage"), src_answer("q2", "Region?", "EU")],
        "file_answers": 0,
    }


def target_ctx(
    tmp_path,
    kind="models",
    attached=("MQ",),
    current=(),
    published=("MQ",),
    dry_run=False,
    patch_response=None,
    mode="full",
    origin="created",
    track=True,
):
    target = SpecTenant(
        {f"{kind}/t1/questionnaires": [{"id": f"a-{k}", "key": k, "version": 2} for k in attached]},
        specs={("MQ", 2): TARGET_MQ_2},
        json_bodies={
            "questionnaires/MQ/versions": [
                {"version": 1, "draft": "MQ" not in published},
                {"version": 2, "draft": "MQ" not in published},
            ]
        },
    )
    internal = FakeTenant(
        {
            f"{kind}/t1/evidences": list(current),
            f"{kind}/t1/questionnaire_sections": [{"id": "es1", "attributes": {"section_id": "ts1"}}],
        },
        jsonapi=True,
        patch_response=patch_response,
        json_bodies={f"{kind}/t1": {"data": {"id": "t1", "attributes": {"entity_id": "e-t1", "trigger_mode": mode}}}},
    )
    ctx = make_ctx(tmp_path, target=target, target_internal=internal, dry_run=dry_run)
    if track:
        ctx.tracker(kind).record("s1", "t1", "Answered", origin)
    return ctx


def answer_posts(ctx, kind="models"):
    return [(p, b) for p, b in ctx.target.posts if p == f"{kind}/t1/questionnaire_sections/es1/evidences"]


@pytest.mark.parametrize("kind", ["models", "vendors"])
def test_load_pauses_triggers_then_writes_the_missing_answers(tmp_path, caplog, kind):
    module, noun = SPECS[kind]
    save_extract(tmp_path, module.ENTITY, [load_item()])
    ctx = target_ctx(tmp_path, kind, current=[evidence("x1", "q2", "ts1", "EU")])
    with caplog.at_level(logging.INFO, logger="kit.load"):
        module.load(ctx)
    assert ctx.target_internal.patches == [("entities/e-t1/trigger_mode", PAUSE)]
    assert ctx.target.posts == [
        (f"{kind}/t1/questionnaire_sections/es1/evidences", {"question_id": "q1", "data": {"value": "Triage"}})
    ]
    assert ctx.summary.to_dict()[module.ENTITY] == {"created": 1, "matched": 0, "skipped": 0, "failed": 0}
    assert f"{module.ENTITY}: answers: 1 written, 1 already there, 0 conflicts, 0 unmapped, 0 failed" in caplog.text
    assert not ctx.summary.needs_attention
    assert "Triage" not in caplog.text


def test_load_attaches_a_missing_questionnaire_at_its_latest_published_version_first(tmp_path):
    save_extract(tmp_path, "model_answers", [load_item()])
    ctx = target_ctx(tmp_path, attached=())
    entity_answers.model_answers.load(ctx)
    assert ctx.target.posts[0] == ("models/t1/questionnaires", {"key": "MQ", "version": 2})
    assert len(answer_posts(ctx)) == 2
    assert not ctx.summary.needs_attention


def test_load_without_a_published_version_skips_that_key_and_says_publish(tmp_path, caplog):
    save_extract(tmp_path, "model_answers", [load_item()])
    ctx = target_ctx(tmp_path, attached=(), published=())
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        entity_answers.model_answers.load(ctx)
    assert ctx.target.posts == [] and ctx.target_internal.patches == []
    assert "publish questionnaire MQ on the target, then re-run" in caplog.text
    assert ctx.summary.needs_attention


@pytest.mark.parametrize("origin,why", [(None, "isn't tracked"), ("matched", "already on the target")])
def test_load_skips_an_entity_the_kit_did_not_create_with_its_answer_count(tmp_path, caplog, origin, why):
    save_extract(tmp_path, "vendor_answers", [load_item()])
    ctx = target_ctx(tmp_path, "vendors", track=origin is not None, origin=origin or "created")
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        entity_answers.vendor_answers.load(ctx)
    assert ctx.target.posts == [] and ctx.target_internal.patches == []
    assert ctx.summary.to_dict()["vendor_answers"]["skipped"] == 1
    assert ctx.summary.needs_attention
    assert "vendor 'Answered'" in caplog.text and why in caplog.text and "2 answer(s) not copied" in caplog.text


def test_load_proceeds_when_the_target_has_no_trigger_mode_for_it(tmp_path):
    save_extract(tmp_path, "model_answers", [load_item()])
    ctx = target_ctx(tmp_path, patch_response=FakeResponse(404, text='{"reason":"feature_flag_disabled"}'))
    entity_answers.model_answers.load(ctx)
    assert len(answer_posts(ctx)) == 2
    assert ctx.tracker("model_answers").records == []
    assert not ctx.summary.needs_attention


def test_load_writes_nothing_when_pausing_fails(tmp_path, caplog):
    save_extract(tmp_path, "model_answers", [load_item()])
    ctx = target_ctx(tmp_path, attached=(), patch_response=FakeResponse(500, text="boom"))
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        entity_answers.model_answers.load(ctx)
    assert ctx.target.posts == []
    assert ctx.summary.to_dict()["model_answers"]["failed"] == 1
    assert ctx.summary.needs_attention


def test_load_fails_the_entity_when_its_attachments_cannot_be_read(tmp_path, caplog):
    save_extract(tmp_path, "model_answers", [load_item()])
    ctx = target_ctx(tmp_path)

    def broken(path, params=None):
        raise APIError(f"GET {path} failed: 500", 500)

    ctx.target.get_all = broken
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        entity_answers.model_answers.load(ctx)
    assert ctx.target.posts == [] and ctx.target_internal.patches == []
    assert ctx.summary.to_dict()["model_answers"]["failed"] == 1
    assert "model 'Answered'" in caplog.text


def test_second_run_writes_nothing(tmp_path):
    save_extract(tmp_path, "model_answers", [load_item()])
    ctx = target_ctx(tmp_path, current=[evidence("x1", "q1", "ts1", "Triage"), evidence("x2", "q2", "ts1", "EU")])
    ctx.tracker("model_answers").record("s1", "t1", "Answered", "created")
    entity_answers.model_answers.load(ctx)
    assert ctx.target.posts == [] and ctx.target_internal.patches == [] and ctx.target_internal.posts == []
    assert ctx.summary.to_dict()["model_answers"] == {"created": 0, "matched": 0, "skipped": 1, "failed": 0}
    assert not ctx.summary.needs_attention


def test_dry_run_writes_nothing_and_counts_like_a_live_run(tmp_path, caplog):
    save_extract(tmp_path / "live", "model_answers", [load_item()])
    save_extract(tmp_path / "dry", "model_answers", [load_item()])
    live = target_ctx(tmp_path / "live", attached=())
    entity_answers.model_answers.load(live)
    dry = target_ctx(tmp_path / "dry", attached=(), dry_run=True)
    with caplog.at_level(logging.INFO, logger="kit.load"):
        entity_answers.model_answers.load(dry)
    assert dry.target.posts == [] and dry.target_internal.patches == []
    assert dry.summary.to_dict() == live.summary.to_dict()
    assert "would attach questionnaire MQ+2 to model 'Answered'" in caplog.text
    assert "model_answers: answers: 2 would be written" in caplog.text


def test_dry_run_on_a_model_created_earlier_in_the_same_dry_run(tmp_path):
    save_extract(tmp_path, "model_answers", [load_item()])
    ctx = target_ctx(tmp_path, dry_run=True, track=False)
    ctx.tracker("models").record("s1", "dry:s1", "Answered", "created")
    entity_answers.model_answers.load(ctx)
    assert ctx.target.posts == [] and ctx.target_internal.patches == []
    assert ctx.summary.to_dict()["model_answers"]["created"] == 1
    assert not ctx.summary.needs_attention


def test_rerun_with_triggers_turned_back_on_writes_no_answers(tmp_path, caplog):
    save_extract(tmp_path, "model_answers", [load_item()])
    ctx = target_ctx(tmp_path, mode="full")
    ctx.tracker("model_answers").record("s1", "t1", "Answered", "created")  # paused by an earlier run
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        entity_answers.model_answers.load(ctx)
    assert ctx.target.posts == [] and ctx.target_internal.patches == []
    assert "triggers are on for model 'Answered'; the kit won't write answers" in caplog.text


def test_rerun_of_a_paused_entity_writes_without_pausing_again(tmp_path):
    save_extract(tmp_path, "model_answers", [load_item()])
    ctx = target_ctx(tmp_path, mode="hidden_questions_only")
    ctx.tracker("model_answers").record("s1", "t1", "Answered", "created")
    entity_answers.model_answers.load(ctx)
    assert ctx.target_internal.patches == []
    assert len(answer_posts(ctx)) == 2


def test_a_paused_entity_is_remembered_for_later_runs(tmp_path):
    save_extract(tmp_path, "model_answers", [load_item()])
    ctx = target_ctx(tmp_path)
    entity_answers.model_answers.load(ctx)
    assert ctx.tracker("model_answers").target_id("s1") == "t1"


def test_an_api_error_on_one_entity_does_not_stop_the_next(tmp_path, caplog):
    second = dict(load_item(), id="s2", name="Second")
    save_extract(tmp_path, "model_answers", [load_item(), second])
    ctx = target_ctx(tmp_path)
    ctx.tracker("models").record("s2", "t2", "Second", "created")
    ctx.target.collections["models/t2/questionnaires"] = [{"id": "a-MQ", "key": "MQ", "version": 2}]
    ctx.target_internal.collections.update(
        {
            "models/t2/evidences": [],
            "models/t2/questionnaire_sections": [{"id": "es2", "attributes": {"section_id": "ts1"}}],
        }
    )
    ctx.target_internal.json_bodies["models/t2"] = {
        "data": {"id": "t2", "attributes": {"entity_id": "e-t2", "trigger_mode": "full"}}
    }
    original = ctx.target_internal.get_all_cursor

    def read(path, params=None):
        if path == "models/t1/evidences":
            raise APIError(f"GET {path} failed: 500", 500)
        return original(path, params)

    ctx.target_internal.get_all_cursor = read
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        entity_answers.model_answers.load(ctx)
    assert [p for p, _ in ctx.target.posts] == ["models/t2/questionnaire_sections/es2/evidences"] * 2
    assert ctx.summary.to_dict()["model_answers"] == {"created": 1, "matched": 0, "skipped": 0, "failed": 1}
    assert ctx.summary.needs_attention
    assert "model 'Answered'" in caplog.text and "500" in caplog.text
