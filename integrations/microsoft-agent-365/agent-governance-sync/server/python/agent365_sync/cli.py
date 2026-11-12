"""CLI for agent365-sync.

A single-shot sync, intended to be run on a schedule (cron, a Kubernetes
CronJob, a scheduled CI workflow): Agent 365 has no push notification for agent
creation or publication, so there is nothing to serve and no daemon to keep
alive.

    python main.py                       # register agents as Credo AI Use Cases
    python main.py --dry-run             # log the plan, write nothing
    python main.py --enforce             # ...and block/unblock agents to match
    python main.py --enforce --dry-run   # preview exactly what would be blocked
    python main.py --limit 5             # only the 5 newest agents in scope
    python main.py --asset-id T_...      # only this agent (repeatable)
    python main.py --login               # one-time admin sign-in for --enforce

One cycle reads the Agent 365 catalog, gives each in-scope agent a Use Case in
Credo AI, and — only with --enforce — blocks every agent whose Use Case has not
cleared its workflow and unblocks every one that has.

Registering agents is safe to run first. --enforce changes who can use which
agents across your whole Microsoft 365 tenant, so preview it with --dry-run
before the first live run: every agent that has no cleared Use Case yet is
blocked, and on a tenant where agents already exist, that is all of them.

Exit codes: 0 all good, 1 the cycle ran but something failed (one agent's Use
Case or block call was rejected), 2 it could not start (missing or wrong
credentials, an unreachable host, no saved admin sign-in for --enforce).
"""

from __future__ import annotations

import argparse
import logging
import sys

from credoai.errors import CredoAIError
from pydantic import ValidationError

from agent365_sync import sync
from agent365_sync.agent365 import Agent365Client, Agent365Error
from agent365_sync.config import _ENV_FILE, Settings, get_settings
from agent365_sync.credo import CredoClient
from agent365_sync.delegated_auth import (
    DelegatedAuth,
    DelegatedAuthError,
    DelegatedLoginRequired,
)

logger = logging.getLogger("agent365_sync.cli")


class StartupError(Exception):
    """The run cannot begin — a setup problem the reader fixes, not a crash."""


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return number


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Register Microsoft Agent 365 agents as Credo AI Use Cases and, "
        "with --enforce, block or unblock each agent to match its Use Case's "
        "governance workflow.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would be created, updated, blocked or unblocked without "
        "writing to Credo AI or Agent 365",
    )
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="Also block every agent whose Use Case has not cleared its workflow, "
        "and unblock the ones that have. Needs a one-time --login. Off by "
        "default: registering agents never changes who can use them.",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        metavar="N",
        help="Only the N most recently created agents in scope — a safe first run",
    )
    parser.add_argument(
        "--asset-id",
        action="append",
        default=[],
        metavar="ID",
        help="Only this agent, by its Agent 365 asset ID. Repeat for several. "
        "Overrides AGENT365_PACKAGE_TYPES.",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Sign in once as an Agent 365 admin so --enforce can block and "
        "unblock agents unattended afterwards, then exit",
    )
    return parser


def _missing(settings: Settings, names: list[str]) -> list[str]:
    return [n.upper() for n in names if not getattr(settings, n)]


def _delegated(settings: Settings) -> DelegatedAuth:
    return DelegatedAuth(
        settings.ms_tenant_id, settings.ms_client_id, settings.token_cache_path()
    )


def login(settings: Settings) -> int:
    missing = _missing(settings, ["ms_tenant_id", "ms_client_id"])
    if missing:
        logger.error("Missing %s — see README Step 2.", ", ".join(missing))
        return 2
    auth = _delegated(settings)
    current = auth.signed_in_user()
    if current:
        print(f"Currently signed in as {current}. Signing in again replaces it.\n")
    username = auth.login_with_device_code()
    print(
        f"\nSigned in as {username}. `--enforce` will now block and unblock agents "
        "as this account, with no further sign-in."
    )
    return 0


def run(args: argparse.Namespace) -> int:
    settings = get_settings()

    missing = _missing(
        settings,
        [
            "credo_api_key",
            "credo_tenant",
            "ms_tenant_id",
            "ms_client_id",
            "ms_client_secret",
        ],
    )
    if missing:
        raise StartupError(
            f"Missing {', '.join(missing)}. Copy .env.example to .env at the "
            "cookbook root and fill it in — see README Step 1."
        )

    # Connect to Credo AI before touching Microsoft. The SDK authenticates
    # inside its constructor, so a bad key or an unreachable host is reported in
    # a second instead of after reading the whole catalog.
    with CredoClient(settings) as credo:
        delegated = _delegated(settings) if args.enforce else None
        if delegated is not None:
            # Fail before any write if the saved admin sign-in is missing or
            # dead, rather than after Use Cases were created and the first
            # block call fails.
            try:
                delegated.get_access_token()
            except DelegatedLoginRequired as exc:
                if not args.dry_run:
                    raise
                logger.warning(
                    "%s A live --enforce run would stop here; this dry run goes on.",
                    exc,
                )

        with Agent365Client(
            settings.ms_tenant_id,
            settings.ms_client_id,
            settings.ms_client_secret,
            delegated=delegated,
        ) as agent365:
            packages = agent365.list_packages()
            agents, missing_ids = sync.select_agents(
                packages,
                types=settings.in_scope_types(),
                asset_ids=args.asset_id,
                limit=args.limit,
            )
            if missing_ids:
                raise StartupError(
                    "Not in the Agent 365 catalog: "
                    + ", ".join(missing_ids)
                    + ". Run without --asset-id to sync by type instead."
                )
            logger.info(
                "Catalog: %d package(s), %d selected (%s%s)",
                len(packages),
                len(agents),
                "chosen by --asset-id"
                if args.asset_id
                else f"types: {settings.agent365_package_types}",
                f", newest {args.limit}" if args.limit else "",
            )

            use_case_summary, outcomes = sync.sync_use_cases(
                agents,
                credo=credo,
                use_cases=credo.list_use_cases(),
                questionnaire=settings.intake_questionnaire(),
                dry_run=args.dry_run,
            )
            enforce_summary = (
                sync.enforce(
                    agents,
                    outcomes,
                    credo=credo,
                    agent365=agent365,
                    dry_run=args.dry_run,
                )
                if args.enforce
                else None
            )

    verb = "planned" if args.dry_run else "complete"
    logger.info("Sync %s: use cases %s", verb, use_case_summary.format())
    errors = list(use_case_summary.errors)
    if enforce_summary is not None:
        logger.info("Sync %s: enforcement %s", verb, enforce_summary.format())
        errors += enforce_summary.errors
    else:
        logger.info("Enforcement not requested — no agent was blocked or unblocked")

    for message in errors:
        logger.error("  %s", message)
    return 1 if errors else 0


def _log_format() -> str:
    return "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def main() -> None:
    args = _build_parser().parse_args()

    try:
        settings = get_settings()
    except ValidationError as exc:
        # A mistyped or outdated `.env` is a "could not start" condition, not a
        # crash: report the offending keys and exit 2, the same as missing
        # credentials.
        logging.basicConfig(level=logging.INFO, format=_log_format())
        for error in exc.errors():
            key = ".".join(str(part) for part in error["loc"]).upper()
            logger.error("Configuration: %s — %s", key, error["msg"])
        logger.error("Check %s against .env.example.", _ENV_FILE.name)
        sys.exit(2)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else settings.log_level.upper(),
        format=_log_format(),
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        rc = login(settings) if args.login else run(args)
    except CredoAIError as exc:
        # Every SDK failure lands here — bad key, unreachable host, rejected
        # payload on the listing. The run never got going, so it exits 2.
        logger.error("Credo AI: %s", exc)
        logger.error(
            "Check CREDO_API_KEY, CREDO_TENANT and CREDO_API_BASE_URL in .env, "
            "and that %s is reachable.",
            settings.credo_api_base_url,
        )
        sys.exit(2)
    except StartupError as exc:
        logger.error("%s", exc)
        sys.exit(2)
    except Agent365Error as exc:
        logger.error("Microsoft: %s", exc)
        sys.exit(2)
    except DelegatedAuthError as exc:
        logger.error("Microsoft sign-in: %s", exc)
        sys.exit(2)
    except Exception as exc:
        logger.error("Unexpected error: %s", exc)
        if args.verbose:
            raise
        sys.exit(1)
    else:
        sys.exit(rc)
