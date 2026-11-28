"""Entities in load order. Each module (or object) has ENTITY, extract(ctx) -> int and load(ctx) -> None.

Order: risk_types, risk_type_weights, custom_fields, controls, policy_packs, risk_scenarios, questionnaires, vendors,
users, teams, triggers, models, domains, use_cases, model_answers, vendor_answers.
Modules in kit/internal/ need the internal API for at least part of their work; each says why.
"""

from kit.entities import custom_fields, models, risk_types, use_cases, vendors
from kit.entities.entity_answers import model_answers, vendor_answers
from kit.internal import (
    controls,
    domains,
    policy_packs,
    questionnaires,
    risk_scenarios,
    risk_type_weights,
    teams,
    triggers,
    users,
)

ENTITIES = {
    m.ENTITY: m
    for m in (
        risk_types,
        risk_type_weights,
        custom_fields,
        controls,
        policy_packs,
        risk_scenarios,
        questionnaires,
        vendors,
        users,
        teams,
        triggers,
        models,
        domains,
        use_cases,
        model_answers,
        vendor_answers,
    )
}
