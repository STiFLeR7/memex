"""W18: actionable freshness and capability diagnostics for one worktree.

A graph connection's health is reported separately and never as a freshness
result. Each finding carries a short actionable message, e.g. "index is two
generations behind; current action checked as unknown".
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from memex.runtime.modes import read_mode


def _rows(db, query, params=()):
    try:
        return db.execute(query, params).fetchall()
    except sqlite3.Error:
        return []


def diagnose(registration, *, graph_ok: bool | None = None) -> dict:
    """Read-only. Never starts a service, writes state or contacts a host."""
    from memex.runtime import guard
    from memex.runtime.journal import ChangeJournal

    findings: list[dict] = []

    def finding(level: str, code: str, message: str, **detail):
        findings.append({"level": level, "code": code, "message": message, **detail})

    report: dict = {"repository": {"repo_id": registration.repo_id, "worktree_id": registration.worktree_id,
                                   "root": registration.root, "common_dir": registration.common_dir},
                    "mode": read_mode(registration), "guarded_writes": guard.guarded(registration)}
    runtime = Path(registration.runtime_path)
    if not runtime.exists():
        finding("warning", "no_runtime",
                "memex has not indexed this worktree yet; run a session or `memex v1 migrate`.")
        report["findings"] = findings
        return report

    journal = ChangeJournal(runtime)
    view = journal.current(registration.worktree_id)
    pending = journal.pending(registration.worktree_id)
    if view is None:
        finding("warning", "no_view", "no indexed view yet; the first action will be checked as unknown.")
    else:
        behind = view.content_generation - view.indexed_generation
        report["generations"] = {"observed": view.content_generation, "indexed": view.indexed_generation,
                                 "pending_views": len(pending)}
        if behind > 0:
            finding("warning", "index_behind",
                    f"index is {behind} generation{'s' if behind != 1 else ''} behind; current actions are "
                    "checked as unknown until it catches up.", behind=behind)

    db = sqlite3.connect(runtime, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        dirty = _rows(db, "SELECT task_id FROM live_tasks WHERE pending IS NOT NULL")
        report["tasks"] = {"active": len(_rows(db, "SELECT task_id FROM live_tasks")),
                           "with_pending_correction": len(dirty)}
        if dirty:
            finding("info", "pending_corrections",
                    f"{len(dirty)} task(s) hold a correction their session has not yet confirmed; it is "
                    "re-offered at the next action.")
        stale_sessions = _rows(db, "SELECT harness, native_session_id FROM live_adapter_sessions "
                                   "WHERE context_retained=0")
        if stale_sessions:
            finding("warning", "resync_required",
                    f"{len(stale_sessions)} session(s) must resynchronize (after compaction, resume or "
                    "re-enabling live mode); their next action receives a full replacement packet.")
        failed = _rows(db, "SELECT harness, kind, sequence FROM live_adapter_deliveries WHERE state='failed' "
                           "ORDER BY rowid DESC LIMIT 1")
        if failed:
            row = failed[0]
            finding("warning", "last_failed_insertion",
                    f"the last {row['kind']} delivery to a {row['harness']} session was never confirmed in its "
                    "transcript; it stays pending and is re-offered.")
        exhausted = _rows(db, "SELECT count(*) AS n FROM live_trace WHERE reason='reconsideration_limit'")
        if exhausted and exhausted[0]["n"]:
            finding("warning", "retry_exhausted",
                    f"{exhausted[0]['n']} action(s) hit the reconsideration ceiling; the conflict was reported, "
                    "not retried.")
        unresolved_dir = Path(registration.common_dir) / "memex" / "guard" / registration.worktree_id
        intents = list(unresolved_dir.glob("intent-*.json")) if unresolved_dir.exists() else []
        live_leases = _rows(db, "SELECT target FROM guard_leases WHERE worktree_id=? "
                                "AND expires_at > strftime('%s','now')",
                            (registration.worktree_id,))
        report["guard"] = {"interrupted_write_intents": len(intents), "live_leases": len(live_leases)}
        if intents:
            finding("error", "unresolved_interrupted_write",
                    "an interrupted guarded write is held; inspect it, then resolve it with discard_intent. Its "
                    "targets refuse guarded writes until then.")
    finally:
        db.close()

    report["capabilities"] = capabilities(registration)
    for host, cap in report["capabilities"].items():
        if not cap["registered"]:
            finding("info", f"{host}_not_registered", f"{host} is not registered for this worktree; it gets no "
                    "context and no action checks.")
        elif not cap["hooks_installed"]:
            finding("warning", f"{host}_hooks_missing",
                    f"{host} is registered but its hooks are not installed in this checkout; it receives no "
                    "context and its edits are not checked.")
    if report["mode"] == "shadow":
        finding("info", "shadow_mode", "shadow mode: checks are recorded but nothing is delivered or denied.")
    elif report["mode"] == "off":
        finding("info", "off", "v1 is off: hooks abstain; legacy readers are unaffected.")
    if report["guarded_writes"] and report["mode"] != "live":
        finding("warning", "guarded_without_live",
                "guarded writes are on but v1 is not live: the guard still fences its own writes, but native "
                "edits are not redirected to it.")
    report["graph"] = ("reachable" if graph_ok else "unreachable" if graph_ok is False else "not checked") + \
        " (connection health only; not a freshness result)"
    report["findings"] = findings
    return report


def capabilities(registration) -> dict:
    root, common = Path(registration.root), Path(registration.common_dir)
    claude_settings = [root / ".claude" / "settings.json", root / ".claude" / "settings.local.json"]
    out = {}
    for host, capability, installed in (
        ("claude_code", common / "memex" / "adapters" / "claude-code.json",
         any(p.exists() and "memex.integrations.claude_code" in p.read_text(encoding="utf-8", errors="replace")
             for p in claude_settings)),
        ("codex", common / "memex" / "adapters" / "codex.json",
         (root / ".codex" / "hooks.json").exists()
         and "memex" in (root / ".codex" / "hooks.json").read_text(encoding="utf-8", errors="replace")),
    ):
        version = None
        if capability.exists():
            try:
                version = json.loads(capability.read_text(encoding="utf-8")).get("version")
            except ValueError:
                version = "unreadable"
        out[host] = {"registered": capability.exists(), "adapter_version": version, "hooks_installed": installed,
                     "delivery": "native action loop" if host == "claude_code" else "native action loop (app-server)",
                     "guarded_writes": "cooperating writers through guard_mcp only"}
    return out


def render(report: dict) -> str:
    lines = [f"memex v1 doctor: {report['repository']['root']}",
             f"  repository {report['repository']['repo_id']}  worktree {report['repository']['worktree_id']}",
             f"  mode {report['mode']}; guarded writes {'on' if report['guarded_writes'] else 'off'}",
             f"  graph: {report['graph']}"]
    if "generations" in report:
        g = report["generations"]
        lines.append(f"  generations: observed {g['observed']}, indexed {g['indexed']}, pending {g['pending_views']}")
    for host, cap in report.get("capabilities", {}).items():
        lines.append(f"  {host}: registered={cap['registered']} hooks={cap['hooks_installed']} "
                     f"version={cap['adapter_version']}")
    for f in report["findings"]:
        lines.append(f"  [{f['level']}] {f['message']}")
    if not report["findings"]:
        lines.append("  no findings")
    return "\n".join(lines)
