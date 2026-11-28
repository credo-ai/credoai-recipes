#!/usr/bin/env python3
"""Credo AI tenant migration kit.

  python migrate.py check                       read-only: confirm access, show counts
  python migrate.py extract <entity|all>        read from the source tenant into data/
  python migrate.py load <entity|all>           dry run: show what a load would do
  python migrate.py load <entity|all> --live --target <tenant>
                                                write to the target tenant

Every load is a dry run unless --live is given. A live load also needs --target, naming the
tenant in TARGET_TENANT.

See README.md for setup, what the kit copies, and the checklist before a live run.
"""

import argparse
import sys
from pathlib import Path

from kit import __version__
from kit.clients.base import APIError
from kit.clients.integration import IntegrationClient
from kit.clients.internal import InternalClient
from kit.config import ConfigError, load_tenant_config
from kit.context import Context
from kit.entities import ENTITIES
from kit.internal.triggers import trigger_version
from kit.internal.users import STATE_TEXT as LOCAL_USERS_TEXT
from kit.internal.users import local_users_state
from kit.loading import LoadStopped
from kit.logger import setup_logging
from kit.run_meta import RunMetaMismatch, check_run_meta

# .env, data/ and logs/ always live next to this file, wherever the command is run from
KIT_DIR = Path(__file__).resolve().parent

# Integration API counts. Its policy_packs and questionnaires counts include only those with a published version:
# the Integration list leaves out packs and questionnaires whose only version is a draft.
CHECK_ENDPOINTS = [
    "risk_types",
    "custom_fields",
    "policy_control_bases",
    "policy_packs",
    "risk_scenarios",
    "questionnaires",
    "vendors",
    "models",
    "use_cases",
    "triggers",
]


def build_context(live):
    """Returns (Context, secrets to keep out of logs)."""
    env_file = KIT_DIR / ".env"
    source_cfg, target_cfg = load_tenant_config("SOURCE", env_file), load_tenant_config("TARGET", env_file)
    dry_run = not live
    ctx = Context(
        # Source clients are read-only: any attempt to write to the source tenant raises an error
        source=IntegrationClient(source_cfg, read_only=True),
        target=IntegrationClient(target_cfg, dry_run=dry_run),
        source_internal=InternalClient(source_cfg, read_only=True),
        target_internal=InternalClient(target_cfg, dry_run=dry_run),
        dry_run=dry_run,
        data_dir=KIT_DIR / "data",
    )
    return ctx, [source_cfg.api_key, target_cfg.api_key]


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="migrate.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="read-only: confirm access to both tenants and show entity counts")
    choices = [*ENTITIES, "all"]
    extract = sub.add_parser("extract", help="read entities from the source tenant into data/")
    extract.add_argument("entity", choices=choices)
    load = sub.add_parser("load", help="load extracted entities into the target tenant")
    load.add_argument("entity", choices=choices)
    load.add_argument("--live", action="store_true", help="write to the target tenant (otherwise a dry run)")
    load.add_argument("--target", metavar="TENANT", help="the target tenant's name; required with --live")
    load.add_argument(
        "--allow-invites", action="store_true", help="load users even if the target would email them invitations"
    )
    load.add_argument(
        "--raise-roles",
        action="store_true",
        help="raise existing target users to their source role where the target's is lower",
    )
    args = parser.parse_args(argv)
    if getattr(args, "live", False) and not args.target:
        parser.error("--live needs --target <tenant>, naming the tenant you mean to write to")
    return args


def run_check(ctx, log):
    ok = True
    for side, integration, internal in (
        ("source", ctx.source, ctx.source_internal),
        ("target", ctx.target, ctx.target_internal),
    ):
        tenant = integration.config.tenant
        apis = (("Integration API", integration, "ping"), ("application API", internal, "entity_types"))
        for api, client, path in apis:
            try:
                client.get_json(path)
                log.info(f"{side} {tenant}: {api} OK")
            except APIError as e:
                ok = False
                log.error(f"{side} {tenant}: {api} FAILED: {e} {e.body[:200]}")
    if not ok:
        return 2
    # Triggers migrate only between two v3 tenants, so show each side's version up front
    for side, internal in (("source", ctx.source_internal), ("target", ctx.target_internal)):
        tenant = internal.config.tenant
        try:
            log.info(f"{side} {tenant}: triggers {trigger_version(internal)}")
        except APIError as e:
            log.warning(f"{side} {tenant}: couldn't tell the trigger version: {e} {e.body[:200]}")
    # A live users load stops while the target may email invitations, so show that before the first live run
    tenant = ctx.target.config.tenant
    state = local_users_state(ctx)
    if state == "off":
        log.info(f"target {tenant}: invite emails off (LOCAL_USERS is off); users load without invitations")
    else:
        log.warning(
            f"target {tenant}: invite emails may be sent (LOCAL_USERS {LOCAL_USERS_TEXT[state]}); a live "
            f"users load will stop unless it is turned off or --allow-invites is given"
        )
    log.info(f"{'endpoint':<22}{'source':>10}{'target':>10}")
    for path in CHECK_ENDPOINTS:
        counts = []
        for client in (ctx.source, ctx.target):
            try:
                counts.append(str(len(client.get_all(path))))
            except APIError as e:
                log.warning(f"Couldn't count {path} on {client.config.tenant}: {e}")
                counts.append(f"error {e.status_code}")
        log.info(f"{path:<22}{counts[0]:>10}{counts[1]:>10}")
    return 0


def run_entities(ctx, args, log):
    entities = list(ENTITIES) if args.entity == "all" else [args.entity]
    if args.command == "load":
        missing = [e for e in entities if not (Path(ctx.data_dir) / f"{e}.json").exists()]
        if missing:
            raise FileNotFoundError(
                f"No extract file for {', '.join(missing)}. Run: python migrate.py extract "
                f"{missing[0] if len(missing) == 1 else 'all'}"
            )
    for entity in entities:
        module = ENTITIES[entity]
        if args.command == "extract":
            log.info(f"{entity}: extracted {module.extract(ctx)}")
        else:
            module.load(ctx)


def main(argv=None, context_factory=build_context, log_dir=None):
    args = parse_args(argv)
    live = getattr(args, "live", False)
    try:
        ctx, secrets = context_factory(live)
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 2
    ctx.allow_invites = getattr(args, "allow_invites", False)
    ctx.raise_roles = getattr(args, "raise_roles", False)
    run_name = args.command if args.command == "check" else f"{args.command}_{args.entity}"
    log, log_path = setup_logging(run_name, log_dir=log_dir or KIT_DIR / "logs", secrets=secrets)
    src, tgt = ctx.source.config, ctx.target.config
    log.info(f"Migration kit {__version__}: {' '.join(argv if argv is not None else sys.argv[1:])}")
    log.info(f"Source: {src.tenant} ({src.base_url})   Target: {tgt.tenant} ({tgt.base_url})")
    if (src.base_url, src.tenant) == (tgt.base_url, tgt.tenant):
        log.error("Source and target are the same tenant. Check SOURCE_* and TARGET_* in .env and your shell.")
        return 2
    named = getattr(args, "target", None)
    if named is not None and named != tgt.tenant:
        log.error(
            f"--target {named} doesn't match the configured target tenant {tgt.tenant}. "
            "Check TARGET_TENANT in .env and your shell."
        )
        return 2
    code = 0
    try:
        if args.command == "check":
            code = run_check(ctx, log)
        else:
            check_run_meta(ctx, args.command, live)
            if args.command == "load":
                log.info(
                    f"LIVE RUN: writing to {tgt.tenant}"
                    if live
                    else "DRY RUN: nothing will be written. Add --live to write."
                )
            run_entities(ctx, args, log)
    except APIError as e:
        log.error(f"Stopped: {e} {e.body[:300]}")
        code = 2
    except LoadStopped as e:
        # A deliberate refusal, already flagged in the summary like any item needing attention: exit 1, not 2
        log.error(f"Stopped: {e}")
        code = 1
    except (FileNotFoundError, RunMetaMismatch) as e:
        log.error(f"Stopped: {e}")
        code = 2
    except Exception:
        log.exception("Stopped by an unexpected error")
        code = 2
    if args.command == "load":
        log.info("Summary\n" + ctx.summary.table())
        ctx.summary.save(log_path.with_name(f"{log_path.stem}_summary.json"))
        if code == 0 and (ctx.summary.has_failures or ctx.summary.needs_attention):
            code = 1
    elif args.command == "extract" and ctx.summary.needs_attention:
        # An extract has no counts to show, but something it couldn't copy still needs to end the output
        log.info("Summary\n" + ctx.summary.table())
        if code == 0:
            code = 1
    log.info(f"Full log: {log_path}")
    return code


if __name__ == "__main__":
    sys.exit(main())
