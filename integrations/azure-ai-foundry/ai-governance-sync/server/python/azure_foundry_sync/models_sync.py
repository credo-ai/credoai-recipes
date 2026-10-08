"""Sync the Azure AI Foundry model catalog into the Credo AI models registry.

Matched by name against a full listing — the SDK has no server-side filter-by-name yet, so this
pages through the existing registry once (`client.models.list_all()`) and builds a set of names. A
model already in that set is skipped with zero API calls; only genuinely new models get a `create`.
"""

from __future__ import annotations

import logging

from credoai import CredoAI
from credoai.errors import ApiError
from credoai.models import ModelCreate

from azure_foundry_sync.azure_client import AzureClient
from azure_foundry_sync.credo_private import CredoPrivateClient
from azure_foundry_sync.summary import SyncCounts

logger = logging.getLogger("azure_foundry_sync.models_sync")

SOURCE_NAME = "Azure AI Foundry"


def _model_attrs(model: dict) -> ModelCreate:
    return ModelCreate(
        name=f"Azure {model.get('displayName')} {model.get('version')}",
        source=SOURCE_NAME,
        summary=f"imported from Azure Foundry — {model.get('assetId')}\n{model.get('summary')}",
    )


def _ensure_source(credo_private: CredoPrivateClient, *, dry_run: bool) -> None:
    if dry_run:
        logger.info("[dry-run] would create source %r", SOURCE_NAME)
        return
    try:
        resp = credo_private.create_source(SOURCE_NAME)
        if resp.status_code in (200, 201):
            logger.info("%s Source created successfully", SOURCE_NAME)
    except Exception as exc:
        logger.error("Failed to create source %r: %s", SOURCE_NAME, exc)


def sync_models(
    credo: CredoAI,
    azure: AzureClient,
    credo_private: CredoPrivateClient,
    *,
    dry_run: bool,
) -> SyncCounts:
    counts = SyncCounts(label="models")

    try:
        azure_models = azure.list_foundation_models()
    except Exception:
        logger.exception("Failed to fetch Azure AI Foundry model catalog")
        counts.errors += 1
        return counts

    _ensure_source(credo_private, dry_run=dry_run)

    try:
        existing_names = {m.name for m in credo.models.list_all()}
    except ApiError as exc:
        logger.error(
            "Failed to list existing models, falling back to blind create: %s", exc
        )
        existing_names = set()

    for model in azure_models:
        counts.scanned += 1
        attrs = _model_attrs(model)

        if attrs.name in existing_names:
            counts.skipped += 1
            continue

        if dry_run:
            logger.info("[dry-run] would create model %r", attrs.name)
            counts.created += 1
            continue

        try:
            credo.models.create(attrs)
            counts.created += 1
        except ApiError as exc:
            if exc.status_code == 422:
                # Race between the listing and this create — still not an error.
                counts.skipped += 1
            else:
                logger.error("Failed to create model %r: %s", attrs.name, exc)
                counts.errors += 1

    return counts
