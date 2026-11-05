"""SQLite state: the delivery outbox and the record of emails already sent.

Two tables carry the server's guarantees:

- `outbox` holds every accepted delivery, written *before* the server answers
  Svix. Svix doesn't redeliver after a 2xx, so if the process dies mid-send the
  row is still here and is picked up again on the next start. Its primary key is
  the `svix-id` header, which also absorbs Svix's own redeliveries.
- `sent` is keyed on what an email is *about* (`model_added:<model_id>`), not on
  `svix-id`, so a new `svix-id` for the same model still can't make the same
  email go out twice.

One connection, guarded by a lock: the web handler and the worker thread share
it, and the volume here is a handful of rows a day.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

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

CREATE TABLE IF NOT EXISTS meta (
    name  TEXT PRIMARY KEY,
    value TEXT NOT NULL
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

    def complete(self, svix_id: str, key: str, recipient: str, reason: str) -> None:
        """Record the email and close the delivery in one transaction."""
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute(
                    "INSERT OR IGNORE INTO sent (key, recipient, reason, at) VALUES (?, ?, ?, ?)",
                    (key, recipient, reason, now),
                )
                self._conn.execute(
                    "UPDATE outbox SET status = ?, note = ? WHERE svix_id = ?",
                    (DONE, f"{reason} to {recipient}", svix_id),
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def pending_payloads(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM outbox WHERE status = ?", (PENDING,)
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    # ---- sent keys --------------------------------------------------------

    def was_sent(self, key: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM sent WHERE key = ?", (key,)
            ).fetchone()
        return row is not None

    def seed(self, keys: Iterable[str]) -> int:
        """Mark keys as already sent, without sending anything."""
        now = time.time()
        rows = [(k, None, "seeded", now) for k in keys]
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.executemany(
                    "INSERT OR IGNORE INTO sent (key, recipient, reason, at) VALUES (?, ?, ?, ?)",
                    rows,
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return len(rows)

    # ---- meta -------------------------------------------------------------

    def get_meta(self, name: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE name = ?", (name,)
            ).fetchone()
        return row[0] if row else None

    def set_meta(self, name: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta (name, value) VALUES (?, ?) "
                "ON CONFLICT (name) DO UPDATE SET value = excluded.value",
                (name, value),
            )
