"""Shared per-domain counters, printed as one summary line per sync domain."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SyncCounts:
    label: str
    scanned: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0

    def line(self) -> str:
        return (
            f"Sync complete: {self.label:<14} "
            f"scanned={self.scanned} created={self.created} updated={self.updated} "
            f"skipped={self.skipped} errors={self.errors}"
        )

    def dry_run_line(self) -> str:
        return (
            f"[dry-run] plan: {self.label:<14} "
            f"scanned={self.scanned} would_create={self.created} would_update={self.updated} "
            f"would_skip={self.skipped} errors={self.errors}"
        )
