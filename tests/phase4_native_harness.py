"""Orchestration for Phase 4 native concurrency runs: two live clients, one test.

A run keeps a real Claude Code process and a real Codex app-server turn alive at
the same time. Barrier hooks hold each session after it reads its target; the
test then makes its change and releases the sessions in the order under test,
so ordering is chosen by the test and recorded, not inferred from timing.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

from memex.runtime.trace import TraceStore
from tests import phase4_support as support

RELAY = '''from validate import validate


def relay(payload):
    raise NotImplementedError("relay is not implemented yet")
'''

RELAY_CONTRACT = '''"""Objective contract for relay(). Exits 0 only if every case holds."""
import sys

from relay import relay


def main() -> int:
    assert relay("hello") == {"relayed": True, "payload": "hello"}, "a usable payload is relayed"
    assert relay("") == {"relayed": False, "reason": "invalid"}, "an unusable payload is reported"
    print("contract ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

CLAUDE_PROMPT = (
    "Implement send(payload) in api.py. It must return {'ok': True, 'value': payload} for a usable "
    "payload and {'ok': False, 'error': 'invalid'} for an unusable one, and it must not raise. Use "
    "validate() from validate.py. Start from the memex engineering context already provided for "
    "validate()'s contract instead of reading validate.py up front, but do re-read it if memex tells "
    "you it changed. Edit only api.py. Do not run any commands."
)

CODEX_PROMPT = (
    "Implement relay(payload) in relay.py. It must return {'relayed': True, 'payload': payload} for a "
    "usable payload and {'relayed': False, 'reason': 'invalid'} for an unusable one, and it must not "
    "raise. Use validate() from validate.py. Start from the memex engineering context already provided "
    "for validate()'s contract instead of reading validate.py up front, but do re-read it if memex tells "
    "you it changed. Edit only relay.py, using apply_patch. You may read files with read-only shell "
    "commands; do not run or test code, and do not write files with shell commands."
)


class ClaudeRun:
    """A live `claude -p` session in a fixture checkout."""

    def __init__(self, repo: pathlib.Path, prompt: str, stream: pathlib.Path, *, max_turns: int = 30,
                 extra_args: tuple = ()):
        env = dict(os.environ, PYTHONPATH=str(support.CHECKOUT))
        self.stream_path = stream
        self._out = open(stream, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            # Fixture isolation, per invocation: load only this checkout's project
            # settings (memex's hooks and the barrier) and no user plugins or MCP
            # servers. A first P4 run let the user's global memex MCP plugin, which
            # failed to connect, tell the agent "memex was offline", and its first
            # proposal turned defensive. Nothing global is changed.
            ["claude", "-p", prompt, "--permission-mode", "acceptEdits", "--output-format", "stream-json",
             "--verbose", "--max-turns", str(max_turns), "--setting-sources", "project,local",
             "--strict-mcp-config", *extra_args],
            cwd=repo, env=env, stdin=subprocess.DEVNULL, stdout=self._out, stderr=subprocess.PIPE, text=True)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def wait(self, timeout: float = 1200) -> int:
        try:
            _, self.stderr = self.proc.communicate(timeout=timeout)
        finally:
            self._out.close()
        return self.proc.returncode

    def records(self) -> list[dict]:
        return [json.loads(line) for line in self.stream_path.read_text(encoding="utf-8").splitlines()
                if line.strip()]


def claude_settings(repo: pathlib.Path, hooks_launcher: pathlib.Path, state: pathlib.Path, trigger: str,
                    *, barrier: str, barrier_tool: str = "Read", timeout: int = 1200) -> None:
    """memex's own Claude hooks, plus the fixture barrier and proposal recorder."""
    from memex.integrations.claude_code import install_hooks
    settings_path = repo / ".claude" / "settings.json"
    install_hooks(settings_path, timeout=60)
    settings = json.loads(settings_path.read_text())
    hook = support.fixture_command
    settings["hooks"].setdefault("PostToolUse", []).append({"matcher": barrier_tool, "hooks": [
        {"type": "command", "timeout": timeout, "command": hook(hooks_launcher, "barrier", state, barrier,
                                                                 trigger, timeout - 30)}]})
    settings["hooks"].setdefault("PreToolUse", []).append({"matcher": "Edit|Write|MultiEdit", "hooks": [
        {"type": "command", "timeout": 30, "command": hook(hooks_launcher, "record", state)}]})
    settings_path.write_text(json.dumps(settings, indent=2))


def codex_extra(hooks_launcher: pathlib.Path, state: pathlib.Path, trigger: str, *, barrier: str,
                barrier_tool: str = "Bash", timeout: int = 1200) -> dict:
    hook = support.fixture_command
    return {
        "PostToolUse": [{"matcher": barrier_tool, "hooks": [{"type": "command", "timeout": timeout,
                         "command": hook(hooks_launcher, "barrier", state, barrier, trigger, timeout - 30)}]}],
        "PreToolUse": [{"matcher": "apply_patch", "hooks": [{"type": "command", "timeout": 30,
                        "command": hook(hooks_launcher, "record", state)}]}],
    }


def wait_ready(state: pathlib.Path, names, timeout: float = 900, alive=()) -> dict:
    """Block until every named barrier is held. Fail early if a session died first."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = {n: (state / f"{n}.ready") for n in names}
        if all(p.exists() for p in found.values()):
            return {n: json.loads(p.read_text()) for n, p in found.items()}
        for label, check in alive:
            assert check(), f"{label} ended before reaching its barrier"
        time.sleep(0.5)
    raise TimeoutError(f"barriers never reached: {[n for n in names if not (state / f'{n}.ready').exists()]}")


def release(state: pathlib.Path, name: str) -> float:
    (state / f"go-{name}").write_text(json.dumps({"at": time.time()}))
    return time.time()


def wait_until(predicate, timeout: float = 900, poll: float = 1.0, what: str = "condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(poll)
    raise TimeoutError(f"{what} never held")


def events(registration, harness: str | None = None) -> list[dict]:
    rows = TraceStore(registration.runtime_path).events()
    return [r for r in rows if harness is None or r["harness"] == harness]


def executed_on(registration, harness: str, target: str):
    """The first execution on `target` by `harness` that follows a prevention on it."""
    rows = events(registration, harness)
    prevented = [r for r in rows if r["gate"] == "prevented" and target in json.loads(r["targets"] or "[]")]
    if not prevented:
        return None
    return next((r for r in rows if r["event"] == "action_executed" and r["ordering"] > prevented[0]["ordering"]
                 and target in json.loads(r["targets"] or "[]")), None)


def ledger(registration, task_id: str, sequence: int) -> dict:
    import sqlite3
    db = sqlite3.connect(registration.runtime_path)
    db.row_factory = sqlite3.Row
    try:
        return dict(db.execute("SELECT * FROM live_adapter_deliveries WHERE task_id=? AND sequence=?",
                               (task_id, sequence)).fetchone())
    finally:
        db.close()


def assert_reconsidered(*, host: str, registration, repo: pathlib.Path, target: str, original: str,
                        contract: str, scratch: pathlib.Path, state: pathlib.Path, reread) -> dict:
    """Assert the eight-step semantic reconsideration for one host, and return its key events.

    `reread(denial_marker)` returns True when the host's own record shows the
    changed dependency read after the correction reached the model.
    """
    rows = events(registration, host)
    assert rows, f"{host}: memex hooks never ran"
    starts = [r for r in rows if r["event"] == "session_start"]
    confirmed = [r for r in rows if r["event"] == "delivery" and r["insertion"] == "confirmed"]
    assert starts and starts[0]["sequence"] in [r["sequence"] for r in confirmed], \
        f"{host}: initial packet never confirmed"
    checks = [r for r in rows if r["event"] == "action_check"]
    prevented = [r for r in checks if r["gate"] == "prevented" and target in json.loads(r["targets"] or "[]")]
    assert prevented, f"{host}: no mutation on {target} was prevented; gates={[r['gate'] for r in checks]}"
    denial = prevented[0]
    executed = [r for r in rows if r["event"] == "action_executed"]
    assert all(r["attempt_id"] != denial["attempt_id"] for r in executed), f"{host}: denied attempt executed"
    correction = [r for r in confirmed if r["sequence"] == denial["sequence"]]
    assert correction, f"{host}: correction insertion never confirmed"
    row = ledger(registration, denial["task_id"], denial["sequence"])
    assert row["harness"] == host and row["state"] == "confirmed"
    assert reread(row["marker"]), f"{host}: the changed dependency was not re-read after the correction"
    revised = [r for r in checks if r["original_attempt_id"] == denial["attempt_id"]]
    assert revised, f"{host}: no attempt was linked to the prevented one"

    proposal = [p for p in support.proposals(state) if p["tool_use_id"] == denial["attempt_id"]]
    assert proposal, f"{host}: the denied proposal was not recorded"
    proposed = _proposed(proposal[0], target, original)
    failed = _contract(repo, target, proposed, contract, scratch, f"{host}-proposed")
    assert failed.returncode != 0, f"{host}: the denied proposal still passes the changed contract\n{proposed}"
    shipped = (repo / target).read_text()
    passed = _contract(repo, target, shipped, contract, scratch, f"{host}-shipped")
    assert passed.returncode == 0, f"{host}: shipped implementation fails\n{shipped}\n{passed.stderr}"
    assert shipped != proposed
    TraceStore(registration.runtime_path).record_objective(
        revised[0]["attempt_id"], "passed", f"{contract} passes for the shipped {target} and fails for the denial")
    return {"denial": denial, "revised": revised[0], "executed": next(
        r for r in executed if r["ordering"] > denial["ordering"] and target in json.loads(r["targets"] or "[]"))}


def _proposed(proposal: dict, target: str, original: str) -> str:
    if proposal["tool"] == "apply_patch":
        command = proposal["input"]["command"]
        return support.apply_patch_text(command, {target: original})[target]
    return support.proposed_api(proposal["tool"], proposal["input"], original)


def _contract(repo, target, source, contract, scratch, label):
    sandbox = scratch / f"contract-{label}"
    if sandbox.exists():
        shutil.rmtree(sandbox)
    sandbox.mkdir(parents=True)
    for name in ("validate.py", contract):
        shutil.copyfile(repo / name, sandbox / name)
    (sandbox / target).write_text(source, newline="\n")
    return subprocess.run([sys.executable, contract], cwd=sandbox, capture_output=True, text=True, timeout=60)


def claude_reread(records: list[dict], marker: str) -> bool:
    index = None
    for position, entry in enumerate(records):
        if entry.get("type") == "user":
            for block in entry["message"].get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error") \
                        and marker in json.dumps(block):
                    index = position
    if index is None:
        return False
    return any(block.get("type") == "tool_use" and "validate" in json.dumps(block.get("input"))
               for entry in records[index + 1:] if entry.get("type") == "assistant"
               for block in entry["message"].get("content", []))


def codex_reread(rollout: pathlib.Path, marker: str) -> bool:
    records = support.rollout_records(rollout)
    index = support.denial_index(records, marker)
    return index is not None and any("validate.py" in t for t in support.rollout_texts_after(records, index))
