"""Questionnaire triggers and their actions.

Lives in kit/internal/ because actions go through the internal API: the Integration API's action endpoints drop
show_alert, can't create add_questionnaire actions and reject risk_type_ids. The version check below uses it too.
Triggers themselves are listed through the Integration API, which returns them all at once with their conditions.
"""

import json
import logging
from pathlib import Path
from urllib.parse import quote

from kit.clients.base import APIError
from kit.internal.questionnaires import read_sections, read_versions
from kit.loading import LoadStopped, create_target, data_id
from kit.tracking import load_extract, save_extract

ENTITY = "triggers"
ACTIONS = "trigger_actions"
CONDITION_TYPES = ("questionnaire_evidence",)
ACTION_TYPES = (
    "add_question",
    "add_standalone_control",
    "add_visual_alert",
    "change_risk_category",
    "add_policy_pack",
    "add_risk_scenario",
    "set_custom_field",
    "add_triggered_review_task",
    "add_questionnaire",
)
extract_log = logging.getLogger("kit.extract")
load_log = logging.getLogger("kit.load")


def trigger_version(internal):
    """Triggers & Actions version ("v3" or "v2") of the tenant behind an internal client; APIError if unknown."""
    try:
        internal.get_json("resolve_triggers", {"page[size]": 1})
    except APIError as e:
        # A tenant without v3 triggers answers 404 with reason feature_flag_disabled. Any other 404 is unexplained,
        # so it's raised rather than read as v2.
        if e.status_code == 404 and "feature_flag_disabled" in (e.body or ""):
            return "v2"
        raise
    return "v3"


def _split_version_id(trigger):
    """(questionnaire key, version number) from a questionnaire_version_id like "INTK1-COPY+2"."""
    value = trigger.get("questionnaire_version_id")
    key, sep, number = value.rpartition("+") if isinstance(value, str) else ("", "", "")
    if not (key and sep and number.isdigit()):
        raise APIError(
            f"{ENTITY}: trigger {trigger.get('id')!r} has an unexpected questionnaire_version_id "
            f"{value!r} (expected KEY+N)",
            body=str(trigger)[:300],
        )
    return key, int(number)


def _actions(internal, trigger_id):
    """The trigger's actions, in the order the list returns them."""
    actions = []
    for a in internal.get_data(f"resolve_triggers/{trigger_id}/actions"):
        attrs = a.get("attributes") or {}
        # The action's kind is attributes.type; the item's own type is always resolve_action
        if "type" not in attrs or "data" not in attrs:
            raise APIError(
                f"{ENTITY}: action {a.get('id')!r} of trigger {trigger_id!r} has no 'type' or 'data'", body=str(a)[:300]
            )
        actions.append(
            {
                "id": a["id"],
                "description": attrs.get("description"),
                "type": attrs["type"],
                "data": attrs["data"],
                "show_alert": bool(attrs.get("show_alert", False)),
            }
        )
    return actions


def extract(ctx):
    if trigger_version(ctx.source_internal) == "v2":
        extract_log.error(
            f"{ENTITY}: the source {ctx.source.config.tenant} uses v2 triggers. The kit copies triggers "
            f"only between tenants on v3 triggers. Nothing was extracted."
        )
        # Empty rather than removed: `load all` needs every extract file, and a load must not copy triggers from an
        # earlier extract
        path = save_extract(ctx.data_dir, ENTITY, [])
        extract_log.warning(f"{ENTITY}: the triggers extract {path} was emptied, so stale triggers can't be loaded")
        # Flagged, not stopped: nothing later depends on triggers, so `extract all` goes on
        ctx.summary.flag(ENTITY, "not extracted: the source uses v2 triggers, and the kit copies only v3 triggers")
        return 0
    copied = {q["key"]: q["version"] for q in load_extract(ctx.data_dir, "questionnaires")}
    items, archived, not_copied, older, newer = [], 0, 0, 0, []
    for trigger in ctx.source.get_all("triggers"):
        if trigger.get("archived"):
            archived += 1
            continue
        key, number = _split_version_id(trigger)
        if key not in copied:
            not_copied += 1
            continue
        if number != copied[key]:
            if number < copied[key]:
                older += 1
            else:
                newer.append(trigger["questionnaire_version_id"])
            continue
        items.append(
            {
                "id": trigger["id"],
                "description": trigger.get("description"),
                "match": trigger.get("match"),
                "questionnaire_key": key,
                "source_version": number,
                "conditions": trigger.get("conditions"),
                "actions": _actions(ctx.source_internal, trigger["id"]),
            }
        )
    if newer:
        # The questionnaires extract always takes the latest version, so this means it is out of date
        extract_log.warning(
            f"{ENTITY}: left out {len(newer)} on versions newer than the questionnaires extract "
            f"({', '.join(sorted(set(newer)))}). Run: python migrate.py extract questionnaires, "
            f"then extract triggers again."
        )
    extract_log.info(
        f"{ENTITY}: left out {archived} archived, {not_copied} on questionnaires not copied (built in, "
        f"archived or not extracted) and {older} on an older version of their questionnaire"
    )
    extract_log.info(f"{ENTITY}: kept {len(items)} triggers with {sum(len(i['actions']) for i in items)} actions")
    save_extract(ctx.data_dir, ENTITY, items)
    return len(items)


# Load


def _condition_key(c):
    d = c["data"]
    return (c["type"], d.get("question_id"), d.get("section_id"), tuple(sorted(d.get("options") or [])))


def trigger_fingerprint(version_id, trigger):
    """What makes two triggers the same: version, match, description and conditions (options as sets)."""
    return (
        version_id,
        trigger.get("match"),
        trigger.get("description"),
        tuple(sorted(_condition_key(c) for c in trigger["conditions"])),
    )


def action_fingerprint(action):
    """What makes two actions the same. risk_classification_id is left out: the server derives it."""
    data = {k: v for k, v in (action.get("data") or {}).items() if v is not None and k != "risk_classification_id"}
    return (action["type"], action.get("description"), bool(action.get("show_alert")), json.dumps(data, sort_keys=True))


def _label(item):
    return f"{item.get('description')!r} ({item['id']})"


def _count(n):
    return f"{n} action" if n == 1 else f"{n} actions"


class _References:
    """Resolves a source trigger's references against the target. Target reads are made once and cached."""

    def __init__(self, ctx):
        self.ctx = ctx
        self._versions = {}  # questionnaire key: (version ID, questions, sections) or problem text
        self._lists = {}
        self._published = {}  # (kind, key): problem text or None

    def _list(self, name, read):
        if name not in self._lists:
            self._lists[name] = _read_list(name, read)
        return self._lists[name]

    # The questionnaire the trigger sits on

    def target_version(self, key):
        """(version ID, {question ID: question}, {question ID: section ID}) for a kit-created questionnaire, or a
        problem message."""
        if key not in self._versions:
            try:
                self._versions[key] = self._read_version(key)
            except APIError as e:
                self._versions[key] = f"questionnaire {key} couldn't be read on the target ({e})"
        return self._versions[key]

    def _read_version(self, key):
        record = self.ctx.tracker("questionnaires").find(key)
        if str(record["target_id"]).startswith("dry:"):
            # Created earlier in this dry run: the load makes version 1 from the extract
            item = next(q for q in load_extract(self.ctx.data_dir, "questionnaires") if q["key"] == key)
            number, sections = 1, item["sections"]
        else:
            versions = read_versions(self.ctx.target, key)
            if not versions:
                return f"questionnaire {key} has no version on the target"
            latest = max(versions, key=lambda v: v["version"])
            if latest["draft"] is not True:
                return (
                    f"{key}'s latest version on the target is published, and published versions can't take "
                    f"triggers. Create a new draft version of {key} on the target, then re-run (load triggers "
                    f"before publishing)."
                )
            number, sections = latest["version"], read_sections(self.ctx.target, key, latest["version"])
        questions, section_of = {}, {}
        for section in sections:
            for q in section.get("questions") or []:
                questions[q["id"]] = q
                section_of[q["id"]] = section["id"]
        return f"{key}+{number}", questions, section_of

    def _question(self, version, question_id, section_id, where):
        """A problem message if the question isn't in that section of the target version, else None."""
        version_id, questions, section_of = version
        if question_id is None:
            return f"{where} has no question_id"
        if section_id is not None and section_id not in section_of.values():
            return f"{where}: section {section_id} isn't in {version_id} on the target"
        if question_id not in questions or (section_id is not None and section_of[question_id] != section_id):
            return f"{where}: question {question_id} isn't in section {section_id} of {version_id} on the target"
        return None

    # Keyed content: controls, policy packs and questionnaires

    def _bases(self, kind):
        """{key: (built in, archived)} for one kind of keyed content on the target."""
        if kind == "control":
            return self._list(
                kind,
                lambda: {
                    b["id"]: (b.get("ownership_type") == "ootb", bool(b.get("archived")))
                    for b in self.ctx.target.get_all("policy_control_bases")
                },
            )
        if kind == "policy pack":
            # Internal list, since the Integration API doesn't show packs that only have a draft
            return self._list(
                kind,
                lambda: {
                    b["id"]: (
                        (b.get("attributes") or {}).get("ownership_type") == "ootb",
                        bool((b.get("attributes") or {}).get("archived")),
                    )
                    for b in self.ctx.target_internal.get_all_cursor("policy_pack_bases")
                },
            )
        return self._list(
            kind,
            lambda: {
                b["id"]: (
                    (b.get("attributes") or {}).get("custom") is False,
                    bool((b.get("attributes") or {}).get("archived")),
                )
                for b in self.ctx.target_internal.get_all_cursor("questionnaire_bases")
            },
        )

    def _has_published(self, kind, key):
        path_key = quote(key, safe="")
        if kind == "questionnaire":
            return any(v["draft"] is False for v in read_versions(self.ctx.target, key))
        base = "policy_control_bases" if kind == "control" else "policy_pack_bases"
        versions = self.ctx.target_internal.get_data(f"{base}/{path_key}/versions")
        return any((v.get("attributes") or {}).get("draft") is False for v in versions)

    def published(self, kind, key):
        """A problem message unless the target has the key with a published version (the server rejects actions
        that point at draft-only content), else None."""
        if (kind, key) not in self._published:
            self._published[(kind, key)] = self._check_published(kind, key)
        return self._published[(kind, key)]

    def _check_published(self, kind, key):
        entity = {"control": "controls", "policy pack": "policy_packs", "questionnaire": "questionnaires"}[kind]
        bases = self._bases(kind)
        if key not in bases:
            record = self.ctx.tracker(entity).find(key)
            if record and str(record["target_id"]).startswith("dry:"):
                # Created earlier in this dry run, as a draft
                return f"publish {kind} {key} on the target, then re-run"
            return f"{kind} {key} isn't on the target; load {entity} first"
        built_in, archived = bases[key]
        if archived:
            return f"{kind} {key} is archived on the target; unarchive it there, then re-run"
        if built_in:
            return None  # built-in content is published
        try:
            if self._has_published(kind, key):
                return None
        except APIError as e:
            return f"the versions of {kind} {key} couldn't be read on the target ({e})"
        return f"publish {kind} {key} on the target, then re-run"

    # IDs

    def _by_id(self, entity, label, source_id, list_path=None):
        """(target ID, None) from the entity's tracker, else the same ID if list_path lists it (built in), else
        (None, problem)."""
        target_id = self.ctx.tracker(entity).target_id(source_id)
        if target_id is not None:
            return target_id, None
        if list_path and source_id in self._list(
            list_path, lambda: {i["id"] for i in self.ctx.target.get_all(list_path)}
        ):
            return source_id, None
        where = (
            f"isn't on the target and isn't in loaded_{entity}.json" if list_path else f"isn't in loaded_{entity}.json"
        )
        return None, f"{label} {source_id} {where}; load {entity} first"

    # Actions

    def _action_data(self, action, version):
        """(remapped data, problems) for one action."""
        kind, data = action["type"], dict(action.get("data") or {})
        where = f"{kind} action {action['id']}"
        problems = []

        def need(field):
            if data.get(field) is None:
                problems.append(f"{where} has no {field}")
                return False
            return True

        def remap(field, entity, label, list_path=None):
            target_id, problem = self._by_id(entity, label, data[field], list_path)
            if problem:
                problems.append(f"{where}: {problem}")
            else:
                data[field] = target_id

        def keyed(field, label):
            if need(field):
                problem = self.published(label, data[field])
                if problem:
                    problems.append(f"{where}: {problem}")

        if kind not in ACTION_TYPES:
            return data, [f"action {action['id']}: the kit doesn't know action type {kind!r}"]
        if kind == "add_question":
            problem = self._question(version, data.get("question_id"), data.get("section_id"), where)
            if problem:
                problems.append(problem)
        elif kind == "add_standalone_control":
            keyed("control_key", "control")
            if data.get("risk_scenario_id") is not None:
                remap("risk_scenario_id", "risk_scenarios", "risk scenario", "risk_scenarios")
        elif kind == "change_risk_category":
            data.pop("risk_classification_id", None)  # tenant-specific; the server derives it from the level
        elif kind == "add_policy_pack":
            keyed("policy_pack_key", "policy pack")
        elif kind == "add_questionnaire":
            keyed("questionnaire_key", "questionnaire")
        elif kind == "add_risk_scenario":
            if need("risk_scenario_id"):
                remap("risk_scenario_id", "risk_scenarios", "risk scenario", "risk_scenarios")
            if data.get("risk_type_ids") is not None:
                ids = []
                for rt in data["risk_type_ids"]:
                    target_id, problem = self._by_id("risk_types", "risk type", rt, "risk_types")
                    if problem:
                        problems.append(f"{where}: {problem}")
                    ids.append(target_id)
                data["risk_type_ids"] = ids
        elif kind == "set_custom_field":
            if need("custom_field_id"):
                remap("custom_field_id", "custom_fields", "custom field")
        elif kind == "add_triggered_review_task":
            reviewer = data.get("type")
            if reviewer not in ("user", "team"):
                problems.append(f"{where}: reviewer type {reviewer!r} isn't user or team")
            elif need("reviewer_id"):
                remap("reviewer_id", f"{reviewer}s", reviewer)
        return data, problems

    def resolve_trigger(self, item):
        """(Integration create body, [(source action, internal create body)], problems). Nothing may be written for
        the trigger unless problems is empty."""
        version = self.target_version(item["questionnaire_key"])
        if isinstance(version, str):
            return None, [], [version]
        version_id = version[0]
        problems, conditions = [], []
        for n, c in enumerate(item.get("conditions") or [], 1):
            where = f"condition {n}"
            if c.get("type") not in CONDITION_TYPES:
                problems.append(f"{where}: the kit doesn't know condition type {c.get('type')!r}")
                continue
            d = c.get("data") or {}
            problem = self._question(version, d.get("question_id"), d.get("section_id"), where)
            if problem:
                problems.append(problem)
            else:
                known = version[1][d["question_id"]].get("select_options") or []
                for option in d.get("options") or []:
                    if option not in known:
                        load_log.warning(
                            f"{ENTITY}: {_label(item)}: {where} option {option!r} isn't one of question "
                            f"{d['question_id']}'s options, so it can never match; copied as is"
                        )
            conditions.append({"type": c["type"], "data": d})
        if not conditions and not problems:
            problems.append("it has no conditions")
        payload = {
            "description": item.get("description"),
            "match": item.get("match"),
            "questionnaire_version_id": version_id,
            "conditions": conditions,
        }
        actions = []
        for action in item.get("actions") or []:
            data, action_problems = self._action_data(action, version)
            problems += action_problems
            actions.append(
                (
                    action,
                    {
                        "data": {
                            "type": "resolve_action",
                            "attributes": {
                                "description": action.get("description"),
                                "type": action["type"],
                                "data": data,
                                "show_alert": bool(action.get("show_alert")),
                            },
                        }
                    },
                )
            )
        return payload, actions, problems


_LIST_NAMES = {
    "control": "controls",
    "policy pack": "policy packs",
    "questionnaire": "questionnaires",
    "risk_scenarios": "risk scenarios",
    "risk_types": "risk types",
    ENTITY: "triggers",
}


def _read_list(name, read):
    """read(), or LoadStopped (after logging why) if the target can't list it: every later trigger would need it."""
    what = _LIST_NAMES.get(name, name)
    try:
        return read()
    except APIError as e:
        load_log.error(
            f"{ENTITY}: couldn't list {what} on the target ({e}); what was already loaded is tracked, so "
            f"re-run when the target responds"
        )
        raise LoadStopped(f"{ENTITY}: stopped because the target couldn't list {what} (see above)") from e


def _check_ready(ctx):
    """False (after logging and flagging why) if the target is on v2 triggers: nothing later depends on triggers, so
    `load all` goes on. Raises LoadStopped (after logging why) if questionnaires weren't loaded."""
    tenant = ctx.target.config.tenant
    if trigger_version(ctx.target_internal) == "v2":
        load_log.error(
            f"{ENTITY}: the target {tenant} uses v2 triggers. The kit copies triggers only between tenants "
            f"on v3 triggers. Nothing was loaded."
        )
        ctx.summary.flag(ENTITY, "not loaded: the target uses v2 triggers, and the kit copies only v3 triggers")
        return False
    tracked = (Path(ctx.data_dir) / "loaded_questionnaires.json").exists()
    if not tracked and not (ctx.dry_run and ctx.tracker("questionnaires").records):
        # In a dry run the questionnaires tracker of this run counts, since it isn't saved
        load_log.error(
            f"{ENTITY}: data/loaded_questionnaires.json not found. Triggers go only onto questionnaires the "
            f"kit created, so load questionnaires first. Nothing was loaded."
        )
        raise LoadStopped(f"{ENTITY}: not loaded because questionnaires haven't been loaded (see above)")
    return True


def _action_index(ctx, target_id):
    """{action fingerprint: action ID} for the actions already on a target trigger."""
    index = {}
    for a in ctx.target_internal.get_data(f"resolve_triggers/{target_id}/actions"):
        attrs = a.get("attributes") or {}
        index.setdefault(action_fingerprint(attrs), a["id"])
    return index


class _Loader:
    def __init__(self, ctx):
        self.ctx = ctx
        self.refs = _References(ctx)
        self.triggers = ctx.tracker(ENTITY)
        self.actions = ctx.tracker(ACTIONS)
        self.verb = "would create" if ctx.dry_run else "created"
        target_triggers = _read_list(ENTITY, lambda: ctx.target.get_all("triggers"))
        # Target trigger ID: the questionnaire version it sits on
        self.on_target = {t["id"]: t.get("questionnaire_version_id") for t in target_triggers}
        ours = {r["target_id"] for r in self.triggers.records if r["origin"] == "created"}
        # Trigger fingerprint: (target ID, origin, what it is), for target triggers and the ones mapped in this run.
        # A target trigger the kit created in an earlier run is ours, so an identical source trigger gets its actions
        # added to it, as in an uninterrupted run.
        self.known = {}
        for t in target_triggers:
            fp = trigger_fingerprint(t.get("questionnaire_version_id"), dict(t, conditions=t.get("conditions") or []))
            archived = " (archived)" if t.get("archived") else ""
            if t["id"] in ours:
                self.known[fp] = (t["id"], "created", f"target trigger {t['id']}{archived}, created by the kit earlier")
            else:
                self.known.setdefault(fp, (t["id"], "matched", f"target trigger {t['id']}{archived}"))
        self.action_indexes = {}  # target trigger ID: {action fingerprint: action ID}
        self.foreign_checked = {}  # target trigger ID the kit didn't create: True if it has actions
        self.created_on = set()  # questionnaire keys that got a trigger in this run
        self.incomplete_on = set()  # questionnaire keys with a trigger or action this run couldn't load

    def _gate(self, item, warned):
        """True if the trigger's questionnaire was created by the kit. Warns once per other questionnaire."""
        key = item["questionnaire_key"]
        record = self.ctx.tracker("questionnaires").find(key)
        if record and record["origin"] == "created":
            return True
        if key not in warned:
            warned.add(key)
            why = (
                "already existed on the target, and the kit doesn't change what it didn't create"
                if record
                else "isn't in data/loaded_questionnaires.json, so the kit didn't create it on the target"
            )
            load_log.warning(f"{ENTITY}: questionnaire {key} {why}; skipping its triggers")
        return False

    def run(self, items):
        warned = set()
        for item in items:
            if not self._gate(item, warned):
                self.ctx.summary.record(ENTITY, "skipped")
                continue
            before = self._problems()
            self._one(item)
            if self._problems() != before:
                self.incomplete_on.add(item["questionnaire_key"])

    def _problems(self):
        """Failures and flags so far, to tell whether loading one trigger left something undone."""
        counts = self.ctx.summary.to_dict()
        return (sum(counts.get(e, {}).get("failed", 0) for e in (ENTITY, ACTIONS)), len(self.ctx.summary.attention))

    def _fail_actions(self, actions, message):
        for action in actions:
            load_log.error(f"{ACTIONS}: {action.get('description')!r} ({action['id']}) not added: {message}")
            self.ctx.summary.record(ACTIONS, "failed")

    def _one(self, item):
        label = _label(item)
        record = self.triggers.find(item["id"])
        untracked = [a for a in item.get("actions") or [] if not self.actions.find(a["id"])]
        if record:
            self.ctx.summary.record(ENTITY, "skipped")
            if record["origin"] != "created":
                load_log.debug(f"{ENTITY}: {label} is tracked as matched (not the kit's); skipping it and its actions")
                return
            for _ in range(len(item.get("actions") or []) - len(untracked)):
                self.ctx.summary.record(ACTIONS, "skipped")
            if not untracked:
                load_log.debug(f"{ENTITY}: {label} already tracked with all its actions, skipping")
                return
            target_id = record["target_id"]
            if not str(target_id).startswith("dry:") and target_id not in self.on_target:
                fix = (
                    f"its trigger {target_id} is tracked but no longer on the target; remove its row from "
                    f"loaded_triggers.json to recreate it"
                )
                self._fail_actions(untracked, fix)
                self.ctx.summary.flag(
                    ENTITY,
                    f"{label} on {item['questionnaire_key']}: {_count(len(untracked))} not "
                    f"added because {fix}, then re-run",
                )
                return
        payload, actions, problems = self.refs.resolve_trigger(item)
        if record and not problems and not str(record["target_id"]).startswith("dry:"):
            on = self.on_target[record["target_id"]]
            if on != payload["questionnaire_version_id"]:
                problems = [
                    f"trigger {record['target_id']} is on {on}, but the latest target version of "
                    f"{item['questionnaire_key']} is {payload['questionnaire_version_id']}; its actions can't "
                    f"be added to a published version. Delete target trigger {record['target_id']} and remove "
                    f"its row from loaded_triggers.json, then re-run to recreate it on the new draft."
                ]
        if problems:
            n = len(untracked) if record else len(item.get("actions") or [])
            what = f"{_count(n)} not added" if record else f"not loaded, and its {_count(n)} not added"
            load_log.error(f"{ENTITY}: {label} on {item['questionnaire_key']} {what}: " + "; ".join(problems))
            if record:
                for _ in untracked:
                    self.ctx.summary.record(ACTIONS, "failed")
                self.ctx.summary.flag(
                    ENTITY,
                    f"{label} on {item['questionnaire_key']}: {what} (see the log); re-run after fixing to add them",
                )
            else:
                self.ctx.summary.record(ENTITY, "failed")
            return
        if record:
            pending = {a["id"] for a in untracked}
            self._add_actions(item, record["target_id"], [(a, b) for a, b in actions if a["id"] in pending])
            return
        fp = trigger_fingerprint(payload["questionnaire_version_id"], payload)
        if fp in self.known:
            target_id, origin, what = self.known[fp]
            if origin != "created" and actions and not self._foreign_has_actions(item, target_id):
                # Not recorded, so the check repeats each run, and once the trigger is deleted it's recreated
                self.ctx.summary.record(ENTITY, "failed")
                return
            self.triggers.record(item["id"], target_id, item.get("description"), origin)
            self.ctx.summary.record(ENTITY, "matched")
            if origin != "created":
                load_log.warning(
                    f"{ENTITY}: {label} is identical to {what}, which the kit didn't create; recorded as "
                    f"matched and its {_count(len(actions))} not added"
                )
                return
            load_log.warning(f"{ENTITY}: {label} is identical to {what}; mapping it to the same target trigger")
            self._add_actions(item, target_id, actions)
            return
        target_id = self._create(item, payload)
        if target_id is None:
            load_log.error(f"{ENTITY}: {label}: its {_count(len(actions))} weren't attempted")
            self.ctx.summary.record(ENTITY, "failed")
            return
        self.triggers.record(item["id"], target_id, item.get("description"), "created")
        self.known[fp] = (target_id, "created", f"source trigger {item['id']}, created in this run")
        self.action_indexes[target_id] = {}
        self.created_on.add(item["questionnaire_key"])
        load_log.info(f"{ENTITY}: {self.verb} {label} on {payload['questionnaire_version_id']}")
        self.ctx.summary.record(ENTITY, "created")
        self._add_actions(item, target_id, actions)

    def _foreign_has_actions(self, item, target_id):
        """False (after reporting it once per target trigger) if a matched target trigger the kit didn't create has
        no actions, which likely means the kit created it but couldn't track it, or if its actions can't be read."""
        if target_id in self.foreign_checked:
            if not self.foreign_checked[target_id]:
                load_log.debug(f"{ENTITY}: {_label(item)} also matches target trigger {target_id} (reported above)")
            return self.foreign_checked[target_id]
        try:
            ok = bool(self.ctx.target_internal.get_data(f"resolve_triggers/{target_id}/actions"))
        except APIError as e:
            ok = False
            load_log.error(
                f"{ENTITY}: {_label(item)} matches target trigger {target_id}, whose actions couldn't be "
                f"read to check it ({e}); not loaded, re-run when the target responds"
            )
        else:
            if not ok:
                message = (
                    f"target trigger {target_id} matches source trigger {_label(item)} but has no actions. If "
                    f"an earlier kit run created it and was interrupted (and you didn't make it yourself), "
                    f"delete it on the target, then re-run; the kit will recreate it with its actions."
                )
                load_log.error(f"{ENTITY}: {message}")
                self.ctx.summary.flag(ENTITY, message)
        self.foreign_checked[target_id] = ok
        return ok

    def _create(self, item, payload):
        warnings = []

        def id_of(body):
            warnings.extend(body.get("warnings") or [])
            return body["id"]

        try:
            target_id = create_target(
                self.ctx, ENTITY, "triggers", payload, item["id"], item.get("description"), id_of=id_of
            )
        except APIError as e:
            load_log.error(f"{ENTITY}: creating {_label(item)} failed: {e}")
            return None
        if warnings:
            load_log.warning(f"{ENTITY}: the target warned about {_label(item)}: {'; '.join(map(str, warnings))}")
        return target_id

    def _add_actions(self, item, target_id, actions):
        """Adds the actions not already on the target trigger, in source order. Flags the trigger if any fail."""
        if not actions:
            return
        if target_id not in self.action_indexes:
            try:
                self.action_indexes[target_id] = _action_index(self.ctx, target_id)
            except APIError as e:
                self._fail_actions(
                    [a for a, _ in actions], f"the actions of target trigger {target_id} couldn't be read ({e})"
                )
                self.ctx.summary.flag(ENTITY, f"{_label(item)}: actions not added; re-run after fixing to add them")
                return
        index = self.action_indexes[target_id]
        failed = 0
        for action, body in actions:
            name = action.get("description")
            if self.actions.find(action["id"]):
                self.ctx.summary.record(ACTIONS, "skipped")
                continue
            fp = action_fingerprint(body["data"]["attributes"])
            if fp in index:
                self.actions.record(action["id"], index[fp], name, "matched")
                load_log.warning(
                    f"{ACTIONS}: {name!r} ({action['id']}) is identical to action {index[fp]} on target "
                    f"trigger {target_id} (already there, or added earlier in this run); not added again"
                )
                self.ctx.summary.record(ACTIONS, "matched")
                continue
            try:
                action_id = create_target(
                    self.ctx,
                    ACTIONS,
                    f"resolve_triggers/{target_id}/actions",
                    body,
                    action["id"],
                    name,
                    id_of=data_id,
                    client=self.ctx.target_internal,
                )
            except APIError as e:
                load_log.error(f"{ACTIONS}: creating {name!r} failed: {e}")
                action_id = None
            if action_id is None:
                failed += 1
                self.ctx.summary.record(ACTIONS, "failed")
                continue
            self.actions.record(action["id"], action_id, name, "created")
            index[fp] = action_id
            load_log.debug(f"{ACTIONS}: {self.verb} {name!r} on trigger {target_id}")
            self.ctx.summary.record(ACTIONS, "created")
        if failed:
            self.ctx.summary.flag(
                ENTITY,
                f"{_label(item)} on {item['questionnaire_key']}: {_count(failed)} not "
                f"added; re-run after fixing to add them",
            )


def load(ctx):
    items = load_extract(ctx.data_dir, ENTITY)
    if not items:
        # Nothing to copy, so a v2 target or a run without custom questionnaires mustn't stop `load all`
        load_log.info(f"{ENTITY}: the extract has no triggers; nothing to load")
        return
    if not _check_ready(ctx):
        return
    loader = _Loader(ctx)
    loader.run(items)
    if ctx.dry_run:
        return
    ready = loader.created_on - loader.incomplete_on
    if ready:
        load_log.info(
            f"{ENTITY}: these target questionnaires now carry triggers and can be published once reviewed: "
            f"{', '.join(sorted(ready))}. Triggers can't be added to a published version, so "
            f"load every trigger before publishing."
        )
    if loader.incomplete_on:
        load_log.info(
            f"{ENTITY}: don't publish {', '.join(sorted(loader.incomplete_on))} yet: some of their triggers "
            f"or actions didn't load (see above). Fix that and re-run first, since a published version "
            f"can't take triggers."
        )
