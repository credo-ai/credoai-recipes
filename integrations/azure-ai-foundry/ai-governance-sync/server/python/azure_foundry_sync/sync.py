"""Orchestrates the four sync domains against one Credo AI tenant, one shared Credo AI client."""

from __future__ import annotations

from azure_foundry_sync.azure_client import AzureClient
from azure_foundry_sync.config import Settings
from azure_foundry_sync.controls_sync import sync_controls
from azure_foundry_sync.credo_v2 import CredoClientV2
from azure_foundry_sync.custom_fields_sync import sync_custom_fields
from azure_foundry_sync.models_sync import sync_models
from azure_foundry_sync.questionnaire_sync import sync_questionnaire
from azure_foundry_sync.summary import SyncCounts


def run(settings: Settings, *, dry_run: bool) -> list[SyncCounts]:
    results: list[SyncCounts] = []

    if not any(
        (
            settings.sync_models,
            settings.sync_controls,
            settings.sync_custom_fields,
            settings.sync_questionnaire,
        )
    ):
        return results

    with CredoClientV2(settings) as credo:
        if settings.sync_models:
            azure = AzureClient(settings)
            try:
                results.append(sync_models(credo, azure, dry_run=dry_run))
            finally:
                azure.close()

        if settings.sync_controls:
            results.append(sync_controls(credo, dry_run=dry_run))
        if settings.sync_custom_fields:
            results.append(sync_custom_fields(credo, dry_run=dry_run))
        if settings.sync_questionnaire:
            results.append(sync_questionnaire(credo, settings, dry_run=dry_run))

    return results
