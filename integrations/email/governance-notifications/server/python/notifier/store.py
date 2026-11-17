"""SQLite state: the delivery outbox and the record of emails already sent.

Two tables carry the server's guarantees:

- `outbox` holds every accepted delivery, written *before* the server answers
  Svix. Svix doesn't redeliver after a 2xx, so if the process dies mid-send the
  row is still here and is picked up again on the next start. Its primary key is
  the `svix-id` header, which also absorbs Svix's own redeliveries. Scheduled
  reminders are queued here too, under an id naming the reminder and its period.
- `sent` is keyed on what an email is *about* and who it went to (for example
  `review_approved:<review_id>:<address>`), not on `svix-id`. A redelivery with
  a fresh `svix-id`, or a retry after a partly sent batch, can't send the same
  email twice. Each key has a window (see the handlers); after it, the same
  email may go out again.

One connection, guarded by a lock: the web handler, the worker thread and the
reminder scheduler share it, and the volume here is a handful of rows a day.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PENDING = "pending"
DONE = "done"
DROPPED = "dropped"
FAILED = "failed"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    svix_id         TEXT PRIMARY KEY,
    subject         TEXT NOT NULL,
    payload         TEXT NOT NULL,
    received_at     REAL NOT NULL,
    status          TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL,
    note            TEXT
);
CREATE INDEX IF NOT EXISTS outbox_due ON outbox (status, next_attempt_at);

CREATE TABLE IF NOT EXISTS sent (
    key       TEXT PRIMARY KEY,
    recipient TEXT,
    reason    TEXT NOT NULL,
    at        REAL NOT NULL
);
"""


@dataclass(frozen=True)
class Delivery:
    svix_id: str
    subject: str
    payload: dict[str, Any]
    attempts: int


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # The database holds recipient addresses; keep it private to this user.
        # SQLite gives the -wal and -shm files the database file's mode, so set
        # that before connecting, then fix any left over from an older run.
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        self._conn = sqlite3.connect(
            path, check_same_thread=False, isolation_level=None
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        # `remind.py` may write while the server runs; wait for its lock.
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._lock = threading.Lock()
        for suffix in ("-wal", "-shm"):
            sidecar = path.with_name(path.name + suffix)
            if sidecar.exists():
                sidecar.chmod(0o600)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- outbox -----------------------------------------------------------

    def enqueue(self, svix_id: str, subject: str, payload: dict[str, Any]) -> bool:
        """Store a delivery. False when this `svix-id` was already accepted."""
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO outbox "
                "(svix_id, subject, payload, received_at, status, next_attempt_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (svix_id, subject, json.dumps(payload), now, PENDING, now),
            )
            return cur.rowcount == 1

    def next_due(self) -> Delivery | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT svix_id, subject, payload, attempts FROM outbox "
                "WHERE status = ? AND next_attempt_at <= ? "
                "ORDER BY next_attempt_at LIMIT 1",
                (PENDING, time.time()),
            ).fetchone()
        if row is None:
            return None
        return Delivery(
            svix_id=row[0], subject=row[1], payload=json.loads(row[2]), attempts=row[3]
        )

    def seconds_until_next(self) -> float | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT MIN(next_attempt_at) FROM outbox WHERE status = ?", (PENDING,)
            ).fetchone()
        if row is None or row[0] is None:
            return None
        return max(0.0, row[0] - time.time())

    def finish(self, svix_id: str, status: str, note: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE outbox SET status = ?, note = ? WHERE svix_id = ?",
                (status, note, svix_id),
            )

    def retry_later(self, svix_id: str, attempts: int, delay: float, note: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE outbox SET attempts = ?, next_attempt_at = ?, note = ? WHERE svix_id = ?",
                (attempts, time.time() + delay, note, svix_id),
            )

    # ---- sent keys --------------------------------------------------------

    def sent_within(self, key: str, window: float | None) -> bool:
        """Whether this email went out in the last `window` seconds (None: ever)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT at FROM sent WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return False
        return window is None or row[0] > time.time() - window

    def record_sent(self, key: str, recipient: str, reason: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO sent (key, recipient, reason, at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (key) DO UPDATE SET recipient = excluded.recipient, "
                "reason = excluded.reason, at = excluded.at",
                (key, recipient, reason, time.time()),
            )
