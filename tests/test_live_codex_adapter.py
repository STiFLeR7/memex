"""W12: the Codex adapter on real Git/SQLite/Neo4j, against S02's measured record shapes.

The record builders below copy the shapes captured from codex-cli 0.157.1 app-server
rollouts during S02. The native ordering proof is in `test_phase4_native_codex.py`;
passing here does not establish it.
"""
import asyncio
from hashlib import sha256
import io
import json
import os
import pathlib
import subprocess

import pytest
import pytest_asyncio
from graphiti_core.driver.neo4j_driver import Neo4jDriver

from memex.context.live import ClaimRevision, EvidenceRef
from memex.integrations import codex
from memex.integrations.claude_code import ClaudeCodeAdapter
from memex.integrations.claude_code import register as register_claude
from memex.integrations.codex import CodexAdapter, register
from memex.runtime.actions import LiveContextEngine
from memex.runtime.coordinator import RepositoryIndexer
from memex.runtime.graph import StructuralGraphStore
from memex.runtime.supports import ClaimStore
from memex.runtime.tasks import TaskStore
from memex.runtime.views import discover_repository

NATIVE = "01a10ae6-2393-7313-8213-1f153abc89b2"
TURN = "01a10ae6-2b8d-7ea1-b218-e77acf72534c"


def digest(content: bytes) -> str:
    return "sha256:" + sha256(content).hexdigest()


class Clock:
    def __init__(self, start: float = 100.0):
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> float:
        self.value += seconds
        return self.value


# -- the client's own record shapes ----------------------------------------- #

def rollout_path(home: pathlib.Path, session=NATIVE) -> pathlib.Path:
    return home / "sessions" / "2026" / "10" / "05" / f"rollout-2026-10-05T12-35-55-{session}.jsonl"


def start_rollout(home: pathlib.Path, session=NATIVE, meta_id=None) -> str:
    path = rollout_path(home, session)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"timestamp": "2026-10-05T07:06:00.000Z", "type": "session_meta",
            "payload": {"id": meta_id or session, "cwd": "repo", "cli_version": "0.157.1"}}
    path.write_text(json.dumps(meta) + "\n", encoding="utf-8", newline="\n")
    return str(path)


def append(path, *records):
    with open(path, "a", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write((record if isinstance(record, str) else json.dumps(record)) + "\n")


def developer_context(text, turn=TURN):
    """Insertion: hook context in the model-visible history."""
    return {"timestamp": "2026-10-05T07:06:13.829Z", "type": "response_item",
            "payload": {"type": "message", "role": "developer",
                        "content": [{"type": "input_text", "text": text}],
                        "internal_chat_message_metadata_passthrough": {
                            "turn_id": turn, "content_item_kinds": ["hooks.additional_context"]}}}


def code_mode_denial(reason, turn=TURN, patch="*** Begin Patch\n*** End Patch"):
    """Insertion: a denial reaching the model through a code-mode script."""
    return {"timestamp": "2026-10-05T07:06:28.013Z", "type": "response_item",
            "payload": {"type": "custom_tool_call_output", "call_id": "call_5abz",
                        "output": [{"type": "input_text", "text": "Script failed\nWall time 0.4 seconds\nOutput:\n"},
                                   {"type": "input_text", "text": "Script error:\nCommand blocked by PreToolUse hook: "
                                    + reason + ". Command: " + patch}],
                        "internal_chat_message_metadata_passthrough": {"turn_id": turn}}}


def direct_denial(reason, turn=TURN):
    """Insertion: a denial reaching the model as a plain function output."""
    return {"type": "response_item",
            "payload": {"type": "function_call_output", "call_id": "call_9",
                        "output": "Command blocked by PreToolUse hook: " + reason,
                        "internal_chat_message_metadata_passthrough": {"turn_id": turn}}}


def agent_prose(text, turn=TURN):
    return {"type": "response_item",
            "payload": {"type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                        "internal_chat_message_metadata_passthrough": {"turn_id": turn}}}


def ui_item(text):
    """The app-server projection of an item. Not model-visible history."""
    return {"type": "event_msg", "payload": {"type": "item_completed",
            "item": {"type": "AgentMessage", "content": [{"type": "Text", "text": text}]}}}


# -- pure parsing ------------------------------------------------------------ #

def test_a_multi_file_patch_declares_every_target():
    patch = ("*** Begin Patch\n*** Add File: notes.txt\n+hi\n*** Update File: util.py\n@@\n-x\n+y\n"
             "*** Delete File: old.py\n*** Update File: a.py\n*** Move to: b.py\n@@\n*** End Patch")
    assert codex.declared_targets("apply_patch", {"command": patch}) == (
        "notes.txt", "util.py", "old.py", "a.py", "b.py")


def test_shell_and_unreadable_patches_are_opaque_not_targetless():
    assert codex.declared_targets("Bash", {"command": "Set-Content api.py x"}) is None
    assert codex.declared_targets("apply_patch", {"command": "not a patch"}) is None
    assert codex.declared_targets("apply_patch", "garbage") is None
    assert codex.declared_targets("apply_patch", {"cmd": 1}) is None


def test_rollout_provenance_requires_place_name_and_self_identification(tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    good = start_rollout(home)
    assert codex.authorized_transcript(good, NATIVE) == pathlib.Path(good).resolve()
    assert codex.authorized_transcript(good, "another-thread") is None

    # Named for this thread but self-identifying as another: a forged or forked file.
    forged = start_rollout(home, session="forged-0000", meta_id="someone-else")
    assert codex.authorized_transcript(forged, "forged-0000") is None

    # Right name, outside the sessions tree.
    stray = tmp_path / f"rollout-x-{NATIVE}.jsonl"
    stray.write_text(pathlib.Path(good).read_text())
    assert codex.authorized_transcript(str(stray), NATIVE) is None
    assert codex.authorized_transcript(None, NATIVE) is None


def test_only_model_visible_insertions_count():
    marker = "memex-delivery:0123456789abcdef"
    blob = "\n".join(json.dumps(r) for r in (
        developer_context(f"ctx [{marker}]"),
        agent_prose(f"I saw [{marker}]"),
        ui_item(f"Command blocked by PreToolUse hook: [{marker}]"),
        {"type": "response_item", "payload": {"type": "function_call_output",
                                               "output": f"ok [{marker}]"}},
        code_mode_denial(f"replan\n[{marker}]"),
    )) + "\n{\"type\": \"response_item\", broken [" + marker + "]\n"
    kinds = [kind for kind, _, _ in codex.insertion_evidence(blob, NATIVE)]
    assert kinds == ["additional_context", "tool_denial"]


# -- adapter lifecycle on the real core -------------------------------------- #

@pytest.fixture
def codex_home(tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    return home


@pytest_asyncio.fixture(loop_scope="function")
async def host(tmp_path, codex_home):
    uri = os.getenv("MEMEX_PHASE1_NEO4J_URI")
    if not uri:
        pytest.skip("isolated native Neo4j required")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (repo / "api.py").write_text("def send(payload):\n    return payload\n")
    (repo / "other.py").write_text("def other():\n    return 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)

    registration = discover_repository(repo)
    capability = register(repo, "owner")
    claude_capability = register_claude(repo, "owner")
    driver = await asyncio.to_thread(Neo4jDriver, uri, None, None)
    indexer = RepositoryIndexer(registration, StructuralGraphStore(driver))
    await indexer.refresh()
    claims = ClaimStore(driver)
    await claims.put_evidence(EvidenceRef(evidence_id="e-api", repo_id=registration.repo_id, path="api.py",
                                          content_hash=digest((repo / "api.py").read_bytes()),
                                          source_kind="source", observed_at=1.0))
    await claims.put_claim(ClaimRevision(claim_id="c-send", revision_id="r1", repo_id=registration.repo_id,
                                         assertion="send() returns its payload unchanged", authority="inferred",
                                         support_sets=(("e-api",),), observed_at=1.0))
    tasks = TaskStore(registration.runtime_path,
                      authenticate=lambda s: s.principal_id == "owner" and s.harness in ("codex", "claude_code"))
    now = Clock()
    engine = LiveContextEngine(indexer, claims, tasks, authorize_view=lambda s, r: True,
                               authorize_source=lambda s, p: p not in capability.denied_paths, clock=now)
    adapter = CodexAdapter(engine, capability, clock=now)
    claude = ClaudeCodeAdapter(engine, claude_capability, clock=now)
    yield adapter, repo, registration, claude, codex_home
    await driver.close()


def payload(event, repo, home, **extra):
    base = {"hook_event_name": event, "session_id": NATIVE, "cwd": str(repo), "model": "gpt-6-sol",
            "permission_mode": "default", "transcript_path": str(rollout_path(home)), "turn_id": TURN}
    if event == "SessionStart":
        base.update(turn_id=None, source="startup")
    base.update(extra)
    return base


def patch_payload(repo, home, attempt="exec-1", turn=TURN, target="api.py"):
    patch = (f"*** Begin Patch\n*** Update File: {target}\n@@\n"
             "-def send(payload):\n+def send(payload, t):\n*** End Patch")
    return payload("PreToolUse", repo, home, tool_name="apply_patch", tool_use_id=attempt,
                   turn_id=turn, tool_input={"command": patch})


def acked(adapter, delivery_key, native=NATIVE):
    task_id, _, _ = delivery_key.rpartition(":")
    return adapter.engine.tasks.get(task_id, adapter.session_identity(native), now=adapter.clock()).ack_sequence


def ledger(adapter, delivery_key, native=NATIVE):
    task_id, _, sequence = delivery_key.rpartition(":")
    return [d for d in adapter.delivery_states(native)
            if d["task_id"] == task_id and d["sequence"] == int(sequence)][0]


async def started(adapter, repo, home):
    """SessionStart whose packet Codex inserts as a developer message."""
    path = start_rollout(home)
    response = await adapter.dispatch(payload("SessionStart", repo, home))
    adapter.emit(response, "SessionStart", io.StringIO())
    append(path, developer_context(response.additional_context))
    return response, path


pytestmark_integration = pytest.mark.integration  # lifecycle cases need the native graph


@pytestmark_integration
@pytest.mark.asyncio
async def test_a_snapshot_is_confirmed_only_from_its_complete_developer_message(host):
    adapter, repo, _, _, home = host
    response, path = await started(adapter, repo, home)
    assert len(response.additional_context) <= codex.HOST_CONTEXT_LIMIT
    assert ledger(adapter, response.delivery_key)["state"] == "emitted"
    # The next hook invocation reads the record S02 measured on disk by then.
    await adapter.dispatch(payload("PreToolUse", repo, home, tool_name="Bash", tool_use_id="exec-r",
                                   tool_input={"command": "Get-Content api.py"}))
    assert ledger(adapter, response.delivery_key)["state"] == "confirmed"
    assert acked(adapter, response.delivery_key) == 1


@pytestmark_integration
@pytest.mark.asyncio
async def test_a_truncated_developer_message_is_not_a_delivery(host):
    """S02: 62,510 characters became 10,100 with both ends, and the marker, intact."""
    adapter, repo, _, _, home = host
    path = start_rollout(home)
    response = await adapter.dispatch(payload("SessionStart", repo, home))
    adapter.emit(response, "SessionStart", io.StringIO())
    packet = response.additional_context
    marker = ledger(adapter, response.delivery_key)["marker"]
    cut = packet.index(marker) + len(marker) + 4
    append(path, developer_context(codex.TRUNCATION_WARNING + " (original token count: 15628)\n"
                                   + packet[:cut] + "\n...\n" + packet[-20:]))
    adapter.confirm_deliveries(adapter.session_identity(NATIVE), path)
    assert acked(adapter, response.delivery_key) == 0
    assert ledger(adapter, response.delivery_key)["state"] == "failed"


@pytestmark_integration
@pytest.mark.asyncio
async def test_diagnostics_prose_and_other_threads_never_confirm(host):
    adapter, repo, _, _, home = host
    path = start_rollout(home)
    response = await adapter.dispatch(payload("SessionStart", repo, home))
    adapter.emit(response, "SessionStart", io.StringIO())
    packet = response.additional_context
    append(path, agent_prose(packet), ui_item(packet),
           {"type": "response_item", "payload": {"type": "message", "role": "developer",
                                                  "content": [{"type": "input_text", "text": packet}]}})
    # Another thread's rollout holding the complete packet.
    other = start_rollout(home, session="other-thread")
    append(other, developer_context(packet))
    session = adapter.session_identity(NATIVE)
    adapter.confirm_deliveries(session, path)
    adapter.confirm_deliveries(session, other)
    assert acked(adapter, response.delivery_key) == 0


@pytestmark_integration
@pytest.mark.asyncio
async def test_a_correction_is_bound_to_its_turn(host):
    adapter, repo, _, _, home = host
    _, path = await started(adapter, repo, home)
    await adapter.dispatch(patch_payload(repo, home, attempt="exec-warm"))  # confirms the snapshot
    (repo / "api.py").write_text("def send(payload, retries=3):\n    return payload\n")
    denied = await adapter.dispatch(patch_payload(repo, home, attempt="exec-stale"))
    assert denied.decision == "deny" and denied.delivery_key
    adapter.emit(denied, "PreToolUse", io.StringIO())
    baseline = acked(adapter, denied.delivery_key)
    assert ledger(adapter, denied.delivery_key)["binding"] == TURN

    session = adapter.session_identity(NATIVE)
    append(path, code_mode_denial(denied.reason, turn="01a10ae6-ffff-other-turn"))
    adapter.confirm_deliveries(session, path)
    assert acked(adapter, denied.delivery_key) == baseline, "another turn's denial is not this receipt"

    append(path, code_mode_denial(denied.reason))
    adapter.confirm_deliveries(session, path)
    assert acked(adapter, denied.delivery_key) == int(denied.delivery_key.rpartition(":")[2])


@pytestmark_integration
@pytest.mark.asyncio
async def test_a_plain_function_output_denial_also_confirms(host):
    adapter, repo, _, _, home = host
    _, path = await started(adapter, repo, home)
    await adapter.dispatch(patch_payload(repo, home, attempt="exec-warm"))
    (repo / "api.py").write_text("def send(payload, retries=3):\n    return payload\n")
    denied = await adapter.dispatch(patch_payload(repo, home, attempt="exec-stale"))
    adapter.emit(denied, "PreToolUse", io.StringIO())
    append(path, direct_denial(denied.reason))
    adapter.confirm_deliveries(adapter.session_identity(NATIVE), path)
    assert acked(adapter, denied.delivery_key) == int(denied.delivery_key.rpartition(":")[2])


@pytestmark_integration
@pytest.mark.asyncio
async def test_a_multi_file_patch_is_checked_across_every_target(host):
    adapter, repo, _, _, home = host
    await started(adapter, repo, home)
    await adapter.dispatch(patch_payload(repo, home, attempt="exec-warm"))
    (repo / "api.py").write_text("def send(payload, retries=3):\n    return payload\n")
    patch = ("*** Begin Patch\n*** Update File: other.py\n@@\n-    return 1\n+    return 2\n"
             "*** Update File: api.py\n@@\n-x\n+y\n*** End Patch")
    response = await adapter.dispatch(payload("PreToolUse", repo, home, tool_name="apply_patch",
                                              tool_use_id="exec-multi", tool_input={"command": patch}))
    assert response.decision == "deny", "a stale target anywhere in the patch stops the whole patch"
    check = [e for e in adapter.trace.events(native_session_id=NATIVE)
             if e["event"] == "action_check" and e["attempt_id"] == "exec-multi"][0]
    assert set(json.loads(check["targets"])) == {"api.py", "other.py"}


@pytestmark_integration
@pytest.mark.asyncio
async def test_shell_is_recorded_as_opaque_and_never_denied(host):
    adapter, repo, _, _, home = host
    await started(adapter, repo, home)
    response = await adapter.dispatch(payload("PreToolUse", repo, home, tool_name="Bash", tool_use_id="exec-sh",
                                              tool_input={"command": "Set-Content api.py 'x'"}))
    assert response.decision is None
    assert "opaque" in response.reason


@pytestmark_integration
@pytest.mark.asyncio
async def test_expired_replay_and_revoked_access_hold_for_codex(host):
    adapter, repo, _, _, home = host
    await started(adapter, repo, home)
    first = await adapter.dispatch(patch_payload(repo, home, attempt="exec-replay"))
    adapter.clock.advance(adapter.engine.deadline_ms / 1000 + 1)
    again = await adapter.dispatch(patch_payload(repo, home, attempt="exec-replay"))
    assert first.decision is None and again.decision == "deny"
    assert "attempt_expired" in again.reason

    object.__setattr__(adapter.capability, "denied_paths", ("api.py",))
    revoked = await adapter.dispatch(patch_payload(repo, home, attempt="exec-revoked"))
    assert revoked.decision is None and "not authorized" in revoked.reason
    assert "send()" not in revoked.reason


@pytestmark_integration
@pytest.mark.asyncio
async def test_identical_native_session_ids_never_share_state_across_hosts(host, tmp_path, monkeypatch):
    """Two hosts can report the same native ID; their streams must stay apart."""
    adapter, repo, _, claude, home = host
    claude_config = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_config))
    cx_start, path = await started(adapter, repo, home)
    cc_start = await claude.dispatch({"hook_event_name": "SessionStart", "session_id": NATIVE,
                                      "cwd": str(repo), "transcript_path": None})
    assert adapter.session_identity(NATIVE).memex_session_id != claude.session_identity(NATIVE).memex_session_id
    assert adapter._binding(NATIVE)["task_id"] != claude._binding(NATIVE)["task_id"]
    assert {d["task_id"] for d in adapter.delivery_states(NATIVE)}.isdisjoint(
        {d["task_id"] for d in claude.delivery_states(NATIVE)})

    # Codex confirming its own packet advances Codex's cursor only.
    adapter.confirm_deliveries(adapter.session_identity(NATIVE), path)
    assert acked(adapter, cx_start.delivery_key) == 1
    assert acked(claude, cc_start.delivery_key) == 0

    # And Claude cannot reach Codex's task by naming the same native session.
    with pytest.raises(PermissionError):
        claude.engine.tasks.get(cx_start.delivery_key.rpartition(":")[0],
                                claude.session_identity(NATIVE), now=claude.clock())


@pytestmark_integration
@pytest.mark.asyncio
async def test_compaction_and_resume_redeliver_and_reconfirm(host):
    """S02: SessionStart re-fires with source=compact and source=resume."""
    adapter, repo, _, _, home = host
    first, path = await started(adapter, repo, home)
    adapter.confirm_deliveries(adapter.session_identity(NATIVE), path)
    for source, turn in (("compact", "01a10ae6-compact-turn"), ("resume", "01a10ae6-resume-turn")):
        again = await adapter.dispatch(payload("SessionStart", repo, home, source=source))
        assert again.delivery_key != first.delivery_key, "a fresh task, not an assumed retention"
        adapter.emit(again, "SessionStart", io.StringIO())
        # The historical packet in this same rollout does not confirm the new one.
        adapter.confirm_deliveries(adapter.session_identity(NATIVE), path)
        assert acked(adapter, again.delivery_key) == 0
        append(path, developer_context(again.additional_context, turn=turn))
        adapter.confirm_deliveries(adapter.session_identity(NATIVE), path)
        assert acked(adapter, again.delivery_key) == 1


def test_hook_configuration_is_wrapped_reversible_and_launcher_based(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    registration = discover_repository(repo)
    launcher = codex.write_launcher(registration, python_executable="C:/Python/python.exe",
                                    pythonpath="C:/checkout")
    text = launcher.read_text()
    assert "memex.integrations.codex" in text and "C:/checkout" in text

    hooks_json = repo / ".codex" / "hooks.json"
    hooks_json.parent.mkdir()
    hooks_json.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "keep-me"}]}]}}))
    once = codex.install_hooks(hooks_json, launcher)
    twice = codex.install_hooks(hooks_json, launcher)
    assert once == twice and "hooks" in twice
    assert twice["hooks"]["PreToolUse"][0]["matcher"] == "apply_patch|Bash"
    assert twice["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"] <= 3  # S02: SessionEnd caps at 3 s
    restored = codex.uninstall_hooks(hooks_json, launcher)
    assert restored == {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "keep-me"}]}]}}

    flags = codex.session_flags(launcher)
    assert flags[0] == "-c" and all('"' not in f.split("command=")[1].split("'")[0] for f in flags[1::2]
                                     if "command=" in f)
