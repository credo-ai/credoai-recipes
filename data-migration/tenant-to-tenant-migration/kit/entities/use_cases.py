"""Use cases, with everything attached to them: owner and other stakeholders, custom field values, risk
classification, policy packs, risk scenarios and their control links, control evidence, domains, model and vendor
links, questionnaire attachments and answers, workflow stage and step.

The Integration API reads the use case and handles links, packs, risk scenarios, control evidence, attachments and
answers. The internal API creates the use case (the Integration API's create looks the name up first with an unescaped
query, so a name with "&", "#", "+" or "%" fails there) and covers what the Integration API can't: stakeholders, the
stage name, the risk classification, domains, the trigger mode, and the stage and step changes (the Integration
workflow route drops the step).

Content rules: a use case whose name is already on the target is skipped with a warning, never adopted or changed,
and only use cases the kit created are ever written to again.
"""

import logging
from urllib.parse import quote

from kit import answers
from kit import risk_type_refs as risk_types
from kit.clients.base import APIError
from kit.entities.custom_fields import field_names_for
from kit.internal import trigger_mode
from kit.internal.questionnaires import published_version_id
from kit.loading import MissingReference, create_target
from kit.people import People
from kit.remap import AmbiguousMatch
from kit.tracking import load_extract, save_extract

ENTITY = "use_cases"
USE_CASE_TYPE = "use_case"
START_STEP = "assessment"  # where a use case lands when it enters a stage
extract_log = logging.getLogger("kit.extract")
load_log = logging.getLogger("kit.load")


def _empty(value):
    return value is None or value == "" or value == []


def _internal_attributes(internal, use_case_id, name):
    """The internal API's attributes of a use case, with its domain IDs."""
    body = internal.get_json(f"use_cases/{use_case_id}", {"include": "domain_links"})
    attrs = ((body or {}).get("data") or {}).get("attributes") if isinstance(body, dict) else None
    if not isinstance(attrs, dict):
        raise APIError(f"{ENTITY}: the internal API returned no attributes for {name!r}", body=str(body)[:300])
    return attrs


def _people(item_name, rows, emails, team_names):
    """(owner, other stakeholders) from internal stakeholder rows, with users as emails and teams as names."""
    owner, others = None, []
    for row in rows:
        attrs = row.get("attributes") or {}
        role, user_id, team_id = attrs.get("role"), attrs.get("user_id"), attrs.get("team_id")
        if team_id:
            who = {"team_name": team_names.get(team_id)} if team_names.get(team_id) else None
        else:
            who = {"email": emails.get(user_id)} if emails.get(user_id) else None
        if who is None:
            extract_log.warning(
                f"{ENTITY}: {item_name!r}: stakeholder {user_id or team_id} isn't in the source's "
                f"users or teams list (perhaps a hidden system account); leaving it out"
            )
            continue
        if role == "owner" and owner is None:
            owner = who
        else:
            others.append(dict(who, role=role))
    if owner is None:
        extract_log.warning(
            f"{ENTITY}: {item_name!r} has no owner the kit can map; it will be created with the API key's user as owner"
        )
    return owner, others


def _risk_scenarios(ctx, use_case_id, type_names):
    items = []
    for rs in ctx.source.get_all(f"use_cases/{use_case_id}/risk_scenarios"):
        item = {
            "catalog_id": rs.get("risk_scenario_id"),
            "name": rs.get("name"),
            "description": rs.get("description"),
            "risk_type_ids": list(rs.get("risk_type_ids") or []),
        }
        item["risk_type_names"] = [type_names.get(i, f"unknown:{i}") for i in item["risk_type_ids"]]
        item.update(
            {
                "inherent_likelihood": rs.get("inherent_risk_likelihood"),
                "inherent_impact": rs.get("inherent_risk_impact"),
                "residual_likelihood": rs.get("residual_risk_likelihood"),
                "residual_impact": rs.get("residual_risk_impact"),
                "control_keys": list(rs.get("control_keys") or []),
            }
        )
        items.append(item)
    return items


def _controls(ctx, use_case_id, name):
    """Controls with evidence: [{key, evidence: [{hash_key, data}]}]. File evidence is counted and left out."""
    by_key, files = {}, 0
    for control in ctx.source.get_all(f"use_cases/{use_case_id}/controls"):
        key = control.get("control_key")
        for ev in ctx.source.get_all(f"use_cases/{use_case_id}/controls/{control['id']}/evidences"):
            if ev.get("type") == "file":
                files += 1
                extract_log.warning(
                    f"{ENTITY}: {name!r}: file evidence on control {key} isn't copied (the kit "
                    f"doesn't copy files); add it on the target by hand"
                )
                continue
            if not ev.get("control_requirement_id") or _empty((ev.get("data") or {}).get("value")):
                continue
            evidence = by_key.setdefault(key, [])
            if all(e["hash_key"] != ev["control_requirement_id"] for e in evidence):
                evidence.append({"hash_key": ev["control_requirement_id"], "data": ev["data"]})
    return [{"key": k, "evidence": v} for k, v in by_key.items() if v]


def _linked(ctx, use_case_id, kind):
    linked = ctx.source.get_all(f"use_cases/{use_case_id}/{kind}")
    return sorted(x["id"] for x in linked), {x["id"]: x.get("name") for x in linked}


def extract(ctx):
    emails = {u["id"]: (u.get("attributes") or {}).get("email") for u in ctx.source_internal.get_all_cursor("users")}
    team_names = {t["id"]: (t.get("attributes") or {}).get("name") for t in ctx.source_internal.get_all_cursor("teams")}
    stages = {s["id"]: (s.get("attributes") or {}).get("name") for s in ctx.source_internal.get_data("workflow_stages")}
    type_names = risk_types.names_by_id(ctx.source)
    items, n_answers, n_files = [], 0, 0
    for uc in ctx.source.get_all("use_cases"):
        uid, name = uc["id"], uc["name"]
        fields = uc.get("custom_fields")
        if fields is None:
            # Only the create response leaves them out; read the use case itself
            fields = ctx.source.get_json(f"use_cases/{uid}").get("custom_fields") or []
        attrs = _internal_attributes(ctx.source_internal, uid, name)
        owner, others = _people(name, ctx.source_internal.get_data(f"use_cases/{uid}/stakeholders"), emails, team_names)
        model_ids, model_names = _linked(ctx, uid, "models")
        vendor_ids, vendor_names = _linked(ctx, uid, "vendors")
        qa = answers.extract_answers(ctx, ENTITY, uid, label=f"{ENTITY}: {name!r}")
        if attrs.get("domain_ids") is None:
            extract_log.warning(
                f"{ENTITY}: {name!r}: its domains couldn't be read (no domain_ids in the response); "
                f"its domains won't be copied"
            )
        stage_id = attrs.get("workflow_stage_id")
        if stage_id and stage_id not in stages:
            extract_log.warning(
                f"{ENTITY}: {name!r}: its workflow stage {stage_id} isn't in the source's stage list; "
                f"its stage won't be set"
            )
        items.append(
            {
                "id": uid,
                "name": name,
                "description": uc.get("description"),
                "owner": owner,
                "stakeholders": others,
                "custom_fields": [
                    {"name": f["name"], "value": f["value"]} for f in fields if not _empty(f.get("value"))
                ],
                "stage_name": stages.get(stage_id),
                "step": attrs.get("workflow_stage_step"),
                "risk_classification_level": attrs.get("risk_classification_level"),
                "policy_packs": [{"key": p["key"]} for p in ctx.source.get_all(f"use_cases/{uid}/policy_packs")],
                "risk_scenarios": _risk_scenarios(ctx, uid, type_names),
                "controls": _controls(ctx, uid, name),
                "domain_ids": sorted(attrs.get("domain_ids") or []),
                "model_ids": model_ids,
                "vendor_ids": vendor_ids,
                "model_names": model_names,
                "vendor_names": vendor_names,
                "attachments": qa["attachments"],
                "answers": qa["answers"],
                "file_answers": qa["file_answers"],
            }
        )
        n_answers += len(qa["answers"])
        n_files += qa["file_answers"]
    extract_log.info(f"{ENTITY}: {len(items)} use case(s), {n_answers} answer(s), {n_files} file answer(s) not copied")
    save_extract(ctx.data_dir, ENTITY, items)
    return len(items)


# --- load ---


def _ok(resp):
    return resp is None or 200 <= resp.status_code < 300


def _names(values):
    return ", ".join(f["name"] for f in values)


def _who(who):
    who = who or {}
    if who.get("team_name"):
        return f"team {who['team_name']}"
    return f"user {who.get('email')}" if who.get("email") else "no one"


class _Parts:
    """Everything after the create, in a fixed order. Each part reads the target first and writes only what is
    missing, so a re-run completes a use case an earlier run left half done. A missing reference is reported with
    the use case's name and flags the run, but never stops the use case."""

    def __init__(self, ctx, versions):
        self.ctx = ctx
        self.versions = versions  # {questionnaire key: "KEY+N" or None}, shared with the checks before the create
        self.totals = None  # answer counts over the run
        self.people = People(ctx)
        self._cache = {}

    def cached(self, name, read):
        """A target list read once per run."""
        if name not in self._cache:
            self._cache[name] = read()
        return self._cache[name]

    def report(self, item, message):
        message = f"{ENTITY}: {item['name']!r}: {message}"
        load_log.error(message)
        self.ctx.summary.flag(ENTITY, message)

    def write(self, item, client, method, path, body, what, hint="", status_only=False):
        """One write. Returns (True, response) when written (response None in a dry run), (False, None) after
        reporting a failure. status_only: the error body may repeat a value the log mustn't show."""
        if self.ctx.dry_run:
            load_log.info(f"{ENTITY}: would {what} on {item['name']!r}")
            return True, None
        try:
            resp = getattr(client, method)(path, body)
            if _ok(resp):
                load_log.debug(f"{ENTITY}: {what} on {item['name']!r}")
                return True, resp
            problem = resp.status_code if status_only else f"{resp.status_code} {resp.text[:300]}"
        except APIError as e:
            problem = (e.status_code or "lost connection") if status_only else str(e)
        self.report(item, f"couldn't {what} ({problem}){'; ' + hint if hint else ''}")
        return False, None

    def run(self, item, target_id, answers_ok):
        """answers_ok: False when the use case's triggers aren't paused, so no questionnaire is attached or
        answered."""
        exists = not str(target_id).startswith("dry:")
        state = self.state(item, target_id, exists)
        self.custom_fields(item, target_id, exists)
        self.risk_classification(item, target_id, state)
        self.policy_packs(item, target_id, exists)
        self.risk_scenarios(item, target_id, exists)
        self.control_evidence(item, target_id, exists)
        self.domains(item, target_id, state)
        for kind in ("models", "vendors"):
            self.links(item, target_id, exists, kind)
        self.stakeholders(item, target_id, exists)
        if answers_ok:
            self.questionnaires(item, target_id, exists)
        self.stage(item, target_id, exists)

    def state(self, item, target_id, exists):
        """The internal API's attributes of the target use case ({} for one a dry run would create), or None after
        reporting a failed read."""
        if not exists:
            return {}
        try:
            return _internal_attributes(self.ctx.target_internal, target_id, item["name"])
        except APIError as e:
            self.report(
                item,
                f"couldn't read it on the target ({e}); its risk classification, domains and stage weren't checked",
            )
            return None

    # --- custom fields ---

    def custom_fields(self, item, target_id, exists):
        values = item["custom_fields"]
        if not values:
            return
        try:
            current = (
                {
                    f.get("name"): f.get("value")
                    for f in self.ctx.target.get_json(f"{ENTITY}/{target_id}").get("custom_fields") or []
                }
                if exists
                else {}
            )
        except (APIError, AttributeError) as e:
            self.report(
                item,
                f"couldn't read its custom field values ({e}); check custom field(s) {_names(values)} "
                f"on the target by hand",
            )
            return
        missing = [f for f in values if _empty(current.get(f["name"]))]
        differing = [f for f in values if f not in missing and _norm(current[f["name"]]) != _norm(f["value"])]
        if differing:
            load_log.warning(
                f"{ENTITY}: {item['name']!r}: the target has other values in custom field(s) "
                f"{_names(differing)}; left them as they are"
            )
        if missing:
            self.write(
                item,
                self.ctx.target,
                "patch",
                f"{ENTITY}/{target_id}",
                {"custom_fields": missing},
                f"set custom field(s) {_names(missing)}",
                "set them on the target by hand",
                status_only=True,
            )

    # --- risk classification ---

    def risk_classification(self, item, target_id, state):
        level = item.get("risk_classification_level")
        if level is None or state is None:
            return
        current = state.get("risk_classification_level")
        if current == level:
            return
        if current is not None:
            load_log.warning(
                f"{ENTITY}: {item['name']!r}: the target has risk classification level {current}, the "
                f"source {level}; left it as it is"
            )
            return
        body = {"data": {"type": "use_cases", "attributes": {"risk_classification_level": level}}}
        self.write(
            item,
            self.ctx.target_internal,
            "patch",
            f"{ENTITY}/{target_id}/risk_classification",
            body,
            f"set risk classification level {level}",
            "set it on the target by hand",
        )

    # --- policy packs ---

    def pack_version(self, key):
        """The latest published version of a policy pack on the target, or None."""
        versions = self.cached(f"pack {key}", lambda: self._pack_versions(key))
        published = [v["attributes"]["version"] for v in versions if v["attributes"].get("draft") is False]
        return max(published) if published else None

    def _pack_versions(self, key):
        try:
            return self.ctx.target_internal.get_data(f"policy_pack_bases/{quote(key, safe='')}/versions")
        except APIError as e:
            if e.status_code == 404:
                return []
            raise

    def policy_packs(self, item, target_id, exists):
        keys = [p["key"] for p in item["policy_packs"]]
        if not keys:
            return
        try:
            current = (
                {p["key"] for p in self.ctx.target.get_all(f"{ENTITY}/{target_id}/policy_packs")} if exists else set()
            )
        except APIError as e:
            self.report(item, f"couldn't read its policy packs ({e}); attach {', '.join(keys)} on the target by hand")
            return
        for key in keys:
            if key in current:
                continue
            try:
                version = self.pack_version(key)
            except APIError as e:
                self.report(item, f"couldn't read the versions of policy pack {key} ({e}); attach it by hand")
                continue
            if version is None:
                self.report(
                    item,
                    f"policy pack {key} has no published version on the target; publish it on the target, then re-run",
                )
                continue
            self.write(
                item,
                self.ctx.target,
                "post",
                f"{ENTITY}/{target_id}/policy_packs",
                {"key": key, "version": version},
                f"attach policy pack {key}+{version}",
                "a re-run tries it again",
            )

    # --- risk scenarios and their control links ---

    def catalog_id(self, source_id):
        """The target catalog ID of a source catalog scenario: the risk_scenarios tracker, else the same ID."""
        tracked = self.ctx.tracker("risk_scenarios").target_id(source_id)
        if tracked:
            return tracked
        on_target = self.cached("risk_scenarios", lambda: {s["id"] for s in self.ctx.target.get_all("risk_scenarios")})
        return source_id if source_id in on_target else None

    def risk_type_ids(self, item, scenario):
        tracker = self.ctx.tracker("risk_types")
        index = self.cached("risk_types", lambda: risk_types.target_index(self.ctx))
        ids = []
        for source_id, name in zip(scenario["risk_type_ids"], scenario["risk_type_names"]):
            target_id = tracker.target_id(source_id)
            if target_id is None and name.startswith("unknown:"):
                load_log.warning(
                    f"{ENTITY}: {item['name']!r}: risk scenario {scenario['name']!r}: risk type ID "
                    f"{source_id} doesn't exist on the source; loading without it"
                )
                continue
            if target_id is None:
                try:
                    target_id = index.lookup(name)
                except AmbiguousMatch:
                    target_id = None
            if target_id is None:
                self.report(
                    item,
                    f"risk scenario {scenario['name']!r}: risk type {name!r} isn't on the target; added without it",
                )
                continue
            ids.append(target_id)
        return ids

    def control_on_target(self, key):
        def read():
            keys = {b["id"] for b in self.ctx.target.get_all("policy_control_bases")}
            return keys | {r["source_id"] for r in self.ctx.tracker("controls").records}

        return key in self.cached("control_keys", read)

    def risk_scenarios(self, item, target_id, exists):
        if not item["risk_scenarios"]:
            return
        try:
            current = self.ctx.target.get_all(f"{ENTITY}/{target_id}/risk_scenarios") if exists else []
        except APIError as e:
            self.report(item, f"couldn't read its risk scenarios ({e}); add them on the target by hand")
            return
        by_catalog = {rs["risk_scenario_id"]: rs for rs in current if rs.get("risk_scenario_id")}
        by_name = {rs.get("name"): rs for rs in current if not rs.get("risk_scenario_id")}
        custom_names = set()
        for n, scenario in enumerate(item["risk_scenarios"], 1):
            label = f"risk scenario {scenario['name']!r}"
            if not scenario.get("catalog_id"):
                if scenario["name"] in custom_names:
                    # Custom scenarios are matched by name, so a re-run couldn't tell the second from the first
                    self.report(
                        item,
                        f"the source has more than one risk scenario named {scenario['name']!r}; only "
                        f"the first is copied; add the other on the target by hand",
                    )
                    continue
                custom_names.add(scenario["name"])
            if scenario.get("catalog_id"):
                catalog = self.catalog_id(scenario["catalog_id"])
                if catalog is None:
                    self.report(
                        item, f"{label} ({scenario['catalog_id']}) isn't in the target's catalog; add it by hand"
                    )
                    continue
                existing = by_catalog.get(catalog)
                body = {"risk_scenario_id": catalog}
            else:
                existing = by_name.get(scenario["name"])
                body = {"name": scenario["name"], "description": scenario.get("description") or ""}
                if not existing:
                    type_ids = self.risk_type_ids(item, scenario)
                    if type_ids:
                        body["risk_type_ids"] = type_ids
            if existing:
                rs_id, linked = existing["id"], set(existing.get("control_keys") or [])
            else:
                for field in ("likelihood", "impact"):
                    value = scenario.get(f"inherent_{field}")
                    if isinstance(value, int) and 1 <= value <= 5:  # null or 0 means not set
                        body[f"inherent_risk_{field}"] = value
                ok, resp = self.write(
                    item,
                    self.ctx.target,
                    "post",
                    f"{ENTITY}/{target_id}/risk_scenarios",
                    body,
                    f"add {label}",
                    "a re-run tries it again",
                )
                if not ok:
                    continue
                self._report_residual(item, scenario)
                if resp is None:
                    rs_id, linked = f"dry:{n}", set()
                else:
                    try:
                        created = resp.json()
                        rs_id, linked = created["id"], set(created.get("control_keys") or [])
                    except (ValueError, KeyError, TypeError):
                        self.report(
                            item,
                            f"{label} was added, but the response had no ID; link its controls "
                            f"{', '.join(scenario['control_keys'])} by hand",
                        )
                        continue
            self.control_links(item, target_id, rs_id, scenario, linked)

    def _report_residual(self, item, scenario):
        for field in ("likelihood", "impact"):
            inherent, residual = scenario.get(f"inherent_{field}"), scenario.get(f"residual_{field}")
            if residual is not None and residual != inherent:
                self.report(
                    item,
                    f"risk scenario {scenario['name']!r}: residual {field} {residual} differs from "
                    f"inherent {inherent} on the source; the target derives residual values, so set it "
                    f"by hand",
                )

    def control_links(self, item, target_id, rs_id, scenario, linked):
        for key in scenario["control_keys"]:
            if key in linked:
                continue
            if not self.control_on_target(key):
                self.report(
                    item, f"risk scenario {scenario['name']!r}: control {key} isn't on the target; link it by hand"
                )
                continue
            self.write(
                item,
                self.ctx.target,
                "post",
                f"{ENTITY}/{target_id}/risk_scenarios/{rs_id}/controls",
                {"key": key},
                f"link control {key} to risk scenario {scenario['name']!r}",
                "a re-run tries it again",
            )

    # --- control evidence ---

    def control_evidence(self, item, target_id, exists):
        if not item["controls"]:
            return
        if not exists:
            for control in item["controls"]:
                load_log.info(
                    f"{ENTITY}: would add {len(control['evidence'])} evidence item(s) to control "
                    f"{control['key']} on {item['name']!r}, if the control reaches the new use case"
                )
            return
        try:
            rows = self.ctx.target_internal.get_all_cursor(f"{ENTITY}/{target_id}/controls")
        except APIError as e:
            self.report(item, f"couldn't read its controls ({e}); add control evidence on the target by hand")
            return
        by_key = {}
        for row in rows:
            attrs = row.get("attributes") or {}
            key = attrs.get("control_key") or attrs.get("key")
            # A model-scoped control has a row per linked model; the use case's own row comes first
            if key not in by_key or (by_key[key][1].get("model_id") and not attrs.get("model_id")):
                by_key[key] = (row["id"], attrs)
        for control in item["controls"]:
            key = control["key"]
            if key not in by_key:
                self.report(
                    item,
                    f"control {key} isn't on the use case on the target (controls reach a use case "
                    f"through a policy pack, a risk scenario or a trigger); add its evidence by hand",
                )
                continue
            control_id, attrs = by_key[key]
            compliances = {c.get("requirement_id"): c for c in attrs.get("compliances") or []}
            for ev in control["evidence"]:
                compliance = compliances.get(ev["hash_key"])
                if compliance is None:
                    self.report(
                        item,
                        f"control {key} on the target has no evidence requirement {ev['hash_key']}; "
                        f"add that evidence by hand",
                    )
                    continue
                if compliance.get("evidence_ids"):
                    continue
                self.write(
                    item,
                    self.ctx.target,
                    "post",
                    f"{ENTITY}/{target_id}/controls/{control_id}/evidences",
                    {"control_requirement_id": ev["hash_key"], "data": ev["data"]},
                    f"add evidence to control {key}",
                    "a re-run tries it again",
                    status_only=True,
                )

    # --- domains ---

    def domains(self, item, target_id, state):
        wanted = item["domain_ids"]
        if not wanted or state is None:
            return

        def read():
            ids = {d["id"] for d in self.ctx.target_internal.get_data("domains")}
            # A domain keeps its ID on the target, so the tracked source IDs are target IDs (also in a dry run)
            return ids | {r["source_id"] for r in self.ctx.tracker("domains").records}

        try:
            available = self.cached("domains", read)
        except APIError as e:
            self.report(item, f"couldn't read the target's domains ({e}); add domain(s) {', '.join(wanted)} by hand")
            return
        missing = [d for d in wanted if d not in available]
        if missing:
            self.report(
                item, f"domain(s) {', '.join(missing)} aren't on the target; load domains first, then add them by hand"
            )
        if state and state.get("domain_ids") is None:
            # Missing or null is unreadable, not "no domains": the PATCH below replaces the whole set
            self.report(item, "its current domains couldn't be read; add domains on the target by hand")
            return
        current = list(state.get("domain_ids") or [])
        add = [d for d in wanted if d in available and d not in current]
        if not add:
            return
        # The PATCH replaces the whole set, so it carries the current domains too
        body = {"data": [{"type": "domains", "id": d} for d in current + add]}
        self.write(
            item,
            self.ctx.target_internal,
            "patch",
            f"{ENTITY}/{target_id}/relationships/domains",
            body,
            f"add domain(s) {', '.join(add)}",
            "add them on the target by hand",
        )

    # --- model and vendor links ---

    def links(self, item, target_id, exists, kind):
        source_ids = item[f"{kind[:-1]}_ids"]
        if not source_ids:
            return
        names = item.get(f"{kind[:-1]}_names") or {}
        tracked = self.ctx.tracker(kind).id_map()
        wanted = {}
        for source_id in source_ids:
            found = tracked.get(source_id)
            if found is None:
                # Built-in models and vendors keep their IDs across tenants
                on_target = self.cached(kind, lambda: {x["id"] for x in self.ctx.target.get_all(kind)})
                found = source_id if source_id in on_target else None
            if found is None:
                self.report(
                    item,
                    f"{kind[:-1]} {names.get(source_id) or source_id} ({source_id}) isn't tracked or "
                    f"on the target; load {kind} first, then link it by hand",
                )
                continue
            wanted.setdefault(found, names.get(source_id) or source_id)
        if not wanted:
            return
        try:
            current = {x["id"] for x in self.ctx.target.get_all(f"{ENTITY}/{target_id}/{kind}")} if exists else set()
        except APIError as e:
            self.report(item, f"couldn't read its {kind} links ({e}); link {', '.join(wanted.values())} by hand")
            return
        for found, label in wanted.items():
            if found not in current:
                self.write(
                    item,
                    self.ctx.target,
                    "post",
                    f"{ENTITY}/{target_id}/{kind}",
                    {"id": found},
                    f"link {kind[:-1]} {label}",
                    "a re-run tries it again",
                )

    # --- other stakeholders ---

    def stakeholders(self, item, target_id, exists):
        if not item["stakeholders"]:
            return
        try:
            rows = self.ctx.target_internal.get_data(f"{ENTITY}/{target_id}/stakeholders") if exists else []
        except APIError as e:
            self.report(item, f"couldn't read its stakeholders ({e}); add them on the target by hand")
            return
        current = {}
        for row in rows:
            attrs = row.get("attributes") or {}
            current[attrs.get("team_id") or attrs.get("user_id")] = (row["id"], attrs.get("role"))
        for who in item["stakeholders"]:
            role = who["role"]
            found = self.people.lookup(who)
            if found is None:
                self.report(item, f"stakeholder {_who(who)} ({role}) isn't on the target; add them by hand")
                continue
            hint = (
                "only read-only admin users can be observers on the target; the kit doesn't change the role"
                if role == "observer"
                else "add them on the target by hand"
            )
            if found[0] in current:
                row_id, current_role = current[found[0]]
                if current_role == role:
                    continue
                if current_role == "owner":
                    load_log.warning(
                        f"{ENTITY}: {item['name']!r}: {_who(who)} owns it on the target, where the source "
                        f"has them as {role}; left them as owner"
                    )
                    continue
                body = {"data": {"type": "stakeholders", "attributes": {"role": role}}}
                self.write(
                    item,
                    self.ctx.target_internal,
                    "patch",
                    f"{ENTITY}/{target_id}/stakeholders/{row_id}",
                    body,
                    f"make {_who(who)} a {role}",
                    hint,
                )
                continue
            key = "team_id" if found[1] == "team" else "user_id"
            body = {"data": {"type": "stakeholders", "attributes": {key: found[0], "role": role}}}
            ok, _ = self.write(
                item,
                self.ctx.target_internal,
                "post",
                f"{ENTITY}/{target_id}/stakeholders",
                body,
                f"add {_who(who)} as {role}",
                hint,
            )
            if ok:
                current[found[0]] = (None, role)

    # --- questionnaire attachments and answers ---

    def questionnaires(self, item, target_id, exists):
        wanted = list(dict.fromkeys(a["key"] for a in item["attachments"]))
        if not wanted:
            return
        try:
            current = (
                {a["key"]: a["version"] for a in answers.read_attachments(self.ctx.target, ENTITY, target_id)}
                if exists
                else {}
            )
        except APIError as e:
            self.report(
                item, f"couldn't read its questionnaire attachments ({e}); no questionnaires were attached or answered"
            )
            return
        attached = dict(current)
        for key in wanted:
            if key in current:
                continue  # an entity can hold only one version of a key; answers map onto the one it has
            version_id = self.versions.get(key)
            if version_id is None:
                version_id = self.versions[key] = published_version_id(self.ctx.target, key)
            if version_id is None:
                self.report(
                    item,
                    f"questionnaire {key} has no published version on the target; publish it on the "
                    f"target, then re-run",
                )
                continue
            version = int(version_id.rsplit("+", 1)[1])
            ok, _ = self.write(
                item,
                self.ctx.target,
                "post",
                f"{ENTITY}/{target_id}/questionnaires",
                {"key": key, "version": version},
                f"attach questionnaire {key}+{version}",
                "a re-run tries it again",
            )
            if ok:
                attached[key] = version
        if item["answers"]:
            counts = answers.write_answers(
                self.ctx, ENTITY, target_id, item["answers"], attachments=attached, label=f"{ENTITY}: {item['name']!r}"
            )
            self.totals = answers.add_counts(self.totals, counts)

    # --- workflow stage, then step ---

    def stages(self):
        return self.cached(
            "stages",
            lambda: {s["id"]: s.get("attributes") or {} for s in self.ctx.target_internal.get_data("workflow_stages")},
        )

    def stage(self, item, target_id, exists):
        want = item.get("stage_name")
        if not want:
            return
        try:
            stages = self.stages()
        except APIError as e:
            self.report(item, f"couldn't read the target's workflow stages ({e}); move it to stage {want!r} by hand")
            return
        if not exists:
            self._stage_dry(item, stages, want)
            return
        state = self.state(item, target_id, exists)
        if state is None:
            return
        current = state.get("workflow_stage_id")
        if current not in stages:
            self.report(
                item,
                f"its stage on the target ({current}) isn't in the target's stage list; move it to "
                f"stage {want!r} by hand",
            )
            return
        moved = False
        if stages[current].get("name") != want:
            ahead = _walk(stages, current, "child_id")
            found = next((sid for sid in ahead if stages[sid].get("name") == want), None)
            if found is None:
                if any(stages[sid].get("name") == want for sid in _walk(stages, current, "parent_id")):
                    load_log.info(f"{ENTITY}: {item['name']!r} is already past stage {want!r} on the target; left it")
                else:
                    self.report(item, f"stage {want!r} isn't in its workflow on the target; move it by hand")
                return
            body = {"data": {"type": "use_cases", "attributes": {"workflow_stage_id": found}}}
            moved, _ = self.write(
                item,
                self.ctx.target_internal,
                "patch",
                f"{ENTITY}/{target_id}/workflow_stage",
                body,
                f"move it to stage {want!r}",
                "move it on the target by hand",
            )
            if not moved:
                return
        self.step(item, target_id, START_STEP if moved else state.get("workflow_stage_step"))

    def _stage_dry(self, item, stages, want):
        """A use case a dry run would create doesn't exist, so its workflow is unknown: any start stage is assumed."""
        named = [sid for sid, s in stages.items() if s.get("name") == want]
        if not named:
            self.report(item, f"stage {want!r} isn't on the target; move it by hand")
            return
        if not any(not stages[sid].get("parent_id") for sid in named):
            load_log.info(f"{ENTITY}: would move {item['name']!r} to stage {want!r}")
        self.step(item, "dry", START_STEP)

    def step(self, item, target_id, current):
        want = item.get("step")
        if not want or want == current:
            return
        body = {"data": {"type": "use_cases", "attributes": {"workflow_stage_step": want}}}
        self.write(
            item,
            self.ctx.target_internal,
            "patch",
            f"{ENTITY}/{target_id}/step",
            body,
            f"set its step to {want!r}",
            "set it on the target by hand",
        )


def _walk(stages, start, link):
    """Stage IDs reached from start (not included) by following parent_id or child_id."""
    found, seen = [], {start}
    nxt = stages.get(start, {}).get(link)
    while nxt and nxt in stages and nxt not in seen:
        found.append(nxt)
        seen.add(nxt)
        nxt = stages[nxt].get(link)
    return found


def _norm(value):
    return sorted(value, key=str) if isinstance(value, list) else value


def load(ctx):
    items = load_extract(ctx.data_dir, ENTITY)
    tracker = ctx.tracker(ENTITY)
    verb = "would create" if ctx.dry_run else "created"
    target_names = {u["name"] for u in ctx.target.get_all("use_cases")}
    field_names = field_names_for(ctx, USE_CASE_TYPE)
    versions = {}
    parts = _Parts(ctx, versions)
    people = parts.people

    def check(item):
        """Raises MissingReference for what would lose data or be rejected, before anything is written."""
        keys = list(dict.fromkeys(a["key"] for a in item["attachments"]))
        for key in keys:
            if key not in versions:
                versions[key] = published_version_id(ctx.target, key)
        unpublished = [k for k in keys if versions[k] is None]
        if unpublished:
            names = ", ".join(f"questionnaire {k}" for k in unpublished)
            has, it = ("has", "it") if len(unpublished) == 1 else ("have", "them")
            raise MissingReference(
                f"{names} {has} no published version on the target; publish {it} on the target, then re-run"
            )
        unknown = [f for f in item["custom_fields"] if f["name"] not in field_names]
        if unknown:
            raise MissingReference(
                f"custom field(s) {_names(unknown)} don't apply to use cases on the target; load custom_fields first"
            )

    def run_parts(item, target_id, answers_ok):
        """parts.run for one use case. An API error stops only this use case. answers_ok None: a re-run, which
        reads whether its triggers are still paused."""
        try:
            if answers_ok is None:
                answers_ok = _answers_allowed_on_rerun(ctx, parts, item, target_id)
            parts.run(item, target_id, answers_ok)
        except APIError as e:
            parts.report(item, f"stopped part way ({e}); a re-run completes what is missing")

    paused = 0
    seen = set()
    for item in items:
        name = item["name"]
        label = f"use case {name!r}"
        record = tracker.find(item["id"])
        if record:
            load_log.debug(f"{ENTITY}: {name!r} already tracked, skipping")
            ctx.summary.record(ENTITY, "skipped")
            seen.add(name)
            if record["origin"] == "created":
                # An earlier run may have stopped part way; every part only adds what is missing. A dry run reads
                # the same way and reports what it would add, since every write is gated.
                run_parts(item, record["target_id"], None)
            continue
        if name in seen:
            load_log.warning(
                f"{ENTITY}: more than one source use case is named {name!r}; skipping this one "
                f"({item['id']}); to copy both, rename one on the source, then re-extract"
            )
            ctx.summary.record(ENTITY, "skipped")
            continue
        seen.add(name)
        if name in target_names:
            load_log.warning(
                f"{ENTITY}: a use case named {name!r} is already on the target; skipping it (the kit "
                f"doesn't adopt or change existing use cases). If an earlier kit run created it and was "
                f"interrupted, delete it on the target and re-run."
            )
            ctx.summary.record(ENTITY, "skipped")
            continue
        try:
            check(item)
            owner = people.lookup(item.get("owner"))
        except (MissingReference, APIError) as e:
            load_log.error(f"{ENTITY}: skipping {name!r}: {e}")
            ctx.summary.record(ENTITY, "failed")
            continue
        payload = {"name": name, "description": item.get("description") or ""}
        if owner:
            payload["owner"] = {"owner_id": owner[0], "owner_type": owner[1]}
        else:
            message = (
                f"{ENTITY}: {name!r}: its owner ({_who(item.get('owner'))}) isn't on the target, so the API "
                f"key's user will own it; change the owner on the target by hand"
            )
            load_log.warning(message)
            ctx.summary.flag(ENTITY, message)
        try:
            # Internal API: the Integration API's create first looks the name up with an unescaped query, so a name
            # with "&", "#", "+" or "%" fails there. Both reach the same backend create.
            created = create_target(
                ctx,
                ENTITY,
                "use_cases",
                {"data": {"type": "use_cases", "attributes": payload}},
                item["id"],
                name,
                client=ctx.target_internal,
                id_of=lambda body: (body["data"]["id"], body["data"]["attributes"].get("entity_id")),
            )
        except APIError as e:
            if e.status_code is None:
                # A lost connection: the server may have created it, and a re-run would skip it by name
                load_log.error(
                    f"{ENTITY}: {label} may have been created on the target before the connection was "
                    f"lost; check the target: if {label} is there, delete it, then re-run ({e})"
                )
            else:
                load_log.error(f"{ENTITY}: creating {name!r} failed: {e}")
            created = None
        if created is None:
            ctx.summary.record(ENTITY, "failed")
            continue
        target_id, entity_id = (created, created) if isinstance(created, str) else created
        tracker.record(item["id"], target_id, name, "created")
        load_log.info(f"{ENTITY}: {verb} {name!r}")
        ctx.summary.record(ENTITY, "created")
        if entity_id:
            mode = trigger_mode.pause_for_load(ctx, entity_id, f"{ENTITY}: {label}")
        else:
            load_log.error(
                f"{ENTITY}: the create response for {name!r} had no entity_id, so its triggers can't be "
                f"paused; no questionnaires will be attached or answered"
            )
            mode = None
        if mode is None:
            ctx.summary.flag(
                ENTITY,
                f"{name!r}: triggers not paused, so no questionnaires were attached or answered; "
                f"attach and answer them on the target by hand",
            )
        elif mode == "paused":
            paused += 1
        run_parts(item, target_id, mode is not None)

    if parts.totals is not None:
        answers.report_totals(ctx, ENTITY, parts.totals)
    trigger_mode.summary_note(ctx, paused)


def _answers_allowed_on_rerun(ctx, parts, item, target_id):
    """A tracked use case gets questionnaires and answers only while its triggers are still paused: the operator may
    have turned them back on, and answers written then would fire them."""
    if not item["attachments"] and not item["answers"]:
        return True
    try:
        mode = trigger_mode.read_mode(ctx.target_internal, f"{ENTITY}/{target_id}")
    except APIError as e:
        parts.report(item, f"couldn't read its trigger mode ({e}); no questionnaires were attached or answered")
        return False
    if mode == "full":
        # It may never have been paused (a pause that failed on an earlier run), so not "turned back on"
        load_log.warning(
            f"{ENTITY}: triggers aren't paused on use case {item['name']!r} (mode full); the kit won't "
            f"attach questionnaires or write answers. Set Partial Pause in the gear menu, Triggers and "
            f"actions settings, then re-run, or leave it."
        )
        return False
    return True
