"""Entry point for the Bedrock → Credo AI inventory sync.

    python main.py --dry-run     # show the plan, write nothing
    python main.py               # sync

A one-shot command you put on a schedule, not a server: Bedrock has no webhook
or event feed for its inventory, so there is nothing to listen for. See
README.md for the scheduling examples.

The sync itself lives in `bedrock_sync/`, one module per concern — AWS models,
AWS agents, the Credo AI client, and the orchestration that joins them.
"""

from bedrock_sync.cli import main

if __name__ == "__main__":
    main()
