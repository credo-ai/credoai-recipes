"""The per-stage wording for stage-change emails, from `stage_messages.yaml`.

The file is data, so the wording can change without touching code. Entries
are keyed by a workflow stage's id or its name; an id wins. Names match
case-insensitively. `default` fills in any hook a stage doesn't set; a stage
can opt out of the default with an empty string (`on_enter: ""`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import yaml

_HOOKS = ("on_enter", "on_leave")


class Stage(Protocol):
    id: str
    name: str


class StageMessages:
    def __init__(self, path: Path) -> None:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{path.name}: expected a mapping at the top level")
        self._default = _entry(raw.get("default"), f"{path.name}: default")
        stages = raw.get("stages") or {}
        if not isinstance(stages, dict):
            raise ValueError(f"{path.name}: `stages` must be a mapping")
        self._stages = {
            str(key).strip().casefold(): _entry(value, f"{path.name}: stages.{key}")
            for key, value in stages.items()
        }

    def on_enter(self, stage: Stage) -> str | None:
        return self._lookup(stage, "on_enter")

    def on_leave(self, stage: Stage) -> str | None:
        return self._lookup(stage, "on_leave")

    def _lookup(self, stage: Stage, hook: str) -> str | None:
        for key in (stage.id, stage.name):
            entry = self._stages.get(str(key).strip().casefold())
            if entry is not None:
                if hook in entry:
                    # "" is an explicit opt-out of the default.
                    return entry[hook] or None
                break
        return self._default.get(hook)


def _entry(value: Any, where: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{where}: expected on_enter / on_leave")
    unknown = set(value) - set(_HOOKS)
    if unknown:
        raise ValueError(f"{where}: unknown key(s) {sorted(unknown)}")
    out = {}
    for hook in _HOOKS:
        text = value.get(hook)
        if text is not None and not isinstance(text, str):
            raise ValueError(f"{where}.{hook}: must be text")
        if text is not None:
            out[hook] = text.strip()
    return out
