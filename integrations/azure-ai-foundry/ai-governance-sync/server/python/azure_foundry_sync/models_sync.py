"""Sync the Azure AI Foundry model catalog into the Credo AI models registry.

Matched by name against a single upfront listing call — per Trevor Berreth (2026-09-29): the API
has no server-side filter-by-name yet, so this pages through the registry once, builds a set of
existing names, and only POSTs models that aren't already there. A model already in the set is
skipped with zero API calls, rather than attempting a create and reading a `422` back.
"""

from __future__ import annotations

import logging

from azure_foundry_sync.azure_client import AzureClient
from azure_foundry_sync.credo_v2 import CredoApiError, CredoClientV2
from azure_foundry_sync.summary import SyncCounts

logger = logging.getLogger("azure_foundry_sync.models_sync")

SOURCE_NAME = "Azure AI Foundry"


def _model_attrs(model: dict) -> dict:
    return {
        "name": f"Azure {model.get('displayName')} {model.get('version')}",
        "description": "imported from Azure Foundry",
        "source": SOURCE_NAME,
        "summary": f"{model.get('assetId')} \n {model.get('summary')}",
    }


def _ensure_source(credo: CredoClientV2, *, dry_run: bool) -> None:
    if dry_run:
        logger.info("[dry-run] would create source %r", SOURCE_NAME)
        return
    try:
        resp = credo.create_source(SOURCE_NAME)
        if resp.status_code in (200, 201):
            logger.info("%s Source created successfully", SOURCE_NAME)
    except CredoApiError as exc:
        logger.error("Failed to create source %r: %s", SOURCE_NAME, exc)


def sync_models(
    credo: CredoClientV2, azure: AzureClient, *, dry_run: bool
) -> SyncCounts:
    counts = SyncCounts(label="models")

    try:
        azure_models = azure.list_foundation_models()
    except Exception:
        logger.exception("Failed to fetch Azure AI Foundry model catalog")
        counts.errors += 1
        return counts

    _ensure_source(credo, dry_run=dry_run)

    try:
        existing_names = {m["attributes"]["name"] for m in credo.list_models()}
    except CredoApiError as exc:
        logger.error(
            "Failed to list existing models, falling back to blind create: %s", exc
        )
        existing_names = set()

    for model in azure_models:
        counts.scanned += 1
        attrs = _model_attrs(model)

        if attrs["name"] in existing_names:
            counts.skipped += 1
            continue

        if dry_run:
            logger.info("[dry-run] would create model %r", attrs["name"])
            counts.created += 1
            continue

        try:
            resp = credo.create_model(attrs)
            if resp.status_code in (200, 201):
                counts.created += 1
            elif resp.status_code == 422:
                # Race between the listing and this create (or the listing's page was
                # truncated) — still not an error, just a late-discovered duplicate.
                counts.skipped += 1
            else:
                raise CredoApiError(
                    f"create_model {attrs['name']!r} failed: {resp.status_code}",
                    resp.status_code,
                    resp.text,
                )
        except CredoApiError as exc:
            logger.error("Failed to create model %r: %s", attrs["name"], exc)
            counts.errors += 1

    return counts
