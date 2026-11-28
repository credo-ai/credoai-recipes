import logging

import pytest

from kit.clients.base import APIError
from kit.entities import use_cases
from kit.tracking import load_extract
from tests.fakes import FakeTenant, make_ctx
from tests.test_answers import SpecTenant, evidence, question, section

# --- extract ---

INTAKE_1 = [section("s1", "About", question("q1", "Purpose?"))]


def integration_uc(uid, name, fields=()):
    return {
        "id": uid,
        "entity_id": f"e-{uid}",
        "name": name,
        "description": f"About {name}",
        "questionnaire_ids": [],
        "custom_fields": list(fields),
    }


def cf(name, value):
    return {"custom_field_id": f"cf-{name}", "name": name, "value": value}


def stakeholder(sid, role, user_id=None, team_id=None):
    return {"id": sid, "attributes": {"role": role, "user_id": user_id, "team_id": team_id}}


def internal_uc(uid, stage="st1", step="assessment", level=None, domains=()):
    return {
        "data": {
            "id": uid,
            "attributes": {
                "workflow_stage_id": stage,
                "workflow_stage_step": step,
                "risk_classification_level": level,
                "domain_ids": list(domains),
                "trigger_mode": "full",
            },
        }
    }


def source_ctx(tmp_path, ucs, integration=None, internal=None, bodies=None, specs=None):
    collections = {"use_cases": ucs, "risk_types": [{"id": "rt1", "name": "Privacy"}]}
    for uc in ucs:
        for sub in ("questionnaires", "risk_scenarios", "controls", "policy_packs", "models", "vendors"):
            collections.setdefault(f"use_cases/{uc['id']}/{sub}", [])
    collections.update(integration or {})
    internal_collections = {
        "users": [
            {"id": "u1", "attributes": {"email": "owner@example.com"}},
            {"id": "u2", "attributes": {"email": "helper@example.com"}},
        ],
        "teams": [{"id": "t1", "attributes": {"name": "Reviewers"}}],
        "workflow_stages": [
            {"id": "st1", "attributes": {"name": "Intake", "parent_id": None, "child_id": "st2"}},
            {"id": "st2", "attributes": {"name": "Governance", "parent_id": "st1", "child_id": None}},
        ],
    }
    for uc in ucs:
        internal_collections.setdefault(f"use_cases/{uc['id']}/stakeholders", [stakeholder("k1", "owner", "u1")])
        internal_collections.setdefault(f"use_cases/{uc['id']}/evidences", [])
    internal_collections.update(internal or {})
    json_bodies = {f"use_cases/{uc['id']}": internal_uc(uc["id"]) for uc in ucs}
    json_bodies.update(bodies or {})
    source = SpecTenant(collections, specs=specs or {}, dry_run=True)
    source_internal = FakeTenant(internal_collections, dry_run=True, jsonapi=True, json_bodies=json_bodies)
    return make_ctx(tmp_path, source=source, source_internal=source_internal)


def test_extract_builds_the_item(tmp_path, caplog):
    ctx = source_ctx(
        tmp_path,
        [integration_uc("uc1", "Chatbot", fields=[cf("Tier", "Gold"), cf("Empty", None), cf("Blank", "")])],
        integration={
            "use_cases/uc1/questionnaires": [{"id": "a1", "key": "INTAKE", "version": 1}],
            "use_cases/uc1/risk_scenarios": [
                {
                    "id": "r1",
                    "risk_scenario_id": None,
                    "name": "Leak",
                    "description": "Data leak",
                    "risk_type_ids": ["rt1"],
                    "inherent_risk_likelihood": 3,
                    "inherent_risk_impact": None,
                    "residual_risk_likelihood": 3,
                    "residual_risk_impact": None,
                    "control_keys": ["CTRL-1"],
                },
                {
                    "id": "r2",
                    "risk_scenario_id": "cat1",
                    "name": "Bias",
                    "description": "",
                    "risk_type_ids": [],
                    "inherent_risk_likelihood": None,
                    "inherent_risk_impact": None,
                    "residual_risk_likelihood": None,
                    "residual_risk_impact": None,
                    "control_keys": [],
                },
            ],
            "use_cases/uc1/controls": [{"id": "c1", "control_key": "CTRL-1"}, {"id": "c2", "control_key": "CTRL-2"}],
            "use_cases/uc1/controls/c1/evidences": [
                {"id": "ev1", "type": "text", "control_requirement_id": "h1", "data": {"value": "proof"}},
                {"id": "ev2", "type": "file", "control_requirement_id": "h2", "data": {"file_id": "f"}},
            ],
            "use_cases/uc1/controls/c2/evidences": [],
            "use_cases/uc1/policy_packs": [{"id": "PACK+2", "key": "PACK", "version": 2}],
            "use_cases/uc1/models": [{"id": "m1", "name": "Model A"}],
            "use_cases/uc1/vendors": [{"id": "v1", "name": "Vendor A"}],
        },
        internal={
            "use_cases/uc1/stakeholders": [
                stakeholder("k1", "owner", "u1"),
                stakeholder("k2", "contributor", "u2"),
                stakeholder("k3", "observer", team_id="t1"),
            ],
            "use_cases/uc1/evidences": [evidence("e1", "q1", "s1", "Triage")],
        },
        bodies={"use_cases/uc1": internal_uc("uc1", stage="st2", step="clear", level=2, domains=["nlp"])},
        specs={("INTAKE", 1): INTAKE_1},
    )
    with caplog.at_level(logging.INFO, logger="kit.extract"):
        assert use_cases.extract(ctx) == 1
    [item] = load_extract(tmp_path, "use_cases")
    assert item == {
        "id": "uc1",
        "name": "Chatbot",
        "description": "About Chatbot",
        "owner": {"email": "owner@example.com"},
        "stakeholders": [
            {"email": "helper@example.com", "role": "contributor"},
            {"team_name": "Reviewers", "role": "observer"},
        ],
        "custom_fields": [{"name": "Tier", "value": "Gold"}],
        "stage_name": "Governance",
        "step": "clear",
        "risk_classification_level": 2,
        "policy_packs": [{"key": "PACK"}],
        "risk_scenarios": [
            {
                "catalog_id": None,
                "name": "Leak",
                "description": "Data leak",
                "risk_type_ids": ["rt1"],
                "risk_type_names": ["Privacy"],
                "inherent_likelihood": 3,
                "inherent_impact": None,
                "residual_likelihood": 3,
                "residual_impact": None,
                "control_keys": ["CTRL-1"],
            },
            {
                "catalog_id": "cat1",
                "name": "Bias",
                "description": "",
                "risk_type_ids": [],
                "risk_type_names": [],
                "inherent_likelihood": None,
                "inherent_impact": None,
                "residual_likelihood": None,
                "residual_impact": None,
                "control_keys": [],
            },
        ],
        "controls": [{"key": "CTRL-1", "evidence": [{"hash_key": "h1", "data": {"value": "proof"}}]}],
        "domain_ids": ["nlp"],
        "model_ids": ["m1"],
        "vendor_ids": ["v1"],
        "model_names": {"m1": "Model A"},
        "vendor_names": {"v1": "Vendor A"},
        "attachments": [{"key": "INTAKE", "version": 1}],
        "answers": [
            {
                "key": "INTAKE",
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
    assert "1 use case(s), 1 answer(s)" in caplog.text
    assert "file evidence" in caplog.text and "CTRL-1" in caplog.text


def test_extract_reads_the_internal_use_case_with_its_domains(tmp_path):
    ctx = source_ctx(tmp_path, [integration_uc("uc1", "Chatbot")])
    use_cases.extract(ctx)
    assert ("use_cases/uc1", {"include": "domain_links"}) in ctx.source_internal.gets


def test_extract_owner_can_be_a_team_and_unknown_users_are_left_out(tmp_path, caplog):
    ctx = source_ctx(
        tmp_path,
        [integration_uc("uc1", "Chatbot")],
        internal={
            "use_cases/uc1/stakeholders": [
                stakeholder("k1", "owner", team_id="t1"),
                stakeholder("k2", "contributor", "hidden"),
            ]
        },
    )
    with caplog.at_level(logging.WARNING, logger="kit.extract"):
        use_cases.extract(ctx)
    [item] = load_extract(tmp_path, "use_cases")
    assert item["owner"] == {"team_name": "Reviewers"}
    assert item["stakeholders"] == []
    assert "'Chatbot'" in caplog.text and "hidden" in caplog.text


def test_extract_without_an_owner_keeps_none(tmp_path, caplog):
    ctx = source_ctx(tmp_path, [integration_uc("uc1", "Chatbot")], internal={"use_cases/uc1/stakeholders": []})
    with caplog.at_level(logging.WARNING, logger="kit.extract"):
        use_cases.extract(ctx)
    assert load_extract(tmp_path, "use_cases")[0]["owner"] is None
    assert "no owner" in caplog.text


def test_extract_reads_custom_fields_when_the_list_does_not_carry_them(tmp_path):
    uc = dict(integration_uc("uc1", "Chatbot"), custom_fields=None)
    ctx = source_ctx(tmp_path, [uc])
    ctx.source.json_bodies["use_cases/uc1"] = dict(uc, custom_fields=[cf("Tier", "Gold")])
    use_cases.extract(ctx)
    assert load_extract(tmp_path, "use_cases")[0]["custom_fields"] == [{"name": "Tier", "value": "Gold"}]


def test_extract_stops_on_an_internal_use_case_without_attributes(tmp_path):
    ctx = source_ctx(tmp_path, [integration_uc("uc1", "Chatbot")], bodies={"use_cases/uc1": {"items": []}})
    with pytest.raises(APIError, match="Chatbot"):
        use_cases.extract(ctx)


# --- load: create, owner, trigger mode ---

PAUSE = {"data": {"type": "entities", "attributes": {"trigger_mode": "hidden_questions_only"}}}
TARGET_INTAKE_3 = [section("ts1", "About", question("q1", "Purpose?"))]


class InternalTarget(FakeTenant):
    """Internal API fake whose use case create answers like the real one: an id and an entity_id attribute."""

    def post(self, path, json):
        resp = super().post(path, json)
        if path == "use_cases" and resp is not None and resp.status_code == 201:
            body = resp.json()
            body["data"]["attributes"]["entity_id"] = f"e-{body['data']['id']}"
            resp._body = body
        return resp


def uc_item(uid="uc1", name="Chatbot", owner=None, stakeholders=(), fields=(), attachments=(), answers_=(), **extra):
    item = {
        "id": uid,
        "name": name,
        "description": f"About {name}",
        "owner": owner if owner is not None else {"email": "owner@example.com"},
        "stakeholders": list(stakeholders),
        "custom_fields": list(fields),
        "stage_name": "Intake",
        "step": "assessment",
        "risk_classification_level": None,
        "policy_packs": [],
        "risk_scenarios": [],
        "controls": [],
        "domain_ids": [],
        "model_ids": [],
        "vendor_ids": [],
        "model_names": {},
        "vendor_names": {},
        "attachments": list(attachments),
        "answers": list(answers_),
        "file_answers": 0,
    }
    item.update(extra)
    return item


def src_answer(qid="q1", text="Purpose?", value="Triage", key="INTAKE", version=1):
    return {
        "key": key,
        "version": version,
        "section_title": "About",
        "question_id": qid,
        "question_text": text,
        "evidence_type": "text",
        "multiple": False,
        "value": value,
    }


def target_uc_state(uid, stage="st1", step="assessment", level=None, domains=(), mode="hidden_questions_only"):
    return {
        "data": {
            "id": uid,
            "attributes": {
                "workflow_stage_id": stage,
                "workflow_stage_step": step,
                "risk_classification_level": level,
                "domain_ids": list(domains),
                "trigger_mode": mode,
            },
        }
    }


def target_ctx(
    tmp_path,
    existing=(),
    dry_run=False,
    fields=(),
    integration=None,
    internal=None,
    bodies=None,
    internal_bodies=None,
    published=(("INTAKE", 3),),
    post_responses=None,
    internal_patch=None,
):
    collections = {
        "use_cases": list(existing),
        "custom_fields": list(fields),
        "risk_types": [],
        "risk_scenarios": [],
        "policy_control_bases": [],
        "models": [],
        "vendors": [],
    }
    collections.update(integration or {})
    json_bodies = {f"questionnaires/{k}/versions": [{"version": n, "draft": False}] for k, n in published}
    json_bodies.update(bodies or {})
    target = SpecTenant(
        collections, specs={("INTAKE", 3): TARGET_INTAKE_3}, json_bodies=json_bodies, post_responses=post_responses
    )
    internal_collections = {
        "entity_types": [
            {"id": "et-uc", "attributes": {"name": "use_case"}},
            {"id": "et-model", "attributes": {"name": "model"}},
        ],
        "users": [
            {"id": "tu1", "attributes": {"email": "Owner@example.com"}},
            {"id": "tu2", "attributes": {"email": "helper@example.com"}},
        ],
        "teams": [{"id": "tt1", "attributes": {"name": "Reviewers"}}],
        "workflow_stages": [
            {"id": "st1", "attributes": {"name": "Intake", "parent_id": None, "child_id": "st2"}},
            {"id": "st2", "attributes": {"name": "Governance", "parent_id": "st1", "child_id": None}},
        ],
        "domains": [],
    }
    for n in range(1, 4):
        internal_collections[f"use_cases/new-{n}/stakeholders"] = [stakeholder("ks1", "owner", "tu1")]
        internal_collections[f"use_cases/new-{n}/questionnaire_sections"] = [
            {"id": "es1", "attributes": {"section_id": "ts1"}}
        ]
        internal_collections[f"use_cases/new-{n}/evidences"] = []
        internal_collections[f"use_cases/new-{n}/controls"] = []
    internal_collections.update(internal or {})
    ibodies = {f"use_cases/new-{n}": target_uc_state(f"new-{n}") for n in range(1, 4)}
    ibodies.update(internal_bodies or {})
    target_internal = InternalTarget(
        internal_collections, jsonapi=True, json_bodies=ibodies, patch_response=internal_patch
    )
    return make_ctx(tmp_path, target=target, target_internal=target_internal, dry_run=dry_run)


def uc_posts(ctx):
    return [b["data"]["attributes"] for p, b in ctx.target_internal.posts if p == "use_cases"]


def save_items(tmp_path, *items):
    from kit.tracking import save_extract

    save_extract(tmp_path, "use_cases", list(items))


def test_load_creates_with_owner_and_no_user_or_questionnaires(tmp_path):
    save_items(tmp_path, uc_item(attachments=[{"key": "INTAKE", "version": 1}]))
    ctx = target_ctx(tmp_path)
    use_cases.load(ctx)
    assert uc_posts(ctx) == [
        {"name": "Chatbot", "description": "About Chatbot", "owner": {"owner_id": "tu1", "owner_type": "user"}}
    ]
    record = ctx.tracker("use_cases").find("uc1")
    assert (record["target_id"], record["origin"]) == ("new-1", "created")
    assert (tmp_path / "loaded_use_cases.json").exists()
    assert ctx.summary.to_dict()["use_cases"]["created"] == 1


def test_owner_maps_through_the_users_tracker_first(tmp_path):
    save_items(tmp_path, uc_item())
    ctx = target_ctx(tmp_path)
    ctx.tracker("users").record("su1", "tu9", "owner@example.com", "created")
    use_cases.load(ctx)
    assert uc_posts(ctx)[0]["owner"] == {"owner_id": "tu9", "owner_type": "user"}


def test_team_owner_maps_by_tracker_then_name(tmp_path):
    save_items(tmp_path, uc_item(owner={"team_name": "Reviewers"}), uc_item("uc2", "Agent", owner={"team_name": "Ops"}))
    ctx = target_ctx(tmp_path)
    ctx.tracker("teams").record("st9", "tt9", "Ops", "created")
    use_cases.load(ctx)
    assert [b["owner"] for b in uc_posts(ctx)] == [
        {"owner_id": "tt1", "owner_type": "team"},
        {"owner_id": "tt9", "owner_type": "team"},
    ]


@pytest.mark.parametrize("owner", [{"email": "nobody@example.com"}, {}], ids=["unmapped", "none"])
def test_unmapped_owner_creates_without_owner_and_flags(tmp_path, caplog, owner):
    save_items(tmp_path, uc_item(owner=owner))
    ctx = target_ctx(tmp_path)
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        use_cases.load(ctx)
    [body] = uc_posts(ctx)
    assert "owner" not in body and "user" not in body
    assert ctx.summary.needs_attention
    assert "'Chatbot'" in caplog.text and "API key's user" in caplog.text


def test_name_on_the_target_is_skipped_and_never_touched(tmp_path, caplog):
    save_items(tmp_path, uc_item())
    ctx = target_ctx(tmp_path, existing=[{"id": "t1", "name": "Chatbot"}])
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        use_cases.load(ctx)
    assert ctx.target.posts == [] and ctx.target.patches == []
    assert ctx.target_internal.posts == [] and ctx.target_internal.patches == []
    assert ctx.tracker("use_cases").find("uc1") is None
    assert ctx.summary.to_dict()["use_cases"]["skipped"] == 1
    assert "'Chatbot'" in caplog.text and "already on the target" in caplog.text


def test_second_source_use_case_with_the_same_name_is_skipped(tmp_path, caplog):
    save_items(tmp_path, uc_item(), uc_item("uc2"))
    ctx = target_ctx(tmp_path)
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        use_cases.load(ctx)
    assert len(uc_posts(ctx)) == 1
    assert ctx.summary.to_dict()["use_cases"] == {"created": 1, "matched": 0, "skipped": 1, "failed": 0}
    assert "more than one source use case" in caplog.text


def test_questionnaire_without_a_published_version_fails_before_any_write(tmp_path, caplog):
    save_items(tmp_path, uc_item(attachments=[{"key": "INTAKE", "version": 1}, {"key": "RISK", "version": 2}]))
    ctx = target_ctx(tmp_path, bodies={"questionnaires/RISK/versions": [{"version": 1, "draft": True}]})
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        use_cases.load(ctx)
    assert ctx.target.posts == [] and ctx.target_internal.patches == []
    assert ctx.summary.to_dict()["use_cases"]["failed"] == 1
    assert "questionnaire RISK" in caplog.text and "publish" in caplog.text


def test_custom_field_that_does_not_apply_to_use_cases_fails_before_any_write(tmp_path, caplog):
    save_items(tmp_path, uc_item(fields=[{"name": "Tier", "value": "Gold"}]))
    ctx = target_ctx(tmp_path, fields=[{"id": "f1", "name": "Tier", "entity_type_ids": ["et-model"]}])
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        use_cases.load(ctx)
    assert ctx.target.posts == []
    assert ctx.summary.to_dict()["use_cases"]["failed"] == 1
    assert "Tier" in caplog.text


def test_trigger_mode_is_paused_right_after_the_create(tmp_path, caplog):
    save_items(tmp_path, uc_item(attachments=[{"key": "INTAKE", "version": 1}], answers_=[src_answer()]))
    ctx = target_ctx(tmp_path)
    with caplog.at_level(logging.INFO, logger="kit.load"):
        use_cases.load(ctx)
    assert ctx.target_internal.patches[0] == ("entities/e-new-1/trigger_mode", PAUSE)
    assert ("use_cases/new-1/questionnaires", {"key": "INTAKE", "version": 3}) in ctx.target.posts
    assert (
        "use_cases/new-1/questionnaire_sections/es1/evidences",
        {"question_id": "q1", "data": {"value": "Triage"}},
    ) in ctx.target.posts
    assert "Triggers are paused on 1 migrated use case" in caplog.text


def test_failed_trigger_mode_skips_attachments_and_answers_and_flags(tmp_path, caplog):
    from tests.fakes import FakeResponse

    save_items(tmp_path, uc_item(attachments=[{"key": "INTAKE", "version": 1}], answers_=[src_answer()]))
    ctx = target_ctx(tmp_path, internal_patch=FakeResponse(500, text="boom"))
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        use_cases.load(ctx)
    assert [p for p, _ in ctx.target_internal.posts] == ["use_cases"]
    assert ctx.target.posts == []
    assert ctx.tracker("use_cases").target_id("uc1") == "new-1"
    assert ctx.summary.needs_attention
    assert "Triggers are paused" not in caplog.text


def test_lost_connection_on_create_says_the_use_case_may_exist(tmp_path, caplog):
    save_items(tmp_path, uc_item())
    ctx = target_ctx(tmp_path)

    def lost(path, json):
        raise APIError("POST use_cases lost its connection: reset. The item may or may not have been created")

    ctx.target_internal.post = lost
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        use_cases.load(ctx)
    assert ctx.tracker("use_cases").find("uc1") is None
    assert ctx.summary.to_dict()["use_cases"]["failed"] == 1
    assert (
        "use case 'Chatbot' may have been created on the target before the connection was lost; check the "
        "target: if use case 'Chatbot' is there, delete it, then re-run"
    ) in caplog.text


def test_rerun_with_triggers_on_skips_attachments_and_answers(tmp_path, caplog):
    save_items(tmp_path, uc_item(attachments=[{"key": "INTAKE", "version": 1}], answers_=[src_answer()]))
    ctx = target_ctx(
        tmp_path,
        existing=[{"id": "new-1", "name": "Chatbot"}],
        internal_bodies={"use_cases/new-1": target_uc_state("new-1", mode="full")},
    )
    ctx.tracker("use_cases").record("uc1", "new-1", "Chatbot", "created")
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        use_cases.load(ctx)
    assert ctx.target.posts == [] and ctx.target_internal.patches == []
    assert ctx.summary.to_dict()["use_cases"]["skipped"] == 1
    assert (
        "triggers aren't paused on use case 'Chatbot' (mode full); the kit won't attach questionnaires or write "
        "answers. Set Partial Pause in the gear menu, Triggers and actions settings, then re-run, or leave it."
        in caplog.text
    )


def test_rerun_of_a_paused_use_case_completes_attachments_and_answers(tmp_path):
    save_items(tmp_path, uc_item(attachments=[{"key": "INTAKE", "version": 1}], answers_=[src_answer()]))
    ctx = target_ctx(tmp_path, existing=[{"id": "new-1", "name": "Chatbot"}])
    ctx.tracker("use_cases").record("uc1", "new-1", "Chatbot", "created")
    use_cases.load(ctx)
    assert [p for p, _ in ctx.target.posts] == [
        "use_cases/new-1/questionnaires",
        "use_cases/new-1/questionnaire_sections/es1/evidences",
    ]
    assert ctx.target_internal.patches == []


def test_matched_tracked_use_cases_are_never_touched(tmp_path):
    save_items(tmp_path, uc_item(attachments=[{"key": "INTAKE", "version": 1}], answers_=[src_answer()]))
    ctx = target_ctx(tmp_path, existing=[{"id": "t1", "name": "Chatbot"}])
    ctx.tracker("use_cases").record("uc1", "t1", "Chatbot", "matched")
    use_cases.load(ctx)
    assert ctx.target.posts == [] and ctx.target_internal.gets == []


def test_dry_run_writes_nothing_and_counts_like_a_live_run(tmp_path, caplog):
    def run(path, dry_run):
        save_items(
            path,
            uc_item(attachments=[{"key": "INTAKE", "version": 1}], answers_=[src_answer()]),
            uc_item("uc2", "Agent"),
            uc_item("uc3", "Taken"),
        )
        ctx = target_ctx(path, existing=[{"id": "t1", "name": "Taken"}], dry_run=dry_run)
        use_cases.load(ctx)
        return ctx

    live = run(tmp_path / "live", False)
    with caplog.at_level(logging.INFO, logger="kit.load"):
        dry = run(tmp_path / "dry", True)
    assert dry.target.posts == [] and dry.target_internal.patches == [] and dry.target_internal.posts == []
    assert dry.summary.to_dict() == live.summary.to_dict()
    assert not (tmp_path / "dry" / "loaded_use_cases.json").exists()
    assert "use_cases: answers: 1 would be written" in caplog.text
    assert "Triggers would be paused on 2 migrated use cases" in caplog.text


# --- load: parts ---


def record_writes(ctx):
    """Every write to the target, in order, across both clients."""
    writes = []
    for client, api in ((ctx.target, "integration"), (ctx.target_internal, "internal")):
        for method in ("post", "patch"):
            original = getattr(client, method)

            def wrapped(path, json, _original=original, _method=method, _api=api):
                writes.append((_api, _method.upper(), path))
                return _original(path, json)

            setattr(client, method, wrapped)
    return writes


def pack_versions(*pairs):
    return [{"id": f"v{n}", "attributes": {"version": n, "draft": draft}} for n, draft in pairs]


def scenario(
    name="Leak",
    catalog_id=None,
    likelihood=None,
    impact=None,
    residual=(None, None),
    controls=(),
    type_ids=(),
    type_names=(),
):
    return {
        "catalog_id": catalog_id,
        "name": name,
        "description": f"About {name}",
        "risk_type_ids": list(type_ids),
        "risk_type_names": list(type_names),
        "inherent_likelihood": likelihood,
        "inherent_impact": impact,
        "residual_likelihood": residual[0],
        "residual_impact": residual[1],
        "control_keys": list(controls),
    }


def full_item():
    return uc_item(
        stakeholders=[{"email": "helper@example.com", "role": "contributor"}],
        fields=[{"name": "Tier", "value": "Gold"}],
        stage_name="Governance",
        step="clear",
        risk_classification_level=2,
        policy_packs=[{"key": "PACK"}],
        risk_scenarios=[scenario(likelihood=3, impact=0, controls=["CTRL-1"])],
        controls=[{"key": "CTRL-1", "evidence": [{"hash_key": "h1", "data": {"value": "proof"}}]}],
        domain_ids=["nlp"],
        model_ids=["sm1"],
        vendor_ids=["v1"],
        vendor_names={"v1": "Vendor A"},
        attachments=[{"key": "INTAKE", "version": 1}],
        answers_=[src_answer()],
    )


def full_ctx(tmp_path, dry_run=False, **kwargs):
    ctx = target_ctx(
        tmp_path,
        dry_run=dry_run,
        fields=[{"id": "f1", "name": "Tier", "entity_type_ids": []}],
        integration={"policy_control_bases": [{"id": "CTRL-1"}], "vendors": [{"id": "v1", "name": "Vendor A"}]},
        internal={
            "policy_pack_bases/PACK/versions": pack_versions((1, False), (2, True)),
            "domains": [{"id": "nlp"}],
            "use_cases/new-1/controls": [
                {
                    "id": "c9",
                    "attributes": {
                        "control_key": "CTRL-1",
                        "compliances": [{"requirement_id": "h1", "evidence_ids": []}],
                    },
                }
            ],
        },
        bodies={"use_cases/new-1": {"custom_fields": []}},
        internal_bodies={"use_cases/new-1": target_uc_state("new-1", domains=["other"])},
        **kwargs,
    )
    ctx.tracker("models").record("sm1", "tm1", "Model A", "created")
    return ctx


def test_parts_are_written_in_order_for_one_use_case(tmp_path):
    save_items(tmp_path, full_item())
    ctx = full_ctx(tmp_path)
    writes = record_writes(ctx)
    use_cases.load(ctx)
    assert writes == [
        ("internal", "POST", "use_cases"),
        ("internal", "PATCH", "entities/e-new-1/trigger_mode"),
        ("integration", "PATCH", "use_cases/new-1"),
        ("internal", "PATCH", "use_cases/new-1/risk_classification"),
        ("integration", "POST", "use_cases/new-1/policy_packs"),
        ("integration", "POST", "use_cases/new-1/risk_scenarios"),
        ("integration", "POST", "use_cases/new-1/risk_scenarios/new-2/controls"),
        ("integration", "POST", "use_cases/new-1/controls/c9/evidences"),
        ("internal", "PATCH", "use_cases/new-1/relationships/domains"),
        ("integration", "POST", "use_cases/new-1/models"),
        ("integration", "POST", "use_cases/new-1/vendors"),
        ("internal", "POST", "use_cases/new-1/stakeholders"),
        ("integration", "POST", "use_cases/new-1/questionnaires"),
        ("integration", "POST", "use_cases/new-1/questionnaire_sections/es1/evidences"),
        ("internal", "PATCH", "use_cases/new-1/workflow_stage"),
        ("internal", "PATCH", "use_cases/new-1/step"),
    ]
    assert not ctx.summary.needs_attention


def test_parts_send_the_expected_bodies(tmp_path, caplog):
    save_items(tmp_path, full_item())
    ctx = full_ctx(tmp_path)
    with caplog.at_level(logging.DEBUG, logger="kit.load"):
        use_cases.load(ctx)
    posts, patches = dict(ctx.target.posts), dict(ctx.target_internal.patches)
    assert dict(ctx.target.patches)["use_cases/new-1"] == {"custom_fields": [{"name": "Tier", "value": "Gold"}]}
    assert patches["use_cases/new-1/risk_classification"] == {
        "data": {"type": "use_cases", "attributes": {"risk_classification_level": 2}}
    }
    assert posts["use_cases/new-1/policy_packs"] == {"key": "PACK", "version": 1}
    assert posts["use_cases/new-1/risk_scenarios"] == {
        "name": "Leak",
        "description": "About Leak",
        "inherent_risk_likelihood": 3,
    }
    assert posts["use_cases/new-1/risk_scenarios/new-2/controls"] == {"key": "CTRL-1"}
    assert posts["use_cases/new-1/controls/c9/evidences"] == {
        "control_requirement_id": "h1",
        "data": {"value": "proof"},
    }
    assert patches["use_cases/new-1/relationships/domains"] == {
        "data": [{"type": "domains", "id": "other"}, {"type": "domains", "id": "nlp"}]
    }
    assert posts["use_cases/new-1/models"] == {"id": "tm1"}
    assert posts["use_cases/new-1/vendors"] == {"id": "v1"}
    assert [x for x in ctx.target_internal.posts if x[0] != "use_cases"] == [
        (
            "use_cases/new-1/stakeholders",
            {"data": {"type": "stakeholders", "attributes": {"user_id": "tu2", "role": "contributor"}}},
        )
    ]
    assert patches["use_cases/new-1/workflow_stage"] == {
        "data": {"type": "use_cases", "attributes": {"workflow_stage_id": "st2"}}
    }
    assert patches["use_cases/new-1/step"] == {
        "data": {"type": "use_cases", "attributes": {"workflow_stage_step": "clear"}}
    }
    assert "proof" not in caplog.text and "Triage" not in caplog.text


def test_dry_run_of_every_part_writes_nothing_and_counts_like_a_live_run(tmp_path, caplog):
    save_items(tmp_path / "live", full_item())
    live = full_ctx(tmp_path / "live")
    use_cases.load(live)
    save_items(tmp_path / "dry", full_item())
    dry = full_ctx(tmp_path / "dry", dry_run=True)
    with caplog.at_level(logging.INFO, logger="kit.load"):
        use_cases.load(dry)
    assert dry.target.posts == [] and dry.target.patches == []
    assert dry.target_internal.posts == [] and dry.target_internal.patches == []
    assert dry.summary.to_dict() == live.summary.to_dict()
    assert not dry.summary.needs_attention
    for text in (
        "would set custom field(s) Tier",
        "would attach policy pack PACK+1",
        "would add risk scenario 'Leak'",
        "would link control CTRL-1",
        "would add domain(s) nlp",
        "would link vendor Vendor A",
        "would add user helper@example.com as contributor",
        "would attach questionnaire INTAKE+3",
        "would move 'Chatbot' to stage 'Governance'",
        "would set its step to 'clear'",
    ):
        assert text in caplog.text
    assert "use_cases: answers: 1 would be written" in caplog.text


def rerun_ctx(tmp_path, **kwargs):
    ctx = full_ctx(tmp_path, existing=[{"id": "new-1", "name": "Chatbot"}], **kwargs)
    ctx.tracker("use_cases").record("uc1", "new-1", "Chatbot", "created")
    return ctx


def test_rerun_after_a_complete_run_writes_nothing(tmp_path):
    save_items(tmp_path, full_item())
    ctx = rerun_ctx(tmp_path)
    ctx.target.json_bodies["use_cases/new-1"] = {"custom_fields": [cf("Tier", "Gold")]}
    ctx.target.collections.update(
        {
            "use_cases/new-1/policy_packs": [{"key": "PACK", "version": 1}],
            "use_cases/new-1/risk_scenarios": [
                {"id": "r9", "risk_scenario_id": None, "name": "Leak", "control_keys": ["CTRL-1"]}
            ],
            "use_cases/new-1/models": [{"id": "tm1"}],
            "use_cases/new-1/vendors": [{"id": "v1"}],
            "use_cases/new-1/questionnaires": [{"id": "a1", "key": "INTAKE", "version": 3}],
        }
    )
    ctx.target_internal.collections.update(
        {
            "use_cases/new-1/controls": [
                {
                    "id": "c9",
                    "attributes": {
                        "control_key": "CTRL-1",
                        "compliances": [{"requirement_id": "h1", "evidence_ids": ["ev1"]}],
                    },
                }
            ],
            "use_cases/new-1/stakeholders": [
                stakeholder("ks1", "owner", "tu1"),
                stakeholder("ks2", "contributor", "tu2"),
            ],
            "use_cases/new-1/evidences": [evidence("t1", "q1", "ts1", "Triage")],
        }
    )
    ctx.target_internal.json_bodies["use_cases/new-1"] = target_uc_state(
        "new-1", stage="st2", step="clear", level=2, domains=["nlp"]
    )
    use_cases.load(ctx)
    assert ctx.target.posts == [] and ctx.target.patches == []
    assert ctx.target_internal.posts == [] and ctx.target_internal.patches == []
    assert not ctx.summary.needs_attention


def test_rerun_leaves_other_values_and_a_later_stage_alone(tmp_path, caplog):
    save_items(
        tmp_path,
        uc_item(
            fields=[{"name": "Tier", "value": "Gold"}], risk_classification_level=2, stage_name="Intake", step="clear"
        ),
    )
    ctx = rerun_ctx(tmp_path)
    ctx.target.json_bodies["use_cases/new-1"] = {"custom_fields": [cf("Tier", "Silver")]}
    ctx.target_internal.json_bodies["use_cases/new-1"] = target_uc_state("new-1", stage="st2", level=3)
    with caplog.at_level(logging.INFO, logger="kit.load"):
        use_cases.load(ctx)
    assert ctx.target.patches == [] and ctx.target_internal.patches == []
    assert "Tier" in caplog.text and "level 3" in caplog.text and "already past stage 'Intake'" in caplog.text
    assert "Silver" not in caplog.text


def test_missing_references_are_reported_and_the_use_case_continues(tmp_path, caplog):
    item = uc_item(
        policy_packs=[{"key": "DRAFT-PACK"}],
        domain_ids=["gone"],
        model_ids=["sm9"],
        model_names={"sm9": "Old model"},
        stage_name="Archive",
        risk_scenarios=[
            scenario("Bias", catalog_id="cat9"),
            scenario(controls=["NOPE"], type_ids=["rt1", "rt2"], type_names=["Privacy", "unknown:rt2"]),
        ],
        controls=[{"key": "CTRL-9", "evidence": [{"hash_key": "h1", "data": {"value": "proof"}}]}],
        stakeholders=[{"email": "nobody@example.com", "role": "contributor"}],
    )
    save_items(tmp_path, item)
    ctx = target_ctx(tmp_path, internal={"policy_pack_bases/DRAFT-PACK/versions": pack_versions((1, True))})
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        use_cases.load(ctx)
    flagged = "\n".join(a["message"] for a in ctx.summary.attention)
    for text in (
        "policy pack DRAFT-PACK has no published version",
        "domain(s) gone aren't on the target",
        "model Old model (sm9) isn't tracked or on the target",
        "stage 'Archive' isn't in its workflow",
        "risk scenario 'Bias' (cat9) isn't in the target's catalog",
        "risk type 'Privacy' isn't on the target",
        "control NOPE isn't on the target",
        "control CTRL-9 isn't on the use case",
        "stakeholder user nobody@example.com (contributor) isn't on the target",
    ):
        assert text in flagged
    assert all("'Chatbot'" in a["message"] for a in ctx.summary.attention)
    assert "risk type ID rt2 doesn't exist on the source" in caplog.text
    assert ("use_cases/new-1/risk_scenarios", {"name": "Leak", "description": "About Leak"}) in ctx.target.posts
    assert ctx.summary.to_dict()["use_cases"]["created"] == 1


def test_risk_scenario_values_and_risk_types(tmp_path):
    save_items(
        tmp_path,
        uc_item(
            risk_scenarios=[
                scenario("A", likelihood=5, impact=6, type_ids=["rt1"], type_names=["Privacy"]),
                scenario("B", catalog_id="cat1", likelihood=1, impact=2),
                scenario("C", catalog_id="cat2"),
            ]
        ),
    )
    ctx = target_ctx(
        tmp_path,
        integration={"risk_scenarios": [{"id": "cat2"}], "risk_types": [{"id": "trt-builtin", "name": "Privacy"}]},
    )
    ctx.tracker("risk_scenarios").record("cat1", "tcat1", "B", "created")
    use_cases.load(ctx)
    assert [b for p, b in ctx.target.posts if p == "use_cases/new-1/risk_scenarios"] == [
        {"name": "A", "description": "About A", "risk_type_ids": ["trt-builtin"], "inherent_risk_likelihood": 5},
        {"risk_scenario_id": "tcat1", "inherent_risk_likelihood": 1, "inherent_risk_impact": 2},
        {"risk_scenario_id": "cat2"},
    ]


def test_risk_types_map_through_the_tracker_first(tmp_path):
    save_items(tmp_path, uc_item(risk_scenarios=[scenario(type_ids=["rt1"], type_names=["Privacy"])]))
    ctx = target_ctx(tmp_path)
    ctx.tracker("risk_types").record("rt1", "trt1", "Privacy", "created")
    use_cases.load(ctx)
    assert dict(ctx.target.posts)["use_cases/new-1/risk_scenarios"]["risk_type_ids"] == ["trt1"]


def test_residual_values_are_reported_not_sent(tmp_path):
    save_items(tmp_path, uc_item(risk_scenarios=[scenario(likelihood=4, impact=4, residual=(2, 4))]))
    ctx = target_ctx(tmp_path)
    use_cases.load(ctx)
    body = dict(ctx.target.posts)["use_cases/new-1/risk_scenarios"]
    assert not any(k.startswith("residual") for k in body)
    assert ctx.target.patches == []
    [flag] = ctx.summary.attention
    assert "residual likelihood 2 differs from inherent 4" in flag["message"]


def test_existing_scenarios_are_matched_by_catalog_id_and_name_and_only_missing_links_added(tmp_path):
    save_items(
        tmp_path,
        uc_item(
            risk_scenarios=[
                scenario("Bias", catalog_id="cat1", controls=["C1", "C2"]),
                scenario("Leak", controls=["C1"]),
            ]
        ),
    )
    ctx = rerun_ctx(tmp_path)
    ctx.target.collections.update(
        {
            "risk_scenarios": [{"id": "cat1"}],
            "policy_control_bases": [{"id": "C1"}, {"id": "C2"}],
            "use_cases/new-1/risk_scenarios": [
                {"id": "r1", "risk_scenario_id": "cat1", "name": "Bias", "control_keys": ["C1"]},
                {"id": "r2", "risk_scenario_id": None, "name": "Leak", "control_keys": ["C1"]},
            ],
        }
    )
    use_cases.load(ctx)
    assert ctx.target.posts == [("use_cases/new-1/risk_scenarios/r1/controls", {"key": "C2"})]


def test_control_evidence_skips_requirements_that_have_evidence(tmp_path, caplog):
    save_items(
        tmp_path,
        uc_item(
            controls=[
                {
                    "key": "C1",
                    "evidence": [
                        {"hash_key": "h1", "data": {"value": "one"}},
                        {"hash_key": "h2", "data": {"value": "two"}},
                        {"hash_key": "h3", "data": {"value": "three"}},
                    ],
                }
            ]
        ),
    )
    ctx = target_ctx(
        tmp_path,
        internal={
            "use_cases/new-1/controls": [
                {"id": "m-row", "attributes": {"control_key": "C1", "model_id": "m1", "compliances": []}},
                {
                    "id": "c1",
                    "attributes": {
                        "control_key": "C1",
                        "model_id": None,
                        "compliances": [
                            {"requirement_id": "h1", "evidence_ids": ["ev"]},
                            {"requirement_id": "h2", "evidence_ids": []},
                        ],
                    },
                },
            ]
        },
        post_responses={"use_cases/new-1/controls/c1/evidences": None},
    )
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        use_cases.load(ctx)
    assert [b for p, b in ctx.target.posts if p.endswith("/evidences")] == [
        {"control_requirement_id": "h2", "data": {"value": "two"}}
    ]
    assert "no evidence requirement h3" in caplog.text
    assert "three" not in caplog.text


def test_failed_control_evidence_logs_the_status_only(tmp_path, caplog):
    from tests.fakes import FakeResponse

    save_items(
        tmp_path, uc_item(controls=[{"key": "C1", "evidence": [{"hash_key": "h1", "data": {"value": "secret"}}]}])
    )
    ctx = target_ctx(
        tmp_path,
        internal={
            "use_cases/new-1/controls": [
                {
                    "id": "c1",
                    "attributes": {"control_key": "C1", "compliances": [{"requirement_id": "h1", "evidence_ids": []}]},
                }
            ]
        },
        post_responses={"use_cases/new-1/controls/c1/evidences": FakeResponse(422, text='{"value": "secret"}')},
    )
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        use_cases.load(ctx)
    assert "add evidence to control C1 (422)" in caplog.text and "secret" not in caplog.text
    assert ctx.summary.needs_attention


def test_domains_send_the_union_only_when_one_is_missing(tmp_path):
    save_items(tmp_path, uc_item(domain_ids=["nlp", "vision"]))
    ctx = rerun_ctx(tmp_path)
    ctx.target_internal.collections["domains"] = [{"id": "nlp"}, {"id": "vision"}, {"id": "x"}]
    ctx.target_internal.json_bodies["use_cases/new-1"] = target_uc_state("new-1", domains=["x", "nlp"])
    use_cases.load(ctx)
    assert ctx.target_internal.patches == [
        (
            "use_cases/new-1/relationships/domains",
            {
                "data": [
                    {"type": "domains", "id": "x"},
                    {"type": "domains", "id": "nlp"},
                    {"type": "domains", "id": "vision"},
                ]
            },
        )
    ]


def test_domains_created_by_the_kit_count_as_present(tmp_path):
    save_items(tmp_path, uc_item(domain_ids=["custom_one"]))
    ctx = target_ctx(tmp_path, dry_run=True)
    ctx.tracker("domains").record("custom_one", "dry:custom_one", "custom_one", "created")
    use_cases.load(ctx)
    assert not ctx.summary.needs_attention


def test_links_skip_current_ones(tmp_path):
    save_items(tmp_path, uc_item(model_ids=["sm1", "builtin"], vendor_ids=["sv1"]))
    ctx = rerun_ctx(tmp_path)
    ctx.tracker("vendors").record("sv1", "tv1", "Vendor", "created")
    ctx.target.collections.update(
        {"models": [{"id": "builtin"}], "use_cases/new-1/models": [{"id": "tm1"}], "use_cases/new-1/vendors": []}
    )
    use_cases.load(ctx)
    assert ctx.target.posts == [
        ("use_cases/new-1/models", {"id": "builtin"}),
        ("use_cases/new-1/vendors", {"id": "tv1"}),
    ]


def test_stakeholders_add_teams_patch_roles_and_leave_the_owner(tmp_path, caplog):
    save_items(
        tmp_path,
        uc_item(
            stakeholders=[
                {"team_name": "Reviewers", "role": "contributor"},
                {"email": "helper@example.com", "role": "contributor"},
                {"email": "owner@example.com", "role": "contributor"},
            ]
        ),
    )
    ctx = rerun_ctx(tmp_path)
    ctx.target_internal.collections["use_cases/new-1/stakeholders"] = [
        stakeholder("ks1", "owner", "tu1"),
        stakeholder("ks2", "observer", "tu2"),
    ]
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        use_cases.load(ctx)
    assert ctx.target_internal.posts == [
        (
            "use_cases/new-1/stakeholders",
            {"data": {"type": "stakeholders", "attributes": {"team_id": "tt1", "role": "contributor"}}},
        )
    ]
    assert ctx.target_internal.patches == [
        ("use_cases/new-1/stakeholders/ks2", {"data": {"type": "stakeholders", "attributes": {"role": "contributor"}}})
    ]
    assert "left them as owner" in caplog.text


def test_refused_observer_is_reported_not_changed(tmp_path, caplog):
    from tests.fakes import FakeResponse

    save_items(tmp_path, uc_item(stakeholders=[{"email": "helper@example.com", "role": "observer"}]))
    ctx = target_ctx(tmp_path)
    ctx.target_internal.post_responses = {"use_cases/new-1/stakeholders": FakeResponse(422, text="bad role")}
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        use_cases.load(ctx)
    assert [p for p, _ in ctx.target_internal.posts if p != "use_cases"] == ["use_cases/new-1/stakeholders"]
    assert "only read-only admin users can be observers" in caplog.text
    assert ctx.summary.needs_attention


def test_stage_is_found_by_name_from_the_use_case_own_stage(tmp_path):
    # Two workflows share stage names; only the use case's own chain counts
    save_items(tmp_path, uc_item(stage_name="Governance"))
    ctx = target_ctx(
        tmp_path,
        internal={
            "workflow_stages": [
                {"id": "x1", "attributes": {"name": "Intake", "parent_id": None, "child_id": "x2"}},
                {"id": "x2", "attributes": {"name": "Governance", "parent_id": "x1", "child_id": None}},
                {"id": "st1", "attributes": {"name": "Intake", "parent_id": None, "child_id": "st2"}},
                {"id": "st2", "attributes": {"name": "Governance", "parent_id": "st1", "child_id": None}},
            ]
        },
    )
    use_cases.load(ctx)
    assert ctx.target_internal.patches[1:] == [
        ("use_cases/new-1/workflow_stage", {"data": {"type": "use_cases", "attributes": {"workflow_stage_id": "st2"}}})
    ]


def test_step_alone_is_set_when_the_stage_already_matches(tmp_path):
    save_items(tmp_path, uc_item(stage_name="Intake", step="rejected"))
    ctx = target_ctx(tmp_path)
    use_cases.load(ctx)
    assert ctx.target_internal.patches[1:] == [
        ("use_cases/new-1/step", {"data": {"type": "use_cases", "attributes": {"workflow_stage_step": "rejected"}}})
    ]


# --- review fixes ---


def fail_reads(client, failing, method="get_all_cursor"):
    """Makes client.<method> raise a 500 for the paths in failing."""
    original = getattr(client, method)

    def read(path, params=None):
        if path in failing:
            raise APIError(f"GET {path} failed: 500", 500)
        return original(path, params)

    setattr(client, method, read)


def test_an_api_error_in_one_use_case_does_not_stop_the_next(tmp_path, caplog):
    save_items(
        tmp_path,
        uc_item(attachments=[{"key": "INTAKE", "version": 1}], answers_=[src_answer()]),
        uc_item("uc2", "Agent", attachments=[{"key": "INTAKE", "version": 1}], answers_=[src_answer()]),
    )
    ctx = target_ctx(tmp_path)
    fail_reads(ctx.target_internal, {"use_cases/new-1/evidences"})
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        use_cases.load(ctx)
    second = ctx.tracker("use_cases").target_id("uc2")
    assert f"use_cases/{second}/questionnaire_sections/es1/evidences" in [p for p, _ in ctx.target.posts]
    assert ctx.summary.needs_attention
    assert "'Chatbot'" in caplog.text and "500" in caplog.text


def test_an_api_error_on_a_rerun_does_not_stop_the_next(tmp_path, caplog):
    save_items(
        tmp_path,
        uc_item(attachments=[{"key": "INTAKE", "version": 1}], answers_=[src_answer()]),
        uc_item("uc2", "Agent"),
    )
    ctx = target_ctx(tmp_path, existing=[{"id": "new-1", "name": "Chatbot"}])
    ctx.tracker("use_cases").record("uc1", "new-1", "Chatbot", "created")
    fail_reads(ctx.target_internal, {"use_cases/new-1/evidences"})
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        use_cases.load(ctx)
    assert [b["name"] for b in uc_posts(ctx)] == ["Agent"]
    assert ctx.summary.needs_attention and "'Chatbot'" in caplog.text


def test_an_api_error_before_the_create_fails_only_that_use_case(tmp_path, caplog):
    save_items(tmp_path, uc_item(attachments=[{"key": "BROKEN", "version": 1}]), uc_item("uc2", "Agent"))
    ctx = target_ctx(tmp_path)
    fail_reads(ctx.target, {"questionnaires/BROKEN/versions"}, method="get_json")
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        use_cases.load(ctx)
    assert [b["name"] for b in uc_posts(ctx)] == ["Agent"]
    assert ctx.summary.to_dict()["use_cases"]["failed"] == 1
    assert "'Chatbot'" in caplog.text


def test_control_evidence_reads_every_page_of_the_target_controls(tmp_path):
    save_items(tmp_path, uc_item(controls=[{"key": "C2", "evidence": [{"hash_key": "h1", "data": {"value": "x"}}]}]))
    ctx = target_ctx(tmp_path)
    pages = [
        [{"id": "c1", "attributes": {"control_key": "C1", "compliances": []}}],
        [
            {
                "id": "c2",
                "attributes": {"control_key": "C2", "compliances": [{"requirement_id": "h1", "evidence_ids": []}]},
            }
        ],
    ]
    original_data, original_cursor = ctx.target_internal.get_data, ctx.target_internal.get_all_cursor

    def get_data(path, params=None):
        # The server pages this list; one GET returns only the first page
        return pages[0] if path == "use_cases/new-1/controls" else original_data(path, params)

    def get_all_cursor(path, params=None):
        return pages[0] + pages[1] if path == "use_cases/new-1/controls" else original_cursor(path, params)

    ctx.target_internal.get_data, ctx.target_internal.get_all_cursor = get_data, get_all_cursor
    use_cases.load(ctx)
    assert (
        "use_cases/new-1/controls/c2/evidences",
        {"control_requirement_id": "h1", "data": {"value": "x"}},
    ) in ctx.target.posts
    assert not ctx.summary.needs_attention


def test_dry_run_of_a_rerun_predicts_what_a_tracked_use_case_is_missing(tmp_path, caplog):
    save_items(tmp_path, uc_item(vendor_ids=["v1"], vendor_names={"v1": "Vendor A"}))
    ctx = target_ctx(
        tmp_path,
        existing=[{"id": "new-1", "name": "Chatbot"}],
        dry_run=True,
        integration={"vendors": [{"id": "v1", "name": "Vendor A"}], "use_cases/new-1/vendors": []},
    )
    ctx.tracker("use_cases").record("uc1", "new-1", "Chatbot", "created")
    with caplog.at_level(logging.INFO, logger="kit.load"):
        use_cases.load(ctx)
    assert "would link vendor Vendor A on 'Chatbot'" in caplog.text
    assert ctx.target.posts == [] and ctx.target_internal.patches == []


def test_domains_that_read_as_null_on_the_target_are_reported_not_replaced(tmp_path):
    save_items(tmp_path, uc_item(domain_ids=["nlp"]))
    ctx = rerun_ctx(tmp_path)
    state = target_uc_state("new-1")
    state["data"]["attributes"]["domain_ids"] = None
    ctx.target_internal.json_bodies["use_cases/new-1"] = state
    use_cases.load(ctx)
    assert not any(p.endswith("relationships/domains") for p, _ in ctx.target_internal.patches)
    assert any("current domains couldn't be read" in a["message"] for a in ctx.summary.attention)
    assert ("use_cases/new-1", {"include": "domain_links"}) in ctx.target_internal.gets


def test_extract_warns_when_the_source_domains_read_as_null(tmp_path, caplog):
    body = internal_uc("uc1")
    body["data"]["attributes"]["domain_ids"] = None
    ctx = source_ctx(tmp_path, [integration_uc("uc1", "Chatbot")], bodies={"use_cases/uc1": body})
    with caplog.at_level(logging.WARNING, logger="kit.extract"):
        use_cases.extract(ctx)
    assert load_extract(tmp_path, "use_cases")[0]["domain_ids"] == []
    assert "'Chatbot'" in caplog.text and "domains couldn't be read" in caplog.text


def test_second_custom_risk_scenario_with_the_same_name_is_reported_not_added(tmp_path):
    save_items(tmp_path, uc_item(risk_scenarios=[scenario("Leak", likelihood=2), scenario("Leak", likelihood=4)]))
    ctx = target_ctx(tmp_path)
    use_cases.load(ctx)
    assert [b for p, b in ctx.target.posts if p == "use_cases/new-1/risk_scenarios"] == [
        {"name": "Leak", "description": "About Leak", "inherent_risk_likelihood": 2}
    ]
    [flag] = ctx.summary.attention
    assert "more than one risk scenario named 'Leak'" in flag["message"]
