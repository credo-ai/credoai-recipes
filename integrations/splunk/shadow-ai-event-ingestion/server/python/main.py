"""
Splunk -> Credo AI Shadow AI event ingestion poller.

Long-running process, not a webhook receiver: Splunk doesn't push, so this
pulls on an interval via the same REST search export API any external tool
would use, transforms results into Credo AI's event schema, and POSTs them
to Credo AI's backend Shadow AI bulk endpoint (Research Preview — see
../../README.md for the preview note and full setup instructions).

Run: python main.py
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()  # reads ../../.env (or cwd .env) into os.environ — must run
# before the required os.environ[...] reads below.

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [splunk-bridge] %(message)s",
)
log = logging.getLogger("splunk-bridge")

# ---- Credo AI -------------------------------------------------------------
# Integration Service — used only to exchange CREDO_API_KEY for a bearer
# token via /auth/token. Shadow AI Discovery is Research Preview and has no
# public Integration Service endpoint yet, so the bulk upload itself goes
# straight to the backend (CREDO_BACKEND_BASE_URL) below. See README.md.
CREDO_BASE_URL = os.environ["CREDO_BASE_URL"]
CREDO_API_KEY = os.environ["CREDO_API_KEY"]
CREDO_TENANT = os.environ["CREDO_TENANT"]
# Backend host for the (private, preview-only) Shadow AI bulk endpoint —
# not the same host as CREDO_BASE_URL. Subject to change without notice
# until Shadow AI ships on the public Integration Service.
CREDO_BACKEND_BASE_URL = os.environ["CREDO_BACKEND_BASE_URL"]

# ---- Splunk -----------------------------------------------------------
SPLUNK_HOST = os.environ["SPLUNK_HOST"]
SPLUNK_PORT = os.environ.get("SPLUNK_PORT", "8089")
SPLUNK_USERNAME = os.environ["SPLUNK_USERNAME"]
SPLUNK_PASSWORD = os.environ["SPLUNK_PASSWORD"]
SPLUNK_INDEX = os.environ["SPLUNK_INDEX"]
SPLUNK_SOURCETYPE = os.environ["SPLUNK_SOURCETYPE"]
SPLUNK_BASE_URL = f"https://{SPLUNK_HOST}:{SPLUNK_PORT}"
# Default True — only disable for a self-signed lab/dev cert, never for real
# customer data.
SPLUNK_VERIFY_SSL = os.environ.get("SPLUNK_VERIFY_SSL", "true").strip().lower() not in (
    "false",
    "0",
    "no",
)

# ---- Tuning -----------------------------------------------------------
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "200"))
CHECKPOINT_PATH = os.environ.get("CHECKPOINT_PATH", "./checkpoint.txt")

# Client-side delivery dedup: caps memory use for the in-memory "already sent"
# fingerprint set (see `_seen_fingerprints` below). No backend idempotency key
# exists yet, so this is a best-effort guard against re-sending events already
# delivered earlier in the same process lifetime — it does not survive a
# restart, since the checkpoint (saved per-batch, see `run_cycle`) is what
# bounds the re-send window across restarts to at most one in-flight batch.
DEDUP_CACHE_SIZE = int(os.environ.get("DEDUP_CACHE_SIZE", "20000"))

# Customize this if your Splunk field names differ from the examples below —
# see README.md Step 3. Left side: Credo AI field. Right side: your Splunk
# field name.
FIELD_MAP = {
    "user_email": "user_email",
    "department": "department",
    "device_hostname": "device_hostname",
    "url": "url",
    "referrer_url": "referrer_url",
    "action": "action",
    "app_name": "app_name",
    "category": "category",
    "risk_score": "risk_score",
}

# The trailing `| fields ...` is load-bearing: Splunk's `output_mode=json` for
# `search/jobs/export` returns only `_raw` + internal fields without an
# explicit field reference, silently skipping auto-extracted ones. List every
# Splunk-side field name you rely on here, not just the Credo AI-side names.
SEARCH_TEMPLATE = (
    "search index={index} sourcetype={sourcetype} earliest={earliest} "
    "| sort 0 _time "
    "| head {limit} "
    "| fields _time, {fields}"
)


_seen_fingerprints: set[str] = set()
_seen_order: deque[str] = deque()


def _fingerprint(event: dict) -> str:
    return hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()


def _already_sent(fingerprint: str) -> bool:
    return fingerprint in _seen_fingerprints


def _mark_sent(fingerprint: str) -> None:
    if fingerprint in _seen_fingerprints:
        return
    _seen_fingerprints.add(fingerprint)
    _seen_order.append(fingerprint)
    if len(_seen_order) > DEDUP_CACHE_SIZE:
        _seen_fingerprints.discard(_seen_order.popleft())


def _load_checkpoint() -> str:
    """Return the SPL `earliest` value to resume from."""
    try:
        with open(CHECKPOINT_PATH, "r", encoding="utf-8") as fh:
            saved = fh.read().strip()
            if saved:
                return saved
    except FileNotFoundError:
        pass
    return "-1h"  # first run: only look back 1 hour, not the whole index


def _save_checkpoint(latest_epoch: float) -> None:
    with open(CHECKPOINT_PATH, "w", encoding="utf-8") as fh:
        fh.write(f"{latest_epoch:.6f}")


def _parse_time(raw: Any) -> datetime | None:
    """Normalize Splunk's `_time` (format varies by version) to a UTC datetime."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith(" UTC"):
        text = text[: -len(" UTC")] + "+00:00"
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.fromtimestamp(float(raw), tz=timezone.utc)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fetch_events(client: httpx.Client, earliest: str) -> list[dict]:
    fields = ", ".join(FIELD_MAP.values())
    spl = SEARCH_TEMPLATE.format(
        index=SPLUNK_INDEX,
        sourcetype=SPLUNK_SOURCETYPE,
        earliest=earliest,
        limit=BATCH_SIZE * 5,  # pull more than one batch; we page client-side
        fields=fields,
    )
    resp = client.post(
        f"{SPLUNK_BASE_URL}/services/search/jobs/export",
        auth=(SPLUNK_USERNAME, SPLUNK_PASSWORD),
        data={"search": spl, "output_mode": "json"},
        timeout=30,
    )
    resp.raise_for_status()

    results = []
    for line in resp.text.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if payload.get("preview"):
            continue
        result = payload.get("result")
        if isinstance(result, dict):
            results.append(result)
    return results


def transform(raw: dict, dt: datetime) -> dict:
    event: dict[str, Any] = {"timestamp": dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")}
    for credo_field, splunk_field in FIELD_MAP.items():
        value = raw.get(splunk_field)
        if value in (None, ""):
            continue
        if credo_field == "risk_score":
            try:
                event[credo_field] = max(0, min(100, int(float(value))))
            except (TypeError, ValueError):
                continue
        else:
            event[credo_field] = str(value)
    return event


class BatchResult(Enum):
    DELIVERED = "delivered"
    # Backend permanently rejected this batch (entitlement off, bad data). Not
    # retryable — the same request would fail the same way every time, so the
    # poller logs it, skips it, and keeps going rather than stalling on it.
    DROPPED = "dropped"
    # Transient failure (5xx, exhausted retries). The batch might succeed
    # later, so the poller stops the cycle here and retries it next time
    # instead of skipping past it.
    RETRY = "retry"


class CredoClient:
    def __init__(self, client: httpx.Client) -> None:
        self._client = client
        self._token: str | None = None

    def _get_token(self) -> str:
        resp = self._client.post(
            f"{CREDO_BASE_URL}/auth/token",
            headers={"X-API-Key": CREDO_API_KEY, "X-Tenant": CREDO_TENANT},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def send_batch(self, events: list[dict]) -> BatchResult:
        """POST one batch. See `BatchResult` for what each outcome means for
        the caller's checkpoint/continue decision."""
        if not events:
            return BatchResult.DELIVERED
        if self._token is None:
            self._token = self._get_token()

        for attempt in range(3):
            resp = self._client.post(
                # Backend endpoint, not the public Integration Service — see
                # the preview note in README.md. Auth token above still comes
                # from the Integration Service's /auth/token.
                f"{CREDO_BACKEND_BASE_URL}/api/v2/{CREDO_TENANT}/shadow_ai/ai_events/bulk",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                json={"events": events},
                timeout=60,
            )

            if resp.status_code == 401:
                if attempt == 0:
                    log.info("token rejected; refreshing once")
                    self._token = self._get_token()
                    continue
                log.error("bad credentials after token refresh — stopping poller")
                raise SystemExit(1)

            if resp.status_code == 404:
                log.warning(
                    "404 from bulk endpoint (batch of %d dropped, poller "
                    "continuing) — either the SHADOW_AI entitlement isn't "
                    "enabled for tenant %s, or CREDO_BACKEND_BASE_URL/tenant "
                    "is wrong (see README status banner)",
                    len(events),
                    CREDO_TENANT,
                )
                return BatchResult.DROPPED

            if resp.status_code == 422:
                log.warning(
                    "422 validation error (batch of %d dropped, poller continuing): %s",
                    len(events),
                    resp.text[:500],
                )
                return BatchResult.DROPPED

            if resp.status_code >= 500:
                log.warning(
                    "server %s on attempt %d; backing off",
                    resp.status_code,
                    attempt + 1,
                )
                time.sleep(2**attempt)
                continue

            resp.raise_for_status()
            return BatchResult.DELIVERED

        log.error(
            "bulk POST exhausted retries (batch of %d held for next cycle)",
            len(events),
        )
        return BatchResult.RETRY


def run_cycle(splunk_client: httpx.Client, credo: CredoClient) -> None:
    earliest = _load_checkpoint()
    raw_events = fetch_events(splunk_client, earliest)

    # (timestamp, event, fingerprint) tuples, in the order Splunk returned
    # them — batches below are sliced off this list in the same order.
    transformed: list[tuple[datetime, dict, str]] = []
    malformed = 0
    duplicates = 0
    seen_this_cycle: set[str] = set()
    for raw in raw_events:
        dt = _parse_time(raw.get("_time"))
        if dt is None:
            malformed += 1
            continue
        event = transform(raw, dt)
        fingerprint = _fingerprint(event)
        if _already_sent(fingerprint) or fingerprint in seen_this_cycle:
            # Already delivered earlier in this process's lifetime (e.g.
            # re-fetched due to a checkpoint boundary tie), or a genuine
            # duplicate raw event fetched twice in this same cycle — either
            # way, no backend idempotency key exists yet, so this in-memory
            # check is what keeps it from going out twice. Still counts
            # toward advancing the checkpoint below since it's already
            # handled.
            duplicates += 1
            continue
        seen_this_cycle.add(fingerprint)
        transformed.append((dt, event, fingerprint))

    sent = 0
    for i in range(0, len(transformed), BATCH_SIZE):
        batch = transformed[i : i + BATCH_SIZE]
        result = credo.send_batch([event for _, event, _ in batch])

        if result == BatchResult.RETRY:
            # Transient failure — stop here without advancing the checkpoint
            # past this batch, so it (and anything after it) is retried next
            # cycle instead of being skipped or resent out of order.
            log.warning(
                "cycle stopping early: batch starting at event %d needs "
                "retry, %d event(s) left for next cycle",
                i,
                len(transformed) - i,
            )
            break

        # DELIVERED and DROPPED are both "handled": a dropped batch is
        # permanently unrecoverable (entitlement off / bad data), so there's
        # nothing to gain by blocking the rest of the cycle on it — log it
        # and move on to the next batch.
        for _, _, fingerprint in batch:
            _mark_sent(fingerprint)
        if result == BatchResult.DELIVERED:
            sent += len(batch)
        batch_latest = max(dt for dt, _, _ in batch)
        # Advance per-batch, not once at the end of the cycle: if the process
        # crashes mid-cycle, this bounds the re-send window to at most the
        # one batch that was in flight, not every batch already handled this
        # cycle.
        _save_checkpoint(batch_latest.timestamp() + 0.000001)

    log.info(
        "cycle: seen=%d sent=%d duplicates=%d malformed=%d",
        len(raw_events),
        sent,
        duplicates,
        malformed,
    )


def main() -> None:
    log.info(
        "starting poll loop: splunk=%s tenant=%s interval=%ds",
        SPLUNK_BASE_URL,
        CREDO_TENANT,
        POLL_INTERVAL_SECONDS,
    )
    # Separate clients: SPLUNK_VERIFY_SSL (often false for a lab's self-signed
    # cert) must never apply to the Credo AI client, which always verifies.
    with (
        httpx.Client(verify=SPLUNK_VERIFY_SSL) as splunk_client,
        httpx.Client() as credo_http,
    ):
        credo = CredoClient(credo_http)
        while True:
            try:
                run_cycle(splunk_client, credo)
            except httpx.HTTPStatusError as e:
                log.error("HTTP error: %s", e)
            except httpx.RequestError as e:
                log.error("network error: %s", e)
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
