import json

import pytest

import migrate
from kit.config import ConfigError
from kit.entities import ENTITIES
from kit.tracking import load_extract, save_extract
from tests.fakes import FakeResponse, FakeTenant, make_ctx


@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    # Keep logs out of the real migration_kit/logs folder
    monkeypatch.setattr(migrate, "KIT_DIR", tmp_path)
    return tmp_path


def factory(ctx, seen=None):
    def build(live):
        if seen is not None:
            seen.append(live)
        return ctx, []

    return build


LOCAL_USERS_OFF = [{"id": "LOCAL_USERS", "attributes": {"value": False}}]
LOCAL_USERS_ON = [{"id": "LOCAL_USERS", "attributes": {"value": True}}]


def phase4_extracts(tmp):
    """One vendor, one user and one team (with that user as owner) as the extract step would write them."""
    save_extract(
        tmp,
        "vendors",
        [
            {
                "id": "sv1",
                "name": "Acme AI",
                "description": "A vendor",
                "questionnaire_keys": ["CR-VENDOR"],
                "custom_fields": [],
            }
        ],
    )
    save_extract(
        tmp,
        "users",
        [{"id": "su1", "email": "ana@credo.ai", "given_name": "Ana", "family_name": "Lee", "role": "admin"}],
    )
    save_extract(
        tmp,
        "teams",
        [
            {
                "id": "st1",
                "name": "Governance",
                "description": "Gov team",
                "members": [{"email": "ana@credo.ai", "role": "owner"}],
            }
        ],
    )


def trigger_extracts(tmp):
    """One trigger with one action on INTAKE, a questionnaire an earlier run created on the target (so
    loaded_questionnaires.json exists, which the triggers load needs)."""
    (tmp / "loaded_questionnaires.json").write_text(
        json.dumps([{"source_id": "INTAKE", "target_id": "INTAKE", "name_or_key": "INTAKE", "origin": "created"}])
    )
    save_extract(
        tmp,
        "triggers",
        [
            {
                "id": "sT1",
                "description": "When yes",
                "match": "all",
                "questionnaire_key": "INTAKE",
                "source_version": 1,
                "conditions": [
                    {
                        "type": "questionnaire_evidence",
                        "data": {"options": ["yes"], "question_id": "q1", "section_id": "s1"},
                    }
                ],
                "actions": [
                    {
                        "id": "sA1",
                        "description": "Ask more",
                        "type": "add_question",
                        "data": {"question_id": "q2", "section_id": "s1"},
                        "show_alert": False,
                    }
                ],
            }
        ],
    )


def phase6_extracts(tmp):
    """One model, on a source the target doesn't have, linked to the phase4_extracts vendor; and one custom domain."""
    save_extract(
        tmp,
        "models",
        [
            {
                "id": "sm1",
                "name": "Vision Model",
                "summary": "Labels images",
                "source": "Example Source",
                "status": "none",
                "questionnaire_keys": ["CR-MODEL"],
                "custom_fields": [],
                "vendor_ids": ["sv1"],
                "owner_email": "ana@credo.ai",
            }
        ],
    )
    save_extract(tmp, "domains", [{"id": "store_ops", "name": "Store Operations", "description": "Shelves"}])


def phase7_extracts(tmp):
    """One use case owned by the phase4_extracts user, and one answer on the phase6_extracts model."""
    save_extract(
        tmp,
        "use_cases",
        [
            {
                "id": "suc1",
                "name": "Chatbot",
                "description": "Answers questions",
                "owner": {"email": "ana@credo.ai"},
                "stakeholders": [],
                "custom_fields": [],
                "stage_name": None,
                "step": None,
                "risk_classification_level": None,
                "policy_packs": [],
                "risk_scenarios": [],
                "controls": [],
                "domain_ids": [],
                "model_ids": [],
                "vendor_ids": [],
                "model_names": {},
                "vendor_names": {},
                "attachments": [],
                "answers": [],
                "file_answers": 0,
            }
        ],
    )
    save_extract(
        tmp,
        "model_answers",
        [
            {
                "id": "sm1",
                "name": "Vision Model",
                "attachments": [{"key": "CR-MODEL", "version": 1}],
                "answers": [
                    {
                        "key": "CR-MODEL",
                        "version": 1,
                        "section_title": None,
                        "question_id": "q2",
                        "question_text": "Which AI?",
                        "evidence_type": "text",
                        "multiple": False,
                        "value": "Vision",
                    }
                ],
                "file_answers": 0,
            }
        ],
    )


INTAKE_SPEC = {
    "sections": [
        {
            "id": "s1",
            "questions": [
                {"id": "q1", "question": "Uses AI?", "evidence_type": "select", "select_options": ["yes", "no"]},
                {"id": "q2", "question": "Which AI?", "evidence_type": "text"},
            ],
        }
    ]
}


SCORING_CONFIG = {"data": {"attributes": {"formula_config": {"weights": {}}}}}


def all_entities_target(local_users=LOCAL_USERS_OFF):
    """Target fakes for `load all`: empty for the earlier entities, and what vendors, users, teams, triggers, models
    and domains read.
    The internal fake creates the user as new-1 and the team as new-2, so new-2's memberships show the user as owner.
    Both fakes report v3 triggers, INTAKE has one draft version with INTAKE_SPEC, and CR-VENDOR and CR-MODEL are
    published."""
    target = FakeTenant(
        {
            "risk_types": [],
            "custom_fields": [],
            "policy_control_bases": [],
            "risk_scenarios": [],
            "vendors": [],
            "triggers": [],
            "models": [],
        },
        json_bodies={
            "questionnaires/CR-VENDOR/versions": [{"version": 1, "draft": False}],
            "questionnaires/CR-MODEL/versions": [{"version": 1, "draft": False}],
            "questionnaires/INTAKE/versions": [{"version": 1, "draft": True}],
            "questionnaires/INTAKE/spec": INTAKE_SPEC,
            "questionnaires/CR-MODEL/spec": INTAKE_SPEC,
        },
    )
    target_internal = FakeTenant(
        {
            "entity_types": [],
            "policy_pack_bases": [],
            "questionnaire_bases": [],
            "users": [],
            "teams": [],
            "roles": [{"id": "admin"}, {"id": "user"}],
            "entitlements": local_users,
            "sources": [],
            "domains": [],
        },
        jsonapi=True,
        # The use case create answers with its entity_id, as the real one does
        post_responses={"use_cases": FakeResponse(201, {"data": {"id": "uc-t1", "attributes": {"entity_id": "e-uc"}}})},
        json_bodies={
            "resolve_triggers": {"data": []},
            "risk_scoring_config": SCORING_CONFIG,
            "teams/new-2": {
                "data": {"id": "new-2", "relationships": {"memberships": {"data": [{"id": "m1"}]}}},
                "included": [{"type": "team_memberships", "attributes": {"member_id": "new-1", "role": "owner"}}],
            },
            # The model is the third Integration create (after the vendor and the trigger); the API key's user owns it
            "models/new-3": {"data": {"id": "new-3", "attributes": {"entity_id": "e-model", "user_id": "api-user"}}},
            "use_cases/uc-t1": {
                "data": {
                    "id": "uc-t1",
                    "attributes": {
                        "workflow_stage_id": None,
                        "domain_ids": [],
                        "trigger_mode": "hidden_questions_only",
                    },
                }
            },
        },
    )
    target_internal.collections["entities/e-model/stakeholders"] = [
        {"id": "sh1", "attributes": {"user_id": "api-user", "team_id": None, "role": "owner"}}
    ]
    target_internal.collections["models/new-3/questionnaire_sections"] = [
        {"id": "es1", "attributes": {"section_id": "s1"}}
    ]
    return target, target_internal


def test_load_is_a_dry_run_without_live(in_tmp):
    seen = []
    save_extract(in_tmp, "risk_types", [])
    assert migrate.main(["load", "risk_types"], context_factory=factory(make_ctx(in_tmp, dry_run=True), seen)) == 0
    assert seen == [False]


def test_live_flag_is_passed_through(in_tmp):
    seen = []
    save_extract(in_tmp, "risk_types", [])
    migrate.main(
        ["load", "risk_types", "--live", "--target", "target-tenant"], context_factory=factory(make_ctx(in_tmp), seen)
    )
    assert seen == [True]


def test_allow_invites_reaches_the_context(in_tmp):
    save_extract(in_tmp, "risk_types", [])
    ctx = make_ctx(in_tmp)
    migrate.main(
        ["load", "risk_types", "--live", "--target", "target-tenant", "--allow-invites"], context_factory=factory(ctx)
    )
    assert ctx.allow_invites is True
    ctx = make_ctx(in_tmp)
    migrate.main(["load", "risk_types", "--live", "--target", "target-tenant"], context_factory=factory(ctx))
    assert ctx.allow_invites is False


def test_raise_roles_reaches_the_context(in_tmp):
    save_extract(in_tmp, "risk_types", [])
    ctx = make_ctx(in_tmp)
    migrate.main(["load", "risk_types", "--raise-roles"], context_factory=factory(ctx))
    assert ctx.raise_roles is True
    ctx = make_ctx(in_tmp)
    migrate.main(["load", "risk_types"], context_factory=factory(ctx))
    assert ctx.raise_roles is False


def test_help_is_short_and_points_to_the_readme(capsys):
    with pytest.raises(SystemExit):
        migrate.parse_args(["--help"])
    out = capsys.readouterr().out
    assert "README.md" in out
    assert "--live --target <tenant>" in out


def test_live_without_target_is_rejected(in_tmp):
    with pytest.raises(SystemExit):
        migrate.main(["load", "risk_types", "--live"], context_factory=factory(make_ctx(in_tmp)))


def test_live_with_wrong_target_is_refused(in_tmp):
    ctx = make_ctx(in_tmp)
    save_extract(in_tmp, "risk_types", [{"id": "r1", "name": "Model Risk"}])
    assert migrate.main(["load", "risk_types", "--live", "--target", "other-tenant"], context_factory=factory(ctx)) == 2
    assert ctx.target.posts == []
    assert not (in_tmp / "run_meta.json").exists()


def test_target_is_checked_on_a_dry_run_too(in_tmp):
    save_extract(in_tmp, "risk_types", [])
    ctx = make_ctx(in_tmp, dry_run=True)
    assert migrate.main(["load", "risk_types", "--target", "other-tenant"], context_factory=factory(ctx)) == 2


def test_load_exits_1_and_saves_summary_when_an_item_fails(in_tmp):
    target = FakeTenant(post_response=FakeResponse(500, {"detail": "boom"}))
    save_extract(in_tmp, "risk_types", [{"id": "r1", "name": "Model Risk", "custom": True}])
    assert (
        migrate.main(
            ["load", "risk_types", "--live", "--target", "target-tenant"],
            context_factory=factory(make_ctx(in_tmp, target=target)),
        )
        == 1
    )
    assert len(list((in_tmp / "logs").glob("*_summary.json"))) == 1


def test_missing_extract_file_exits_2(in_tmp):
    assert migrate.main(["load", "risk_types"], context_factory=factory(make_ctx(in_tmp, dry_run=True))) == 2


def test_config_error_exits_2(in_tmp):
    def broken(live):
        raise ConfigError("Missing required settings: TARGET_API_KEY")

    assert migrate.main(["check"], context_factory=broken) == 2


@pytest.mark.parametrize("v3", [True, False], ids=["v3", "v2-source"])
def test_extract_all_writes_every_entity_file(in_tmp, monkeypatch, v3):
    # A v2 source can't give triggers, but nothing later depends on them: every later entity is still extracted
    from kit.clients.base import APIError

    monkeypatch.delenv("USER_EMAIL_DOMAINS", raising=False)
    monkeypatch.delenv("SKIP_USER_EMAIL_DOMAINS", raising=False)
    source = FakeTenant(
        {
            "risk_types": [],
            "custom_fields": [],
            "vendors": [
                {"id": "v1", "name": "Acme AI", "description": "A vendor", "status": "none"},
                {"id": "v2", "name": "OpenAI", "description": None, "status": "none"},
            ],
            # On a built-in questionnaire, which the questionnaires extract leaves out, so the trigger is left out too
            "triggers": [
                {
                    "id": "T1",
                    "description": "When yes",
                    "match": "all",
                    "questionnaire_version_id": "CR-MODEL+1",
                    "conditions": [],
                    "archived": False,
                }
            ],
            "models": [
                {
                    "id": "m1",
                    "name": "Vision Model",
                    "summary": "Labels images",
                    "source": "Example Source",
                    "status": "none",
                    "custom": True,
                    "questionnaire_ids": ["CR-MODEL+1"],
                },
                {"id": "m2", "name": "GPT-4", "custom": False},
            ],
            "models/m1/vendors": [{"id": "v1"}],
        },
        dry_run=True,
    )
    source_internal = FakeTenant(
        {
            "vendors": [
                {"id": "v1", "attributes": {"custom": True, "questionnaire_ids": ["CR-VENDOR+1"]}},
                {"id": "v2", "attributes": {"custom": False}},
            ],
            "vendors/v1/custom_fields": [],
            "users": [
                {
                    "id": "u1",
                    "attributes": {
                        "email": "ana@credo.ai",
                        "given_name": "Ana",
                        "family_name": "Lee",
                        "role": "admin",
                        "deleted_at": None,
                    },
                }
            ],
            "teams": [{"id": "t1", "attributes": {"name": "Governance", "description": "Gov team"}}],
            "models": [{"id": "m1", "attributes": {"user_id": "u1"}}],
            "models/m1/custom_fields": [],
            "workflow_stages": [],
            "domains": [
                {"id": "anomaly_detection", "attributes": {"name": "Anomaly Detection", "custom": False}},
                {
                    "id": "store_ops",
                    "attributes": {"name": "Store Operations", "description": "Shelves", "custom": True},
                },
            ],
        },
        dry_run=True,
        json_bodies={
            "resolve_triggers": {"data": []}
            if v3
            else APIError("GET resolve_triggers failed: 404", 404, '{"reason": "feature_flag_disabled"}'),
            "teams/t1": {
                "data": {"id": "t1", "relationships": {"memberships": {"data": [{"id": "m1"}]}}},
                "included": [{"type": "team_memberships", "attributes": {"member_id": "u1", "role": "owner"}}],
            },
            "risk_scoring_config": SCORING_CONFIG,
        },
    )
    ctx = make_ctx(in_tmp, source=source, source_internal=source_internal, dry_run=True)
    assert migrate.main(["extract", "all"], context_factory=factory(ctx)) == (0 if v3 else 1)
    assert all((in_tmp / f"{e}.json").exists() for e in ENTITIES)
    assert [v["name"] for v in load_extract(in_tmp, "vendors")] == ["Acme AI"]
    assert [u["email"] for u in load_extract(in_tmp, "users")] == ["ana@credo.ai"]
    assert load_extract(in_tmp, "teams")[0]["members"] == [{"email": "ana@credo.ai", "role": "owner"}]
    assert load_extract(in_tmp, "triggers") == []
    [model] = load_extract(in_tmp, "models")
    assert (model["name"], model["vendor_ids"], model["owner_email"]) == ("Vision Model", ["v1"], "ana@credo.ai")
    assert load_extract(in_tmp, "domains") == [
        {"id": "store_ops", "name": "Store Operations", "description": "Shelves"}
    ]
    assert load_extract(in_tmp, "use_cases") == []
    assert load_extract(in_tmp, "model_answers") == [] and load_extract(in_tmp, "vendor_answers") == []


def test_unknown_entity_is_rejected(in_tmp):
    with pytest.raises(SystemExit):
        migrate.main(["load", "widgets"], context_factory=factory(make_ctx(in_tmp)))


def test_check_passes_when_both_tenants_respond(in_tmp):
    assert migrate.main(["check"], context_factory=factory(make_ctx(in_tmp, dry_run=True))) == 0


def test_check_names_the_application_api(in_tmp, caplog):
    import logging

    with caplog.at_level(logging.INFO):
        assert migrate.main(["check"], context_factory=factory(make_ctx(in_tmp, dry_run=True))) == 0
    assert caplog.text.count("application API OK") == 2
    assert "internal API" not in caplog.text


def test_check_logs_each_tenants_trigger_version(in_tmp, caplog):
    import logging

    from kit.clients.base import APIError

    flag_off = APIError("GET resolve_triggers failed: 404", 404, '{"reason": "feature_flag_disabled"}')
    ctx = make_ctx(in_tmp, dry_run=True, target_internal=FakeTenant(json_bodies={"resolve_triggers": flag_off}))
    with caplog.at_level(logging.INFO):
        assert migrate.main(["check"], context_factory=factory(ctx)) == 0
    assert caplog.text.count("source source-tenant: triggers v3") == 1
    assert caplog.text.count("target target-tenant: triggers v2") == 1


def test_check_warns_when_it_cant_tell_the_trigger_version(in_tmp, caplog):
    import logging

    from kit.clients.base import APIError

    broken = APIError("GET resolve_triggers failed: 500", 500, "oops")
    ctx = make_ctx(in_tmp, dry_run=True, source_internal=FakeTenant(json_bodies={"resolve_triggers": broken}))
    with caplog.at_level(logging.INFO):
        assert migrate.main(["check"], context_factory=factory(ctx)) == 0
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("source source-tenant" in w and "trigger version" in w for w in warnings)
    assert "target target-tenant: triggers v3" in caplog.text


@pytest.mark.parametrize(
    "entitlements, expected",
    [
        (LOCAL_USERS_OFF, "target target-tenant: invite emails off"),
        (LOCAL_USERS_ON, "target target-tenant: invite emails may be sent (LOCAL_USERS is on)"),
        ([], "target target-tenant: invite emails may be sent (LOCAL_USERS isn't in the target's entitlements list"),
    ],
    ids=["off", "on", "missing"],
)
def test_check_reports_the_targets_invite_email_setting(in_tmp, caplog, entitlements, expected):
    import logging

    ctx = make_ctx(in_tmp, dry_run=True, target_internal=FakeTenant({"entitlements": entitlements}, jsonapi=True))
    with caplog.at_level(logging.INFO):
        assert migrate.main(["check"], context_factory=factory(ctx)) == 0
    assert expected in caplog.text
    assert "source source-tenant: invite emails" not in caplog.text


def test_same_source_and_target_is_refused(in_tmp):
    ctx = make_ctx(in_tmp, target=FakeTenant(tenant="source-tenant"))
    save_extract(in_tmp, "risk_types", [{"id": "r1", "name": "Model Risk"}])
    assert (
        migrate.main(["load", "risk_types", "--live", "--target", "source-tenant"], context_factory=factory(ctx)) == 2
    )
    assert ctx.target.posts == []


def test_run_stopped_by_api_error_still_saves_summary(in_tmp):
    from kit.clients.base import APIError

    class Exploding(FakeTenant):
        def get_all(self, path, params=None):
            raise APIError("GET custom_fields failed: 503", 503)

    save_extract(in_tmp, "risk_types", [{"id": "r1", "name": "Model Risk"}])
    for entity in ENTITIES:
        if entity != "risk_types":
            save_extract(in_tmp, entity, [])
    target = Exploding()
    ctx = make_ctx(in_tmp, target=target)
    assert migrate.main(["load", "all", "--live", "--target", "target-tenant"], context_factory=factory(ctx)) == 2
    assert len(list((in_tmp / "logs").glob("*_summary.json"))) == 1


def test_unexpected_exception_exits_2(in_tmp):
    save_extract(in_tmp, "risk_types", [{"no_id": True}])
    assert migrate.main(["load", "risk_types"], context_factory=factory(make_ctx(in_tmp, dry_run=True))) == 2


def test_load_all_checks_every_extract_file_before_writing(in_tmp):
    save_extract(in_tmp, "risk_types", [{"id": "r1", "name": "Model Risk"}])
    ctx = make_ctx(in_tmp)
    assert migrate.main(["load", "all", "--live", "--target", "target-tenant"], context_factory=factory(ctx)) == 2
    assert ctx.target.posts == []


def test_dry_run_predicts_live_run(in_tmp):
    items = [{"id": "r1", "name": "Model Risk"}, {"id": "r2", "name": "Existing"}]
    existing = {"risk_types": [{"id": "t9", "name": "Existing"}]}
    save_extract(in_tmp, "risk_types", items)
    dry = make_ctx(in_tmp, target=FakeTenant(existing, dry_run=True), dry_run=True)
    migrate.main(["load", "risk_types"], context_factory=factory(dry))
    live = make_ctx(in_tmp, target=FakeTenant(existing))
    migrate.main(["load", "risk_types", "--live", "--target", "target-tenant"], context_factory=factory(live))
    assert dry.summary.to_dict() == live.summary.to_dict()


def test_dry_run_predicts_live_run_for_load_all(in_tmp, caplog):
    import logging

    for entity in ENTITIES:
        save_extract(in_tmp, entity, [])
    phase4_extracts(in_tmp)
    trigger_extracts(in_tmp)
    phase6_extracts(in_tmp)
    phase7_extracts(in_tmp)
    target, target_internal = all_entities_target()
    dry = make_ctx(in_tmp, target=target, target_internal=target_internal, dry_run=True)
    assert migrate.main(["load", "all"], context_factory=factory(dry)) == 0
    assert target.posts == [] and target_internal.posts == [] and target_internal.patches == []
    target, target_internal = all_entities_target()
    live = make_ctx(in_tmp, target=target, target_internal=target_internal)
    with caplog.at_level(logging.INFO):
        assert migrate.main(["load", "all", "--live", "--target", "target-tenant"], context_factory=factory(live)) == 0
    assert dry.summary.to_dict() == live.summary.to_dict()
    created = {"created": 1, "matched": 0, "skipped": 0, "failed": 0}
    new = (
        "vendors",
        "users",
        "teams",
        "triggers",
        "trigger_actions",
        "sources",
        "models",
        "domains",
        "use_cases",
        "model_answers",
    )
    assert {e: live.summary.to_dict()[e] for e in new} == dict.fromkeys(new, created)
    assert not live.summary.needs_attention
    model_id = live.tracker("models").target_id("sm1")
    vendor_id = live.tracker("vendors").target_id("sv1")
    assert (
        "models",
        {
            "name": "Vision Model",
            "summary": "Labels images",
            "source": "Example Source",
            "questionnaire_ids": ["CR-MODEL+1"],
        },
    ) in target.posts
    assert (f"models/{model_id}/vendors", {"id": vendor_id}) in target.posts
    assert [path for path, _body in target_internal.posts][-4:] == [
        "sources",
        "entities/e-model/stakeholders",
        "domains",
        "use_cases",
    ]
    assert target_internal.posts[-3][1]["data"]["attributes"] == {"user_id": "new-1", "role": "owner"}
    assert target_internal.deletes == ["entities/e-model/stakeholders/sh1"]
    # Phase 7: the use case is created with its owner and paused; the model gets its questionnaire and answer
    assert (
        "use_cases",
        {
            "data": {
                "type": "use_cases",
                "attributes": {
                    "name": "Chatbot",
                    "description": "Answers questions",
                    "owner": {"owner_id": "new-1", "owner_type": "user"},
                },
            }
        },
    ) in target_internal.posts
    pause = {"data": {"type": "entities", "attributes": {"trigger_mode": "hidden_questions_only"}}}
    assert target_internal.patches[-2:] == [
        ("entities/e-uc/trigger_mode", pause),
        ("entities/e-model/trigger_mode", pause),
    ]
    assert target.posts[-2:] == [
        (f"models/{model_id}/questionnaires", {"key": "CR-MODEL", "version": 1}),
        (f"models/{model_id}/questionnaire_sections/es1/evidences", {"question_id": "q2", "data": {"value": "Vision"}}),
    ]
    # The trigger reminder is repeated after the summary table, the end of the run's output
    summary = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Summary\n")][-1]
    assert "Notes:" in summary and "Triggers are paused on 1 migrated use case," in summary
    assert "'Triggers and actions settings'" in summary


def test_load_all_live_creates_vendors_users_teams_and_triggers_with_local_users_off(in_tmp):
    for entity in ENTITIES:
        save_extract(in_tmp, entity, [])
    phase4_extracts(in_tmp)
    trigger_extracts(in_tmp)
    target, target_internal = all_entities_target()
    ctx = make_ctx(in_tmp, target=target, target_internal=target_internal)
    assert migrate.main(["load", "all", "--live", "--target", "target-tenant"], context_factory=factory(ctx)) == 0
    assert target.posts == [
        ("vendors", {"name": "Acme AI", "description": "A vendor", "questionnaire_ids": ["CR-VENDOR+1"]}),
        (
            "triggers",
            {
                "description": "When yes",
                "match": "all",
                "questionnaire_version_id": "INTAKE+1",
                "conditions": [
                    {
                        "type": "questionnaire_evidence",
                        "data": {"options": ["yes"], "question_id": "q1", "section_id": "s1"},
                    }
                ],
            },
        ),
    ]
    assert [path for path, _body in target_internal.posts] == ["users", "teams", "resolve_triggers/new-2/actions"]
    assert target_internal.patches == [
        ("teams/new-2/relationships/members", {"data": [{"id": "new-1", "type": "users", "role": "owner"}]})
    ]
    assert not ctx.summary.has_failures


def test_load_users_live_stops_while_local_users_is_on_unless_invites_are_allowed(in_tmp):
    phase4_extracts(in_tmp)
    target, target_internal = all_entities_target(local_users=LOCAL_USERS_ON)
    ctx = make_ctx(in_tmp, target=target, target_internal=target_internal)
    assert migrate.main(["load", "users", "--live", "--target", "target-tenant"], context_factory=factory(ctx)) == 1
    assert target_internal.posts == [] and target_internal.patches == []
    assert not (in_tmp / "loaded_users.json").exists()

    target, target_internal = all_entities_target(local_users=LOCAL_USERS_ON)
    ctx = make_ctx(in_tmp, target=target, target_internal=target_internal)
    assert (
        migrate.main(
            ["load", "users", "--live", "--target", "target-tenant", "--allow-invites"], context_factory=factory(ctx)
        )
        == 0
    )
    assert [path for path, _body in target_internal.posts] == ["users"]
    assert ctx.summary.to_dict()["users"]["created"] == 1


def test_build_context_wiring(monkeypatch):
    for side in ("SOURCE", "TARGET"):
        monkeypatch.setenv(f"{side}_BASE_URL", "https://api.example.com")
        monkeypatch.setenv(f"{side}_TENANT", side.lower())
        monkeypatch.setenv(f"{side}_API_KEY", f"{side.lower()}-key")
    for live in (False, True):
        ctx, secrets = migrate.build_context(live)
        assert ctx.source.read_only and ctx.source_internal.read_only
        assert ctx.target.dry_run is (not live) and ctx.target_internal.dry_run is (not live)
        assert not ctx.target.read_only
        assert ctx.dry_run is (not live)
        assert ctx.data_dir == migrate.KIT_DIR / "data"
        assert secrets == ["source-key", "target-key"]


def test_load_all_dry_run_carries_a_new_risk_type_into_controls_and_scenarios(in_tmp):
    # The risk type isn't on the target yet: later entities must see its dry-run placeholder
    save_extract(in_tmp, "risk_types", [{"id": "s-rt", "name": "Agent Risk", "custom": True}])
    save_extract(in_tmp, "risk_type_weights", [{"id": "s-rt", "risk_type": "Agent Risk", "weight": "100"}])
    save_extract(in_tmp, "custom_fields", [])
    save_extract(
        in_tmp,
        "controls",
        [
            {
                "id": "C1",
                "key": "C1",
                "version": 1,
                "draft": False,
                "info": {"description": "C1"},
                "evidence_requirements": [],
                "risk_type_ids": ["s-rt"],
                "risk_type_names": ["Agent Risk"],
                "published_on_source": True,
            }
        ],
    )
    save_extract(in_tmp, "policy_packs", [])
    save_extract(
        in_tmp,
        "risk_scenarios",
        [
            {
                "id": "s1",
                "name": "Rogue agent",
                "description": "About it",
                "author": None,
                "domains": [],
                "industries": [],
                "regions": [],
                "risk_type_ids": ["s-rt"],
                "risk_type_names": ["Agent Risk"],
                "ownership_type": "custom",
                "archived": False,
            }
        ],
    )
    for entity in (
        "questionnaires",
        "vendors",
        "users",
        "teams",
        "triggers",
        "models",
        "domains",
        "use_cases",
        "model_answers",
        "vendor_answers",
    ):
        save_extract(in_tmp, entity, [])
    trigger_extracts(in_tmp)  # the triggers load stops unless loaded_questionnaires.json exists
    target, target_internal = all_entities_target()
    ctx = make_ctx(in_tmp, target=target, target_internal=target_internal, dry_run=True)
    assert migrate.main(["load", "all"], context_factory=factory(ctx)) == 0
    summary = ctx.summary.to_dict()
    assert summary["risk_type_weights"] == {"created": 1, "matched": 0, "skipped": 0, "failed": 0}
    assert summary["controls"] == {"created": 1, "matched": 0, "skipped": 0, "failed": 0}
    assert summary["risk_scenarios"] == {"created": 1, "matched": 0, "skipped": 0, "failed": 0}
    assert not ctx.summary.has_failures
    assert target.posts == [] and target_internal.posts == []


def test_questionnaires_load_after_risk_scenarios():
    order = list(ENTITIES)
    assert order.index("questionnaires") == order.index("risk_scenarios") + 1


def test_vendors_users_teams_load_after_questionnaires_in_order():
    order = list(ENTITIES)
    assert order.index("questionnaires") < order.index("vendors") < order.index("users") < order.index("teams")


def test_triggers_load_after_teams():
    order = list(ENTITIES)
    assert order.index("triggers") == order.index("teams") + 1


def test_models_domains_use_cases_then_answers_load_last_after_triggers():
    order = list(ENTITIES)
    assert order[-6:] == ["triggers", "models", "domains", "use_cases", "model_answers", "vendor_answers"]


def test_load_exits_1_when_an_item_needs_attention(in_tmp):
    item = {
        "id": "K",
        "key": "K",
        "name": "Intake",
        "target": "use_case",
        "info": {},
        "show_on_create": False,
        "version": 1,
        "draft": False,
        "published_on_source": True,
        "sections": [],
    }
    save_extract(in_tmp, "questionnaires", [item])
    ctx = make_ctx(
        in_tmp,
        target=FakeTenant(patch_response=FakeResponse(500, text="boom")),
        target_internal=FakeTenant({"questionnaire_bases": []}, jsonapi=True),
    )
    assert (
        migrate.main(["load", "questionnaires", "--live", "--target", "target-tenant"], context_factory=factory(ctx))
        == 1
    )
    assert ctx.summary.to_dict()["questionnaires"]["created"] == 1
    [summary] = (in_tmp / "logs").glob("*_summary.json")
    assert "needs_attention" in summary.read_text()


def test_load_all_live_with_local_users_on_stops_before_teams(in_tmp, caplog):
    import logging

    for entity in ENTITIES:
        save_extract(in_tmp, entity, [])
    phase4_extracts(in_tmp)
    target, target_internal = all_entities_target(local_users=LOCAL_USERS_ON)
    ctx = make_ctx(in_tmp, target=target, target_internal=target_internal)
    with caplog.at_level(logging.ERROR):
        assert migrate.main(["load", "all", "--live", "--target", "target-tenant"], context_factory=factory(ctx)) == 1
    assert target_internal.posts == [] and target_internal.patches == []
    assert "teams" not in ctx.summary.to_dict()
    assert "Stopped: users: " in caplog.text
    assert ctx.summary.needs_attention
    assert len(list((in_tmp / "logs").glob("*_summary.json"))) == 1
