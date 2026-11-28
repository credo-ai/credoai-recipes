import json
import logging

import pytest

from kit.clients.base import APIError
from kit.internal import triggers
from kit.loading import LoadStopped
from kit.tracking import load_extract, save_extract
from tests.fakes import FakeResponse, FakeTenant, make_ctx

FLAG_OFF = '{"errors": [{"status": "404", "reason": "feature_flag_disabled"}]}'


def test_version_is_v3_when_resolve_triggers_lists():
    internal = FakeTenant(json_bodies={"resolve_triggers": {"data": []}})
    assert triggers.trigger_version(internal) == "v3"
    assert internal.gets == [("resolve_triggers", {"page[size]": 1})]


def test_version_is_v2_when_the_v3_flag_is_off():
    internal = FakeTenant(json_bodies={"resolve_triggers": APIError("GET resolve_triggers failed: 404", 404, FLAG_OFF)})
    assert triggers.trigger_version(internal) == "v2"


@pytest.mark.parametrize(
    "error",
    [
        APIError("GET resolve_triggers failed: 404", 404, '{"errors": [{"detail": "Not found"}]}'),
        APIError("GET resolve_triggers failed: 403", 403, FLAG_OFF),
        APIError("GET resolve_triggers failed: 500", 500, ""),
    ],
)
def test_any_other_error_is_raised(error):
    internal = FakeTenant(json_bodies={"resolve_triggers": error})
    with pytest.raises(APIError) as raised:
        triggers.trigger_version(internal)
    assert raised.value is error


# Extract


def trig(tid, qvid, archived=False, description="When yes", conditions=None):
    return {
        "id": tid,
        "description": description,
        "match": "all",
        "questionnaire_version_id": qvid,
        "conditions": conditions
        if conditions is not None
        else [
            {"type": "questionnaire_evidence", "data": {"options": ["yes"], "question_id": "q1", "section_id": "s1"}}
        ],
        "archived": archived,
        "actions": None,
        "warnings": None,
    }


def action(aid, kind="add_question", data=None, show_alert=False, **extra):
    attrs = {
        "description": f"Do {aid}",
        "type": kind,
        "data": data if data is not None else {"question_id": "q2"},
        "show_alert": show_alert,
        "target_group_key": "group-1",
        **extra,
    }
    return {"id": aid, "type": "resolve_action", "attributes": attrs}


def extract_ctx(tmp_path, source_triggers, actions_by_trigger, questionnaires=(("INTAKE1", 1),), v3=True):
    if questionnaires is not None:
        save_extract(tmp_path, "questionnaires", [{"key": k, "version": v} for k, v in questionnaires])
    version_body = {"data": []} if v3 else APIError("GET resolve_triggers failed: 404", 404, FLAG_OFF)
    internal = FakeTenant(
        {f"resolve_triggers/{tid}/actions": acts for tid, acts in actions_by_trigger.items()},
        dry_run=True,
        jsonapi=True,
        json_bodies={"resolve_triggers": version_body},
    )
    source = FakeTenant({"triggers": source_triggers}, dry_run=True)
    return make_ctx(tmp_path, source=source, source_internal=internal)


def test_extract_on_a_v2_source_flags_and_leaves_an_empty_extract(tmp_path, caplog):
    # Nothing later depends on triggers, so `extract all` goes on to the next entity
    ctx = extract_ctx(tmp_path, [trig("t1", "INTAKE1+1")], {"t1": []}, v3=False)
    with caplog.at_level(logging.ERROR, logger="kit.extract"):
        assert triggers.extract(ctx) == 0
    assert "v3" in caplog.text
    assert ctx.summary.needs_attention
    assert load_extract(tmp_path, "triggers") == []
    assert ctx.source_internal.gets == [("resolve_triggers", {"page[size]": 1})]


def test_extract_on_a_v2_source_empties_an_old_extract_so_a_load_does_nothing(tmp_path, caplog):
    ctx = extract_ctx(tmp_path, [trig("t1", "INTAKE1+1")], {"t1": []}, v3=False)
    save_extract(tmp_path, "triggers", [titem("old")])
    with caplog.at_level(logging.INFO, logger="kit.extract"):
        triggers.extract(ctx)
    assert load_extract(tmp_path, "triggers") == []
    assert "emptied" in caplog.text and "stale triggers can't be loaded" in caplog.text
    load = make_ctx(tmp_path)
    triggers.load(load)
    assert load.target.posts == [] and load.target_internal.posts == []
    assert load.summary.to_dict() == {}


def test_extract_needs_the_questionnaires_extract(tmp_path):
    ctx = extract_ctx(tmp_path, [trig("t1", "INTAKE1+1")], {"t1": []}, questionnaires=None)
    with pytest.raises(FileNotFoundError, match="extract questionnaires"):
        triggers.extract(ctx)
    assert not (tmp_path / "triggers.json").exists()


def test_extract_keeps_live_triggers_on_the_copied_version(tmp_path, caplog):
    ctx = extract_ctx(
        tmp_path,
        [
            trig("t1", "INTAKE1+3"),
            trig("t2", "INTAKE1+3", archived=True),
            trig("t3", "CR-MODEL+1"),
            trig("t4", "INTAKE1+2"),
            trig("t5", "INTK1-COPY+1"),
            trig("t6", "CLASS_101+12"),
        ],
        {"t1": [action("a1")], "t5": [action("a5"), action("a6")], "t6": []},
        questionnaires=(("INTAKE1", 3), ("INTK1-COPY", 1), ("CLASS_101", 12)),
    )
    with caplog.at_level(logging.INFO, logger="kit.extract"):
        assert triggers.extract(ctx) == 3
    assert [t["id"] for t in load_extract(tmp_path, "triggers")] == ["t1", "t5", "t6"]
    assert "1 archived" in caplog.text
    assert "1 on questionnaires not copied" in caplog.text
    assert "1 on an older version" in caplog.text
    assert "3 triggers with 3 actions" in caplog.text


def test_extract_item_shape_and_action_order(tmp_path):
    conditions = [
        {"type": "questionnaire_evidence", "data": {"options": ["a", "b"], "question_id": "q9", "section_id": "s9"}}
    ]
    ctx = extract_ctx(
        tmp_path,
        [trig("t1", "INTAKE1+1", description="If a or b", conditions=conditions)],
        {
            "t1": [
                action("a2", "add_questionnaire", {"questionnaire_id": "X+1"}, show_alert=True),
                action("a1", "add_question", {"question_id": "q2"}),
            ]
        },
    )
    triggers.extract(ctx)
    assert load_extract(tmp_path, "triggers") == [
        {
            "id": "t1",
            "description": "If a or b",
            "match": "all",
            "questionnaire_key": "INTAKE1",
            "source_version": 1,
            "conditions": conditions,
            "actions": [
                {
                    "id": "a2",
                    "description": "Do a2",
                    "type": "add_questionnaire",
                    "data": {"questionnaire_id": "X+1"},
                    "show_alert": True,
                },
                {
                    "id": "a1",
                    "description": "Do a1",
                    "type": "add_question",
                    "data": {"question_id": "q2"},
                    "show_alert": False,
                },
            ],
        }
    ]


def test_extract_defaults_a_missing_show_alert_to_false(tmp_path):
    act = action("a1")
    del act["attributes"]["show_alert"]
    ctx = extract_ctx(tmp_path, [trig("t1", "INTAKE1+1")], {"t1": [act]})
    triggers.extract(ctx)
    assert load_extract(tmp_path, "triggers")[0]["actions"][0]["show_alert"] is False


@pytest.mark.parametrize("qvid", ["INTAKE1", "INTAKE1+", "INTAKE1+v1", "+1", None])
def test_extract_raises_on_an_unexpected_questionnaire_version_id(tmp_path, qvid):
    ctx = extract_ctx(tmp_path, [trig("t1", qvid)], {"t1": []})
    with pytest.raises(APIError, match="questionnaire_version_id"):
        triggers.extract(ctx)
    assert not (tmp_path / "triggers.json").exists()


@pytest.mark.parametrize("missing", ["type", "data"])
def test_extract_raises_on_an_action_without_type_or_data(tmp_path, missing):
    act = action("a1")
    del act["attributes"][missing]
    ctx = extract_ctx(tmp_path, [trig("t1", "INTAKE1+1")], {"t1": [act]})
    with pytest.raises(APIError, match="a1"):
        triggers.extract(ctx)
    assert not (tmp_path / "triggers.json").exists()


def test_extract_warns_when_triggers_are_on_a_version_newer_than_the_questionnaires_extract(tmp_path, caplog):
    ctx = extract_ctx(tmp_path, [trig("t1", "INTAKE1+2")], {"t1": []}, questionnaires=(("INTAKE1", 1),))
    with caplog.at_level(logging.WARNING, logger="kit.extract"):
        assert triggers.extract(ctx) == 0
    assert "INTAKE1+2" in caplog.text and "extract questionnaires" in caplog.text


# Load

SPEC = {
    "sections": [
        {
            "id": "s1",
            "title": "One",
            "questions": [
                {"id": "q1", "question": "Pick", "evidence_type": "select", "select_options": ["yes", "no"]},
                {"id": "q2", "question": "Why", "evidence_type": "text", "select_options": []},
            ],
        },
        {
            "id": "s2",
            "title": "Two",
            "questions": [{"id": "q3", "question": "More", "evidence_type": "select", "select_options": ["a", "b"]}],
        },
    ]
}
DRAFT = [{"version": 1, "draft": True}]


def cond(q="q1", s="s1", options=("yes",)):
    return {"type": "questionnaire_evidence", "data": {"options": list(options), "question_id": q, "section_id": s}}


def act(aid, kind="add_question", data=None, show_alert=False, description=None):
    return {
        "id": aid,
        "description": description or f"Do {aid}",
        "type": kind,
        "data": data if data is not None else {"question_id": "q2", "section_id": "s1"},
        "show_alert": show_alert,
    }


def titem(tid, key="INTAKE1", actions=(), description="When yes", conditions=None):
    return {
        "id": tid,
        "description": description,
        "match": "all",
        "questionnaire_key": key,
        "source_version": 1,
        "conditions": conditions or [cond()],
        "actions": list(actions),
    }


def target_trigger(tid, key="INTAKE1", version=1, description="When yes", conditions=None, archived=False):
    return {
        "id": tid,
        "description": description,
        "match": "all",
        "questionnaire_version_id": f"{key}+{version}",
        "conditions": conditions or [cond()],
        "archived": archived,
        "actions": None,
        "warnings": None,
    }


def target_action(aid, kind="add_question", data=None, show_alert=False, description=None):
    a = act(aid, kind, data, show_alert, description)
    return {
        "id": aid,
        "type": "resolve_action",
        "attributes": {
            "description": a["description"],
            "type": kind,
            "data": a["data"],
            "show_alert": show_alert,
            "target_group_key": "g",
        },
    }


def track(tmp_path, entity, rows):
    """rows: [(source ID, target ID, origin)]"""
    (tmp_path / f"loaded_{entity}.json").write_text(
        json.dumps([{"source_id": s, "target_id": t, "name_or_key": s, "origin": o} for s, t, o in rows])
    )


class Recording(FakeTenant):
    """FakeTenant that also records get_data paths."""

    def get_data(self, path, params=None):
        self.gets.append((path, params))
        return super().get_data(path, params)


def load_ctx(
    tmp_path,
    items,
    questionnaires=(("INTAKE1", "created"),),
    versions=None,
    target=None,
    internal=None,
    target_triggers=(),
    dry_run=False,
    v3=True,
):
    """versions: {key: versions list}; defaults to one draft per tracked questionnaire."""
    save_extract(tmp_path, "triggers", items)
    if questionnaires is not None:
        track(tmp_path, "questionnaires", [(k, k, o) for k, o in questionnaires])
    bodies = {}
    for key, _origin in questionnaires or ():
        bodies[f"questionnaires/{key}/versions"] = (versions or {}).get(key, DRAFT)
        bodies[f"questionnaires/{key}/spec"] = SPEC
    tgt = Recording(dict({"triggers": list(target_triggers)}, **(target or {})), json_bodies=bodies)
    version_body = {"data": []} if v3 else APIError("GET resolve_triggers failed: 404", 404, FLAG_OFF)
    tgt_i = Recording(internal or {}, jsonapi=True, json_bodies={"resolve_triggers": version_body})
    return make_ctx(tmp_path, target=tgt, target_internal=tgt_i, dry_run=dry_run)


def action_posts(ctx):
    return [(p, b) for p, b in ctx.target_internal.posts if p.startswith("resolve_triggers/")]


def test_load_on_a_v2_target_flags_and_writes_nothing(tmp_path, caplog):
    # Nothing later depends on triggers, so `load all` goes on to the next entity
    ctx = load_ctx(tmp_path, [titem("t1", actions=[act("a1")])], v3=False)
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        triggers.load(ctx)
    assert "v2" in caplog.text
    assert ctx.summary.needs_attention
    assert ctx.target.posts == [] and ctx.target_internal.posts == []


def test_load_stops_without_the_questionnaires_tracker(tmp_path, caplog):
    ctx = load_ctx(tmp_path, [titem("t1", actions=[act("a1")])], questionnaires=None)
    with caplog.at_level(logging.ERROR, logger="kit.load"), pytest.raises(LoadStopped):
        triggers.load(ctx)
    assert "loaded_questionnaires.json" in caplog.text
    assert ctx.target.posts == [] and ctx.target_internal.posts == []


def test_load_with_no_triggers_does_nothing_even_on_a_v2_target_without_questionnaires(tmp_path, caplog):
    ctx = load_ctx(tmp_path, [], questionnaires=None, v3=False)
    with caplog.at_level(logging.INFO, logger="kit.load"):
        triggers.load(ctx)
    assert "nothing to load" in caplog.text
    assert ctx.target_internal.gets == [] and ctx.target.posts == []


@pytest.mark.parametrize("questionnaires", [(("INTAKE1", "matched"),), (("OTHER", "created"),)])
def test_load_skips_triggers_on_questionnaires_the_kit_did_not_create(tmp_path, caplog, questionnaires):
    ctx = load_ctx(tmp_path, [titem("t1", actions=[act("a1")]), titem("t2")], questionnaires=questionnaires)
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        triggers.load(ctx)
    assert ctx.target.posts == [] and ctx.target_internal.posts == []
    assert not any("INTAKE1" in path for path, _ in ctx.target.gets)
    assert ctx.summary.to_dict()["triggers"]["skipped"] == 2
    assert "INTAKE1" in caplog.text
    assert caplog.text.count("INTAKE1") == 1  # one warning per questionnaire


def test_load_targets_the_latest_draft_version(tmp_path):
    ctx = load_ctx(
        tmp_path, [titem("t1")], versions={"INTAKE1": [{"version": 1, "draft": False}, {"version": 2, "draft": True}]}
    )
    triggers.load(ctx)
    assert ctx.target.posts == [
        (
            "triggers",
            {
                "description": "When yes",
                "match": "all",
                "questionnaire_version_id": "INTAKE1+2",
                "conditions": [cond()],
            },
        )
    ]


def test_load_fails_triggers_on_a_published_latest_version(tmp_path, caplog):
    ctx = load_ctx(
        tmp_path,
        [titem("t1", actions=[act("a1")]), titem("t2", description="Other")],
        versions={"INTAKE1": [{"version": 1, "draft": False}]},
    )
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        triggers.load(ctx)
    assert ctx.target.posts == [] and ctx.target_internal.posts == []
    assert ctx.summary.to_dict()["triggers"]["failed"] == 2
    assert (
        "INTAKE1's latest version on the target is published, and published versions can't take triggers. "
        "Create a new draft version of INTAKE1 on the target, then re-run (load triggers before publishing)."
        in caplog.text
    )


@pytest.mark.parametrize(
    "condition, reason",
    [
        (cond(q="q9"), "question q9"),
        (cond(s="s9"), "section s9"),
        (cond(q="q3", s="s1", options=("a",)), "question q3"),  # the question is in another section
    ],
)
def test_load_fails_a_trigger_whose_condition_is_not_in_the_target_spec(tmp_path, caplog, condition, reason):
    ctx = load_ctx(tmp_path, [titem("t1", conditions=[condition], actions=[act("a1")])])
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        triggers.load(ctx)
    assert ctx.target.posts == [] and ctx.target_internal.posts == []
    assert reason in caplog.text
    assert ctx.summary.to_dict()["triggers"]["failed"] == 1


def test_load_fails_a_trigger_whose_add_question_is_not_in_the_target_spec(tmp_path, caplog):
    ctx = load_ctx(tmp_path, [titem("t1", actions=[act("a1", data={"question_id": "q9", "section_id": "s1"})])])
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        triggers.load(ctx)
    assert ctx.target.posts == []
    assert "q9" in caplog.text


def test_load_warns_about_an_option_the_question_does_not_have_and_still_creates(tmp_path, caplog):
    ctx = load_ctx(tmp_path, [titem("t1", conditions=[cond(options=("yes", "maybe"))])])
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        triggers.load(ctx)
    assert len(ctx.target.posts) == 1
    assert "'maybe'" in caplog.text


# References


def refs_ctx(tmp_path, **kwargs):
    track(tmp_path, "custom_fields", [("cf-s", "cf-t", "created")])
    track(tmp_path, "risk_scenarios", [("rs-s", "rs-t", "created")])
    track(tmp_path, "risk_types", [("rt-s", "rt-t", "created")])
    track(tmp_path, "users", [("u-s", "u-t", "matched")])
    track(tmp_path, "teams", [("team-s", "team-t", "created")])
    track(tmp_path, "controls", [("KITDRAFT", "KITDRAFT", "created")])
    target = {
        "risk_scenarios": [{"id": "rs-builtin"}],
        "risk_types": [{"id": "rt-builtin"}],
        "policy_control_bases": [
            {"id": "CREDOAI-014", "ownership_type": "ootb", "archived": False},
            {"id": "MINE", "ownership_type": "custom", "archived": False},
            {"id": "KITDRAFT", "ownership_type": "custom", "archived": False},
            {"id": "OLD", "ownership_type": "custom", "archived": True},
        ],
    }
    internal = {
        "policy_control_bases/MINE/versions": [
            {"id": "v1", "attributes": {"version": 1, "draft": False}},
            {"id": "v2", "attributes": {"version": 2, "draft": True}},
        ],
        "policy_control_bases/KITDRAFT/versions": [{"id": "v1", "attributes": {"version": 1, "draft": True}}],
        "policy_pack_bases": [
            {"id": "EU-HIGH", "attributes": {"ownership_type": "ootb", "archived": False}},
            {"id": "PACK", "attributes": {"ownership_type": "custom", "archived": False}},
        ],
        "policy_pack_bases/PACK/versions": [{"id": "p1", "attributes": {"version": 1, "draft": False}}],
        "questionnaire_bases": [
            {"id": "INTK1", "attributes": {"custom": True, "archived": False}},
            {"id": "PUBQ", "attributes": {"custom": True, "archived": False}},
            {"id": "CR-VENDOR", "attributes": {"custom": False, "archived": False}},
        ],
    }
    ctx = load_ctx(tmp_path, kwargs.pop("items", []), target=target, internal=internal, **kwargs)
    ctx.target.json_bodies["questionnaires/INTK1/versions"] = DRAFT
    ctx.target.json_bodies["questionnaires/PUBQ/versions"] = [
        {"version": 1, "draft": False},
        {"version": 2, "draft": True},
    ]
    return ctx


GOOD_ACTIONS = [
    act("a1", "add_question", {"question_id": "q3", "section_id": "s2"}),
    act("a2", "add_standalone_control", {"control_key": "CREDOAI-014", "risk_scenario_id": "rs-builtin"}),
    act("a3", "add_standalone_control", {"control_key": "MINE", "risk_scenario_id": "rs-s"}),
    act("a4", "add_visual_alert", {}, show_alert=True),
    act("a5", "change_risk_category", {"risk_category_level": 2, "risk_classification_id": "rc-source"}),
    act("a6", "add_policy_pack", {"policy_pack_key": "EU-HIGH"}),
    act("a7", "add_policy_pack", {"policy_pack_key": "PACK"}),
    act(
        "a8",
        "add_risk_scenario",
        {
            "risk_scenario_id": "rs-s",
            "inherent_likelihood": None,
            "inherent_impact": 3,
            "risk_type_ids": ["rt-s", "rt-builtin"],
        },
    ),
    act(
        "a9",
        "add_risk_scenario",
        {"risk_scenario_id": "rs-builtin", "inherent_likelihood": None, "inherent_impact": None, "risk_type_ids": None},
    ),
    act("a10", "set_custom_field", {"custom_field_id": "cf-s", "value": "x", "values": None}),
    act("a11", "add_triggered_review_task", {"type": "user", "reviewer_id": "u-s"}),
    act("a12", "add_triggered_review_task", {"type": "team", "reviewer_id": "team-s"}),
    act("a13", "add_questionnaire", {"questionnaire_key": "PUBQ"}),
    act("a14", "add_questionnaire", {"questionnaire_key": "CR-VENDOR"}),
]
GOOD_DATA = [
    {"question_id": "q3", "section_id": "s2"},
    {"control_key": "CREDOAI-014", "risk_scenario_id": "rs-builtin"},
    {"control_key": "MINE", "risk_scenario_id": "rs-t"},
    {},
    {"risk_category_level": 2},
    {"policy_pack_key": "EU-HIGH"},
    {"policy_pack_key": "PACK"},
    {
        "risk_scenario_id": "rs-t",
        "inherent_likelihood": None,
        "inherent_impact": 3,
        "risk_type_ids": ["rt-t", "rt-builtin"],
    },
    {"risk_scenario_id": "rs-builtin", "inherent_likelihood": None, "inherent_impact": None, "risk_type_ids": None},
    {"custom_field_id": "cf-t", "value": "x", "values": None},
    {"type": "user", "reviewer_id": "u-t"},
    {"type": "team", "reviewer_id": "team-t"},
    {"questionnaire_key": "PUBQ"},
    {"questionnaire_key": "CR-VENDOR"},
]


def test_resolve_trigger_remaps_every_action_type(tmp_path):
    ctx = refs_ctx(tmp_path)
    payload, actions, problems = triggers._References(ctx).resolve_trigger(titem("t1", actions=GOOD_ACTIONS))
    assert problems == []
    assert payload == {
        "description": "When yes",
        "match": "all",
        "questionnaire_version_id": "INTAKE1+1",
        "conditions": [cond()],
    }
    assert [src["id"] for src, _ in actions] == [a["id"] for a in GOOD_ACTIONS]
    assert [body["data"]["attributes"]["data"] for _, body in actions] == GOOD_DATA
    assert actions[3][1] == {
        "data": {
            "type": "resolve_action",
            "attributes": {"description": "Do a4", "type": "add_visual_alert", "data": {}, "show_alert": True},
        }
    }


@pytest.mark.parametrize(
    "action, reason",
    [
        (
            act("b1", "add_standalone_control", {"control_key": "NOPE", "risk_scenario_id": "rs-builtin"}),
            "control NOPE isn't on the target",
        ),
        (
            act("b2", "add_standalone_control", {"control_key": "KITDRAFT", "risk_scenario_id": "rs-builtin"}),
            "publish control KITDRAFT on the target, then re-run",
        ),
        (
            act("b3", "add_standalone_control", {"control_key": "OLD", "risk_scenario_id": "rs-builtin"}),
            "control OLD is archived",
        ),
        (
            act("b4", "add_standalone_control", {"control_key": "CREDOAI-014", "risk_scenario_id": "rs-gone"}),
            "risk scenario rs-gone",
        ),
        (act("b5", "add_policy_pack", {"policy_pack_key": "NOPACK"}), "policy pack NOPACK isn't on the target"),
        (
            act("b6", "add_questionnaire", {"questionnaire_key": "INTK1"}),
            "publish questionnaire INTK1 on the target, then re-run",
        ),
        (act("b7", "add_questionnaire", {"questionnaire_key": "NOQ"}), "questionnaire NOQ isn't on the target"),
        (act("b8", "set_custom_field", {"custom_field_id": "cf-gone", "value": "x"}), "custom field cf-gone"),
        (
            act("b9", "add_risk_scenario", {"risk_scenario_id": "rs-builtin", "risk_type_ids": ["rt-gone"]}),
            "risk type rt-gone",
        ),
        (act("b10", "add_triggered_review_task", {"type": "user", "reviewer_id": "u-gone"}), "user u-gone"),
        (act("b11", "add_triggered_review_task", {"type": "team", "reviewer_id": "u-s"}), "team u-s"),
        (act("b12", "launch_rocket", {}), "the kit doesn't know action type 'launch_rocket'"),
    ],
)
def test_resolve_trigger_reports_unresolvable_references(tmp_path, action, reason):
    ctx = refs_ctx(tmp_path)
    _payload, _actions, problems = triggers._References(ctx).resolve_trigger(titem("t1", actions=[action]))
    assert len(problems) == 1 and reason in problems[0]


def test_load_logs_every_problem_of_a_trigger_together_and_creates_nothing(tmp_path, caplog):
    bad = [
        act("b1", "add_policy_pack", {"policy_pack_key": "NOPACK"}),
        act("b2", "add_questionnaire", {"questionnaire_key": "INTK1"}),
        act("b3", "launch_rocket", {}),
    ]
    ctx = refs_ctx(tmp_path, items=[titem("t1", conditions=[cond(q="q9")], actions=GOOD_ACTIONS[:2] + bad)])
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        triggers.load(ctx)
    assert ctx.target.posts == [] and ctx.target_internal.posts == []
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    for reason in ("q9", "NOPACK", "publish questionnaire INTK1", "launch_rocket"):
        assert reason in errors[0]
    assert ctx.summary.to_dict()["triggers"]["failed"] == 1


def test_target_reads_are_cached(tmp_path):
    ctx = refs_ctx(tmp_path)
    refs = triggers._References(ctx)
    for tid in ("t1", "t2"):
        refs.resolve_trigger(titem(tid, actions=GOOD_ACTIONS))
    paths = [p for p, _ in ctx.target.gets + ctx.target_internal.gets]
    assert len(paths) == len(set(paths))


# Creating


def test_load_creates_triggers_then_their_actions_in_order(tmp_path):
    ctx = load_ctx(tmp_path, [titem("t1", actions=[act("a1"), act("a2", "add_visual_alert", {}, show_alert=True)])])
    triggers.load(ctx)
    assert ctx.target.posts == [
        (
            "triggers",
            {
                "description": "When yes",
                "match": "all",
                "questionnaire_version_id": "INTAKE1+1",
                "conditions": [cond()],
            },
        )
    ]
    assert action_posts(ctx) == [
        (
            "resolve_triggers/new-1/actions",
            {
                "data": {
                    "type": "resolve_action",
                    "attributes": {
                        "description": "Do a1",
                        "type": "add_question",
                        "data": {"question_id": "q2", "section_id": "s1"},
                        "show_alert": False,
                    },
                }
            },
        ),
        (
            "resolve_triggers/new-1/actions",
            {
                "data": {
                    "type": "resolve_action",
                    "attributes": {"description": "Do a2", "type": "add_visual_alert", "data": {}, "show_alert": True},
                }
            },
        ),
    ]
    assert [(r["source_id"], r["target_id"], r["origin"]) for r in ctx.tracker("triggers").records] == [
        ("t1", "new-1", "created")
    ]
    assert [(r["source_id"], r["target_id"], r["origin"]) for r in ctx.tracker("trigger_actions").records] == [
        ("a1", "new-1", "created"),
        ("a2", "new-2", "created"),
    ]
    assert (tmp_path / "loaded_triggers.json").exists() and (tmp_path / "loaded_trigger_actions.json").exists()
    assert ctx.summary.to_dict() == {
        "triggers": {"created": 1, "matched": 0, "skipped": 0, "failed": 0},
        "trigger_actions": {"created": 2, "matched": 0, "skipped": 0, "failed": 0},
    }


def test_load_logs_server_warnings_on_a_new_trigger(tmp_path, caplog):
    ctx = load_ctx(tmp_path, [titem("t1")])
    ctx.target.post_responses["triggers"] = FakeResponse(201, {"id": "T1", "warnings": ["option not found"]})
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        triggers.load(ctx)
    assert "option not found" in caplog.text


def test_second_run_skips_everything(tmp_path):
    items = [titem("t1", actions=[act("a1"), act("a2", "add_visual_alert", {})])]
    ctx = load_ctx(tmp_path, items)
    triggers.load(ctx)
    again = load_ctx(
        tmp_path, items, target_triggers=ctx.target.collections["triggers"], internal=ctx.target_internal.collections
    )
    triggers.load(again)
    assert again.target.posts == [] and again.target_internal.posts == []
    assert again.summary.to_dict() == {
        "triggers": {"created": 0, "matched": 0, "skipped": 1, "failed": 0},
        "trigger_actions": {"created": 0, "matched": 0, "skipped": 2, "failed": 0},
    }


def test_an_interrupted_run_finishes_a_tracked_trigger(tmp_path):
    track(tmp_path, "triggers", [("t1", "T1", "created")])
    track(tmp_path, "trigger_actions", [("a1", "A1", "created")])
    item = titem(
        "t1",
        actions=[act("a1"), act("a2", "add_visual_alert", {}), act("a3", "add_visual_alert", {}, description="Third")],
    )
    # a2 was created but its response was lost, so it isn't tracked
    ctx = load_ctx(
        tmp_path,
        [item],
        target_triggers=[target_trigger("T1")],
        internal={
            "resolve_triggers/T1/actions": [
                target_action("A1"),
                target_action("A2", "add_visual_alert", {}, description="Do a2"),
            ]
        },
    )
    triggers.load(ctx)
    assert ctx.target.posts == []
    assert [p for p, _ in action_posts(ctx)] == ["resolve_triggers/T1/actions"]
    assert action_posts(ctx)[0][1]["data"]["attributes"]["description"] == "Third"
    records = {r["source_id"]: (r["target_id"], r["origin"]) for r in ctx.tracker("trigger_actions").records}
    assert records["a2"] == ("A2", "matched") and records["a3"][1] == "created"


def test_an_untracked_trigger_matching_one_on_the_target_is_matched_without_actions(tmp_path, caplog):
    # Options are compared as sets
    existing = target_trigger("T9", conditions=[cond(q="q3", s="s2", options=("b", "a"))])
    ctx = load_ctx(
        tmp_path,
        [titem("t1", conditions=[cond(q="q3", s="s2", options=("a", "b"))], actions=[act("a1")])],
        target_triggers=[existing],
        internal={"resolve_triggers/T9/actions": [target_action("X1", "add_visual_alert", {})]},
    )
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        triggers.load(ctx)
    assert ctx.target.posts == [] and ctx.target_internal.posts == []
    assert ctx.tracker("triggers").find("t1")["target_id"] == "T9"
    assert ctx.tracker("triggers").find("t1")["origin"] == "matched"
    assert ctx.tracker("trigger_actions").records == []
    assert "T9" in caplog.text
    assert ctx.summary.attention == []


def test_a_trigger_tracked_as_matched_never_gets_actions(tmp_path):
    track(tmp_path, "triggers", [("t1", "T9", "matched")])
    ctx = load_ctx(
        tmp_path,
        [titem("t1", actions=[act("a1")])],
        target_triggers=[target_trigger("T9")],
        internal={"resolve_triggers/T9/actions": []},
    )
    triggers.load(ctx)
    assert ctx.target_internal.posts == []
    assert ctx.summary.to_dict()["triggers"]["skipped"] == 1


def test_a_trigger_on_another_version_or_with_another_description_is_not_matched(tmp_path):
    ctx = load_ctx(
        tmp_path,
        [titem("t1")],
        target_triggers=[target_trigger("T8", version=2), target_trigger("T9", description="Other")],
    )
    triggers.load(ctx)
    assert len(ctx.target.posts) == 1


def test_identical_source_triggers_and_actions_map_to_the_first(tmp_path, caplog):
    items = [
        titem("t1", actions=[act("a1"), act("a2", description="Do a1")]),
        titem("t2", actions=[act("a3", description="Do a1"), act("a4", "add_visual_alert", {})]),
    ]
    ctx = load_ctx(tmp_path, items)
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        triggers.load(ctx)
    assert len(ctx.target.posts) == 1
    assert [b["data"]["attributes"]["description"] for _, b in action_posts(ctx)] == ["Do a1", "Do a4"]
    assert ctx.tracker("triggers").find("t2")["target_id"] == "new-1"
    actions = {r["source_id"]: (r["target_id"], r["origin"]) for r in ctx.tracker("trigger_actions").records}
    assert actions["a2"] == ("new-1", "matched") and actions["a3"] == ("new-1", "matched")
    assert "t1" in caplog.text and "a1" in caplog.text
    assert ctx.summary.to_dict()["triggers"] == {"created": 1, "matched": 1, "skipped": 0, "failed": 0}
    assert ctx.summary.to_dict()["trigger_actions"] == {"created": 2, "matched": 2, "skipped": 0, "failed": 0}


def test_a_failed_trigger_create_skips_its_actions(tmp_path, caplog):
    ctx = load_ctx(tmp_path, [titem("t1", actions=[act("a1"), act("a2", "add_visual_alert", {})])])
    ctx.target.post_responses["triggers"] = FakeResponse(422, {"errors": [{"detail": "bad"}]})
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        triggers.load(ctx)
    assert ctx.target_internal.posts == []
    assert ctx.summary.to_dict()["triggers"]["failed"] == 1
    assert "2 actions" in caplog.text
    assert ctx.tracker("triggers").records == []


def test_a_failed_action_flags_its_trigger(tmp_path):

    class FailSecond(Recording):
        def post(self, path, json):
            if json["data"]["attributes"]["description"] == "Do a2":
                self.posts.append((path, json))
                return FakeResponse(422, {"errors": [{"detail": "not found in catalog"}]})
            return super().post(path, json)

    ctx = load_ctx(
        tmp_path,
        [
            titem(
                "t1",
                actions=[
                    act("a1"),
                    act("a2", "add_visual_alert", {}),
                    act("a3", "add_visual_alert", {}, description="Third"),
                ],
            )
        ],
    )
    ctx.target_internal = FailSecond(
        ctx.target_internal.collections, jsonapi=True, json_bodies=ctx.target_internal.json_bodies
    )
    triggers.load(ctx)
    assert len(ctx.target_internal.posts) == 3
    assert ctx.tracker("triggers").find("t1")["origin"] == "created"
    assert ctx.tracker("trigger_actions").find("a2") is None
    assert ctx.summary.to_dict()["trigger_actions"] == {"created": 2, "matched": 0, "skipped": 0, "failed": 1}
    assert len(ctx.summary.attention) == 1
    assert ctx.summary.attention[0]["entity"] == "triggers"
    assert "When yes" in ctx.summary.attention[0]["message"] and "re-run" in ctx.summary.attention[0]["message"]


def test_a_tracked_trigger_deleted_on_the_target_fails_its_remaining_actions(tmp_path, caplog):
    track(tmp_path, "triggers", [("t1", "T1", "created")])
    track(tmp_path, "trigger_actions", [("a1", "A1", "created")])
    ctx = load_ctx(
        tmp_path,
        [titem("t1", actions=[act("a1"), act("a2", "add_visual_alert", {})])],
        internal={"resolve_triggers/T1/actions": []},
    )
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        triggers.load(ctx)
    assert ctx.target.posts == [] and ctx.target_internal.posts == []
    assert "remove its row from loaded_triggers.json" in caplog.text
    assert ctx.summary.to_dict()["trigger_actions"]["failed"] == 1
    assert len(ctx.summary.attention) == 1
    assert "loaded_triggers.json" in ctx.summary.attention[0]["message"]


def test_dry_run_writes_nothing_and_counts_like_a_live_run(tmp_path):
    items = [
        titem("t1", actions=[act("a1"), act("a2", description="Do a1")]),
        titem("t2", actions=[act("a3", description="Do a1")]),
        titem("t3", description="Other", actions=[act("a4", "add_questionnaire", {"questionnaire_key": "NOQ"})]),
    ]
    (tmp_path / "dry").mkdir()
    dry = load_ctx(tmp_path / "dry", items, dry_run=True)
    triggers.load(dry)
    assert dry.target.posts == [] and dry.target_internal.posts == []
    assert not (tmp_path / "dry" / "loaded_triggers.json").exists()
    assert dry.tracker("triggers").find("t1")["target_id"] == "dry:t1"
    assert not any(p.startswith("resolve_triggers/dry:") for p, _ in dry.target_internal.gets)
    (tmp_path / "live").mkdir()
    live = load_ctx(tmp_path / "live", items)
    triggers.load(live)
    assert dry.summary.to_dict() == live.summary.to_dict()


def test_dry_run_reads_the_spec_from_the_extract_for_a_questionnaire_created_in_the_same_run(tmp_path):
    ctx = load_ctx(tmp_path, [titem("t1", actions=[act("a1")])], questionnaires=(), dry_run=True)
    save_extract(tmp_path, "questionnaires", [{"key": "INTAKE1", "version": 3, "sections": SPEC["sections"]}])
    ctx.tracker("questionnaires").record("INTAKE1", "dry:INTAKE1", "INTAKE1", "created")
    triggers.load(ctx)
    assert ctx.summary.to_dict()["triggers"]["created"] == 1
    assert ctx.target.posts == []
    assert not any("INTAKE1" in p for p, _ in ctx.target.gets)


def test_publish_reminder_after_a_live_load_that_created_something(tmp_path, caplog):
    ctx = load_ctx(tmp_path, [titem("t1")])
    with caplog.at_level(logging.INFO, logger="kit.load"):
        triggers.load(ctx)
    assert "can be published once reviewed" in caplog.text and "INTAKE1" in caplog.text
    caplog.clear()
    again = load_ctx(tmp_path, [titem("t1")], target_triggers=ctx.target.collections["triggers"])
    with caplog.at_level(logging.INFO, logger="kit.load"):
        triggers.load(again)
    assert "can be published" not in caplog.text


def test_publish_reminder_leaves_out_a_questionnaire_with_a_trigger_still_to_load(tmp_path, caplog):
    items = [
        titem("t1"),
        titem("t2", description="Other", actions=[act("a1", "add_questionnaire", {"questionnaire_key": "NOQ"})]),
        titem("t3", key="INTAKE2"),
    ]
    ctx = load_ctx(tmp_path, items, questionnaires=(("INTAKE1", "created"), ("INTAKE2", "created")))
    with caplog.at_level(logging.INFO, logger="kit.load"):
        triggers.load(ctx)
    ready = next(r.getMessage() for r in caplog.records if "can be published once reviewed" in r.getMessage())
    assert "INTAKE2" in ready and "INTAKE1" not in ready
    waiting = next(r.getMessage() for r in caplog.records if "don't publish" in r.getMessage())
    assert "INTAKE1" in waiting and "INTAKE2" not in waiting


def test_a_trigger_created_in_an_earlier_run_is_ours_for_an_identical_source_trigger(tmp_path):
    # Run 1 created t1 as T1 and stopped before t2, an identical trigger with other actions
    track(tmp_path, "triggers", [("t1", "T1", "created")])
    track(tmp_path, "trigger_actions", [("a1", "A1", "created")])
    items = [
        titem("t1", actions=[act("a1")]),
        titem("t2", actions=[act("a1b", description="Do a1"), act("a2", "add_visual_alert", {})]),
    ]
    ctx = load_ctx(
        tmp_path,
        items,
        target_triggers=[target_trigger("T1")],
        internal={"resolve_triggers/T1/actions": [target_action("A1", description="Do a1")]},
    )
    triggers.load(ctx)
    assert ctx.target.posts == []
    assert [(p, b["data"]["attributes"]["description"]) for p, b in action_posts(ctx)] == [
        ("resolve_triggers/T1/actions", "Do a2")
    ]
    record = ctx.tracker("triggers").find("t2")
    assert (record["target_id"], record["origin"]) == ("T1", "created")
    acts = {r["source_id"]: (r["target_id"], r["origin"]) for r in ctx.tracker("trigger_actions").records}
    assert acts["a1b"] == ("A1", "matched") and acts["a2"][1] == "created"
    assert ctx.summary.to_dict()["triggers"] == {"created": 0, "matched": 1, "skipped": 1, "failed": 0}


def test_a_trigger_created_but_never_tracked_is_reported_on_the_next_run(tmp_path, caplog):

    class LostResponse(Recording):
        def post(self, path, json):
            super().post(path, json)  # reaches the server
            raise APIError("POST triggers failed: connection reset")

    items = [titem("t1", actions=[act("a1"), act("a2", "add_visual_alert", {})])]
    ctx = load_ctx(tmp_path, items)
    ctx.target = LostResponse(ctx.target.collections, json_bodies=ctx.target.json_bodies)
    triggers.load(ctx)
    assert ctx.summary.to_dict()["triggers"]["failed"] == 1
    assert ctx.target_internal.posts == []
    again = load_ctx(
        tmp_path,
        items,
        target_triggers=ctx.target.collections["triggers"],
        internal={"resolve_triggers/new-1/actions": []},
    )
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        triggers.load(again)
    assert again.target.posts == [] and again.target_internal.posts == []
    logged = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert len(logged) == 1 and logged[0][0] == logging.ERROR
    message = logged[0][1]
    assert "target trigger new-1 matches source trigger" in message and "has no actions" in message
    assert "you didn't make it yourself" in message and "the kit will recreate it with its actions" in message
    assert "loaded_triggers.json" not in message
    assert again.tracker("triggers").find("t1") is None  # repeats each run until it's deleted
    assert len(again.summary.attention) == 1 and "new-1" in again.summary.attention[0]["message"]
    # Once deleted on the target, the next run recreates it cleanly
    third = load_ctx(tmp_path, items)
    triggers.load(third)
    assert len(third.target.posts) == 1 and len(action_posts(third)) == 2
    assert third.summary.attention == []


def test_identical_source_triggers_matching_an_empty_foreign_trigger_are_reported_once(tmp_path, caplog):
    items = [titem("t1", actions=[act("a1")]), titem("t2", actions=[act("a2", "add_visual_alert", {})])]
    ctx = load_ctx(
        tmp_path, items, target_triggers=[target_trigger("T9")], internal={"resolve_triggers/T9/actions": []}
    )
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        triggers.load(ctx)
    assert ctx.target_internal.posts == []
    assert len([r for r in caplog.records if r.levelno >= logging.WARNING]) == 1
    assert len(ctx.summary.attention) == 1
    assert ctx.tracker("triggers").records == []
    assert [p for p, _ in ctx.target_internal.gets].count("resolve_triggers/T9/actions") == 1


def test_a_source_trigger_is_not_recorded_when_the_matched_triggers_actions_cannot_be_read(tmp_path, caplog):
    ctx = load_ctx(tmp_path, [titem("t1", actions=[act("a1")])], target_triggers=[target_trigger("T9")])
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        triggers.load(ctx)
    assert ctx.target_internal.posts == []
    assert ctx.tracker("triggers").records == []
    assert "T9" in caplog.text


@pytest.mark.parametrize("path, what", [("policy_control_bases", "controls"), ("triggers", "triggers")])
def test_a_failed_target_list_stops_the_load_with_a_clear_message(tmp_path, caplog, path, what):

    class FailingList(Recording):
        def get_all(self, p, params=None):
            if p == path:
                raise APIError(f"GET {p} failed: 503", 503)
            return super().get_all(p, params)

    item = titem("t1", actions=[act("a1", "add_standalone_control", {"control_key": "MINE"})])
    ctx = load_ctx(tmp_path, [item])
    ctx.target = FailingList(ctx.target.collections, json_bodies=ctx.target.json_bodies)
    with caplog.at_level(logging.ERROR, logger="kit.load"), pytest.raises(LoadStopped):
        triggers.load(ctx)
    assert f"couldn't list {what} on the target" in caplog.text and "503" in caplog.text
    assert "re-run when the target responds" in caplog.text
    assert ctx.target.posts == [] and ctx.target_internal.posts == []


def test_a_tracked_trigger_on_an_older_version_fails_its_untracked_actions(tmp_path, caplog):
    track(tmp_path, "triggers", [("t1", "T1", "created")])
    track(tmp_path, "trigger_actions", [("a1", "A1", "created")])
    ctx = load_ctx(
        tmp_path,
        [titem("t1", actions=[act("a1"), act("a2", "add_visual_alert", {})])],
        versions={"INTAKE1": [{"version": 1, "draft": False}, {"version": 2, "draft": True}]},
        target_triggers=[target_trigger("T1", version=1)],
        internal={"resolve_triggers/T1/actions": [target_action("A1")]},
    )
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        triggers.load(ctx)
    assert ctx.target.posts == [] and ctx.target_internal.posts == []
    assert (
        "is on INTAKE1+1, but the latest target version of INTAKE1 is INTAKE1+2; its actions can't be "
        "added to a published version. Delete target trigger T1 and remove its row from loaded_triggers.json, "
        "then re-run to recreate it on the new draft."
    ) in caplog.text
    assert ctx.summary.to_dict()["trigger_actions"] == {"created": 0, "matched": 0, "skipped": 1, "failed": 1}
    assert len(ctx.summary.attention) == 1


def test_a_tracked_trigger_with_problems_is_flagged(tmp_path):
    track(tmp_path, "triggers", [("t1", "T1", "created")])
    ctx = load_ctx(
        tmp_path,
        [titem("t1", actions=[act("a1", "add_questionnaire", {"questionnaire_key": "NOQ"})])],
        target_triggers=[target_trigger("T1")],
        internal={"resolve_triggers/T1/actions": []},
    )
    triggers.load(ctx)
    assert ctx.target_internal.posts == []
    assert ctx.summary.to_dict()["trigger_actions"]["failed"] == 1
    assert len(ctx.summary.attention) == 1
    message = ctx.summary.attention[0]["message"]
    assert "When yes" in message and "re-run" in message


def test_an_interrupted_run_matches_realistic_action_read_backs(tmp_path):
    # The server adds risk_classification_id, returns keys in its own order and nulls for unset fields
    track(tmp_path, "triggers", [("t1", "T1", "created")])
    item = titem(
        "t1",
        actions=[
            act("a5", "change_risk_category", {"risk_category_level": 2, "risk_classification_id": "rc-source"}),
            act(
                "a8",
                "add_risk_scenario",
                {"risk_scenario_id": "rs-builtin", "inherent_likelihood": None, "inherent_impact": 3},
            ),
        ],
    )
    read_back = [
        target_action(
            "A5",
            "change_risk_category",
            {"risk_classification_id": "medium", "risk_category_level": 2},
            description="Do a5",
        ),
        target_action(
            "A8",
            "add_risk_scenario",
            {
                "risk_type_ids": None,
                "inherent_impact": 3,
                "inherent_likelihood": None,
                "risk_scenario_id": "rs-builtin",
            },
            description="Do a8",
        ),
    ]
    ctx = load_ctx(
        tmp_path,
        [item],
        target={"risk_scenarios": [{"id": "rs-builtin"}]},
        target_triggers=[target_trigger("T1")],
        internal={"resolve_triggers/T1/actions": read_back},
    )
    triggers.load(ctx)
    assert ctx.target_internal.posts == []
    acts = {r["source_id"]: (r["target_id"], r["origin"]) for r in ctx.tracker("trigger_actions").records}
    assert acts == {"a5": ("A5", "matched"), "a8": ("A8", "matched")}


def test_dry_run_counts_like_a_live_run_when_finishing_an_interrupted_run(tmp_path):
    item = titem("t1", actions=[act("a1"), act("a2", "add_visual_alert", {})])
    summaries = []
    for name, dry_run in (("dry", True), ("live", False)):
        (tmp_path / name).mkdir()
        track(tmp_path / name, "triggers", [("t1", "T1", "created")])
        track(tmp_path / name, "trigger_actions", [("a1", "A1", "created")])
        ctx = load_ctx(
            tmp_path / name,
            [item],
            target_triggers=[target_trigger("T1")],
            dry_run=dry_run,
            internal={"resolve_triggers/T1/actions": [target_action("A1")]},
        )
        triggers.load(ctx)
        summaries.append(ctx.summary.to_dict())
    assert summaries[0] == summaries[1]
    assert summaries[1]["trigger_actions"] == {"created": 1, "matched": 0, "skipped": 1, "failed": 0}
