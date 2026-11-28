import logging

import pytest

from kit import answers
from kit.clients.base import APIError
from kit.clients.internal import InternalClient
from tests.fakes import CONFIG, FakeResponse, FakeSession, FakeTenant, make_ctx, no_sleep, token


class SpecTenant(FakeTenant):
    """Integration fake that serves questionnaire content per version (the plain fake ignores params)."""

    def __init__(self, collections=None, specs=None, **kwargs):
        super().__init__(collections, **kwargs)
        self.specs = specs or {}  # {(key, version): sections or Exception}
        self.spec_reads = []

    def get_json(self, path, params=None):
        if path.startswith("questionnaires/") and path.endswith("/spec"):
            key = path[len("questionnaires/") : -len("/spec")]
            self.spec_reads.append((key, params["version"]))
            sections = self.specs.get((key, params["version"]))
            if isinstance(sections, Exception):
                raise sections
            if sections is None:
                raise APIError(f"GET {path} failed: 404", 404)
            return {"sections": sections}
        return super().get_json(path, params)


def question(qid, text, evidence_type="text", multiple=False, options=()):
    return {
        "id": qid,
        "question": text,
        "description": None,
        "evidence_type": evidence_type,
        "select_options": list(options),
        "multiple": multiple,
        "hidden": False,
        "required": False,
        "alert_triggers": [],
    }


def section(sid, title, *questions):
    return {"id": sid, "title": title, "description": None, "questions": list(questions)}


def evidence(eid, qid, sid, value, evidence_type="text"):
    return {
        "id": eid,
        "type": "evidences",
        "attributes": {
            "type": evidence_type,
            "data": {"value": value},
            "metadata": {"question_id": qid, "section_id": sid},
        },
    }


INTAKE_1 = [section("s1", "About", question("q1", "Purpose?"), question("q2", "Region?"))]
ASSESS_2 = [
    section(
        "s9",
        "Risk",
        question("q9", "Data types?", "select", True, ["PII", "Health"]),
        question("q10", "Upload a diagram", "file"),
    )
]


def source_ctx(tmp_path, attachments, evidences, specs, internal=None):
    source = SpecTenant({"use_cases/uc1/questionnaires": attachments}, specs=specs, dry_run=True)
    internal = internal or FakeTenant({"use_cases/uc1/evidences": evidences}, dry_run=True, jsonapi=True)
    return make_ctx(tmp_path, source=source, source_internal=internal)


# --- extract ---


def test_extract_answers_reads_attachments_and_answers_with_their_content(tmp_path):
    ctx = source_ctx(
        tmp_path,
        attachments=[{"id": "a1", "key": "INTAKE", "version": 1}, {"id": "a2", "key": "ASSESS", "version": 2}],
        evidences=[evidence("e1", "q1", "s1", "Triage"), evidence("e2", "q9", "s9", ["PII"], "select")],
        specs={("INTAKE", 1): INTAKE_1, ("ASSESS", 2): ASSESS_2},
    )
    result = answers.extract_answers(ctx, "use_cases", "uc1")
    assert result["attachments"] == [{"key": "INTAKE", "version": 1}, {"key": "ASSESS", "version": 2}]
    assert result["answers"] == [
        {
            "key": "INTAKE",
            "version": 1,
            "section_title": "About",
            "question_id": "q1",
            "question_text": "Purpose?",
            "evidence_type": "text",
            "multiple": False,
            "value": "Triage",
        },
        {
            "key": "ASSESS",
            "version": 2,
            "section_title": "Risk",
            "question_id": "q9",
            "question_text": "Data types?",
            "evidence_type": "select",
            "multiple": True,
            "value": ["PII"],
        },
    ]


def test_extract_answers_reads_each_version_once_per_run(tmp_path):
    ctx = source_ctx(
        tmp_path,
        attachments=[{"id": "a1", "key": "INTAKE", "version": 1}],
        evidences=[evidence("e1", "q1", "s1", "Triage")],
        specs={("INTAKE", 1): INTAKE_1},
    )
    answers.extract_answers(ctx, "use_cases", "uc1")
    answers.extract_answers(ctx, "use_cases", "uc1")
    assert ctx.source.spec_reads == [("INTAKE", 1)]


def test_extract_answers_pages_through_evidences(tmp_path):
    session = FakeSession(
        token(),
        FakeResponse(200, {"data": [evidence("e1", "q1", "s1", "Triage")], "meta": {"after": "c2"}}),
        FakeResponse(200, {"data": [evidence("e2", "q2", "s1", "EU")], "meta": {"after": None}}),
    )
    internal = InternalClient(CONFIG, dry_run=True, session=session, sleep=no_sleep, read_only=True)
    ctx = source_ctx(
        tmp_path,
        attachments=[{"id": "a1", "key": "INTAKE", "version": 1}],
        evidences=None,
        specs={("INTAKE", 1): INTAKE_1},
        internal=internal,
    )
    result = answers.extract_answers(ctx, "use_cases", "uc1")
    assert [a["question_id"] for a in result["answers"]] == ["q1", "q2"]
    assert session.calls[1]["url"].endswith("/use_cases/uc1/evidences")
    assert session.calls[2]["params"]["page[after]"] == "c2"


def test_extract_answers_counts_and_leaves_out_file_answers(tmp_path, caplog):
    ctx = source_ctx(
        tmp_path,
        attachments=[{"id": "a2", "key": "ASSESS", "version": 2}],
        evidences=[
            evidence("e1", "q10", "s9", {"file_id": "f1"}, "file"),
            evidence("e2", "q9", "s9", ["PII"], "select"),
        ],
        specs={("ASSESS", 2): ASSESS_2},
    )
    with caplog.at_level(logging.WARNING, logger="kit.extract"):
        result = answers.extract_answers(ctx, "use_cases", "uc1", label="use case 'Chatbot'")
    assert [a["question_id"] for a in result["answers"]] == ["q9"]
    assert result["file_answers"] == 1
    assert "use case 'Chatbot'" in caplog.text and "1 file answer" in caplog.text


def test_extract_answers_skips_control_evidence_and_empty_values(tmp_path):
    control_evidence = {"id": "e9", "attributes": {"type": "text", "data": {"value": "proof"}, "metadata": {}}}
    ctx = source_ctx(
        tmp_path,
        attachments=[{"id": "a1", "key": "INTAKE", "version": 1}],
        evidences=[control_evidence, evidence("e1", "q1", "s1", ""), evidence("e2", "q2", "s1", "EU")],
        specs={("INTAKE", 1): INTAKE_1},
    )
    result = answers.extract_answers(ctx, "use_cases", "uc1")
    assert [a["question_id"] for a in result["answers"]] == ["q2"]


def test_extract_answers_warns_about_answers_outside_the_attached_versions(tmp_path, caplog):
    ctx = source_ctx(
        tmp_path,
        attachments=[{"id": "a1", "key": "INTAKE", "version": 1}],
        evidences=[evidence("e1", "gone", "s-old", "secret value")],
        specs={("INTAKE", 1): INTAKE_1},
    )
    with caplog.at_level(logging.WARNING, logger="kit.extract"):
        result = answers.extract_answers(ctx, "use_cases", "uc1")
    assert result["answers"] == []
    assert "1 answer" in caplog.text and "secret value" not in caplog.text


def test_extract_answers_uses_the_section_to_tell_versions_apart(tmp_path):
    # Two attached questionnaires that share a question ID (one copied from the other)
    copy = [section("s5", "Copy", question("q1", "Purpose, again?"))]
    ctx = source_ctx(
        tmp_path,
        attachments=[{"id": "a1", "key": "INTAKE", "version": 1}, {"id": "a3", "key": "COPY", "version": 1}],
        evidences=[evidence("e1", "q1", "s5", "Triage")],
        specs={("INTAKE", 1): INTAKE_1, ("COPY", 1): copy},
    )
    [answer] = answers.extract_answers(ctx, "use_cases", "uc1")["answers"]
    assert (answer["key"], answer["section_title"], answer["question_text"]) == ("COPY", "Copy", "Purpose, again?")


def test_extract_answers_raises_naming_the_version_it_cannot_read(tmp_path):
    ctx = source_ctx(
        tmp_path,
        attachments=[{"id": "a1", "key": "INTAKE", "version": 3}],
        evidences=[],
        specs={("INTAKE", 3): APIError("GET questionnaires/INTAKE/spec failed: 500", 500)},
    )
    with pytest.raises(APIError, match=r"INTAKE\+3"):
        answers.extract_answers(ctx, "use_cases", "uc1")


def test_extract_answers_skips_a_version_the_source_cannot_return(tmp_path, caplog):
    # An archived questionnaire can list a use case on a version its versions list no longer has (404)
    ctx = source_ctx(
        tmp_path,
        attachments=[{"id": "a1", "key": "INTAKE", "version": 1}, {"id": "a2", "key": "CLASS", "version": 109}],
        evidences=[evidence("e1", "q1", "s1", "Triage"), evidence("e2", "qc", "sc", "secret value")],
        specs={("INTAKE", 1): INTAKE_1},
    )
    with caplog.at_level(logging.WARNING, logger="kit.extract"):
        result = answers.extract_answers(ctx, "use_cases", "uc1", label="use_cases: 'A'")
    assert [a["question_id"] for a in result["answers"]] == ["q1"]
    assert len(result["attachments"]) == 2
    assert "use_cases: 'A': questionnaire CLASS+109 is attached but the source can't return that version" in caplog.text
    assert "secret value" not in caplog.text


def test_extract_answers_raises_on_an_attachment_without_key_or_version(tmp_path):
    ctx = source_ctx(tmp_path, attachments=[{"id": "a1", "key": "INTAKE"}], evidences=[], specs={})
    with pytest.raises(APIError, match="attachment"):
        answers.extract_answers(ctx, "use_cases", "uc1")


# --- mapping ---


def answer(
    qid="q1",
    text="Purpose?",
    title="About",
    value="Triage",
    evidence_type="text",
    multiple=False,
    key="INTAKE",
    version=1,
):
    return {
        "key": key,
        "version": version,
        "section_title": title,
        "question_id": qid,
        "question_text": text,
        "evidence_type": evidence_type,
        "multiple": multiple,
        "value": value,
    }


def test_map_answer_same_question_id_and_text():
    target = [section("t1", "Renamed", question("q1", "Purpose?"))]
    mapped, reason = answers.map_answer(answer(), target)
    assert reason is None
    assert mapped == {"section_id": "t1", "question_id": "q1", "question_text": "Purpose?", "value": "Triage"}


def test_map_answer_same_id_with_different_text_falls_back_to_section_and_text():
    target = [section("t1", "About", question("q1", "Something else?"), question("n1", "Purpose?"))]
    mapped, _ = answers.map_answer(answer(), target)
    assert mapped["question_id"] == "n1"


def test_map_answer_by_section_title_and_text_when_ids_differ():
    target = [section("t1", "Other", question("x1", "Purpose?")), section("t2", "About", question("x2", "Purpose?"))]
    mapped, _ = answers.map_answer(answer(), target)
    assert (mapped["section_id"], mapped["question_id"]) == ("t2", "x2")


def test_map_answer_by_unique_text_alone():
    target = [section("t1", "Renamed", question("x1", " Purpose? "))]
    mapped, _ = answers.map_answer(answer(), target)
    assert mapped["question_id"] == "x1"


def test_map_answer_question_not_found_when_text_is_ambiguous_or_missing():
    target = [section("t1", "A", question("x1", "Purpose?")), section("t2", "B", question("x2", "Purpose?"))]
    assert answers.map_answer(answer(), target) == (None, "question not found")
    assert answers.map_answer(answer(), []) == (None, "question not found")


def test_map_answer_text_to_single_select_matches_an_option_after_trimming():
    target = [section("t1", "About", question("x1", "Purpose?", "select", options=["Triage", "Billing"]))]
    mapped, reason = answers.map_answer(answer(value="  Triage "), target)
    assert (mapped["value"], reason) == ("Triage", None)
    assert answers.map_answer(answer(value="Other"), target) == (None, "value isn't an option")


def test_map_answer_text_to_multi_select_splits_on_semicolons():
    target = [section("t1", "About", question("x1", "Purpose?", "select", True, ["Triage", "Billing", "Other"]))]
    mapped, _ = answers.map_answer(answer(value="Triage; Billing;"), target)
    assert mapped["value"] == ["Triage", "Billing"]
    assert answers.map_answer(answer(value="Triage; Nope"), target) == (None, "value isn't an option")


def test_map_answer_same_type_copies():
    target = [section("t1", "About", question("x1", "Purpose?", "select", True, ["A", "B"]))]
    mapped, _ = answers.map_answer(answer(value=["A", "B"], evidence_type="select", multiple=True), target)
    assert mapped["value"] == ["A", "B"]


def test_map_answer_other_type_changes_are_skipped_with_a_reason():
    target = [section("t1", "About", question("x1", "Purpose?", "user"))]
    assert answers.map_answer(answer(value="Pat Doe"), target) == (
        None,
        "answer to a user question (users differ between tenants)",
    )
    target = [section("t1", "About", question("x1", "Purpose?", "select", True, ["A"]))]
    _, reason = answers.map_answer(answer(value="Z", evidence_type="select"), target)
    assert reason == "single select answer to a multi-select question"
    target = [section("t1", "About", question("x1", "Purpose?", "select", False, ["A", "B"]))]
    _, reason = answers.map_answer(answer(value=["A", "B"], evidence_type="select", multiple=True), target)
    assert reason == "multi-select answer to a single select question"


def test_map_answer_one_selection_moves_between_single_and_multi_select():
    multi = [section("t1", "About", question("x1", "Purpose?", "select", True, ["A", "B"]))]
    mapped, _ = answers.map_answer(answer(value="A", evidence_type="select"), multi)
    assert mapped["value"] == ["A"]
    single = [section("t1", "About", question("x1", "Purpose?", "select", False, ["A", "B"]))]
    mapped, _ = answers.map_answer(answer(value=["B"], evidence_type="select", multiple=True), single)
    assert mapped["value"] == "B"


# --- write ---

TARGET_INTAKE_2 = [
    section(
        "ts1",
        "About",
        question("q1", "Purpose?"),
        question("n2", "Region?", "select", options=["EU", "US"]),
        question("n3", "Owner name?", "user"),
    )
]


def target_ctx(tmp_path, current=(), attachments=None, dry_run=False, post_responses=None, entity_sections=None):
    target = SpecTenant(
        {
            "use_cases/uc9/questionnaires": attachments
            if attachments is not None
            else [{"id": "a1", "key": "INTAKE", "version": 2}]
        },
        specs={("INTAKE", 2): TARGET_INTAKE_2},
        post_responses=post_responses,
    )
    internal = FakeTenant(
        {
            "use_cases/uc9/evidences": list(current),
            "use_cases/uc9/questionnaire_sections": entity_sections
            if entity_sections is not None
            else [{"id": "es1", "attributes": {"section_id": "ts1"}}],
        },
        jsonapi=True,
    )
    return make_ctx(tmp_path, target=target, target_internal=internal, dry_run=dry_run)


SOURCE_ANSWERS = [
    answer(),  # same ID: missing on the target, written
    answer("q2", "Region?", value=" EU "),  # text to select: written as "EU"
    answer("q3", "Owner name?", value="Pat Doe"),  # text to user: unmapped
    answer("q4", "Gone?", value="x"),  # not in the target version: unmapped
    answer("q5", "Other?", key="ASSESS", value="y"),  # questionnaire not attached on the target: unmapped
]


def test_write_answers_writes_only_missing_answers(tmp_path, caplog):
    ctx = target_ctx(tmp_path)
    with caplog.at_level(logging.DEBUG, logger="kit.load"):
        counts = answers.write_answers(ctx, "use_cases", "uc9", SOURCE_ANSWERS, label="use case 'Chatbot'")
    assert counts == {"written": 2, "present": 0, "conflicts": 0, "unmapped": 3, "failed": 0}
    assert ctx.target.posts == [
        ("use_cases/uc9/questionnaire_sections/es1/evidences", {"question_id": "q1", "data": {"value": "Triage"}}),
        ("use_cases/uc9/questionnaire_sections/es1/evidences", {"question_id": "n2", "data": {"value": "EU"}}),
    ]
    assert "'Owner name?'" in caplog.text and "answer to a user question (users differ between tenants)" in caplog.text
    assert "'Gone?'" in caplog.text and "ASSESS" in caplog.text
    for value in ("Triage", "Pat Doe"):
        assert value not in caplog.text


def test_write_answers_skips_equal_and_warns_on_different_values(tmp_path, caplog):
    current = [evidence("t1", "q1", "ts1", "Triage "), evidence("t2", "n2", "ts1", "US", "select")]
    ctx = target_ctx(tmp_path, current=current)
    with caplog.at_level(logging.WARNING, logger="kit.load"):
        counts = answers.write_answers(ctx, "use_cases", "uc9", SOURCE_ANSWERS[:2], label="use case 'Chatbot'")
    assert counts == {"written": 0, "present": 1, "conflicts": 1, "unmapped": 0, "failed": 0}
    assert ctx.target.posts == []
    assert "'Region?'" in caplog.text and "'US'" not in caplog.text


def test_write_answers_compares_lists_in_any_order(tmp_path):
    target = [section("ts1", "About", question("m1", "Data?", "select", True, ["A", "B"]))]
    ctx = target_ctx(tmp_path, current=[evidence("t1", "m1", "ts1", ["B", "A"], "select")])
    ctx.target.specs[("INTAKE", 2)] = target
    counts = answers.write_answers(
        ctx, "use_cases", "uc9", [answer("m1", "Data?", value=["A", "B"], evidence_type="select", multiple=True)]
    )
    assert counts["present"] == 1


def test_write_answers_counts_a_second_answer_to_the_same_target_question(tmp_path):
    # Two source questions with the same text land on one target question
    ctx = target_ctx(tmp_path)
    counts = answers.write_answers(ctx, "use_cases", "uc9", [answer(), answer("old", "Purpose?", value="Other")])
    assert counts == {"written": 1, "present": 0, "conflicts": 1, "unmapped": 0, "failed": 0}


def test_write_answers_dry_run_writes_nothing_with_the_live_counts(tmp_path):
    live = answers.write_answers(target_ctx(tmp_path / "live"), "use_cases", "uc9", SOURCE_ANSWERS)
    ctx = target_ctx(tmp_path / "dry", dry_run=True)
    assert answers.write_answers(ctx, "use_cases", "uc9", SOURCE_ANSWERS) == live
    assert ctx.target.posts == []


def test_write_answers_dry_run_on_an_entity_that_does_not_exist_yet(tmp_path):
    # In a dry run a new use case has a dry: ID; the caller passes what would be attached
    live = answers.write_answers(target_ctx(tmp_path / "live"), "use_cases", "uc9", SOURCE_ANSWERS)
    ctx = target_ctx(tmp_path / "dry", dry_run=True)
    counts = answers.write_answers(ctx, "use_cases", "dry:uc1", SOURCE_ANSWERS, attachments={"INTAKE": 2})
    assert counts == live
    assert ctx.target.posts == [] and ctx.target_internal.gets == []


def test_write_answers_uses_given_attachments_instead_of_reading_them(tmp_path):
    ctx = target_ctx(tmp_path, attachments=[])
    counts = answers.write_answers(ctx, "use_cases", "uc9", [answer()], attachments={"INTAKE": 2})
    assert counts["written"] == 1


def test_write_answers_a_failed_post_is_counted_and_the_rest_continue(tmp_path, caplog):
    ctx = target_ctx(tmp_path)

    calls = []
    original = ctx.target.post

    def post(path, json):
        calls.append(json["question_id"])
        if len(calls) == 1:
            return FakeResponse(422, text="rejected")
        return original(path, json)

    ctx.target.post = post
    with caplog.at_level(logging.ERROR, logger="kit.load"):
        counts = answers.write_answers(ctx, "use_cases", "uc9", SOURCE_ANSWERS[:2], label="use case 'Chatbot'")
    assert counts == {"written": 1, "present": 0, "conflicts": 0, "unmapped": 0, "failed": 1}
    assert calls == ["q1", "n2"]
    assert "'Purpose?'" in caplog.text and "422" in caplog.text and "Triage" not in caplog.text


def test_write_answers_an_api_error_is_counted_and_the_rest_continue(tmp_path):
    ctx = target_ctx(tmp_path)
    original = ctx.target.post

    def post(path, json):
        if json["question_id"] == "q1":
            raise APIError("POST lost its connection")
        return original(path, json)

    ctx.target.post = post
    counts = answers.write_answers(ctx, "use_cases", "uc9", SOURCE_ANSWERS[:2])
    assert (counts["written"], counts["failed"]) == (1, 1)


def test_write_answers_section_missing_on_the_entity_fails_that_answer(tmp_path):
    ctx = target_ctx(tmp_path, entity_sections=[])
    counts = answers.write_answers(ctx, "use_cases", "uc9", [answer()])
    assert (counts["written"], counts["failed"]) == (0, 1)
    assert ctx.target.posts == []


def test_report_totals_logs_one_line_and_flags_what_needs_a_hand_fix(tmp_path, caplog):
    ctx = make_ctx(tmp_path)
    totals = answers.add_counts(answers.add_counts(None, {"written": 2, "unmapped": 1}), {"written": 1, "failed": 1})
    assert totals == {"written": 3, "present": 0, "conflicts": 0, "unmapped": 1, "failed": 1}
    with caplog.at_level(logging.INFO, logger="kit.load"):
        answers.report_totals(ctx, "use_cases", totals)
    assert "use_cases: answers: 3 written, 0 already there, 0 conflicts, 1 unmapped, 1 failed" in caplog.text
    assert ctx.summary.needs_attention


def test_report_totals_does_not_flag_a_clean_run(tmp_path):
    ctx = make_ctx(tmp_path)
    answers.report_totals(ctx, "use_cases", {"written": 3, "present": 2})
    assert not ctx.summary.needs_attention


def test_write_answers_plan_counts_like_a_write_but_writes_and_logs_nothing(tmp_path, caplog):
    live = answers.write_answers(target_ctx(tmp_path / "live"), "use_cases", "uc9", SOURCE_ANSWERS)
    ctx = target_ctx(tmp_path / "plan")
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="kit.load"):
        assert answers.write_answers(ctx, "use_cases", "uc9", SOURCE_ANSWERS, plan=True) == live
    assert ctx.target.posts == []
    assert caplog.text == ""


@pytest.mark.parametrize("source_type,target_type", [("user", "user"), ("user", "text")])
def test_map_answer_never_copies_an_answer_to_a_user_question(source_type, target_type):
    target = [section("t1", "About", question("x1", "Owner?", target_type))]
    source = answer(text="Owner?", value="source-user-id", evidence_type=source_type)
    assert answers.map_answer(source, target) == (None, "answer to a user question (users differ between tenants)")


def test_write_answers_keys_current_answers_by_section_and_question(tmp_path):
    # Two attached questionnaires share question ID q1; the target has answered only the first one's
    ctx = target_ctx(
        tmp_path,
        current=[evidence("t1", "q1", "ts1", "Triage")],
        attachments=[{"id": "a1", "key": "INTAKE", "version": 2}, {"id": "a2", "key": "RISK", "version": 1}],
        entity_sections=[
            {"id": "es1", "attributes": {"section_id": "ts1"}},
            {"id": "es2", "attributes": {"section_id": "rs1"}},
        ],
    )
    ctx.target.specs[("RISK", 1)] = [section("rs1", "Risk", question("q1", "Purpose?"))]
    counts = answers.write_answers(ctx, "use_cases", "uc9", [answer(), answer(key="RISK", title="Risk", value="Other")])
    assert counts["present"] == 1 and counts["written"] == 1 and counts["conflicts"] == 0
    assert ctx.target.posts == [
        ("use_cases/uc9/questionnaire_sections/es2/evidences", {"question_id": "q1", "data": {"value": "Other"}})
    ]
