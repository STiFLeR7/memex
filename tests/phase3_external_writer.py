"""Stands in for a concurrent contributor during the native Phase 3 run.

Registered by the native test as a `PostToolUse` hook on `Read`, so the change
lands after the agent has read its target and before it proposes an edit. It
fires once, writes through a different process than the agent, and touches a
file the agent is not editing: the agent's own target bytes stay identical, so
the host's own stale-read detection cannot see this drift. Only the claim's
evidence changed.

This is not part of the adapter. It is the external writer in the scenario.
"""
import json
import pathlib
import sys

MARKER = "external-writer.done"

CHANGED = '''def validate(payload):
    if not payload:
        raise ValueError("payload required")
    return True
'''


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    repo = pathlib.Path(payload.get("cwd") or ".")
    marker = repo / ".git" / MARKER
    target = (payload.get("tool_input") or {}).get("file_path") or ""

    # Only after the agent has actually read the file its context describes.
    if marker.exists() or not target.endswith("api.py"):
        return 0
    (repo / "validate.py").write_text(CHANGED, newline="\n")
    marker.write_text("1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
