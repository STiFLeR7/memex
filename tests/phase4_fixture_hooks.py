"""Fixture hooks for the Phase 4 native runs. Not part of any adapter.

usage (as a hook command):
  phase4_fixture_hooks.py writer <state-dir> <trigger-file> <name>
      The concurrent contributor. After the agent's first completed read of
      <trigger-file> (Claude's `Read`, or a Codex shell command naming it), it
      rewrites validate.py once with an incompatible failure contract. The
      write comes from a different process than any agent, and touches a file
      no agent is editing.
  phase4_fixture_hooks.py record <state-dir>
      Records each `apply_patch`/`Edit`/`Write` proposal as it is checked, so a
      test can reconstruct exactly what a denied attempt would have written.
  phase4_fixture_hooks.py barrier <state-dir> <name> <wait-for> <timeout>
      Holds a session after a completed read until marker <wait-for> exists,
      after first publishing <name>. Lets a test keep two live sessions in
      lockstep without either finishing early.

Hosts filter or vary hook environments, so all configuration is in argv.
"""
import json
import pathlib
import sys
import time

CHANGED = '''def validate(payload):
    """Raise ValueError when the payload is unusable."""
    if not payload:
        raise ValueError("payload is unusable")
    return True
'''


def mentions(payload: dict, name: str) -> bool:
    tool_input = payload.get("tool_input") or {}
    if isinstance(tool_input, dict):
        path = tool_input.get("file_path") or ""
        command = tool_input.get("command") or ""
    else:
        path, command = "", str(tool_input)
    return path.replace("\\", "/").endswith(name) or (isinstance(command, str) and name in command)


def append(path: pathlib.Path, record: dict) -> None:
    lock = path.with_suffix(".lock")
    for _ in range(500):
        try:
            lock.open("x").close()
            break
        except FileExistsError:
            time.sleep(0.01)
    try:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")
    finally:
        lock.unlink(missing_ok=True)


def main(argv) -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    command, state = argv[0], pathlib.Path(argv[1])
    state.mkdir(parents=True, exist_ok=True)
    repo = pathlib.Path(payload.get("cwd") or ".")

    if command == "writer":
        trigger, name = argv[2], argv[3]
        done = state / f"writer-{name}.done"
        if done.exists() or not mentions(payload, trigger):
            return 0
        if payload.get("tool_name") == "Bash" and "validate.py" in json.dumps(payload.get("tool_input")):
            return 0  # The agent is reading the dependency itself; the drift must come after.
        (repo / "validate.py").write_text(CHANGED, newline="\n")
        done.write_text(json.dumps({"at": time.time(), "session": payload.get("session_id")}))
        return 0

    if command == "record":
        tool_input = payload.get("tool_input")
        append(state / "proposals.jsonl", {
            "at": time.time(), "session": payload.get("session_id"), "tool": payload.get("tool_name"),
            "tool_use_id": payload.get("tool_use_id"), "turn_id": payload.get("turn_id"),
            "cwd": payload.get("cwd"), "input": tool_input})
        return 0

    if command == "barrier":
        name, wait_for, timeout = argv[2], argv[3], float(argv[4])
        mine = state / f"barrier-{name}.ready"
        if mine.exists():
            return 0  # Only the first completed read holds the session.
        mine.write_text(json.dumps({"at": time.time(), "session": payload.get("session_id")}))
        deadline = time.time() + timeout
        while time.time() < deadline and not (state / wait_for).exists():
            time.sleep(0.2)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
