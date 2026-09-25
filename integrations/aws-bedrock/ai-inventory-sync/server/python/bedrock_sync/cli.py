"""CLI for bedrock-sync.

A single-shot sync, intended to be run on a schedule (cron, an EventBridge
rule, a Kubernetes CronJob): Bedrock has no push notification for model
inventory changes, so there is nothing to serve and no daemon to keep alive.

    python main.py                        # sync everything in scope
    python main.py --dry-run              # log the plan, write nothing
    python main.py --all-foundation-models  # this run only; the whole catalog
    python main.py --no-agentcore           # this run only; skip agents

One cycle covers two Credo AI resources: the Bedrock model inventory goes to
the model registry, and each AgentCore harness goes to a Use Case, linked to
the model it calls.

Exit codes: 0 all good, 1 the cycle ran but something failed (a listing was
denied, a model failed to upsert), 2 it could not start (missing Credo AI
credentials, unresolvable AWS region).
"""

from __future__ import annotations

import argparse
import logging
import sys

import httpx
from dotenv import load_dotenv
from pydantic import ValidationError

from bedrock_sync import sync
from bedrock_sync.agentcore import AgentCoreReader
from bedrock_sync.bedrock import AWS_ERRORS, BedrockReader
from bedrock_sync.config import _ENV_FILE, get_settings
from bedrock_sync.credo import CredoApiError, CredoClient

logger = logging.getLogger("bedrock_sync.cli")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Sync Amazon Bedrock models into the Credo AI model registry "
        "and AgentCore agents into Use Cases, linking each agent to the model it "
        "calls and each model to its provider's vendor.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would be created/updated without writing to Credo AI",
    )
    parser.add_argument(
        "--all-foundation-models",
        dest="all_foundation_models",
        action="store_true",
        default=None,
        help="Sync the whole region-wide foundation-model catalog for this run "
        "instead of only the models a harness names "
        "(overrides BEDROCK_SYNC_ALL_FOUNDATION_MODELS)",
    )
    parser.add_argument(
        "--no-agentcore",
        dest="agentcore",
        action="store_false",
        default=None,
        help="Skip AgentCore agents for this run (overrides BEDROCK_SYNC_AGENTCORE)",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    settings = get_settings()

    # Checked before any AWS call: enumerating a whole Bedrock account only to
    # fail on a missing API key wastes a minute and points the blame at AWS.
    if not settings.credo_api_key or not settings.credo_tenant:
        raise CredoApiError(
            "CREDO_API_KEY and CREDO_TENANT are required. Copy .env.example to "
            ".env at the cookbook root and fill them in — see README Step 1."
        )

    all_foundation = (
        settings.bedrock_sync_all_foundation_models
        if args.all_foundation_models is None
        else args.all_foundation_models
    )
    include_agentcore = (
        settings.bedrock_sync_agentcore if args.agentcore is None else args.agentcore
    )

    # Agents are read first: narrowing the catalog to the models they name means
    # knowing what they name before the models are fetched.
    agentcore = None
    if include_agentcore:
        # A second client for a second service (bedrock-agentcore-control), so
        # its failures are independent of the model listings below.
        agentcore = AgentCoreReader(settings.bedrock_region).inventory()
    else:
        logger.info("AgentCore is disabled — skipping agents")

    referenced: set[str] | None = None
    if not all_foundation:
        referenced = {
            r.model_id
            for r in (agentcore.resources if agentcore else [])
            if r.model_provider == "bedrock" and r.model_id
        }
        if not referenced:
            # Deliberate, and loud: widening to the whole catalog because an
            # unrelated switch is off would be a surprise, so report the empty
            # result rather than quietly changing scope.
            logger.warning(
                "No harness names a Bedrock model, so no foundation models are "
                "in scope. The account's own custom and imported models still "
                "sync. Set BEDROCK_SYNC_ALL_FOUNDATION_MODELS=true or pass "
                "--all-foundation-models for the full catalog."
            )

    reader = BedrockReader(settings.bedrock_region)
    # Inference profiles are only read when there are agents: a harness usually
    # names a profile rather than a model id, and the map is what turns that
    # into a registry record — for the catalog filter and the link pass alike.
    inventory = reader.inventory(
        referenced_model_ids=referenced,
        include_inference_profiles=include_agentcore,
    )

    with CredoClient(settings) as credo:
        model_summary = sync.sync_models(
            inventory.models, credo=credo, dry_run=args.dry_run
        )
        vendor_summary = sync.sync_vendors(
            inventory.models,
            synced=model_summary,
            credo=credo,
            dry_run=args.dry_run,
        )
        use_case_summary = (
            sync.sync_use_cases(agentcore.resources, credo=credo, dry_run=args.dry_run)
            if agentcore is not None
            else None
        )
        link_summary = (
            sync.link_use_case_models(
                agentcore.resources,
                models=model_summary,
                use_cases=use_case_summary,
                credo=credo,
                profiles=inventory.inference_profiles,
                dry_run=args.dry_run,
            )
            if agentcore is not None and use_case_summary is not None
            else None
        )

    for message in inventory.errors:
        model_summary.error(message)

    verb = "planned" if args.dry_run else "complete"
    logger.info(
        "Sync %s (region %s): models %s", verb, reader.region, model_summary.format()
    )
    errors = list(model_summary.errors)

    logger.info(
        "Sync %s (region %s): vendors %s",
        verb,
        reader.region,
        vendor_summary.format(),
    )
    errors += vendor_summary.errors

    if use_case_summary is not None:
        for message in agentcore.errors:
            use_case_summary.error(message)
        logger.info(
            "Sync %s (region %s): use cases %s",
            verb,
            reader.region,
            use_case_summary.format(),
        )
        errors += use_case_summary.errors

    if link_summary is not None:
        logger.info(
            "Sync %s (region %s): model links %s",
            verb,
            reader.region,
            link_summary.format(),
        )
        errors += link_summary.errors

    for message in errors:
        logger.error("  %s", message)
    return 1 if errors else 0


def _log_format() -> str:
    return "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def main() -> None:
    load_dotenv(_ENV_FILE)
    args = _build_parser().parse_args()

    try:
        settings = get_settings()
    except ValidationError as exc:
        # A mistyped or outdated `.env` is a "could not start" condition, not a
        # crash: report the offending keys and exit 2, the same as missing
        # credentials or an unresolvable region.
        logging.basicConfig(level=logging.INFO, format=_log_format())
        for error in exc.errors():
            key = ".".join(str(part) for part in error["loc"]).upper()
            logger.error("Configuration: %s — %s", key, error["msg"])
        logger.error("Check .env against .env.example.")
        sys.exit(2)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else settings.log_level.upper(),
        format=_log_format(),
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        rc = run(args)
    except CredoApiError as exc:
        logger.error("Credo AI: %s", exc)
        sys.exit(2)
    except httpx.RequestError as exc:
        # Unreachable host, refused connection, DNS failure, TLS error: the run
        # never started, so it exits 2 like any other configuration problem.
        logger.error(
            "Credo AI at %s is unreachable: %s", settings.credo_api_base_url, exc
        )
        logger.error("Check CREDO_API_BASE_URL in .env, and that the host is up.")
        sys.exit(2)
    except AWS_ERRORS as exc:
        # Client construction itself failed — no region, no credentials. Per
        # listing failures are handled inside BedrockReader.inventory().
        logger.error("AWS: %s", exc)
        sys.exit(2)
    except Exception as exc:
        logger.error("Unexpected error: %s", exc)
        if args.verbose:
            raise
        sys.exit(1)
    else:
        sys.exit(rc)
