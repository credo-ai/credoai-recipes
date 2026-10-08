"""Entry point for the Azure AI Foundry -> Credo AI governance sync.

    python main.py --dry-run     # show the plan, write nothing
    python main.py               # sync

A one-shot command you put on a schedule, not a server: none of the four things this syncs
(model catalog, policy controls, custom fields, questionnaire) has a webhook or event feed, so
there is nothing to listen for. See README.md for the scheduling examples.

The sync itself lives in `azure_foundry_sync/`, one module per concern — Azure auth/catalog, the
two Credo AI API clients, and one sync module per domain.
"""

import sys

from azure_foundry_sync.cli import main

if __name__ == "__main__":
    sys.exit(main())
