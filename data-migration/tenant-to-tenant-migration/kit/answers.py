"""Questionnaire answers on use cases, models and vendors: read on the source, mapped onto the target's attached
version, written only where the target has no answer.

Attachments (which version of each questionnaire an entity has) and answer writes go through the Integration API.
Answers are read through the internal API's evidences list, because each evidence there names its questionnaire
section (metadata.section_id) as well as its question, which tells apart two attached questionnaires that share a
question ID. The answer POST needs the entity's own section ID, which only the internal API lists.

A source entity often has an older version attached than the one the kit copied, and versions can have different
question IDs. So an answer is matched to the target version by question ID (with the same question text), else by
section title and question text, else by question text alone if it's unique in that version. A text answer to what
is now a select question is converted only when its value is an option. Logs name questions, never answer values.
"""

import logging

from kit.clients.base import APIError
from kit.internal.questionnaires import read_sections

KINDS = ("use_cases", "models", "vendors")
TEXT_TYPES = ("text", "short_text")
COUNTS = ("written", "present", "conflicts", "unmapped", "failed")
extract_log = logging.getLogger("kit.extract")
load_log = logging.getLogger("kit.load")


def _check_kind(entity_kind):
    if entity_kind not in KINDS:
        raise ValueError(f"entity_kind must be one of {KINDS}, got {entity_kind!r}")


def read_attachments(client, entity_kind, entity_id):
    """[{"key", "version"}] of the questionnaires attached to an entity (Integration API)."""
    _check_kind(entity_kind)
    path = f"{entity_kind}/{entity_id}/questionnaires"
    attachments = []
    for a in client.get_all(path):
        if not isinstance(a, dict) or not a.get("key") or not isinstance(a.get("version"), int):
            raise APIError(f"GET {path}: an attachment came back without a key or version", body=str(a)[:300])
        attachments.append({"key": a["key"], "version": a["version"]})
    return attachments


def version_content(ctx, side, key, version):
    """The sections of questionnaire version KEY+N on the source or target, read once per run."""
    cache_key = (side, key, version)
    if cache_key not in ctx.questionnaire_content:
        client = ctx.source if side == "source" else ctx.target
        try:
            ctx.questionnaire_content[cache_key] = read_sections(client, key, version)
        except APIError as e:
            raise APIError(
                f"reading questionnaire {key}+{version} on the {side} failed: {e}", e.status_code, e.body
            ) from e
    return ctx.questionnaire_content[cache_key]


def _question_index(ctx, attachments, label):
    """{(section ID, question ID): place} and {question ID: first place} over the attached versions' content.

    A version the source can't return (404, as when an archived questionnaire lost it) is skipped with a warning:
    its answers then count as belonging to no attached version."""
    by_section, by_question = {}, {}
    for a in attachments:
        try:
            sections = version_content(ctx, "source", a["key"], a["version"])
        except APIError as e:
            if e.status_code != 404:
                raise
            extract_log.warning(
                f"{label}: questionnaire {a['key']}+{a['version']} is attached but the source can't "
                f"return that version (404), so its answers can't be read and aren't copied"
            )
            continue
        for s in sections:
            for q in s.get("questions") or []:
                place = (a, s, q)
                by_section.setdefault((s["id"], q["id"]), place)
                by_question.setdefault(q["id"], place)
    return by_section, by_question


def extract_answers(ctx, entity_kind, entity_id, label=None):
    """A source entity's questionnaire attachments and answers.

    Returns {"attachments": [{key, version}], "answers": [{key, version, section_title, question_id, question_text,
    evidence_type, multiple, value}], "file_answers": count}. File answers aren't copied; they are counted and
    reported. Empty answers and control evidence (no question) are left out.
    """
    label = label or f"{entity_kind} {entity_id}"
    attachments = read_attachments(ctx.source, entity_kind, entity_id)
    by_section, by_question = _question_index(ctx, attachments, label)
    items, files, outside = [], 0, 0
    for ev in ctx.source_internal.get_all_cursor(f"{entity_kind}/{entity_id}/evidences"):
        attrs = ev.get("attributes") or {}
        meta = attrs.get("metadata") or {}
        question_id = meta.get("question_id")
        if not question_id or attrs.get("active") is False:
            continue  # control evidence, or an answer that was replaced
        place = by_section.get((meta.get("section_id"), question_id)) or by_question.get(question_id)
        if place is None:
            outside += 1
            continue
        attachment, sec, q = place
        if attrs.get("type") == "file" or q.get("evidence_type") == "file":
            files += 1
            continue
        value = (attrs.get("data") or {}).get("value")
        if value is None or value == "" or value == []:
            continue
        items.append(
            {
                "key": attachment["key"],
                "version": attachment["version"],
                "section_title": sec.get("title"),
                "question_id": question_id,
                "question_text": q.get("question"),
                "evidence_type": q.get("evidence_type"),
                "multiple": bool(q.get("multiple")),
                "value": value,
            }
        )
    if files:
        extract_log.warning(
            f"{label}: {files} file answer(s) aren't copied (the kit doesn't copy files); add them "
            f"on the target by hand"
        )
    if outside:
        extract_log.warning(
            f"{label}: {outside} answer(s) belong to no questionnaire version attached to it, so they aren't copied"
        )
    extract_log.debug(f"{label}: {len(items)} answer(s) in {len(attachments)} questionnaire(s)")
    return {"attachments": attachments, "answers": items, "file_answers": files}


# --- mapping ---


def _text(value):
    """Question text and titles compared with surrounding and repeated spaces ignored."""
    return " ".join(str(value or "").split())


def _type_name(evidence_type, multiple):
    if evidence_type == "select":
        return "multi-select" if multiple else "single select"
    return f"{evidence_type} (multiple)" if multiple else str(evidence_type)


def _find_question(answer, target_sections):
    """(section, question) on the target version for a source answer, or None."""
    text = _text(answer["question_text"])
    pairs = [(s, q) for s in target_sections for q in s.get("questions") or []]
    same_id = [(s, q) for s, q in pairs if q.get("id") == answer["question_id"] and _text(q.get("question")) == text]
    if same_id:
        return same_id[0]
    same_text = [(s, q) for s, q in pairs if _text(q.get("question")) == text]
    in_section = [(s, q) for s, q in same_text if _text(s.get("title")) == _text(answer["section_title"])]
    for candidates in (in_section, same_text):
        if len(candidates) == 1:
            return candidates[0]
    return None


def _convert(answer, question):
    """(value for the target question, None) or (None, why it can't be copied)."""
    source_type, target_type = answer["evidence_type"], question.get("evidence_type")
    source_multiple, target_multiple = bool(answer.get("multiple")), bool(question.get("multiple"))
    value = answer["value"]
    if "user" in (source_type, target_type):
        # A user answer holds user IDs, which differ between tenants
        return None, "answer to a user question (users differ between tenants)"
    if source_type == target_type and source_multiple == target_multiple:
        return value, None
    if source_type in TEXT_TYPES and target_type == "select" and isinstance(value, str):
        options = {}
        for option in question.get("select_options") or []:
            options.setdefault(str(option).strip(), option)
        if target_multiple:
            parts = [p.strip() for p in value.split(";") if p.strip()]
            if parts and all(p in options for p in parts):
                return [options[p] for p in parts], None
        elif value.strip() in options:
            return options[value.strip()], None
        return None, "value isn't an option"
    if source_type == target_type == "select" and source_multiple != target_multiple:
        # One selection fits either way: a list of one for a multi-select, the single item for a single select
        options = set(question.get("select_options") or [])
        if target_multiple and not isinstance(value, list) and value in options:
            return [value], None
        if not target_multiple and isinstance(value, list) and len(value) == 1 and value[0] in options:
            return value[0], None
    return (
        None,
        f"{_type_name(source_type, source_multiple)} answer to a {_type_name(target_type, target_multiple)} question",
    )


def map_answer(answer, target_sections):
    """Maps one extracted answer onto the sections of the target's attached version.

    Returns ({"section_id", "question_id", "question_text", "value"}, None), or (None, reason) when it can't be copied.
    """
    found = _find_question(answer, target_sections)
    if found is None:
        return None, "question not found"
    sec, q = found
    value, reason = _convert(answer, q)
    if reason:
        return None, reason
    return {"section_id": sec["id"], "question_id": q["id"], "question_text": q.get("question"), "value": value}, None


# --- write ---


def _same(a, b):
    def norm(value):
        if isinstance(value, list):
            return sorted(str(v).strip() for v in value)
        return value.strip() if isinstance(value, str) else value

    return norm(a) == norm(b)


def _current_answers(internal, entity_kind, entity_id):
    """{(questionnaire section ID, question ID): value} of the target entity's answers. Two attached questionnaires
    can share a question ID, so the section tells them apart."""
    current = {}
    for ev in internal.get_all_cursor(f"{entity_kind}/{entity_id}/evidences"):
        attrs = ev.get("attributes") or {}
        meta = attrs.get("metadata") or {}
        question_id = meta.get("question_id")
        if question_id and attrs.get("active") is not False:
            current[(meta.get("section_id"), question_id)] = (attrs.get("data") or {}).get("value")
    return current


def _entity_sections(internal, entity_kind, entity_id):
    """{questionnaire section ID: the entity's own section ID}, which the answer POST takes."""
    return {
        (s.get("attributes") or {}).get("section_id"): s["id"]
        for s in internal.get_all_cursor(f"{entity_kind}/{entity_id}/questionnaire_sections")
    }


def empty_counts():
    return dict.fromkeys(COUNTS, 0)


def add_counts(totals, counts):
    """totals + counts, key by key. totals may be None."""
    totals = dict(totals or empty_counts())
    for k in COUNTS:
        totals[k] = totals.get(k, 0) + counts.get(k, 0)
    return totals


class _Silent:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


_SILENT = _Silent()


def write_answers(ctx, entity_kind, target_id, item_answers, attachments=None, label=None, plan=False):
    """Writes the answers the target entity doesn't have. Never overwrites one.

    attachments: {key: version} attached on the target. Read from the target when None; a caller that has just
    attached questionnaires in a dry run (or to an entity a dry run would create) passes what would be attached.
    plan: count only, writing and logging nothing, so a caller can tell whether anything needs writing.
    Returns {"written", "present", "conflicts", "unmapped", "failed"}. In a dry run or plan "written" counts what
    would be.
    """
    log = _SILENT if plan else load_log
    _check_kind(entity_kind)
    label = label or f"{entity_kind} {target_id}"
    counts = empty_counts()
    exists = not str(target_id).startswith("dry:")
    if attachments is None:
        attachments = {a["key"]: a["version"] for a in read_attachments(ctx.target, entity_kind, target_id)}
    current = _current_answers(ctx.target_internal, entity_kind, target_id) if exists else {}
    sections = None  # the entity's section IDs, read before the first write
    for answer in item_answers:
        question = answer["question_text"]
        key = answer["key"]
        if key not in attachments:
            log.warning(f"{label}: answer to {question!r} not copied: questionnaire {key} isn't attached on the target")
            counts["unmapped"] += 1
            continue
        mapped, reason = map_answer(answer, version_content(ctx, "target", key, attachments[key]))
        if mapped is None:
            log.warning(f"{label}: answer to {question!r} ({key}) not copied: {reason}")
            counts["unmapped"] += 1
            continue
        target_question = mapped["question_id"]
        place = (mapped["section_id"], target_question)
        if place in current:
            if _same(current[place], mapped["value"]):
                counts["present"] += 1
            else:
                log.warning(
                    f"{label}: the target already has a different answer to {question!r} ({key}); left it as is"
                )
                counts["conflicts"] += 1
            continue
        if ctx.dry_run or plan:
            log.debug(f"{label}: would answer {question!r} ({key})")
            current[place] = mapped["value"]
            counts["written"] += 1
            continue
        if sections is None:
            sections = _entity_sections(ctx.target_internal, entity_kind, target_id)
        entity_section = sections.get(mapped["section_id"])
        if entity_section is None:
            log.error(f"{label}: answering {question!r} ({key}) failed: its section isn't on the target entity")
            counts["failed"] += 1
            continue
        path = f"{entity_kind}/{target_id}/questionnaire_sections/{entity_section}/evidences"
        try:
            resp = ctx.target.post(path, {"question_id": target_question, "data": {"value": mapped["value"]}})
            # The status only: an error body can repeat the value
            problem = None if resp is None or 200 <= resp.status_code < 300 else resp.status_code
        except APIError as e:
            problem = e.status_code or "lost connection"
        if problem is not None:
            log.error(f"{label}: answering {question!r} ({key}) failed ({problem}); a re-run tries it again")
            counts["failed"] += 1
            continue
        current[place] = mapped["value"]
        counts["written"] += 1
    log.debug(f"{label}: answers {counts}")
    return counts


def report_totals(ctx, entity, totals):
    """One summary line of answer counts for an entity type. Flags the run when answers need a fix by hand."""
    t = add_counts(None, totals)
    verb = "would be written" if ctx.dry_run else "written"
    line = (
        f"{entity}: answers: {t['written']} {verb}, {t['present']} already there, {t['conflicts']} conflicts, "
        f"{t['unmapped']} unmapped, {t['failed']} failed"
    )
    needs_hand = t["conflicts"] or t["unmapped"] or t["failed"]
    (load_log.warning if needs_hand else load_log.info)(line)
    if needs_hand:
        ctx.summary.flag(
            entity,
            f"{t['unmapped']} answers couldn't be mapped, {t['conflicts']} differ from the "
            f"target's and {t['failed']} failed; the log names each question",
        )
