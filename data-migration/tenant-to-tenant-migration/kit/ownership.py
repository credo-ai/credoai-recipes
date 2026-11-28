"""Which source items the kit migrates: custom ones that aren't archived.

'ootb' items come with every tenant. 'transferred' items are retired catalog items; on the tenants checked in
2026-10 every one was archived and deprecated.
"""

import logging
from collections import Counter

log = logging.getLogger("kit.extract")

KNOWN = ("custom", "ootb", "transferred")


def _ids(ids):
    shown = ", ".join(repr(i) for i in ids[:20])
    return f"first 20: {shown}" if len(ids) > 20 else shown


def keep_custom(entity, items, ownership_of, archived_of):
    """Custom, unarchived items. ownership_of and archived_of are accessors, since the APIs return different shapes."""
    kept, skipped, live_transferred, unexpected = [], Counter(), [], []
    for item in items:
        ownership, archived = ownership_of(item), archived_of(item)
        if ownership == "custom" and not archived:
            kept.append(item)
            continue
        skipped["archived custom" if ownership == "custom" else str(ownership)] += 1
        if ownership not in KNOWN:
            unexpected.append(item["id"])
        elif ownership == "transferred" and not archived:
            live_transferred.append(item["id"])
    if skipped:
        log.info(f"{entity}: skipping {', '.join(f'{n} {kind}' for kind, n in sorted(skipped.items()))}")
    if live_transferred:
        log.warning(
            f"{entity}: {len(live_transferred)} 'transferred' items aren't archived, and the kit doesn't "
            f"migrate them: {_ids(live_transferred)}"
        )
    if unexpected:
        log.warning(
            f"{entity}: {len(unexpected)} items have an unexpected ownership type and were skipped: {_ids(unexpected)}"
        )
    return kept
