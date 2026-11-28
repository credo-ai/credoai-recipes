"""An entity's trigger mode, set to Partial Pause before the kit attaches questionnaires or writes answers.

Internal API only: the Integration API shows trigger_mode but can't change it, and no create or write takes a "skip
triggers" option. Every entity has a mode: full (the default), hidden_questions_only (Partial Pause in the UI) or
paused (Full Pause). The server still evaluates every trigger; the mode only decides what it applies. Partial Pause
applies only question reveal and hide, so copied answers to questions a trigger reveals stay visible, while risk
categories, controls, risk scenarios, alerts and the rest are held back. The kit never raises the mode again: raising
it replays the held-back work, which the operator previews and approves in the UI.

Models and vendors answer 404 unless the tenant has MEG triggers; without it triggers don't reach them anyway.
"""

import logging

from kit.clients.base import APIError

PARTIAL_PAUSE = "hidden_questions_only"
log = logging.getLogger("kit.load")


def read_mode(internal, entity_path):
    """The trigger_mode of the entity at entity_path (for example 'use_cases/<id>')."""
    body = internal.get_json(entity_path)
    try:
        return body["data"]["attributes"]["trigger_mode"]
    except (KeyError, TypeError) as e:
        raise APIError(f"GET {entity_path}: no trigger mode in the response", body=str(body)[:300]) from e


def pause_for_load(ctx, entity_id, label):
    """Sets the entity's trigger mode to Partial Pause on the target.

    Returns "paused" (also in dry run, which writes nothing), "not_available" when the target has no trigger mode for
    this entity (404), or None after logging an error: the caller then writes no questionnaires or answers to it.
    label names the entity in the log, for example "use case 'Chatbot'".
    """
    path = f"entities/{entity_id}/trigger_mode"
    body = {"data": {"type": "entities", "attributes": {"trigger_mode": PARTIAL_PAUSE}}}
    try:
        resp = ctx.target_internal.patch(path, body)
    except APIError as e:
        problem = str(e)
    else:
        if resp is None or 200 <= resp.status_code < 300:
            return "paused"
        if resp.status_code == 404:
            log.debug(f"{label}: the target has no trigger mode for it (404), so triggers don't reach it")
            return "not_available"
        problem = f"{resp.status_code} {resp.text[:300]}"
    log.error(
        f"{label}: setting its triggers to Partial Pause failed ({problem}); no answers will be written to it, "
        f"so the target's triggers can't change them"
    )
    return None


def summary_note(ctx, n):
    """The operator's reminder that the kit left triggers paused on the use cases it created. Logged now and kept as
    a note for the end-of-run summary."""
    if not n:
        return
    are = "would be" if ctx.dry_run else "are"
    noun = "use case" if n == 1 else "use cases"
    message = (
        f"Triggers {are} paused on {n} migrated {noun}, so the copies match the source. To turn them back on, "
        f"open the use case, then the gear menu, then 'Triggers and actions settings', and choose Enabled. It "
        f"previews the changes before applying them."
    )
    log.info(message)
    ctx.summary.note(message)  # again after the summary table, since later entities' lines follow this one
