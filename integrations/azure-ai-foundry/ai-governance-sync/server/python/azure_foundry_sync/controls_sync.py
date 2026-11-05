"""Create and publish the MSFT-* policy controls from config/policy_controls/.

Stays on the private API — see `credo_private.py` module docstring for why the public SDK can't
represent these controls' evidence requirements.

Each control directory holds a `base.yaml` (the control's key/metadata) and one or more `vN.yaml`
version files (evidence requirements, risk types, description). The latest local version file is
what gets synced.

Dedup algorithm:
  1. List the control's existing versions (a control with no base yet has none — not an error).
     Only when there are none is the base created (422 = already exists, accepted), so an
     unchanged control costs one read and no writes.
  2. Compare the highest version's `info`, `risk_type_ids`, `evidence_requirements` against our
     desired content (lists order-normalized recursively; server-added fields outside what our
     local yaml defines are ignored, so backend metadata can't cause a false mismatch; fields
     our yaml defines that the server never returns on read are left out of the comparison, since
     they could never match and would otherwise post a new version on every run).
  3. Identical -> skip entirely, no write.
  4. Latest version is a draft and differs -> one `PATCH` with the new content and `draft: false`
     together. Never `POST` while a draft exists — the API rejects a new version in that state.
  5. Latest version is published and differs -> `POST` (which copies the previous version's
     content), then one `PATCH` with the new content and `draft: false` together.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from azure_foundry_sync.config import config_path
from azure_foundry_sync.content_compare import normalize, project, restrict
from azure_foundry_sync.credo_private import CredoPrivateApiError, CredoPrivateClient
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
            "attributes": {"draft": draft, **_content(version_attrs)},
        }
    }


def _version_number(version_id: str) -> int:
    try:
        return int(version_id.rsplit("+", 1)[-1])
    except (ValueError, IndexError):
        return -1


_COMPARED_FIELDS = (("info", {}), ("risk_type_ids", []), ("evidence_requirements", []))


def _content_matches(
    existing_attrs: dict, desired: dict, unreturned: set[str] | None = None
) -> bool:
    unreturned = unreturned if unreturned is not None else set()
    for field, default in _COMPARED_FIELDS:
        wanted = restrict(
            desired[field] or default, existing_attrs.get(field), unreturned, field
        )
        existing = project(existing_attrs.get(field) or default, wanted)
        if normalize(existing) != normalize(wanted):
            return False
    return True


_reported_unreturned: set[str] = set()


def _note_unreturned(control_key: str, unreturned: set[str]) -> None:
    """Say once per run, not once per control, which authored fields the server doesn't return."""
    new = unreturned - _reported_unreturned
    if new:
        _reported_unreturned.update(new)
        logger.warning(
            "Server does not return these fields (first seen on %s), so they are excluded from "
            "the change comparison: %s",
            control_key,
            ", ".join(sorted(new)),
        )


def _content(version_attrs: dict) -> dict:
    return {field: version_attrs[field] for field, _ in _COMPARED_FIELDS}


def _compare_to_latest(
    control_key: str, versions: list[dict], version_attrs: dict
) -> tuple[dict, bool]:
    """The highest existing version, and whether it already holds the desired content."""
    latest = max(versions, key=lambda v: _version_number(v["id"]))
    unreturned: set[str] = set()
    matches = _content_matches(
        latest.get("attributes", {}), _content(version_attrs), unreturned
    )
    _note_unreturned(control_key, unreturned)
    return latest, matches


def _create_and_publish(
    credo: CredoPrivateClient, control_key: str, version_attrs: dict, counts: SyncCounts
) -> None:
    """No existing version at all — first-ever POST + publish for this control."""
    payload = _version_payload(version_attrs, draft=True)
    version_resp = credo.create_control_version(control_key, payload)
    if version_resp.status_code not in (200, 201):
        raise CredoPrivateApiError(
            f"create_control_version {control_key} failed: {version_resp.status_code}",
            version_resp.status_code,
            version_resp.text,
        )
    version_id = version_resp.json()["data"]["id"]

    publish_payload = _version_payload(version_attrs, draft=False)
    publish_resp = credo.update_control_version(version_id, publish_payload)
    if publish_resp.status_code not in (200, 201):
        raise CredoPrivateApiError(
            f"update_control_version {control_key} failed: {publish_resp.status_code}",
            publish_resp.status_code,
            publish_resp.text,
        )
    counts.created += 1


def _patch_in_place(
    credo: CredoPrivateClient, version_id: str, version_attrs: dict, counts: SyncCounts
) -> None:
    """Latest version is a draft that differs from desired content — one PATCH sets the new
    content and publishes it together. Never POST while a draft exists."""
    payload = _version_payload(version_attrs, draft=False)
    resp = credo.update_control_version(version_id, payload)
    if resp.status_code not in (200, 201):
        raise CredoPrivateApiError(
            f"update_control_version {version_id} failed: {resp.status_code}",
            resp.status_code,
            resp.text,
        )
    counts.updated += 1


def _post_then_patch(
    credo: CredoPrivateClient, control_key: str, version_attrs: dict, counts: SyncCounts
) -> None:
    """Latest version is published and differs — POST copies the previous version's content as
    a new draft, then one PATCH sets the real content and publishes it together."""
    post_resp = credo.create_control_version(
        control_key, _version_payload(version_attrs, draft=True)
    )
    if post_resp.status_code not in (200, 201):
        raise CredoPrivateApiError(
            f"create_control_version {control_key} failed: {post_resp.status_code}",
            post_resp.status_code,
            post_resp.text,
        )
    version_id = post_resp.json()["data"]["id"]

    publish_resp = credo.update_control_version(
        version_id, _version_payload(version_attrs, draft=False)
    )
    if publish_resp.status_code not in (200, 201):
        raise CredoPrivateApiError(
            f"update_control_version {version_id} failed: {publish_resp.status_code}",
            publish_resp.status_code,
            publish_resp.text,
        )
    counts.updated += 1


def _sync_one_control(
    credo: CredoPrivateClient, control_key: str, version_attrs: dict, counts: SyncCounts
) -> None:
    versions = credo.list_control_versions(control_key)
    if not versions:
        # Only now is a write needed. 422 here means the base already exists (it just has no
        # versions yet), which is fine.
        base_resp = credo.create_control_base(control_key)
        if base_resp.status_code not in (200, 201, 422):
            raise CredoPrivateApiError(
                f"create_control_base {control_key} failed: {base_resp.status_code}",
                base_resp.status_code,
                base_resp.text,
            )
        _create_and_publish(credo, control_key, version_attrs, counts)
        return

    latest, matches = _compare_to_latest(control_key, versions, version_attrs)
    latest_attrs = latest.get("attributes", {})
    if matches:
        counts.skipped += 1
        return

    if latest_attrs.get("draft"):
        _patch_in_place(credo, latest["id"], version_attrs, counts)
    else:
        _post_then_patch(credo, control_key, version_attrs, counts)


def _plan_one_control(
    credo: CredoPrivateClient, control_key: str, version_attrs: dict, counts: SyncCounts
) -> None:
    """Read-only equivalent of _sync_one_control — lists and compares, writes nothing.

    No `create_control_base` call here: `list_control_versions` already treats a base that
    doesn't exist yet as "no versions" (see credo_private.py), so a brand-new control is reported
    correctly as "would create" without needing a write first.
    """
    versions = credo.list_control_versions(control_key)
    if not versions:
        logger.info(
            "[dry-run] would create control %s (no existing versions)", control_key
        )
        counts.created += 1
        return

    latest, matches = _compare_to_latest(control_key, versions, version_attrs)
    latest_attrs = latest.get("attributes", {})
    if matches:
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


def sync_controls(credo: CredoPrivateClient, *, dry_run: bool) -> SyncCounts:
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
        except CredoPrivateApiError as exc:
            logger.error("Failed to sync control %s: %s", control_key, exc)
            counts.errors += 1

    return counts
