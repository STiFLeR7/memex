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
    pilot = sub.add_parser("pilot", help="Agent-native field pilot: automatic live/shadow crossover and report",
                           parents=[parent_parser])
    psub = pilot.add_subparsers(dest="pilot_command", required=True)
    pstart = psub.add_parser("start", help="Start the weekly live/shadow schedule", parents=[parent_parser])
    pstart.add_argument("--participant", required=True, help="A pseudonym such as P1; never your name")
    pstart.add_argument("--weeks", type=int, default=4)
    pstart.add_argument("--seed", type=int, help="Schedule seed (default: random, recorded in the pilot file)")
    psub.add_parser("status", help="This week's mode and the schedule", parents=[parent_parser])
    psub.add_parser("corrections", help="List corrections so you can label them (shown locally only)",
                    parents=[parent_parser])
    plabel = psub.add_parser("label", help="Label one correction useful or false", parents=[parent_parser])
    plabel.add_argument("attempt_id")
    plabel.add_argument("verdict", choices=("useful", "false"))
    pcheck = psub.add_parser("checkin", help="Record whether you are still using memex", parents=[parent_parser])
    pcheck.add_argument("still_using", choices=("yes", "no"))
    preport = psub.add_parser("report", help="Write the counts-only report to send", parents=[parent_parser])
    preport.add_argument("--out", default="memex-pilot-report.json")
    psub.add_parser("stop", help="End the pilot early; the mode returns to your own setting",
                    parents=[parent_parser])
    for name, text in (("install", "Register a host for this worktree and install its project hooks"),
                       ("uninstall", "Remove memex's project hooks for a host, leaving other hooks intact")):
        host = sub.add_parser(name, help=text, parents=[parent_parser])
        host.add_argument("host", choices=("claude", "codex"))
        if name == "install":
            host.add_argument("--principal", default=os.getenv("USERNAME") or os.getenv("USER") or "owner")
            host.add_argument("--neo4j-uri", help="Graph URI for Codex hooks, which receive a filtered "
                                                  "environment (no credentials are stored)")


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
    if command in ("install", "uninstall"):
        return _install(args, command == "install")
    if command == "pilot":
        return _pilot(args)
    print(f"unknown v1 command {command}", file=sys.stderr)
    return 2


def _pilot(args) -> int:
    import time

    from memex.runtime import pilot
    from memex.runtime.modes import pilot_week, read_mode, read_pilot
    registration = _registration(args.repo)
    action = args.pilot_command
    try:
        if action == "start":
            state = pilot.start(registration, args.participant, weeks=args.weeks, seed=args.seed)
            print(f"Pilot started for {state['participant']}: {' -> '.join(state['schedule'])} (one mode per week).")
            print("Keep working with your agents as usual. At the end run `memex v1 pilot report`.")
        elif action == "status":
            state = read_pilot(registration)
            if state is None:
                print("no pilot in this repository")
            else:
                week = pilot_week(state, time.time())
                print(f"participant {state['participant']}; schedule {state['schedule']}; "
                      f"week {week or '-'}; mode now {read_mode(registration)}"
                      + ("; stopped" if state.get("stopped_at") else ""))
        elif action == "corrections":
            for c in pilot.corrections(registration):
                print(f"{c['attempt_id']}  week {c['week']} {c['mode']:6} {c['tool'] or '-':10} "
                      f"[{c['label'] or 'unlabeled'}] {c['reason']}")
        elif action == "label":
            pilot.label(registration, args.attempt_id, args.verdict)
            print(f"labeled {args.attempt_id} {args.verdict}")
        elif action == "checkin":
            pilot.checkin(registration, args.still_using == "yes")
            print("check-in recorded")
        elif action == "report":
            report = pilot.report(registration)
            Path(args.out).write_text(json.dumps(report, indent=1), encoding="utf-8")
            print(f"wrote {args.out}: counts only, {report['weeks_observed']} complete weeks, "
                  f"{report['sessions']} sessions. Review it, then send it.")
        elif action == "stop":
            pilot.stop(registration)
            print("pilot stopped; this worktree's own mode applies again")
    except ValueError as exc:
        print(f"memex pilot: {exc}", file=sys.stderr)
        return 1
    return 0


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


def _install(args, install: bool) -> int:
    """Project-scope hooks only. The user's global client configuration is never edited."""
    registration = _registration(args.repo)
    root = Path(registration.root)
    if args.host == "claude":
        from memex.integrations import claude_code
        settings = root / ".claude" / "settings.json"
        if install:
            claude_code.register(root, args.principal)
            claude_code.install_hooks(settings)
            print(f"Claude Code: registered {args.principal} and installed hooks in {settings}.")
            print("Next: run `memex v1 doctor`, then start Claude Code in this checkout.")
        else:
            claude_code.uninstall_hooks(settings)
            print(f"Claude Code: removed memex hooks from {settings}; other hooks were kept.")
        return 0
    from memex.integrations import codex
    hooks = root / ".codex" / "hooks.json"
    launcher = codex.write_launcher(registration)
    if install:
        backend = {"neo4j_uri": args.neo4j_uri} if args.neo4j_uri else None
        codex.register(root, args.principal, backend=backend)
        codex.install_hooks(hooks, launcher)
        print(f"Codex: registered {args.principal} and installed hooks in {hooks}.")
        print("Next: open Codex in this checkout and review the hooks with /hooks; untrusted hooks do not run.")
        print("Codex's supported route is an interactive or IDE (app-server) session; `codex exec` runs no hooks.")
    else:
        codex.uninstall_hooks(hooks, launcher)
        print(f"Codex: removed memex hooks from {hooks}; other hooks were kept.")
    return 0
