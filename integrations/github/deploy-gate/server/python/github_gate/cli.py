"""CLI for the gate service.

Subcommands:
    serve     Run the gate service (registers the Credo AI webhook on startup, deletes it on
              Ctrl-C).
    status    Print the state file (webhook + tracked PRs).
    teardown  Delete the tracked webhook (and optionally the tracked use cases), then reset the
              state file. The backstop for a crash that skipped the shutdown cleanup.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from credoai.auth import AuthenticationError as CredoAuthError
from credoai.errors import CredoAIError
from pydantic import ValidationError

from github_gate.config import get_settings
from github_gate.state import GateState

logger = logging.getLogger("github_gate.cli")


def cmd_serve(_args: argparse.Namespace) -> int:
    import uvicorn

    from github_gate.app import create_app

    settings = get_settings()
    _require(settings, "credoai_api_key", "credoai_tenant", "github_token")
    uvicorn.run(
        create_app(settings),
        host=settings.gate_host,
        port=settings.gate_port,
        log_config=None,
    )
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    print(json.dumps(GateState.load().snapshot(), indent=2))
    return 0


def cmd_teardown(args: argparse.Namespace) -> int:
    state = GateState.load()
    if state.webhook_id is None and not state.use_cases:
        logger.info("Nothing tracked in the state file; nothing to teardown.")
        return 0

    if args.dry_run:
        if state.webhook_id:
            logger.info("[dry-run] would delete webhook %s", state.webhook_id)
        for use_case_id in state.use_cases:
            verb = "delete" if args.delete_use_cases else "untrack"
            logger.info("[dry-run] would %s use case %s", verb, use_case_id)
        return 0

    from github_gate.credo import GateCredoClient

    errors = 0
    client = GateCredoClient(get_settings())
    try:
        if state.webhook_id:
            try:
                client.delete_webhook(state.webhook_id)
                logger.info("Deleted webhook %s", state.webhook_id)
                state.clear_webhook()
            except Exception as exc:
                logger.warning("Failed to delete webhook %s: %s", state.webhook_id, exc)
                errors += 1

        for use_case_id in list(state.use_cases):
            if args.delete_use_cases:
                try:
                    client.delete_use_case(use_case_id)
                    logger.info("Deleted use case %s", use_case_id)
                except Exception as exc:
                    logger.warning("Failed to delete use case %s: %s", use_case_id, exc)
                    errors += 1
                    continue
            del state.use_cases[use_case_id]
        state.save()
    finally:
        client.close()

    return 1 if errors else 0


def _require(settings, *names: str) -> None:
    missing = [n.upper() for n in names if not getattr(settings, n)]
    if missing:
        raise RuntimeError(f"Missing required config: {', '.join(missing)}")


_COMMANDS = {
    "serve": cmd_serve,
    "status": cmd_status,
    "teardown": cmd_teardown,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Deploy gate service: a Credo AI review approval unblocks a GitHub PR.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("serve", help="Run the gate service")
    sub.add_parser("status", help="Print the state file")

    p_teardown = sub.add_parser(
        "teardown", help="Delete the tracked webhook and reset state"
    )
    p_teardown.add_argument(
        "--delete-use-cases",
        action="store_true",
        help="Also delete the tracked use cases from the tenant",
    )
    p_teardown.add_argument(
        "--dry-run", action="store_true", help="Log actions without deleting"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        settings = get_settings()
    except ValidationError as exc:
        print(f"Invalid configuration: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else settings.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        return _COMMANDS[args.command](args)
    except (RuntimeError, CredoAIError, CredoAuthError) as exc:
        logger.error("%s", exc)
        return 2
    except Exception as exc:
        logger.error("Unexpected error: %s", exc)
        if args.verbose:
            raise
        return 1
