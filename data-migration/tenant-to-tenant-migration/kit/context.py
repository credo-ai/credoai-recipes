"""Everything one command run shares: clients, dry-run flag, trackers and the run summary."""

from dataclasses import dataclass, field
from pathlib import Path

from kit.logger import RunSummary
from kit.tracking import Tracker


@dataclass
class Context:
    source: object
    target: object
    source_internal: object
    target_internal: object
    dry_run: bool
    data_dir: Path
    allow_invites: bool = False  # load users even if the target would email them invitations
    raise_roles: bool = False  # raise matched users' built-in roles on the target to their source role
    summary: RunSummary = field(default_factory=RunSummary)
    _trackers: dict = field(default_factory=dict, repr=False)
    # Questionnaire version content read during the run: {("source" or "target", key, version): sections}
    questionnaire_content: dict = field(default_factory=dict, repr=False)

    def tracker(self, entity):
        """One Tracker per entity per run, so dry-run placeholders from earlier steps stay visible to later ones."""
        if entity not in self._trackers:
            self._trackers[entity] = Tracker(self.data_dir, entity, self.dry_run)
        return self._trackers[entity]
