"""Create and publish the MSFT-* policy controls from config/policy_controls/.

Each control directory holds a `base.yaml` (the control's key/metadata) and one or more `vN.yaml`
version files (evidence requirements, risk types, description). The latest local version file is
what gets synced.

Dedup algorithm per Trevor Berreth (2026-09-29) — the API has no server-side content dedup, so this
does it client-side:
  1. List the control's existing versions, take the highest.
  2. Compare its `info`, `risk_type_ids`, `evidence_requirements` against our desired content
     (lists sorted, `None`/`[]` treated as equal).
  3. Match -> skip entirely, no write.
  4. Latest is a draft and differs -> PATCH it in place, then PATCH `draft=false`. Never POST
     while a draft exists — the API rejects a new version in that state.
  5. Latest is published and differs -> POST (copies the previous version's content), then PATCH
     the content, then PATCH `draft=false` — the POST alone doesn't apply our new content.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml

from azure_foundry_sync.config import config_path
from azure_foundry_sync.credo_v2 import CredoApiError, CredoClientV2
from azure_foundry_sync.summary import SyncCounts

logger = logging.getLogger("azure_foundry_sync.controls_sync")


def _latest_version_file(control_dir: Path) -> Path:
    version_files = sorted(control_dir.glob("v*.yaml"), key=lambda p: int(p.stem[1:]))
    if not version_files:
        raise ValueError(
            f"No version files (v1.yaml, v2.yaml, ...) found in {control_dir}"
        )
    return version_files[-1]


def _version_payload(version_attrs: dict, *, draft: bool) -> dict:
    return {
        "data": {
            "type": "resource-type",
            "attributes": {
                "draft": draft,
                "risk_type_ids": version_attrs["risk_type_ids"],
                "info": version_attrs["info"],
                "evidence_requirements": version_attrs["evidence_requirements"],
            },
        }
    }


def _version_number(version_id: str) -> int:
    try:
        return int(version_id.rsplit("+", 1)[-1])
    except (ValueError, IndexError):
        return -1


def _normalize_list(items: list | None) -> list:
    items = items or []
    return sorted(
        items,
        key=lambda v: (
            json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else str(v)
        ),
    )


def _content_matches(existing_attrs: dict, desired: dict) -> bool:
    return (
        existing_attrs.get("info") == desired["info"]
        and _normalize_list(existing_attrs.get("risk_type_ids"))
        == _normalize_list(desired["risk_type_ids"])
        and _normalize_list(existing_attrs.get("evidence_requirements"))
        == _normalize_list(desired["evidence_requirements"])
    )


def _create_and_publish(
    credo: CredoClientV2, control_key: str, version_attrs: dict, counts: SyncCounts
) -> None:
    """No existing version at all — first-ever POST + publish for this control."""
    payload = _version_payload(version_attrs, draft=True)
    version_resp = credo.create_control_version(control_key, payload)
    if version_resp.status_code not in (200, 201):
        raise CredoApiError(
            f"create_control_version {control_key} failed: {version_resp.status_code}",
            version_resp.status_code,
            version_resp.text,
        )
    version_id = version_resp.json()["data"]["id"]

    publish_payload = _version_payload(version_attrs, draft=False)
    publish_resp = credo.publish_control_version(version_id, publish_payload)
    if publish_resp.status_code not in (200, 201):
        raise CredoApiError(
            f"publish_control_version {control_key} failed: {publish_resp.status_code}",
            publish_resp.status_code,
            publish_resp.text,
        )
    counts.created += 1


def _patch_in_place(
    credo: CredoClientV2, version_id: str, version_attrs: dict, counts: SyncCounts
) -> None:
    """Latest version is a draft that differs from desired content — PATCH, don't POST."""
    content_resp = credo.publish_control_version(
        version_id, _version_payload(version_attrs, draft=True)
    )
    if content_resp.status_code not in (200, 201):
        raise CredoApiError(
            f"patch content {version_id} failed: {content_resp.status_code}",
            content_resp.status_code,
            content_resp.text,
        )
    publish_resp = credo.publish_control_version(
        version_id, _version_payload(version_attrs, draft=False)
    )
    if publish_resp.status_code not in (200, 201):
        raise CredoApiError(
            f"publish {version_id} failed: {publish_resp.status_code}",
            publish_resp.status_code,
            publish_resp.text,
        )
    counts.updated += 1


def _post_then_patch(
    credo: CredoClientV2, control_key: str, version_attrs: dict, counts: SyncCounts
) -> None:
    """Latest version is published and differs — POST copies the previous version's content,
    so a follow-up PATCH is required to actually apply our desired content."""
    payload = _version_payload(version_attrs, draft=True)
    post_resp = credo.create_control_version(control_key, payload)
    if post_resp.status_code not in (200, 201):
        raise CredoApiError(
            f"create_control_version {control_key} failed: {post_resp.status_code}",
            post_resp.status_code,
            post_resp.text,
        )
    version_id = post_resp.json()["data"]["id"]

    content_resp = credo.publish_control_version(version_id, payload)
    if content_resp.status_code not in (200, 201):
        raise CredoApiError(
            f"patch content {version_id} failed: {content_resp.status_code}",
            content_resp.status_code,
            content_resp.text,
        )
    publish_resp = credo.publish_control_version(
        version_id, _version_payload(version_attrs, draft=False)
    )
    if publish_resp.status_code not in (200, 201):
        raise CredoApiError(
            f"publish {version_id} failed: {publish_resp.status_code}",
            publish_resp.status_code,
            publish_resp.text,
        )
    counts.updated += 1


def _sync_one_control(
    credo: CredoClientV2, control_key: str, version_attrs: dict, counts: SyncCounts
) -> None:
    base_resp = credo.create_control_base(control_key)
    if base_resp.status_code not in (200, 201, 422):
        raise CredoApiError(
            f"create_control_base {control_key} failed: {base_resp.status_code}",
            base_resp.status_code,
            base_resp.text,
        )

    versions = credo.list_control_versions(control_key)
    if not versions:
        _create_and_publish(credo, control_key, version_attrs, counts)
        return

    latest = max(versions, key=lambda v: _version_number(v["id"]))
    latest_attrs = latest.get("attributes", {})
    desired = {
        "info": version_attrs["info"],
        "risk_type_ids": version_attrs["risk_type_ids"],
        "evidence_requirements": version_attrs["evidence_requirements"],
    }

    if _content_matches(latest_attrs, desired):
        counts.skipped += 1
        return

    if latest_attrs.get("draft"):
        _patch_in_place(credo, latest["id"], version_attrs, counts)
    else:
        _post_then_patch(credo, control_key, version_attrs, counts)


def _plan_one_control(
    credo: CredoClientV2, control_key: str, version_attrs: dict, counts: SyncCounts
) -> None:
    """Read-only equivalent of _sync_one_control — lists and compares, writes nothing."""
    versions = credo.list_control_versions(control_key)
    if not versions:
        logger.info(
            "[dry-run] would create control %s (no existing versions)", control_key
        )
        counts.created += 1
        return

    latest = max(versions, key=lambda v: _version_number(v["id"]))
    latest_attrs = latest.get("attributes", {})
    desired = {
        "info": version_attrs["info"],
        "risk_type_ids": version_attrs["risk_type_ids"],
        "evidence_requirements": version_attrs["evidence_requirements"],
    }

    if _content_matches(latest_attrs, desired):
        logger.info("[dry-run] %s unchanged, would skip", control_key)
        counts.skipped += 1
    elif latest_attrs.get("draft"):
        logger.info("[dry-run] would patch draft version %s in place", latest["id"])
        counts.updated += 1
    else:
        logger.info(
            "[dry-run] would post a new version for %s (latest published version differs)",
            control_key,
        )
        counts.updated += 1


def sync_controls(credo: CredoClientV2, *, dry_run: bool) -> SyncCounts:
    counts = SyncCounts(label="controls")
    control_root = config_path("policy_controls")
    control_dirs = sorted(
        d for d in control_root.iterdir() if d.is_dir() and d.name.startswith("MSFT-")
    )

    for control_dir in control_dirs:
        counts.scanned += 1
        try:
            control_key = yaml.safe_load((control_dir / "base.yaml").read_text())["key"]
            version_file = _latest_version_file(control_dir)
            version_attrs = yaml.safe_load(version_file.read_text())
        except (OSError, ValueError, KeyError) as exc:
            logger.error("Failed to load control config from %s: %s", control_dir, exc)
            counts.errors += 1
            continue

        try:
            if dry_run:
                _plan_one_control(credo, control_key, version_attrs, counts)
            else:
                _sync_one_control(credo, control_key, version_attrs, counts)
        except CredoApiError as exc:
            logger.error("Failed to sync control %s: %s", control_key, exc)
            counts.errors += 1

    return counts
