"""Entry point for the Microsoft Agent 365 -> Credo AI governance sync.

    python main.py --dry-run             # show the plan, write nothing
    python main.py                       # register agents as Use Cases
    python main.py --enforce             # ...and block/unblock agents to match
    python main.py --login               # one-time admin sign-in for --enforce

A one-shot command you put on a schedule, not a server: Agent 365 has no webhook
or event feed for agent creation or publication, so there is nothing to listen
for. See README.md for scheduling.

The sync itself lives in `agent365_sync/`, one module per concern — the Agent 365
catalog, the delegated admin sign-in, the Credo AI client, and the orchestration
that joins them.
"""

from agent365_sync.cli import main

if __name__ == "__main__":
    main()
