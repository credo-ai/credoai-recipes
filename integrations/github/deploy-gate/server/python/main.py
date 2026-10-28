"""Entry point for the GitHub deploy gate service.

    python main.py serve       # run the gate service (registers the Credo AI webhook)
    python main.py status      # print tracked PRs and the registered webhook
    python main.py teardown    # delete the tracked webhook (and optionally the use cases)

Unlike the sync cookbooks, this one is a long-running server: GitHub Actions and Credo AI both call
into it. The logic lives in `github_gate/`, one module per concern — see README.md.
"""

import sys

from github_gate.cli import main

if __name__ == "__main__":
    sys.exit(main())
