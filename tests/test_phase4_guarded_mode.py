"""W14: guarded mode end to end below the native layer.

The guard server's contract, its real MCP stdio surface, and how both host
adapters behave once a worktree opts in: native edits are denied with an
instruction to use the guard, while `guard_write` gets the ordinary context
check on its targets before the guard's own fenced hash check.
"""
import asyncio
import json
import os
import subprocess
import sys

import pytest
import pytest_asyncio
from graphiti_core.driver.neo4j_driver import Neo4jDriver

from memex.context.live import ClaimRevision, DeliveryReceipt, EvidenceRef
from memex.integrations import guard_mcp
from memex.integrations.claude_code import ClaudeCodeAdapter
from memex.integrations.claude_code import register as register_claude
from memex.integrations.codex import CodexAdapter
from memex.integrations.codex import register as register_codex
from memex.runtime.actions import LiveContextEngine
from memex.runtime.coordinator import RepositoryIndexer
from memex.runtime.graph import StructuralGraphStore
from memex.runtime.guard import WriteGuard, guarded, set_mode, sha256
from memex.runtime.supports import ClaimStore
from memex.runtime.tasks import TaskStore
from memex.runtime.views import discover_repository

NATIVE = "guarded-session"


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "api.py").write_text("def send(payload):\n    return payload\n", newline="\n")
    (repo / "validate.py").write_text("def validate(p):\n    return bool(p)\n", newline="\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    return repo


def test_the_guard_server_refuses_outside_guarded_mode_and_explains_stale_writes(tmp_path):
    repo = make_repo(tmp_path)
    registration = discover_repository(repo)
    guard = WriteGuard(registration)
    first = guard_mcp.read(guard, "api.py")
    refused = guard_mcp.write(guard, "t", [{"path": "api.py", "expected_sha256": first["sha256"],
                                            "content": "x\n"}])
    assert refused["outcome"] == "refused" and refused["reason"] == "guarded_mode_off"

    set_mode(registration, "guarded")
    assert guarded(registration)
    done = guard_mcp.write(guard, "t", [{"path": "api.py", "expected_sha256": first["sha256"], "content": "v2\n"}])
    assert done["outcome"] == "committed"
    stale = guard_mcp.write(guard, "t", [{"path": "api.py", "expected_sha256": first["sha256"], "content": "v3\n"}])
    assert stale["outcome"] == "replan" and stale["reason"] == "expected_hash_mismatch"
    assert "do not resubmit the same patch" in stale["instruction"]
    assert (repo / "api.py").read_text() == "v2\n"
    created = guard_mcp.write(guard, "t", [{"path": "new.py", "expected_sha256": "absent", "content": "n\n"}])
    assert created["outcome"] == "committed"
    malformed = guard_mcp.write(guard, "t", [{"expected_sha256": "absent"}])
    assert malformed["outcome"] == "refused"
    set_mode(registration, "context")
    assert not guarded(registration)


def test_the_guard_server_speaks_mcp_over_stdio_with_exactly_two_tools(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    repo = make_repo(tmp_path)
    registration = discover_repository(repo)
    set_mode(registration, "guarded")
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "memex.integrations.guard_mcp", "--root", str(repo), "--label", "smoke"],
        env={**os.environ, "PYTHONPATH": str(guard_mcp.Path(__file__).resolve().parents[1])})

    async def session():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                tools = sorted(t.name for t in (await client.list_tools()).tools)
                first = json.loads((await client.call_tool("guard_read", {"path": "api.py"})).content[0].text)
                result = json.loads((await client.call_tool("guard_write", {"changes": [
                    {"path": "api.py", "expected_sha256": first["sha256"], "content": "over mcp\n"}]})).content[0].text)
                return tools, result

    tools, result = asyncio.run(session())
    assert tools == ["guard_read", "guard_write"]
    assert result["outcome"] == "committed"
    assert (repo / "api.py").read_text() == "over mcp\n"


# -- adapters in guarded mode ------------------------------------------------ #

@pytest_asyncio.fixture(loop_scope="function")
async def hosts(tmp_path, monkeypatch):
    uri = os.getenv("MEMEX_PHASE1_NEO4J_URI")
    if not uri:
        pytest.skip("isolated native Neo4j required")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    repo = make_repo(tmp_path)
    registration = discover_repository(repo)
    driver = await asyncio.to_thread(Neo4jDriver, uri, None, None)
    indexer = RepositoryIndexer(registration, StructuralGraphStore(driver))
    await indexer.refresh()
    claims = ClaimStore(driver)
    for name in ("api.py", "validate.py"):
        await claims.put_evidence(EvidenceRef(evidence_id=f"e-{name}", repo_id=registration.repo_id, path=name,
                                              content_hash=sha256((repo / name).read_bytes()),
                                              source_kind="source", observed_at=1.0))
    await claims.put_claim(ClaimRevision(claim_id="c", revision_id="r1", repo_id=registration.repo_id,
                                         assertion="send() relies on validate() returning a bool",
                                         authority="inferred", support_sets=(("e-api.py", "e-validate.py"),),
                                         observed_at=1.0))
    tasks = TaskStore(registration.runtime_path,
                      authenticate=lambda s: s.principal_id == "owner" and s.harness in ("claude_code", "codex"))
    engine = LiveContextEngine(indexer, claims, tasks, authorize_view=lambda s, r: True,
                               authorize_source=lambda s, p: True)
    claude = ClaudeCodeAdapter(engine, register_claude(repo, "owner"))
    codex_adapter = CodexAdapter(engine, register_codex(repo, "owner"))
    yield repo, registration, claude, codex_adapter
    await driver.close()


def hook(event, repo, **extra):
    return {"hook_event_name": event, "session_id": NATIVE, "cwd": str(repo), "transcript_path": None,
            "turn_id": "turn-1", **extra}


def guard_call(repo, content="new\n", expected=None, attempt="g1"):
    return hook("PreToolUse", repo, tool_name="mcp__memex_guard__guard_write", tool_use_id=attempt,
                tool_input={"changes": [{"path": "api.py", "expected_sha256": expected or "x", "content": content}]})


@pytest.mark.integration
@pytest.mark.asyncio
async def test_guarded_mode_denies_native_edits_on_both_hosts_and_says_how_to_proceed(hosts):
    repo, registration, claude, codex_adapter = hosts
    for adapter in (claude, codex_adapter):
        await adapter.dispatch(hook("SessionStart", repo, source="startup"))
    edit = hook("PreToolUse", repo, tool_name="Edit", tool_use_id="e1",
                tool_input={"file_path": str(repo / "api.py"), "old_string": "a", "new_string": "b"})
    patch = hook("PreToolUse", repo, tool_name="apply_patch", tool_use_id="p1",
                 tool_input={"command": "*** Begin Patch\n*** Update File: api.py\n@@\n-a\n+b\n*** End Patch"})

    # Context mode: a native edit goes through the ordinary check, not a blanket denial.
    assert "guarded mode" not in (await claude.dispatch(edit)).reason

    set_mode(registration, "guarded")
    for adapter, action in ((claude, {**edit, "tool_use_id": "e2"}), (codex_adapter, {**patch, "tool_use_id": "p2"})):
        denied = await adapter.dispatch(action)
        assert denied.decision == "deny"
        assert "guard_write" in denied.reason and "guard_read" in denied.reason
    shell = await codex_adapter.dispatch(hook("PreToolUse", repo, tool_name="Bash", tool_use_id="s1",
                                              tool_input={"command": "git checkout -b other"}))
    assert shell.decision is None and "outside the write guard" in shell.reason


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_guarded_write_still_gets_its_context_correction_first(hosts):
    """The guard checks bytes; memex still checks what the agent was told."""
    repo, registration, claude, _ = hosts
    set_mode(registration, "guarded")
    started = await claude.dispatch(hook("SessionStart", repo, source="startup"))
    task_id, _, sequence = started.delivery_key.rpartition(":")
    claude.engine.ack_delivery(DeliveryReceipt(
        session=claude.session_identity(NATIVE), task_id=task_id, sequence=int(sequence),
        view_id=claude.engine.tasks.get(task_id, claude.session_identity(NATIVE), now=claude.clock()).pending.view_id,
        adapter_version="t", accepted_at=claude.clock(), outcome="host_accepted"))

    fresh = await claude.dispatch(guard_call(repo, attempt="g-fresh"))
    assert fresh.decision is None, fresh.reason

    (repo / "validate.py").write_text("def validate(p):\n    raise ValueError\n", newline="\n")
    drifted = await claude.dispatch(guard_call(repo, attempt="g-drift"))
    assert drifted.decision == "deny" and "changed dependency validate.py" in drifted.reason
