"""Orchestrates the four sync domains against one Credo AI tenant.

Models, custom fields, and the questionnaire go through the official `pycredoai` SDK. Controls, the
`Source` record, and the entity-type id lookup custom fields need stay on the private API — see
`credo_private.py` for why.
"""

from __future__ import annotations

from contextlib import ExitStack

from credoai import CredoAI

from azure_foundry_sync.azure_client import AzureClient
from azure_foundry_sync.config import Settings
from azure_foundry_sync.controls_sync import sync_controls
from azure_foundry_sync.credo_private import CredoPrivateClient
from azure_foundry_sync.custom_fields_sync import sync_custom_fields
from azure_foundry_sync.models_sync import sync_models
from azure_foundry_sync.questionnaire_sync import sync_questionnaire
from azure_foundry_sync.summary import SyncCounts


def run(settings: Settings, *, dry_run: bool) -> list[SyncCounts]:
    results: list[SyncCounts] = []

    needs_sdk = (
        settings.sync_models
        or settings.sync_custom_fields
        or settings.sync_questionnaire
    )
    if not (needs_sdk or settings.sync_controls):
        return results

    with ExitStack() as stack:
        credo = None
        if needs_sdk:
            credo = stack.enter_context(
                CredoAI(
                    base_url=settings.credoai_api_url,
                    api_key=settings.credoai_api_key,
                    tenant=settings.credoai_tenant,
                )
            )

        credo_private = None
        if (
            settings.sync_models
            or settings.sync_controls
            or settings.sync_custom_fields
        ):
            credo_private = stack.enter_context(CredoPrivateClient(settings))

        if settings.sync_models:
            azure = AzureClient(settings)
            try:
                # Fail here, before any domain has written anything, if Azure auth is wrong.
                azure.authenticate()
                results.append(
                    sync_models(credo, azure, credo_private, dry_run=dry_run)
                )
            finally:
                azure.close()

        if settings.sync_controls:
            results.append(sync_controls(credo_private, dry_run=dry_run))
        if settings.sync_custom_fields:
            results.append(sync_custom_fields(credo, credo_private, dry_run=dry_run))
        if settings.sync_questionnaire:
            results.append(sync_questionnaire(credo, settings, dry_run=dry_run))

    return results
