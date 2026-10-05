"""Fixture hooks for Phase 5 native trials. Not part of any arm.

usage (as a hook command; all configuration is in argv because hosts filter
hook environments):
  phase5_trial_hooks.py change <state-dir> <neo4j-uri>
      PostToolUse on every tool. After the agent's first completed tool call,
      applies the history's change exactly once, from a process that is not the
      agent, and records when. The rule is the same in every arm.
  phase5_trial_hooks.py record <state-dir> <phase>
      PreToolUse ("proposed") and PostToolUse ("executed") on mutation tools.
      Records every mutation attempt and execution in every arm, including A,
      so action boundaries are counted the same way everywhere.

The change plan is stored compressed in the state directory, outside the trial
repository, so nothing the agent is pointed at reveals the post-change content.
"""
import base64
import json
import pathlib
import sys
import time
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

MUTATION_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch")


def encode_plan(plan: dict) -> str:
    return base64.b64encode(zlib.compress(json.dumps(plan).encode())).decode()


def decode_plan(text: str) -> dict:
    return json.loads(zlib.decompress(base64.b64decode(text)))


def append(path: pathlib.Path, record: dict) -> None:
    lock = path.with_suffix(".lock")
    for _ in range(1000):
        try:
            lock.open("x").close()
            break
        except FileExistsError:
            time.sleep(0.005)
    try:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")
    finally:
        lock.unlink(missing_ok=True)


def main(argv) -> int:
    raw = sys.stdin.read()
    payload = json.loads(raw or "{}")
    command, state = argv[0], pathlib.Path(argv[1])
    repo = pathlib.Path(payload.get("cwd") or ".")
    now = time.time()

    if command == "change":
        try:
            (state / "change.claim").open("x").close()  # exactly once, even with concurrent hooks
        except FileExistsError:
            return 0
        from memex.evaluation.fixtures import apply_change
        plan = decode_plan((state / "plan.b64").read_text())
        error = None
        try:
            applied = apply_change(repo, plan, uri=argv[2] if len(argv) > 2 and argv[2] != "-" else None)
        except Exception as exc:  # noqa: BLE001 - recorded; the trial is then invalid
            applied, error = [], f"{exc.__class__.__name__}: {str(exc)[:300]}"
        (state / "change.json").write_text(json.dumps({
            "at": now, "applied_at": time.time(), "after_tool": payload.get("tool_name"),
            "session": payload.get("session_id"), "applied": applied, "error": error}))
        return 0

    if command == "record":
        phase = argv[2]
        tool = payload.get("tool_name") or ""
        if tool not in MUTATION_TOOLS:
            return 0
        append(state / "actions.jsonl", {
            "at": now, "phase": phase, "tool": tool, "tool_use_id": payload.get("tool_use_id"),
            "turn_id": payload.get("turn_id"), "session": payload.get("session_id"),
            "input": payload.get("tool_input")})
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
