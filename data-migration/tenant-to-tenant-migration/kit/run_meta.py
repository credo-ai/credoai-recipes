"""Ties data/ to one source and target tenant, so tracking from one migration is never reused for another."""

import json
from pathlib import Path


class RunMetaMismatch(Exception):
    pass


def _describe(client):
    return f"{client.config.tenant} ({client.config.base_url})"


def check_run_meta(ctx, command, live):
    """Refuse to run if data/ belongs to a different tenant pair. Records the source on extract and the target on
    the first live load."""
    path = Path(ctx.data_dir) / "run_meta.json"
    meta = json.loads(path.read_text()) if path.exists() else {}
    current = {"source": _describe(ctx.source), "target": _describe(ctx.target)}
    for side in ("source", "target"):
        if side in meta and meta[side] != current[side]:
            raise RunMetaMismatch(
                f"data/ belongs to {side} {meta[side]}, but this run's {side} is {current[side]}. "
                f"To migrate a different tenant pair, move data/*.json somewhere else first."
            )
    changed = False
    if command == "extract" and "source" not in meta:
        meta["source"], changed = current["source"], True
    if command == "load" and live and "target" not in meta:
        meta["target"], changed = current["target"], True
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(meta, indent=2))
