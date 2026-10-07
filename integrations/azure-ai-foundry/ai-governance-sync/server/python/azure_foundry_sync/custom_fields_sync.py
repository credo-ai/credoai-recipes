"""Create the custom fields defined in config/custom_fields.json.

Matched by name against a full listing, same dedup philosophy as models_sync.py. Uses
`custom_fields.list()` directly rather than `list_all()` — the SDK's auto-pagination helper has a
bug for this resource (it calls `self.list(page_limit=..., page_after=...)`, but
`CustomFieldsResource.list()` doesn't accept those params at all). Not a workaround we need anyway:
field *definitions* are a small, bounded, tenant-wide set (unlike models), so the endpoint takes no
pagination params and always returns everything in one response.

`config/custom_fields.json` scopes each field to a `target` entity type by name (e.g. `"use_case"`),
so the field only appears on that kind of record. `CustomFieldCreate.entity_type_ids` takes entity
type *ids*, not names — sending a name makes the server fail with a 500 — and the ids are different
on every tenant. Neither the Integration API nor the SDK can list them, so they are looked up
through the private API (`CredoPrivateClient.entity_type_ids`), once, and only if a field actually
needs creating.

A field whose `target` can't be resolved is reported as an error and skipped. It is deliberately
not created without scoping: omitting `entity_type_ids` makes a field apply to every entity type.
A field with no `target` at all is created unscoped on purpose.
"""

from __future__ import annotations

import json
import logging

from credoai import CredoAI
from credoai.errors import ApiError
from credoai.models import CustomFieldCreate

from azure_foundry_sync.config import config_path
from azure_foundry_sync.credo_private import CredoPrivateApiError, CredoPrivateClient
from azure_foundry_sync.summary import SyncCounts

logger = logging.getLogger("azure_foundry_sync.custom_fields_sync")


def sync_custom_fields(
    credo: CredoAI, credo_private: CredoPrivateClient, *, dry_run: bool
) -> SyncCounts:
    counts = SyncCounts(label="custom fields")

    path = config_path("custom_fields.json")
    try:
        fields = json.loads(path.read_text()).get("custom_fields", [])
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Failed to load %s: %s", path, exc)
        counts.errors += 1
        return counts

    try:
        existing_names = {f.name for f in credo.custom_fields.list().items}
    except ApiError as exc:
        logger.error(
            "Failed to list existing custom fields, falling back to blind create: %s",
            exc,
        )
        existing_names = set()

    entity_type_ids: dict[str, str] | None = None  # looked up on first use

    for field in fields:
        counts.scanned += 1
        name = field.get("name", "Unknown Field")

        if name in existing_names:
            counts.skipped += 1
            continue

        target = field.get("target")
        scope = None
        if target:
            if entity_type_ids is None:
                try:
                    entity_type_ids = credo_private.entity_type_ids()
                except CredoPrivateApiError as exc:
                    logger.error("Failed to look up entity type ids: %s", exc)
                    entity_type_ids = {}
            if target not in entity_type_ids:
                logger.error(
                    "Custom field %r targets entity type %r, which this tenant doesn't have "
                    "(known: %s) — skipped rather than created for every entity type",
                    name,
                    target,
                    ", ".join(sorted(entity_type_ids)) or "none found",
                )
                counts.errors += 1
                continue
            scope = [entity_type_ids[target]]

        if dry_run:
            logger.info(
                "[dry-run] would create custom field %r%s",
                name,
                f" scoped to {target}" if target else "",
            )
            counts.created += 1
            continue

        try:
            credo.custom_fields.create(
                CustomFieldCreate(
                    name=name,
                    type=field.get("type"),
                    element_type=field.get("element_type"),
                    metadata=field.get("metadata"),
                    entity_type_ids=scope,
                )
            )
            counts.created += 1
        except ApiError as exc:
            if exc.status_code == 422:
                counts.skipped += 1
            else:
                logger.error("Failed to create custom field %r: %s", name, exc)
                counts.errors += 1

    return counts
