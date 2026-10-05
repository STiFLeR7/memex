"""W14 default mode and W15 continuity: Claude and Codex adapters in one checkout.

Both hosts run their real adapters over one repository, one control plane and
the native graph, and deliberately report the *same* native session ID. Each
host's packets are confirmed only through its own measured insertion records
(Claude's transcript, Codex's rollout), built with the same helpers as each
adapter's own suite.
"""
import asyncio
import io
import json
import os
import subprocess

import pytest
import pytest_asyncio
from graphiti_core.driver.neo4j_driver import Neo4jDriver
from neo4j.exceptions import ServiceUnavailable

from memex.context.live import ClaimRevision, EvidenceRef
from memex.integrations.claude_code import ClaudeCodeAdapter
from memex.integrations.claude_code import register as register_claude
from memex.integrations.codex import CodexAdapter
from memex.integrations.codex import register as register_codex
from memex.runtime.actions import LiveContextEngine
from memex.runtime.coordinator import RepositoryIndexer
from memex.runtime.graph import StructuralGraphStore
from memex.runtime.guard import sha256
from memex.runtime.supports import ClaimStore
from memex.runtime.tasks import TaskStore
from memex.runtime.views import discover_repository
from tests.test_live_claude_adapter import additional_context_record, denial_record
from tests.test_live_codex_adapter import append, code_mode_denial, developer_context, start_rollout

pytestmark = pytest.mark.integration

SHARED = "same-native-session"
TURN = "turn-shared-1"


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


@pytest_asyncio.fixture(loop_scope="function")
async def checkout(tmp_path, monkeypatch):
    uri = os.getenv("MEMEX_PHASE1_NEO4J_URI")
    if not uri:
        pytest.skip("isolated native Neo4j required")
    claude_home, codex_home = tmp_path / "claude-config", tmp_path / "codex-home"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "api.py").write_text("def send(payload):\n    return payload\n", newline="\n")
    (repo / "caller.py").write_text("from api import send\n\n\ndef relay(x):\n    return send(x)\n", newline="\n")
    (repo / "other.py").write_text("x = 1\n", newline="\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)

    registration = discover_repository(repo)
    driver = await asyncio.to_thread(Neo4jDriver, uri, None, None)
    indexer = RepositoryIndexer(registration, StructuralGraphStore(driver))
    await indexer.refresh()
    claims = ClaimStore(driver)
    for name in ("api.py", "caller.py"):
        await claims.put_evidence(EvidenceRef(evidence_id=f"e-{name}", repo_id=registration.repo_id, path=name,
                                              content_hash=sha256((repo / name).read_bytes()),
                                              source_kind="source", observed_at=1.0))
    # C1: what both agents are told about the caller's dependency. A body-only
    # change to send() leaves its signature intact and still falsifies it.
    await claims.put_claim(ClaimRevision(
        claim_id="c1", revision_id="c1-r1", repo_id=registration.repo_id, authority="inferred",
        assertion="relay() can return send()'s result unchanged because send() returns its payload",
        support_sets=(("e-api.py", "e-caller.py"),), observed_at=1.0))
    tasks = TaskStore(registration.runtime_path,
                      authenticate=lambda s: s.principal_id == "owner" and s.harness in ("claude_code", "codex"))
    clock = Clock()
    engine = LiveContextEngine(indexer, claims, tasks, clock=clock,
                               authorize_view=lambda s, r: True, authorize_source=lambda s, p: True)
    claude_capability, codex_capability = register_claude(repo, "owner"), register_codex(repo, "owner")
    hosts = {
        "claude": ClaudeCodeAdapter(engine, claude_capability, clock=clock),
        "codex": CodexAdapter(engine, codex_capability, clock=clock),
    }
    paths = {"claude": claude_home / "projects" / "slug" / f"{SHARED}.jsonl",
             "codex": None}
    paths["claude"].parent.mkdir(parents=True)
    paths["claude"].write_text("", newline="\n")
    paths["codex"] = start_rollout(codex_home, session=SHARED)
    yield {"repo": repo, "hosts": hosts, "paths": paths, "clock": clock, "engine": engine,
           "capabilities": {"claude": claude_capability, "codex": codex_capability}}
    await driver.close()


def hook(world, host, event, **extra):
    base = {"hook_event_name": event, "session_id": SHARED, "cwd": str(world["repo"]),
            "transcript_path": str(world["paths"][host]), "turn_id": TURN}
    base.update(extra)
    return base


def edit(world, host, attempt, target="caller.py"):
    if host == "claude":
        return hook(world, host, "PreToolUse", tool_name="Edit", tool_use_id=attempt,
                    tool_input={"file_path": str(world["repo"] / target), "old_string": "x", "new_string": "y"})
    return hook(world, host, "PreToolUse", tool_name="apply_patch", tool_use_id=attempt,
                tool_input={"command": f"*** Begin Patch\n*** Update File: {target}\n@@\n-x\n+y\n*** End Patch"})


def inserted(world, host, response):
    """Append the record that host writes when it inserts `response`."""
    adapter = world["hosts"][host]
    adapter.emit(response, "SessionStart" if response.additional_context else "PreToolUse", io.StringIO())
    if host == "claude":
        record = (additional_context_record(response.additional_context, SHARED) if response.additional_context
                  else denial_record(response.reason, _attempt(adapter, response), SHARED))
        with open(world["paths"]["claude"], "a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record) + "\n")
    else:
        append(world["paths"]["codex"], developer_context(response.additional_context, TURN)
               if response.additional_context else code_mode_denial(response.reason, TURN))


def _attempt(adapter, response):
    task_id, _, sequence = response.delivery_key.rpartition(":")
    return next(d["attempt_id"] for d in adapter.delivery_states(SHARED)
                if d["task_id"] == task_id and d["sequence"] == int(sequence))


def acked(world, host, response):
    adapter = world["hosts"][host]
    task_id = response.delivery_key.rpartition(":")[0]
    return adapter.engine.tasks.get(task_id, adapter.session_identity(SHARED), now=world["clock"]()).ack_sequence


async def both_started(world):
    started = {}
    for host in ("claude", "codex"):
        started[host] = await world["hosts"][host].dispatch(hook(world, host, "SessionStart", source="startup"))
        inserted(world, host, started[host])
    for host in ("claude", "codex"):  # each confirms its own packet on its next hook
        world["hosts"][host].confirm_deliveries(world["hosts"][host].session_identity(SHARED),
                                                str(world["paths"][host]))
        assert acked(world, host, started[host]) == 1, f"{host} never confirmed its own packet"
    return started


# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_identical_native_ids_keep_two_hosts_streams_apart(checkout):
    started = await both_started(checkout)
    claude, codex = checkout["hosts"]["claude"], checkout["hosts"]["codex"]
    assert claude._binding(SHARED)["task_id"] != codex._binding(SHARED)["task_id"]
    assert started["claude"].delivery_key != started["codex"].delivery_key


@pytest.mark.asyncio
async def test_a_body_only_change_corrects_each_dependent_host_independently(checkout):
    """F10: one host's acknowledgement never suppresses the other's correction."""
    await both_started(checkout)
    (checkout["repo"] / "api.py").write_text("def send(payload):\n    return dict(payload)\n", newline="\n")

    denied = {host: await checkout["hosts"][host].dispatch(edit(checkout, host, f"{host}-1"))
              for host in ("claude", "codex")}
    for host, response in denied.items():
        assert response.decision == "deny", f"{host} was not corrected"
        assert "changed dependency api.py" in response.reason

    # Claude's correction is inserted and confirmed; Codex's is not yet.
    inserted(checkout, "claude", denied["claude"])
    checkout["hosts"]["claude"].confirm_deliveries(checkout["hosts"]["claude"].session_identity(SHARED),
                                                   str(checkout["paths"]["claude"]))
    assert acked(checkout, "claude", denied["claude"]) == 2
    assert acked(checkout, "codex", denied["codex"]) == 1

    # Codex still holds an unacknowledged correction, so its next attempt is denied again.
    again = await checkout["hosts"]["codex"].dispatch(edit(checkout, "codex", "codex-2"))
    assert again.decision == "deny"
    inserted(checkout, "codex", again)
    await checkout["hosts"]["codex"].dispatch(hook(checkout, "codex", "PreToolUse", tool_name="Bash",
                                                   tool_use_id="codex-read", tool_input={"command": "cat api.py"}))
    assert acked(checkout, "codex", again) == 2


@pytest.mark.asyncio
async def test_an_unrelated_change_interrupts_neither_host(checkout):
    """F04 in a shared checkout: the generation moved, the evidence did not."""
    await both_started(checkout)
    (checkout["repo"] / "other.py").write_text("x = 2\n", newline="\n")
    for host in ("claude", "codex"):
        response = await checkout["hosts"][host].dispatch(edit(checkout, host, f"{host}-quiet"))
        assert response.decision is None, f"{host} was interrupted by an unrelated edit: {response.reason}"


@pytest.mark.asyncio
async def test_a_target_changed_after_read_requests_reconsideration(checkout):
    await both_started(checkout)
    (checkout["repo"] / "caller.py").write_text("from api import send\n\n\ndef relay(x):\n    return send(x) or x\n",
                                                newline="\n")
    for host in ("claude", "codex"):
        response = await checkout["hosts"][host].dispatch(edit(checkout, host, f"{host}-stale"))
        assert response.decision == "deny" and "caller.py: delivered" in response.reason


@pytest.mark.asyncio
async def test_after_two_reconsiderations_the_conflict_is_reported_not_retried_or_decided(checkout):
    """W15: bounded retries; memex explains, it does not choose a winner."""
    await both_started(checkout)
    codex = checkout["hosts"]["codex"]
    reasons = []
    for index in range(4):
        (checkout["repo"] / "api.py").write_text(f"def send(payload):\n    return payload  # {index}\n",
                                                 newline="\n")
        response = await codex.dispatch(edit(checkout, "codex", f"loop-{index}"))
        reasons.append(response.reason)
        if response.delivery_key:
            inserted(checkout, "codex", response)
    assert any("Stop retrying" in r and "surface the unresolved conflict" in r for r in reasons), reasons
    final = reasons[-1].lower()
    assert "wins" not in final and "prefer" not in final and "choose" not in final


@pytest.mark.asyncio
async def test_restart_expiry_and_resume_preserve_independent_cursors(checkout):
    started = await both_started(checkout)
    # Restart: fresh adapter objects over the same control plane keep each host's binding.
    engine, clock = checkout["engine"], checkout["clock"]
    restarted = {"claude": ClaudeCodeAdapter(engine, checkout["capabilities"]["claude"], clock=clock),
                 "codex": CodexAdapter(engine, checkout["capabilities"]["codex"], clock=clock)}
    for host, adapter in restarted.items():
        assert adapter._binding(SHARED)["task_id"] == started[host].delivery_key.rpartition(":")[0]

    # Expiry: a lapsed task yields an explicit advisory, never a certificate.
    clock.value += 4000
    expired = await restarted["codex"].dispatch(edit(checkout, "codex", "after-expiry"))
    assert expired.decision is None and "freshness unknown" in expired.reason

    # Resume: SessionStart opens a fresh task per host and redelivers.
    for host, adapter in restarted.items():
        resumed = await adapter.dispatch(hook(checkout, host, "SessionStart", source="resume"))
        assert resumed.delivery_key and resumed.delivery_key != started[host].delivery_key
        assert acked(checkout, host, resumed) == 0, "a historical packet is not retained context"


@pytest.mark.asyncio
async def test_outage_failed_insertion_and_overflow_degrade_explicitly_for_both_hosts(checkout):
    started = await both_started(checkout)
    engine = checkout["engine"]
    (checkout["repo"] / "api.py").write_text("def send(payload):\n    return None\n", newline="\n")
    codex_denied = await checkout["hosts"]["codex"].dispatch(edit(checkout, "codex", "pending"))
    assert codex_denied.decision == "deny"
    checkout["hosts"]["codex"].emit(codex_denied, "PreToolUse", io.StringIO())  # emitted, never inserted

    async def unavailable(*args, **kwargs):
        raise ServiceUnavailable("isolated backend down")
    original = engine.claims.load
    engine.claims.load = unavailable
    try:
        claude_out = await checkout["hosts"]["claude"].dispatch(edit(checkout, "claude", "outage-c"))
        codex_out = await checkout["hosts"]["codex"].dispatch(edit(checkout, "codex", "outage-x"))
    finally:
        engine.claims.load = original
    assert claude_out.decision is None and "freshness unknown" in claude_out.reason
    assert codex_out.decision == "deny", "a known pending correction survives the outage"
    assert acked(checkout, "codex", codex_denied) == 1, "an uninserted correction was never acknowledged"
    assert acked(checkout, "claude", started["claude"]) == 1

    # Overflow: a working set beyond the packet budget asks for resynchronization.
    registration = checkout["hosts"]["codex"].registration
    for index in range(40):
        await engine.claims.put_claim(ClaimRevision(
            claim_id=f"bulk-{index}", revision_id=f"bulk-{index}-r1", repo_id=registration.repo_id,
            assertion=f"bulk assertion {index}", authority="inferred", support_sets=(("e-caller.py",),),
            observed_at=1.0))
    overflow = await checkout["hosts"]["codex"].dispatch(hook(checkout, "codex", "SessionStart", source="startup"))
    assert overflow.delivery_key is None and "resynchronization required" in overflow.additional_context
