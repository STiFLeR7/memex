"""Opt-in MCP server that routes supported mutations through the write guard.

This is a separate server, started only for a worktree in guarded mode, so the
public memex MCP surface is unchanged. It exposes exactly two tools:

* `guard_read(path)` returns a file's text and the hash a later write must
  name as `expected_sha256`;
* `guard_write(changes)` applies writes, deletes and renames as one fenced
  commit, or refuses with a machine-readable reason and changes nothing.

A refusal is an instruction to reconsider, not to retry the same patch: the
bytes the writer reasoned about are no longer the bytes on disk. Each host's
`PreToolUse` also sees `guard_write` and runs the normal context check on its
targets, so dependency corrections still arrive before a guarded write.

usage: python -m memex.integrations.guard_mcp --root <worktree> --label <host>
"""
import argparse
from pathlib import Path

from memex.runtime.guard import GuardError, Mutation, StaleWrite, WriteGuard, guarded, sha256
from memex.runtime.views import discover_repository

#: Tool name suffix, as both hosts prefix MCP tools (`mcp__<server>__<tool>`).
GUARD_WRITE = "guard_write"

MAX_READ_BYTES = 2_000_000


def read(guard: WriteGuard, path: str) -> dict:
    relative = guard.relative(path)
    target = guard.root / relative
    if not target.exists():
        return {"path": relative, "sha256": None, "exists": False, "text": ""}
    data = target.read_bytes()
    if len(data) > MAX_READ_BYTES:
        return {"path": relative, "sha256": sha256(data), "exists": True, "text": None,
                "note": "file exceeds the guard read limit; read it natively and use this hash"}
    return {"path": relative, "sha256": sha256(data), "exists": True,
            "text": data.decode("utf-8", "replace")}


def _expected(value):
    return None if value in (None, "", "absent") else value


def write(guard: WriteGuard, label: str, changes: list[dict]) -> dict:
    if not guarded(guard.registration):
        return {"outcome": "refused", "reason": "guarded_mode_off",
                "instruction": "This worktree is in context mode; edit natively."}
    try:
        mutations = []
        for change in changes:
            op = change.get("op", "write")
            content = change.get("content")
            mutations.append(Mutation(op, change["path"], _expected(change.get("expected_sha256")),
                                      None if content is None else content.encode("utf-8"),
                                      change.get("to"), _expected(change.get("expected_to_sha256"))))
        results = guard.apply(label, mutations)
    except StaleWrite as exc:
        return {"outcome": "replan", "reason": exc.reason, "path": exc.path,
                "expected_sha256": exc.expected, "observed_sha256": exc.observed,
                "instruction": "The file changed since you read it. Call guard_read again and "
                               "reconsider this change against the current contents; do not "
                               "resubmit the same patch."}
    except GuardError as exc:
        return {"outcome": "replan", "reason": exc.reason, "detail": str(exc)[:300],
                "instruction": "The guarded write was refused and nothing changed. Re-read and reconsider."}
    except (KeyError, ValueError) as exc:
        return {"outcome": "refused", "reason": "malformed_change", "detail": str(exc)[:200]}
    return {"outcome": "committed", "result_sha256": results}


def build_server(root: str, label: str):
    from mcp.server.fastmcp import FastMCP

    guard = WriteGuard(discover_repository(Path(root)))
    server = FastMCP("memex_guard")

    @server.tool(name="guard_read")
    def guard_read(path: str) -> dict:
        """Read a worktree file and the sha256 a guarded write must expect."""
        return read(guard, path)

    @server.tool(name=GUARD_WRITE)
    def guard_write(changes: list[dict]) -> dict:
        """Apply file changes as one fenced commit, or refuse and change nothing.

        Each change is {"op": "write"|"delete"|"rename", "path": ..., "expected_sha256": hash
        from guard_read or "absent" for a new file, "content": text for writes, "to": rename
        destination}.
        """
        return write(guard, label, changes)

    return server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="memex-guard-mcp")
    parser.add_argument("--root", required=True)
    parser.add_argument("--label", required=True)
    args = parser.parse_args(argv)
    build_server(args.root, args.label).run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
