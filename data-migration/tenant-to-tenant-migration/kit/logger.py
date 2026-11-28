"""Logging (console INFO, file DEBUG, secrets removed) and the end-of-run summary."""

import json
import logging
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

OUTCOMES = ("created", "matched", "skipped", "failed")


class RedactFilter(logging.Filter):
    BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+")

    def __init__(self, secrets=()):
        super().__init__()
        self.secrets = [s for s in secrets if s]

    def filter(self, record):
        message = self.BEARER.sub(r"\1[REDACTED]", record.getMessage())
        for secret in self.secrets:
            message = message.replace(secret, "[REDACTED]")
        record.msg, record.args = message, None
        return True


def setup_logging(command, log_dir="logs", secrets=()):
    """Configure the 'kit' logger tree. Returns (logger, path of this run's log file)."""
    logger = logging.getLogger("kit")
    logger.setLevel(logging.DEBUG)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_path = Path(log_dir) / f"{command}_{datetime.now():%Y%m%d_%H%M%S}.log"
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    redact = RedactFilter(secrets)
    for handler, level in ((logging.StreamHandler(), logging.INFO), (logging.FileHandler(log_path), logging.DEBUG)):
        handler.setLevel(level)
        handler.setFormatter(fmt)
        handler.addFilter(redact)
        logger.addHandler(handler)
    return logger, log_path


class RunSummary:
    def __init__(self):
        self.counts = {}
        self.attention = []  # [{"entity", "message"}]: items that count as done but need a fix by hand
        self.notes = []  # reminders for the operator, shown after the table; not problems

    def record(self, entity, outcome):
        if outcome not in OUTCOMES:
            raise ValueError(f"Unknown outcome {outcome!r}")
        self.counts.setdefault(entity, Counter())[outcome] += 1

    def flag(self, entity, message):
        """An item that counts in its outcome but needs a fix by hand. Makes the run exit 1."""
        # Messages written for the log often start with the entity name, which the summary already shows
        prefix = f"{entity}: "
        if message.startswith(prefix):
            message = message[len(prefix) :]
        self.attention.append({"entity": entity, "message": message})

    def note(self, message):
        """A reminder repeated after the summary table, so it ends the run's output. Doesn't change the exit code."""
        self.notes.append(message)

    @property
    def needs_attention(self):
        return bool(self.attention)

    @property
    def has_failures(self):
        return any(c["failed"] for c in self.counts.values())

    def to_dict(self):
        return {entity: {o: c[o] for o in OUTCOMES} for entity, c in self.counts.items()}

    def table(self):
        rows = [f"{'entity':<22}" + "".join(f"{o:>9}" for o in OUTCOMES)]
        rows += [f"{e:<22}" + "".join(f"{c[o]:>9}" for o in OUTCOMES) for e, c in self.counts.items()]
        if self.attention:
            rows += ["", "Needs attention:"] + [f"  {a['entity']}: {a['message']}" for a in self.attention]
        if self.notes:
            rows += ["", "Notes:"] + [f"  {n}" for n in self.notes]
        return "\n".join(rows)

    def save(self, path):
        data = self.to_dict()
        if self.attention:
            data["needs_attention"] = self.attention
        if self.notes:
            data["notes"] = self.notes
        Path(path).write_text(json.dumps(data, indent=2))
