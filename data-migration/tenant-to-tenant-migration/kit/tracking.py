"""data/<entity>.json (extracted items) and data/loaded_<entity>.json (what each load did on the target)."""

import json
import os
from pathlib import Path

ORIGINS = ("created", "matched")


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def save_extract(data_dir, entity, items):
    path = Path(data_dir) / f"{entity}.json"
    _write_json(path, items)
    return path


def load_extract(data_dir, entity):
    path = Path(data_dir) / f"{entity}.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run: python migrate.py extract {entity}")
    return json.loads(path.read_text())


class Tracker:
    """Records which target item each source item became. In dry run nothing is written to disk."""

    def __init__(self, data_dir, entity, dry_run):
        self.path = Path(data_dir) / f"loaded_{entity}.json"
        self.dry_run = dry_run
        self.records = json.loads(self.path.read_text()) if self.path.exists() else []

    def find(self, source_id):
        return next((r for r in self.records if r["source_id"] == source_id), None)

    def target_id(self, source_id):
        record = self.find(source_id)
        return record["target_id"] if record else None

    def id_map(self):
        return {r["source_id"]: r["target_id"] for r in self.records}

    def record(self, source_id, target_id, name_or_key, origin):
        if origin not in ORIGINS:
            raise ValueError(f"origin must be one of {ORIGINS}, got {origin!r}")
        if self.find(source_id):
            raise ValueError(f"{source_id} is already tracked in {self.path.name}")
        if not self.dry_run and str(target_id).startswith("dry:"):
            raise ValueError(f"Refusing to save dry-run placeholder {target_id!r} during a live run")
        self.records.append(
            {"source_id": source_id, "target_id": target_id, "name_or_key": name_or_key, "origin": origin}
        )
        if not self.dry_run:
            _write_json(self.path, self.records)
