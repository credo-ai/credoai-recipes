"""Create the custom fields defined in config/custom_fields.json.

Matched by the API's own 422 behavior — a field with the same name that already exists is
reported as skipped, not an error.
"""

from __future__ import annotations

import json
import logging

from azure_foundry_sync.config import config_path
from azure_foundry_sync.credo_v2 import CredoApiError, CredoClientV2
from azure_foundry_sync.summary import SyncCounts

logger = logging.getLogger("azure_foundry_sync.custom_fields_sync")


def sync_custom_fields(credo: CredoClientV2, *, dry_run: bool) -> SyncCounts:
    counts = SyncCounts(label="custom fields")

    path = config_path("custom_fields.json")
    try:
        fields = json.loads(path.read_text()).get("custom_fields", [])
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Failed to load %s: %s", path, exc)
        counts.errors += 1
        return counts

    for field in fields:
        counts.scanned += 1
        name = field.get("name", "Unknown Field")

        if dry_run:
            logger.info("[dry-run] would create custom field %r", name)
            counts.created += 1
            continue

        try:
            resp = credo.create_custom_field(field)
            if resp.status_code in (200, 201):
                counts.created += 1
            elif resp.status_code == 422:
                counts.skipped += 1
            else:
                raise CredoApiError(
                    f"create_custom_field {name!r} failed: {resp.status_code}",
                    resp.status_code,
                    resp.text,
                )
        except CredoApiError as exc:
            logger.error("Failed to create custom field %r: %s", name, exc)
            counts.errors += 1

    return counts
