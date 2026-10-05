"""`memex v1 ...`: rollout, migration, rollback and diagnostics for the v1 runtime."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path


def add_parser(subparsers, parent_parser) -> None:
    v1 = subparsers.add_parser("v1", help="v1 Live Context rollout, migration and diagnostics",
                               parents=[parent_parser])
    sub = v1.add_subparsers(dest="v1_command", required=True)
    migrate = sub.add_parser("migrate", help="Import legacy knowledge as legacy_unverified (additive)",
                             parents=[parent_parser])
    migrate.add_argument("--dry-run", action="store_true", help="Report counts and mapping; write nothing")
    migrate.add_argument("--batch", type=int, default=100, help="Legacy nodes per checkpointed batch")
    migrate.add_argument("--no-index", action="store_true", help="Skip rebuilding the structural view")
    migrate.add_argument("--legacy-path", help="Legacy repo_path to import (default: this checkout)")
    mode = sub.add_parser("mode", help="Set this worktree's mode", parents=[parent_parser])
    mode.add_argument("mode", choices=("off", "shadow", "live"))
    guarded = sub.add_parser("guarded", help="Opt this worktree in or out of guarded writes",
                             parents=[parent_parser])
    guarded.add_argument("state", choices=("on", "off"))
    sub.add_parser("rollback", help="Disable v1 for this worktree, keeping recovery state",
                   parents=[parent_parser])
    doctor = sub.add_parser("doctor", help="Freshness and capability diagnostics", parents=[parent_parser])
    doctor.add_argument("--json", action="store_true")
    sub.add_parser("legacy", help="List legacy_unverified knowledge (historical retrieval only)",
                   parents=[parent_parser])


def _registration(repo_root: str | None):
    from memex.runtime.views import discover_repository
    return discover_repository(Path(repo_root or "."))


async def _driver():
    from graphiti_core.driver.neo4j_driver import Neo4jDriver
    uri = os.getenv("MEMEX_LIVE_NEO4J_URI") or os.getenv("NEO4J_URI")
    user, password = os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD")
    if not uri:
        from memex.config import get_config
        config = get_config()
        uri, user, password = config.neo4j_uri, config.neo4j_user, config.neo4j_password
    return await asyncio.to_thread(Neo4jDriver, uri, user, password)


async def _graph_ok(driver) -> bool:
    try:
        await driver.execute_query("RETURN 1")
        return True
    except Exception:  # noqa: BLE001 - reported as unreachable, never as a freshness result
        return False


def run(args) -> int:
    command = args.v1_command
    if command == "mode":
        from memex.runtime.migration import set_mode
        print(json.dumps(set_mode(_registration(args.repo), args.mode), indent=1))
        return 0
    if command == "guarded":
        from memex.runtime.guard import set_mode
        registration = _registration(args.repo)
        set_mode(registration, "guarded" if args.state == "on" else "context")
        print(f"guarded writes {args.state} for worktree {registration.worktree_id}")
        return 0
    if command == "rollback":
        from memex.runtime.migration import rollback
        print(json.dumps(rollback(_registration(args.repo)), indent=1))
        return 0
    if command == "doctor":
        return asyncio.run(_doctor(args))
    if command == "migrate":
        return asyncio.run(_migrate(args))
    if command == "legacy":
        return asyncio.run(_legacy(args))
    print(f"unknown v1 command {command}", file=sys.stderr)
    return 2


async def _doctor(args) -> int:
    from memex.runtime.diagnostics import diagnose, render
    registration = _registration(args.repo)
    graph_ok = None
    try:
        driver = await _driver()
        try:
            graph_ok = await _graph_ok(driver)
        finally:
            await driver.close()
    except Exception:  # noqa: BLE001 - no configuration means the graph was not checked
        graph_ok = None
    report = diagnose(registration, graph_ok=graph_ok)
    print(json.dumps(report, indent=1, default=str) if args.json else render(report))
    return 1 if any(f["level"] == "error" for f in report["findings"]) else 0


async def _migrate(args) -> int:
    from memex.config import canonical_repo_path
    from memex.runtime.migration import migrate
    legacy_path = args.legacy_path or canonical_repo_path(str(Path(args.repo or ".").resolve()))
    driver = await _driver()
    try:
        report = await migrate(driver, legacy_path, dry_run=args.dry_run, batch_size=args.batch,
                               index=not args.no_index)
    finally:
        await driver.close()
    print(json.dumps(report, indent=1, default=str))
    return 0 if report.get("status", "dry_run" if args.dry_run else "") in ("complete", "dry_run") else 1


async def _legacy(args) -> int:
    from memex.runtime.migration import legacy_claims
    registration = _registration(args.repo)
    driver = await _driver()
    try:
        claims = await legacy_claims(driver, registration.repo_id)
    finally:
        await driver.close()
    for claim in claims:
        print(f"[{claim['coverage']}, {claim['authority']}] {claim.get('kind')}: {claim.get('text')}")
    if not claims:
        print("no legacy knowledge imported for this repository")
    return 0
