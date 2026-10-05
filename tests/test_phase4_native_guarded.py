"""P4 native gate: two live clients race guarded writes on one file.

The checkout opts into guarded mode. Claude and Codex each add a function to
registry.py through the opt-in guard server (guard_read, then guard_write with
the hash it read). Both read the same bytes and are held there by barriers, so
both are live and both hold the same, soon-to-be-stale expectation. Released in
the order under test:

* the first writer's guarded commit succeeds;
* the second writer's write, still expecting the old bytes, is refused inside
  the fenced commit and changes nothing;
* the second writer re-reads and commits a version that keeps the first
  writer's function. No update is lost, and no stale write ever lands.

Ordinary hooks alone could not provide this: the refusal happens inside the
guard's protected write, not in a check that precedes a host's own write.

Opt-in: drives the installed claude and codex clients with real model turns.
"""
import json
import os
import shutil
import sys

import pytest

from memex.integrations import codex
from memex.integrations.claude_code import register as register_claude
from memex.runtime.guard import WriteGuard, set_mode
from memex.runtime.views import discover_repository
from tests import phase4_native_harness as harness
from tests import phase4_support as support

pytestmark = pytest.mark.integration

REGISTRY = "REGISTRY = {}\n"
GUARD_READ = "mcp__memex_guard__guard_read"


def prompt(name: str, extra: str) -> str:
    return (
        f"This repository is in memex guarded mode. Add a function register_{name}() to registry.py that sets "
        f"REGISTRY['{name}'] = 1, keeping everything already in the file. Read registry.py only with the "
        "guard_read tool and change it only with the guard_write tool, passing changes=[{\"op\": \"write\", "
        "\"path\": \"registry.py\", \"expected_sha256\": <the sha256 guard_read returned>, \"content\": <the "
        "complete new file text>}]. If guard_write returns outcome 'replan', call guard_read again and write a "
        f"new version based on the current contents. Do not edit files any other way. {extra}"
    )


def guard_server(repo, label):
    return {"command": sys.executable,
            "args": ["-m", "memex.integrations.guard_mcp", "--root", str(repo), "--label", label],
            "env": {"PYTHONPATH": str(support.CHECKOUT)}}


@pytest.fixture
def guarded_checkout(tmp_path):
    if os.getenv("MEMEX_PHASE4_NATIVE") != "1":
        pytest.skip("native host run is opt-in; set MEMEX_PHASE4_NATIVE=1")
    uri = os.getenv("MEMEX_PHASE1_NEO4J_URI")
    if not uri:
        pytest.skip("isolated native Neo4j required")
    if shutil.which("codex") is None or shutil.which("claude") is None:
        pytest.skip("both native clients are required")
    repo = tmp_path / "repo"
    repo.mkdir()
    support.git(tmp_path, "init", "-q", str(repo))
    (repo / "registry.py").write_text(REGISTRY, newline="\n")
    support.git(repo, "add", ".")
    support.git(repo, "-c", "user.email=p4@memex.test", "-c", "user.name=p4", "commit", "-qm", "registry")
    registration = discover_repository(repo)
    set_mode(registration, "guarded")
    register_claude(repo, "owner")
    codex.register(repo, "owner", backend={"neo4j_uri": uri})
    return repo, registration, codex.write_launcher(registration, pythonpath=str(support.CHECKOUT))


@pytest.mark.parametrize("order", [("claude", "codex"), ("codex", "claude")], ids=["claude-first", "codex-first"])
def test_racing_guarded_writers_lose_no_update_and_land_no_stale_write(guarded_checkout, tmp_path, order):
    repo, registration, launcher = guarded_checkout
    state = tmp_path / "state"
    hooks = support.script_launcher(tmp_path / "bin", "phase4-hooks", support.FIXTURE_HOOKS)
    harness.claude_settings(repo, hooks, state, "registry.py", barrier="claude", barrier_tool=GUARD_READ)
    mcp_config = tmp_path / "claude-mcp.json"
    mcp_config.write_text(json.dumps({"mcpServers": {"memex_guard": guard_server(repo, "claude")}}))
    python = sys.executable.replace("\\", "/")
    codex_mcp = ["-c", f"mcp_servers.memex_guard.command='{python}'",
                 "-c", "mcp_servers.memex_guard.args=['-m','memex.integrations.guard_mcp','--root',"
                       f"'{repo.as_posix()}','--label','codex']",
                 "-c", f"mcp_servers.memex_guard.env={{PYTHONPATH='{support.CHECKOUT.as_posix()}'}}"]
    flags = codex.session_flags(launcher, extra=harness.codex_extra(hooks, state, "registry.py", barrier="codex",
                                                                    barrier_tool=GUARD_READ)) + codex_mcp
    guard = WriteGuard(registration)

    def outcomes(label):
        return [r["outcome"] for r in guard.results() if r["holder"].startswith(label + ":")]

    server = support.AppServer(flags, log_path=tmp_path / "app-server.jsonl")
    claude = None
    try:
        thread_id = server.start_thread(repo)
        turn_id = server.start_turn(thread_id, prompt("beta", "Do not run shell commands."))
        # Fixture permission for this one invocation: the two guard tools, nothing else.
        claude = harness.ClaudeRun(repo, prompt("alpha", "Do not run any commands."), tmp_path / "claude.jsonl",
                                   extra_args=("--mcp-config", str(mcp_config), "--allowedTools",
                                               "mcp__memex_guard__guard_read", "mcp__memex_guard__guard_write"))

        def codex_alive():
            done = [m for m in server.messages if m.get("method") == "turn/completed"]
            assert not done or done[0]["params"]["turn"].get("status") == "completed", \
                f"codex turn failed: {done[0]['params']['turn'].get('error')}"
            return not done

        ready = harness.wait_ready(state, ["claude", "codex"],
                                   alive=[("claude", claude.alive), ("codex", codex_alive)])
        assert (repo / "registry.py").read_text() == REGISTRY, "both read the same bytes"

        first, second = order
        harness.release(state, first)
        harness.wait_until(lambda: "committed" in outcomes(first), what=f"{first}'s guarded commit")
        after_first = (repo / "registry.py").read_text()
        assert {"claude": claude.alive(), "codex": codex_alive()}[second], f"{second} must still be live"
        harness.release(state, second)

        assert claude.wait() == 0, claude.stderr[-2000:]
        turn = server.wait_turn(thread_id, turn_id)
    finally:
        server.close()
        if claude and claude.alive():
            claude.proc.kill()
    assert turn["status"] == "completed" and server.declined == []

    final = (repo / "registry.py").read_text()
    assert "register_alpha" in final and "register_beta" in final, f"an update was lost:\n{final}"
    assert outcomes(first) == ["committed"], outcomes(first)
    second_outcomes = outcomes(second)
    assert second_outcomes[0] == "expected_hash_mismatch", \
        f"{second}'s first write expected the bytes {first} replaced and must have been refused: {second_outcomes}"
    assert second_outcomes[-1] == "committed"
    # The refused write changed nothing: until the second's own commit, the file was the first's.
    second_commit = [r for r in guard.results() if r["holder"].startswith(second + ":")
                     and r["outcome"] == "committed"][0]
    assert first in ("claude", "codex") and after_first != REGISTRY
    assert json.loads(second_commit["result_hashes"])["registry.py"] == support.digest(repo / "registry.py")

    # Every mutation went through the guard; the hooks checked each guarded write.
    rows = harness.events(registration)
    guarded_checks = [r for r in rows if r["event"] == "action_check"
                      and r["tool_name"] == "mcp__memex_guard__guard_write"]
    assert {r["harness"] for r in guarded_checks} == {"claude_code", "codex"}
    executed = [r for r in rows if r["event"] == "action_executed"]
    assert executed and all(r["tool_name"] == "mcp__memex_guard__guard_write" for r in executed), \
        [r["tool_name"] for r in executed]
    (tmp_path / "order.json").write_text(json.dumps({"order": order, "ready": ready,
                                                     "results": guard.results()}, default=str))
