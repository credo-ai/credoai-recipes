"""Questionnaire answers on custom models and vendors: model_answers and vendor_answers, loaded after the models and
vendors themselves.

Only custom models and vendors the kit created are written to. Answers on built-in ones aren't copied: the backend
refuses answer writes on built-in entities, so their answers come with Credo's catalog (the same on every
tenant) or with the integration that synced them; the extract counts them and says so.

Before attaching a questionnaire or writing an answer, the kit sets the entity's triggers to Partial Pause (internal
API). Without MEG triggers on the target that call answers 404, triggers don't reach models and vendors, and the kit
goes on. loaded_<entity>.json records the entities the kit paused, so a re-run leaves alone one whose triggers the
operator has since turned back on. Attachments and answers go through the Integration API (kit.answers).
"""

import logging

from kit import answers
from kit.clients.base import APIError
from kit.internal import trigger_mode
from kit.internal.questionnaires import published_version_id
from kit.tracking import load_extract, save_extract

extract_log = logging.getLogger("kit.extract")
load_log = logging.getLogger("kit.load")


def _ok(resp):
    return resp is None or 200 <= resp.status_code < 300


class EntityAnswers:
    """One registered entity: ENTITY, extract(ctx) and load(ctx), for one kind of entity (models or vendors)."""

    def __init__(self, entity, kind, noun):
        self.ENTITY = entity
        self.kind = kind  # the API path and the tracker of the entities themselves
        self.noun = noun

    # --- extract ---

    def _listing(self, ctx):
        """[(id, name, custom)] of the source's entities. Models carry the custom flag on the Integration list; vendors
        only on the internal one."""
        rows = ctx.source.get_all(self.kind)
        if self.kind == "models":
            flags = {r["id"]: r.get("custom") for r in rows}
        else:
            internal = {v["id"]: v.get("attributes") or {} for v in ctx.source_internal.get_all_cursor(self.kind)}
            flags = {}
            for r in rows:
                if r["id"] not in internal:
                    raise APIError(
                        f"{self.ENTITY}: {self.noun} {r.get('name')!r} is in the Integration list but not "
                        f"the internal one"
                    )
                flags[r["id"]] = internal[r["id"]].get("custom")
        listing = []
        for r in rows:
            if not isinstance(flags[r["id"]], bool):
                # Without the flag a built-in entity can't be told apart; guessing could copy one
                raise APIError(f"{self.ENTITY}: {self.noun} {r.get('name')!r} has no 'custom' flag")
            listing.append((r["id"], r.get("name"), flags[r["id"]]))
        return listing

    def _count_answers(self, ctx, entity_id):
        return sum(
            1
            for ev in ctx.source_internal.get_all_cursor(f"{self.kind}/{entity_id}/evidences")
            if ((ev.get("attributes") or {}).get("metadata") or {}).get("question_id")
            and (ev.get("attributes") or {}).get("active") is not False
        )

    def extract(self, ctx):
        items, built_in, built_in_answers = [], 0, 0
        for entity_id, name, custom in self._listing(ctx):
            if not custom:
                n = self._count_answers(ctx, entity_id)
                if n:
                    built_in += 1
                    built_in_answers += n
                continue
            found = answers.extract_answers(ctx, self.kind, entity_id, f"{self.ENTITY}: {self.noun} {name!r}")
            if found["answers"]:
                items.append({"id": entity_id, "name": name, **found})
        if built_in_answers:
            extract_log.info(
                f"{self.ENTITY}: {built_in_answers} answer(s) on {built_in} built-in {self.noun}(s) "
                f"aren't copied: the target can't take answers on built-in entities, whose answers come with "
                f"Credo's catalog or with the integration that synced them"
            )
        save_extract(ctx.data_dir, self.ENTITY, items)
        return len(items)

    # --- load ---

    def _flag(self, ctx, message, level=logging.ERROR):
        message = f"{self.ENTITY}: {message}"
        load_log.log(level, message)
        ctx.summary.flag(self.ENTITY, message)

    def load(self, ctx):
        items = load_extract(ctx.data_dir, self.ENTITY)
        tracker = ctx.tracker(self.kind)
        versions = {}  # {questionnaire key: "KEY+N" or None}, read once per run
        totals = None
        for item in items:
            label = f"{self.noun} {item['name']!r}"
            record = tracker.find(item["id"])
            if record is None or record["origin"] != "created":
                why = (
                    f"isn't tracked in loaded_{self.kind}.json (load {self.kind} first)"
                    if record is None
                    else "was already on the target, and the kit doesn't change existing entities"
                )
                self._flag(ctx, f"{label} {why}; its {len(item['answers'])} answer(s) not copied", logging.WARNING)
                ctx.summary.record(self.ENTITY, "skipped")
                continue
            try:
                outcome, counts = self._load_one(ctx, item, record["target_id"], label, versions)
            except APIError as e:
                # One entity's failure stops only that entity
                self._flag(ctx, f"{label}: stopped part way ({e}); a re-run completes what is missing")
                outcome, counts = "failed", None
            if counts is not None:
                totals = answers.add_counts(totals, counts)
            ctx.summary.record(self.ENTITY, outcome)
        if totals is not None:
            answers.report_totals(ctx, self.ENTITY, totals)

    def _load_one(self, ctx, item, target_id, label, versions):
        """(outcome, answer counts or None) for one tracked entity."""
        exists = not str(target_id).startswith("dry:")
        answer_label = f"{self.ENTITY}: {label}"
        try:
            current = (
                {a["key"]: a["version"] for a in answers.read_attachments(ctx.target, self.kind, target_id)}
                if exists
                else {}
            )
        except APIError as e:
            self._flag(
                ctx,
                f"{label}: reading its questionnaire attachments failed ({e}); no questionnaires were "
                f"attached or answered",
            )
            return "failed", None
        attached, to_attach = dict(current), []
        for key in dict.fromkeys(a["key"] for a in item["attachments"]):
            if key in current:
                continue  # an entity holds one version of a key; answers map onto the one it has
            if key not in versions:
                versions[key] = published_version_id(ctx.target, key)
            if versions[key] is None:
                self._flag(
                    ctx,
                    f"{label}: questionnaire {key} has no published version on the target, so its answers "
                    f"weren't copied; publish questionnaire {key} on the target, then re-run",
                )
                continue
            version = int(versions[key].rsplit("+", 1)[1])
            to_attach.append((key, version))
            attached[key] = version
        planned = answers.write_answers(
            ctx, self.kind, target_id, item["answers"], attachments=attached, label=answer_label, plan=True
        )
        if not to_attach and not planned["written"]:
            counts = answers.write_answers(
                ctx, self.kind, target_id, item["answers"], attachments=attached, label=answer_label
            )
            return "skipped", counts
        allowed = self._triggers_paused(ctx, item, target_id, label, exists)
        if allowed is not True:
            return allowed, None
        attach_failed = False
        for key, version in to_attach:
            if ctx.dry_run:
                load_log.info(f"{self.ENTITY}: would attach questionnaire {key}+{version} to {label}")
                continue
            try:
                resp = ctx.target.post(f"{self.kind}/{target_id}/questionnaires", {"key": key, "version": version})
                problem = None if _ok(resp) else f"{resp.status_code} {resp.text[:300]}"
            except APIError as e:
                problem = str(e)
            if problem:
                self._flag(
                    ctx, f"{label}: attaching questionnaire {key}+{version} failed ({problem}); a re-run tries it again"
                )
                del attached[key]
                attach_failed = True
            else:
                load_log.debug(f"{self.ENTITY}: attached questionnaire {key}+{version} to {label}")
        counts = answers.write_answers(
            ctx, self.kind, target_id, item["answers"], attachments=attached, label=answer_label
        )
        if attach_failed or counts["failed"]:
            return "failed", counts
        return ("created" if counts["written"] or to_attach else "skipped"), counts

    def _triggers_paused(self, ctx, item, target_id, label, exists):
        """True when the kit may attach and answer: Partial Pause set (or not available on the target). Otherwise the
        outcome to record."""
        paused = ctx.tracker(self.ENTITY)  # entities this kit paused on an earlier run
        entity_id = target_id  # a dry: placeholder in a dry run, where nothing is written
        if exists:
            try:
                body = ctx.target_internal.get_json(f"{self.kind}/{target_id}")
                attrs = ((body or {}).get("data") or {}).get("attributes") or {}
            except (APIError, AttributeError) as e:
                attrs, problem = {}, str(e)
            else:
                problem = None if attrs.get("entity_id") else "no entity_id in the response"
            if problem:
                self._flag(
                    ctx,
                    f"{label}: reading it on the target failed ({problem}); no questionnaires were "
                    f"attached or answered",
                )
                return "failed"
            entity_id = attrs["entity_id"]
            if paused.find(item["id"]):
                if attrs.get("trigger_mode") == "full":
                    load_log.warning(f"{self.ENTITY}: triggers are on for {label}; the kit won't write answers")
                    return "skipped"
                return True
        mode = trigger_mode.pause_for_load(ctx, entity_id, f"{self.ENTITY}: {label}")
        if mode is None:
            ctx.summary.flag(
                self.ENTITY,
                f"{label}: triggers not paused, so no questionnaires were attached or "
                f"answered; attach and answer them on the target by hand",
            )
            return "failed"
        if mode == "paused" and not paused.find(item["id"]):
            paused.record(item["id"], target_id, item["name"], "created")
        return True


model_answers = EntityAnswers("model_answers", "models", "model")
vendor_answers = EntityAnswers("vendor_answers", "vendors", "vendor")
