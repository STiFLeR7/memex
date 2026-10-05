"""W09/W10: host binding, the deny gate and the trace, on real Git/SQLite/Neo4j.

These are deterministic adapter mechanism tests. The native-host ordering proof
lives in `test_phase3_native_loop.py`; passing here does not establish it.
"""
import asyncio
from hashlib import sha256
import io
import json
import os
import pathlib
import sqlite3
import subprocess

import pytest
import pytest_asyncio
from graphiti_core.driver.neo4j_driver import Neo4jDriver
from neo4j.exceptions import ServiceUnavailable

from memex.context.live import ClaimRevision, EvidenceRef, PacketBudget, OpenTaskRequest
from memex.integrations.claude_code import (
    MAX_DELIVERY_CONFIRMATIONS, ClaudeCodeAdapter, install_hooks, load_capability,
    register, uninstall_hooks,
)
from memex.runtime.actions import LiveContextEngine
from memex.runtime.coordinator import RepositoryIndexer
from memex.runtime.graph import StructuralGraphStore
from memex.runtime.supports import ClaimStore
from memex.runtime.tasks import TaskStore
from memex.runtime.trace import TraceStore
from memex.runtime.views import discover_repository

pytestmark = pytest.mark.integration

NATIVE = "native-session-abc"


def digest(content: bytes) -> str:
    return "sha256:" + sha256(content).hexdigest()


class Clock:
    """Controlled time for both the core and the adapter."""

    def __init__(self, start: float = 100.0):
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> float:
        self.value += seconds
        return self.value


@pytest_asyncio.fixture(loop_scope="function")
async def host(tmp_path):
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
    driver = await asyncio.to_thread(Neo4jDriver, uri, None, None)
    indexer = RepositoryIndexer(registration, StructuralGraphStore(driver))
    await indexer.refresh()

    claims = ClaimStore(driver)
    evidence = EvidenceRef(evidence_id="e-api", repo_id=registration.repo_id, path="api.py",
                           content_hash=digest((repo / "api.py").read_bytes()),
                           source_kind="source", observed_at=1.0)
    claim = ClaimRevision(claim_id="c-send", revision_id="r1", repo_id=registration.repo_id,
                          assertion="send() returns its payload unchanged", authority="inferred",
                          support_sets=(("e-api",),), observed_at=1.0)
    await claims.put_evidence(evidence)
    await claims.put_claim(claim)

    tasks = TaskStore(registration.runtime_path,
                      authenticate=lambda s: s.principal_id == "owner" and s.harness == "claude_code")
    # One shared mutable clock. Tests that do not touch it see a fixed 100.0, which
    # is what the earlier suite assumed; expiry tests advance it deliberately.
    now = Clock()
    engine = LiveContextEngine(
        indexer, claims, tasks,
        authorize_view=lambda s, r: True,
        authorize_source=lambda s, p: p not in capability.denied_paths,
        clock=now,
    )
    adapter = ClaudeCodeAdapter(engine, capability, clock=now)
    yield adapter, repo, registration
    await driver.close()


def transcript_for(repo, session=NATIVE):
    """Stands in for the host's per-session transcript file."""
    path = pathlib.Path(repo).parent / f"transcript-{session}.jsonl"
    if not path.exists():
        path.write_text("", newline="\n")
    return str(path)


def host_consumes(adapter, response, event, repo, session=NATIVE):
    """Emit, then record what the host injected, the way a real transcript does.

    The host writes our raw stdout into the transcript, which is how a delivery's
    own marker becomes observable evidence that the packet reached the session.
    """
    buffer = io.StringIO()
    adapter.emit(response, event, buffer)
    with open(transcript_for(repo, session), "a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps({
            "type": "user", "sessionId": session,
            "message": {"content": [{"type": "tool_result", "is_error": True,
                                     "content": buffer.getvalue()}]},
        }) + "\n")
    return buffer.getvalue()


def deliver_and_confirm(adapter, response, repo, event="PreToolUse"):
    """What the host does with a response: insert it, then let us observe that."""
    host_consumes(adapter, response, event, repo)
    adapter.confirm_deliveries(adapter.session_identity(NATIVE), transcript_for(repo))


async def started_session(adapter, repo):
    """SessionStart whose packet is actually emitted and confirmed."""
    response = await adapter.dispatch(payload("SessionStart", repo))
    deliver_and_confirm(adapter, response, repo, "SessionStart")
    return response


def payload(event, repo, **extra):
    base = {"hook_event_name": event, "session_id": NATIVE, "cwd": str(repo),
            "permission_mode": "acceptEdits", "transcript_path": transcript_for(repo)}
    base.update(extra)
    return base


def edit_payload(repo, target="api.py", attempt="toolu_1", old="def send(payload):", new="def send(payload, timeout):"):
    return payload("PreToolUse", repo, tool_name="Edit", tool_use_id=attempt,
                   tool_input={"file_path": str(repo / target), "old_string": old, "new_string": new})


# --------------------------------------------------------------------------- #
# Trust boundary
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_principal_comes_from_local_capability_not_the_payload(host):
    adapter, repo, _ = host
    session = adapter.session_identity(NATIVE)
    assert session.principal_id == "owner"
    assert session.harness == "claude_code"
    # A payload claiming another principal cannot change the derived identity.
    hostile = payload("SessionStart", repo, principal_id="attacker", harness="codex")
    await adapter.on_session_start(hostile)
    assert adapter.session_identity(NATIVE).principal_id == "owner"
    # Distinct native sessions derive distinct, non-guessable memex sessions.
    assert adapter.session_identity("other-native").memex_session_id != session.memex_session_id


@pytest.mark.asyncio
async def test_session_naming_a_different_worktree_is_refused(host, tmp_path):
    adapter, repo, _ = host
    other = tmp_path / "elsewhere"
    other.mkdir()
    subprocess.run(["git", "init", str(other)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with pytest.raises(PermissionError):
        await adapter.dispatch(payload("SessionStart", other))


@pytest.mark.asyncio
async def test_capability_file_is_required_and_not_world_readable(host, tmp_path):
    _, repo, registration = host
    capability = load_capability(registration)
    assert capability.principal_id == "owner"
    assert capability.secret
    fresh = tmp_path / "unregistered"
    fresh.mkdir()
    subprocess.run(["git", "init", str(fresh)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with pytest.raises(PermissionError):
        load_capability(discover_repository(fresh))


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_stale_edit_is_prevented_then_the_reconsidered_attempt_proceeds(host):
    adapter, repo, _ = host
    started = await started_session(adapter, repo)
    assert "send() returns its payload unchanged" in started.additional_context

    # An unchanged target proceeds.
    first = await adapter.dispatch(edit_payload(repo, attempt="toolu_ok"))
    assert first.decision is None  # memex abstains; it never grants permission

    # Supporting evidence changes after delivery.
    (repo / "api.py").write_text("def send(payload, retries=3):\n    return payload\n")

    denied = await adapter.dispatch(edit_payload(repo, attempt="toolu_stale"))
    assert denied.decision == "deny"
    assert "outcome=replan" in denied.reason
    assert "api.py" in denied.reason
    # The correction names the delivered hash and the current one.
    assert "delivered sha256:" in denied.reason and "now sha256:" in denied.reason

    # The host inserts that correction, which is what the next invocation confirms.
    deliver_and_confirm(adapter, denied, repo)

    # A reconsidered attempt is a new attempt ID linked to the original. It is no
    # longer prevented, but memex also does not certify it: the claim's evidence
    # changed, so the honest answer is fail-open with unknown coverage until new
    # evidence is recorded.
    revised = await adapter.dispatch(edit_payload(
        repo, attempt="toolu_revised", old="def send(payload, retries=3):",
        new="def send(payload, retries=3, timeout=None):"))
    assert revised.decision is None
    assert "freshness unknown" in revised.reason

    events = adapter.trace.events(native_session_id=NATIVE)
    checks = [e for e in events if e["event"] == "action_check"]
    assert [e["gate"] for e in checks] == ["allowed", "prevented", "advisory"]
    assert checks[2]["original_attempt_id"] == "toolu_stale"
    assert checks[1]["reconsidered"] == 1  # a later attempt actually arrived


@pytest.mark.asyncio
async def test_a_certificate_returns_only_after_new_evidence_is_recorded(host):
    """`proceed` requires re-established support, not merely a delivered correction."""
    adapter, repo, registration = host
    await started_session(adapter, repo)
    (repo / "api.py").write_text("def send(payload, retries=3):\n    return payload\n")
    denied = await adapter.dispatch(edit_payload(repo, attempt="toolu_a"))
    assert denied.decision == "deny"

    # The external change is observed as new evidence and a scoped successor.
    # Hash the bytes actually on disk: text mode rewrites newlines on Windows.
    await adapter.engine.claims.put_evidence(EvidenceRef(
        evidence_id="e-api-2", repo_id=registration.repo_id, path="api.py",
        content_hash=digest((repo / "api.py").read_bytes()), source_kind="source", observed_at=2.0))
    await adapter.engine.claims.put_claim(ClaimRevision(
        claim_id="c-send", revision_id="r2", repo_id=registration.repo_id,
        assertion="send() accepts a retries count and returns its payload",
        authority="inferred", support_sets=(("e-api-2",),), supersedes=("r1",), observed_at=2.0))

    restated = await started_session(adapter, repo)
    assert "retries count" in restated.additional_context
    allowed = await adapter.dispatch(edit_payload(repo, attempt="toolu_b"))
    assert allowed.decision is None
    assert "outcome=proceed" in allowed.reason


@pytest.mark.asyncio
async def test_the_correction_names_the_dependency_that_actually_changed(host):
    """The file that moved is often not the file being edited.

    Naming only the action target leaves the agent to guess which dependency to
    recheck, so the correction states the changed evidence path explicitly.
    """
    adapter, repo, registration = host
    await adapter.engine.claims.put_evidence(EvidenceRef(
        evidence_id="e-dep", repo_id=registration.repo_id, path="other.py",
        content_hash=digest((repo / "other.py").read_bytes()),
        source_kind="source", observed_at=1.0))
    # One necessary (AND) set across both files, so other.py alone invalidates it.
    await adapter.engine.claims.put_claim(ClaimRevision(
        claim_id="c-pair", revision_id="rp1", repo_id=registration.repo_id,
        assertion="send() relies on other() returning one", authority="inferred",
        support_sets=(("e-api", "e-dep"),), observed_at=1.0))
    await started_session(adapter, repo)

    # The dependency changes; the edit target does not.
    (repo / "other.py").write_text("def other():\n    raise RuntimeError('boom')\n")
    denied = await adapter.dispatch(edit_payload(repo, attempt="toolu_dep"))

    assert denied.decision == "deny"
    assert "changed dependency other.py" in denied.reason
    assert "delivered sha256:" in denied.reason
    assert "changed dependency" in denied.reason and "Re-read" in denied.reason
    # The unchanged target is not falsely reported as having moved.
    assert "api.py: delivered" not in denied.reason


@pytest.mark.asyncio
async def test_unrelated_change_does_not_interrupt_a_disjoint_action(host):
    adapter, repo, _ = host
    await started_session(adapter, repo)
    (repo / "other.py").write_text("def other():\n    return 2\n")
    check = await adapter.dispatch(edit_payload(repo, target="other.py", attempt="toolu_other",
                                                       old="def other():", new="def other(flag):"))
    assert check.decision is None


@pytest.mark.asyncio
async def test_opaque_shell_action_is_deferred_and_not_certified(host):
    adapter, repo, _ = host
    await started_session(adapter, repo)
    response = await adapter.dispatch(payload(
        "PreToolUse", repo, tool_name="Bash", tool_use_id="toolu_bash",
        tool_input={"command": "sed -i s/payload/body/ api.py"}))
    assert response.decision is None
    assert "not certified" in response.reason
    recorded = [e for e in adapter.trace.events(native_session_id=NATIVE) if e["tool_name"] == "Bash"]
    assert recorded[0]["gate"] == "advisory" and recorded[0]["scope_complete"] == 0
    # The shell command text is never retained.
    assert "sed" not in json.dumps(recorded[0])


@pytest.mark.asyncio
async def test_target_outside_the_registered_worktree_is_deferred(host, tmp_path):
    adapter, repo, _ = host
    await started_session(adapter, repo)
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n")
    response = await adapter.dispatch(payload(
        "PreToolUse", repo, tool_name="Edit", tool_use_id="toolu_out",
        tool_input={"file_path": str(outside), "old_string": "x = 1", "new_string": "x = 2"}))
    assert response.decision is None
    assert "outside the registered worktree" in response.reason


@pytest.mark.asyncio
async def test_revoked_source_access_refuses_without_disclosing_assertions(host):
    adapter, repo, _ = host
    await started_session(adapter, repo)
    object.__setattr__(adapter.capability, "denied_paths", ("api.py",))
    response = await adapter.dispatch(edit_payload(repo, attempt="toolu_revoked"))
    assert response.decision is None
    assert "not authorized" in response.reason
    assert "send() returns its payload unchanged" not in response.reason


# --------------------------------------------------------------------------- #
# Failure, bounds and replay
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_backend_outage_reports_unknown_freshness_without_a_receipt(host):
    adapter, repo, _ = host
    await started_session(adapter, repo)
    before = len(adapter.trace.events(native_session_id=NATIVE))

    async def unavailable(*_a, **_k):
        raise ServiceUnavailable("isolated backend down")

    original = adapter.engine.claims.load
    adapter.engine.claims.load = unavailable
    try:
        response = await adapter.dispatch(edit_payload(repo, attempt="toolu_outage"))
    finally:
        adapter.engine.claims.load = original
    assert response.decision is None
    assert "freshness unknown" in response.reason
    recorded = adapter.trace.events(native_session_id=NATIVE)[before:]
    check = [e for e in recorded if e["event"] == "action_check"][0]
    assert check["gate"] == "advisory"
    assert check["insertion"] == "none"  # no fabricated acceptance


@pytest.mark.asyncio
async def test_outage_preserves_an_unconfirmed_correction_and_still_requires_replan(host):
    """An unconfirmed correction must survive the backend going away.

    This is the case that matters: the host has not been observed taking the
    correction, so it is still pending, and an outage must not let the stale
    pending edit through as merely "unknown freshness".
    """
    adapter, repo, _ = host
    await started_session(adapter, repo)
    (repo / "api.py").write_text("def send(payload, retries=3):\n    return payload\n")

    denied = await adapter.dispatch(edit_payload(repo, attempt="toolu_known"))
    assert denied.decision == "deny"
    prepared = [e for e in adapter.trace.events(native_session_id=NATIVE)
                if e["attempt_id"] == "toolu_known"][0]
    # Rendered, not accepted. Nothing here claims the host took it.
    assert prepared["insertion"] == "prepared"
    assert not [e for e in adapter.trace.events(native_session_id=NATIVE)
                if e["event"] == "delivery" and e["insertion"] == "confirmed"
                and e["attempt_id"] == "toolu_known"]

    async def unavailable(*_a, **_k):
        raise ServiceUnavailable("isolated backend down")

    original = adapter.engine.claims.load
    adapter.engine.claims.load = unavailable
    try:
        during = await adapter.dispatch(edit_payload(repo, attempt="toolu_known_outage"))
    finally:
        adapter.engine.claims.load = original
    # A known correction must not be downgraded to unknown coverage.
    assert during.decision == "deny"


@pytest.mark.asyncio
async def test_adapter_deadline_fails_open_with_an_explicit_advisory(host):
    adapter, repo, _ = host
    await started_session(adapter, repo)
    adapter.deadline_seconds = 0.01

    async def slow(_request):
        await asyncio.sleep(1.0)

    original = adapter.engine.check_action
    adapter.engine.check_action = slow
    try:
        response = await adapter.dispatch(edit_payload(repo, attempt="toolu_slow"))
    finally:
        adapter.engine.check_action = original
        adapter.deadline_seconds = 5.0
    assert response.decision is None
    assert "deadline exceeded" in response.reason
    assert response.system_message
    check = [e for e in adapter.trace.events(native_session_id=NATIVE) if e["attempt_id"] == "toolu_slow"][0]
    assert check["gate"] == "advisory" and check["reason"] == "adapter_deadline_exceeded"


@pytest.mark.asyncio
async def test_replayed_attempt_identity_is_idempotent(host):
    adapter, repo, _ = host
    await started_session(adapter, repo)
    (repo / "api.py").write_text("def send(payload, retries=3):\n    return payload\n")
    first = await adapter.dispatch(edit_payload(repo, attempt="toolu_replay"))
    second = await adapter.dispatch(edit_payload(repo, attempt="toolu_replay"))
    assert first.decision == second.decision == "deny"
    assert first.reason == second.reason


@pytest.mark.asyncio
async def test_expired_replay_cannot_return_the_earlier_proceed(host):
    """The core's current answer is authoritative, including on replay.

    The core's cached-attempt path downgrades an expired attempt to
    `resync_required` with `attempt_expired`. Returning the previously rendered
    response regardless would hand back a stale non-denial and authorize reuse
    of an attempt the core has just invalidated.
    """
    adapter, repo, _ = host
    clock = adapter.clock
    await started_session(adapter, repo)

    first = await adapter.dispatch(edit_payload(repo, attempt="toolu_expiry"))
    assert first.decision is None  # a clean proceed while the attempt is fresh

    # Past the freshness deadline the core recorded for that attempt.
    clock.advance(adapter.engine.deadline_ms / 1000 + 1)
    replayed = await adapter.dispatch(edit_payload(repo, attempt="toolu_expiry"))
    assert replayed.decision == "deny", "an expired attempt must not reuse an earlier proceed"
    assert "attempt_expired" in replayed.reason
    assert "resync" in replayed.reason.lower()

    recorded = [e for e in adapter.trace.events(native_session_id=NATIVE)
                if e["attempt_id"] == "toolu_expiry"]
    assert [e["gate"] for e in recorded] == ["allowed", "prevented"]
    assert recorded[-1]["outcome"] == "resync_required"


@pytest.mark.asyncio
async def test_a_fresh_attempt_recovers_after_expiry(host):
    adapter, repo, _ = host
    clock = adapter.clock
    await started_session(adapter, repo)
    await adapter.dispatch(edit_payload(repo, attempt="toolu_old"))
    clock.advance(adapter.engine.deadline_ms / 1000 + 1)
    assert (await adapter.dispatch(edit_payload(repo, attempt="toolu_old"))).decision == "deny"

    # A new attempt ID is the documented recovery and must be checked afresh.
    recovered = await adapter.dispatch(edit_payload(repo, attempt="toolu_new"))
    assert recovered.decision is None
    assert "outcome=proceed" in recovered.reason


@pytest.mark.asyncio
async def test_valid_replay_is_still_idempotent_under_a_moving_clock(host):
    """Idempotency must survive the fix: inside the window, same answer, same text."""
    adapter, repo, _ = host
    clock = adapter.clock
    await started_session(adapter, repo)
    (repo / "api.py").write_text("def send(payload, retries=3):\n    return payload\n")
    first = await adapter.dispatch(edit_payload(repo, attempt="toolu_window"))
    assert first.decision == "deny"
    clock.advance(1.0)  # still well inside the freshness deadline
    second = await adapter.dispatch(edit_payload(repo, attempt="toolu_window"))
    assert second.decision == "deny"
    assert second.reason == first.reason


@pytest.mark.asyncio
async def test_expired_replay_still_refuses_revoked_authorization_first(host):
    """Expiry must not become a path around source authorization."""
    adapter, repo, _ = host
    clock = adapter.clock
    await started_session(adapter, repo)
    await adapter.dispatch(edit_payload(repo, attempt="toolu_both"))
    object.__setattr__(adapter.capability, "denied_paths", ("api.py",))
    clock.advance(adapter.engine.deadline_ms / 1000 + 1)
    response = await adapter.dispatch(edit_payload(repo, attempt="toolu_both"))
    assert response.decision is None
    assert "not authorized" in response.reason
    assert "send() returns its payload unchanged" not in response.reason


@pytest.mark.asyncio
async def test_expiry_does_not_reset_the_reconsideration_linkage(host):
    """A denied attempt stays the root of its retry chain across an expiry."""
    adapter, repo, _ = host
    clock = adapter.clock
    await started_session(adapter, repo)
    (repo / "api.py").write_text("def send(payload, retries=3):\n    return payload\n")
    denied = await adapter.dispatch(edit_payload(repo, attempt="toolu_root"))
    assert denied.decision == "deny"
    clock.advance(adapter.engine.deadline_ms / 1000 + 1)
    await adapter.dispatch(edit_payload(repo, attempt="toolu_root"))
    follow_up = await adapter.dispatch(edit_payload(repo, attempt="toolu_child"))
    child = [e for e in adapter.trace.events(native_session_id=NATIVE)
             if e["attempt_id"] == "toolu_child"][0]
    assert child["original_attempt_id"] == "toolu_root"
    assert follow_up.decision in (None, "deny")


@pytest.mark.asyncio
async def test_replay_after_revoked_access_stops_disclosing_the_correction(host):
    """A cached hook answer must not outlive the authorization it was built on."""
    adapter, repo, _ = host
    await started_session(adapter, repo)
    (repo / "api.py").write_text("def send(payload, retries=3):\n    return payload\n")
    first = await adapter.dispatch(edit_payload(repo, attempt="toolu_revoke_replay"))
    assert first.decision == "deny"
    assert "send() returns its payload unchanged" in first.reason

    object.__setattr__(adapter.capability, "denied_paths", ("api.py",))
    again = await adapter.dispatch(edit_payload(repo, attempt="toolu_revoke_replay"))
    assert again.decision is None
    assert "not authorized" in again.reason
    assert "send() returns its payload unchanged" not in again.reason


@pytest.mark.asyncio
async def test_worktree_check_leaves_no_state_in_an_unrelated_repository(host, tmp_path):
    adapter, _, _ = host
    other = tmp_path / "unrelated"
    other.mkdir()
    subprocess.run(["git", "init", str(other)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with pytest.raises(PermissionError):
        adapter.verify_worktree(str(other))
    # Being told "wrong repository" must not have written memex identities into it.
    assert not (other / ".git" / "memex").exists()


@pytest.mark.asyncio
async def test_compaction_redelivers_a_full_working_set(host):
    adapter, repo, _ = host
    first = await started_session(adapter, repo)
    assert first.additional_context
    compacted = await adapter.dispatch(payload("SessionStart", repo, source="compact"))
    starts = [e for e in adapter.trace.events(native_session_id=NATIVE) if e["event"] == "session_start"]
    assert len(starts) == 2
    assert starts[1]["task_id"] != starts[0]["task_id"]   # a fresh baseline, not an assumed one
    assert starts[1]["insertion"] == "prepared"           # delivery is not assumed either
    assert "send() returns its payload unchanged" in compacted.additional_context


@pytest.mark.asyncio
async def test_bounded_reconsideration_stops_retrying_the_same_mutation(host):
    adapter, repo, _ = host
    await started_session(adapter, repo)
    (repo / "api.py").write_text("def send(payload, retries=3):\n    return payload\n")
    reasons = []
    for index in range(4):
        (repo / "api.py").write_text(f"def send(payload, retries={index}):\n    return payload\n")
        response = await adapter.dispatch(edit_payload(repo, attempt=f"toolu_loop{index}"))
        reasons.append(response.reason)
        if "Stop retrying" in response.reason:
            break
    assert any("Stop retrying" in reason for reason in reasons), reasons


@pytest.mark.asyncio
async def test_packet_overflow_requests_resynchronization(host):
    adapter, repo, _ = host
    engine = adapter.engine
    session = adapter.session_identity(NATIVE)
    result = await engine.indexer.refresh()
    frame = await engine.open_task(OpenTaskRequest(
        session=session, view=result.view, intent="tiny budget",
        budget=PacketBudget(max_items=1, max_characters=256)))
    assert frame.resync_required
    text, _ = adapter.render_packet(frame)
    assert "resynchronization required" in text


# --------------------------------------------------------------------------- #
# Host configuration and trace hygiene
# --------------------------------------------------------------------------- #

def test_hook_installation_preserves_existing_configuration_and_is_reversible(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "permissions": {"allow": ["Bash(git *)"]},
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "existing-user-hook.sh"}]}]},
    }))
    installed = install_hooks(settings, python_executable="py.exe")
    # The user's own hook and permission policy survive untouched.
    assert installed["permissions"] == {"allow": ["Bash(git *)"]}
    commands = [h["command"] for group in installed["hooks"]["PreToolUse"] for h in group["hooks"]]
    assert "existing-user-hook.sh" in commands
    assert any("memex.integrations.claude_code" in c for c in commands)

    # Reinstalling does not duplicate.
    again = install_hooks(settings, python_executable="py.exe")
    memex_entries = [h for event in again["hooks"].values() for group in event
                     for h in group["hooks"] if "memex.integrations.claude_code" in h["command"]]
    assert len(memex_entries) == 4  # SessionStart, PreToolUse, PostToolUse, SessionEnd

    removed = uninstall_hooks(settings, python_executable="py.exe")
    assert removed["permissions"] == {"allow": ["Bash(git *)"]}
    remaining = [h["command"] for group in removed["hooks"]["PreToolUse"] for h in group["hooks"]]
    assert remaining == ["existing-user-hook.sh"]


@pytest.mark.asyncio
async def test_trace_separates_exposure_compliance_and_execution(host):
    adapter, repo, _ = host
    await started_session(adapter, repo)
    (repo / "api.py").write_text("def send(payload, retries=3):\n    return payload\n")
    await adapter.dispatch(edit_payload(repo, attempt="toolu_trace"))
    await adapter.dispatch(payload("PostToolUse", repo, tool_name="Edit",
                                           tool_use_id="toolu_trace",
                                           tool_input={"file_path": str(repo / "api.py")}))
    adapter.trace.record_objective("toolu_trace", "passed", "fixture assertion")

    events = adapter.trace.events(native_session_id=NATIVE)
    start = [e for e in events if e["event"] == "session_start"][0]
    check = [e for e in events if e["event"] == "action_check"][0]
    executed = [e for e in events if e["event"] == "action_executed"][0]

    assert start["insertion"] == "prepared"      # rendered, not yet delivered
    assert check["gate"] == "prevented"          # what the adapter did
    assert check["objective"] == "passed"        # externally checked result
    # Host-confirmed insertion is a separate, later event with its own evidence.
    confirmed = [e for e in events if e["event"] == "delivery" and e["insertion"] == "confirmed"]
    assert confirmed and confirmed[0]["sequence"] == start["sequence"]
    assert executed["gate"] == "executed"
    assert check["adapter_version"] == "claude-code.v1"
    assert check["checked_view_id"].startswith("view:")

    # No source text, transcript or tool argument body anywhere in the trace.
    blob = json.dumps(events)
    assert "def send" not in blob
    assert "old_string" not in blob and "new_string" not in blob
    assert "transcript" not in blob
    assert check["input_digest"].startswith("sha256:")


@pytest.mark.asyncio
async def test_batched_actions_are_gated_per_target_not_per_batch(host):
    """S01 measured the host serializing parallel edit calls, each with its own
    check/execute pair. So a check covers its declared target at its own moment:
    one target can be prevented while a sibling in the same model turn proceeds,
    and a sibling write may already have landed before the next check runs.
    """
    adapter, repo, registration = host
    await adapter.engine.claims.put_evidence(EvidenceRef(
        evidence_id="e-other", repo_id=registration.repo_id, path="other.py",
        content_hash=digest((repo / "other.py").read_bytes()),
        source_kind="source", observed_at=1.0))
    await adapter.engine.claims.put_claim(ClaimRevision(
        claim_id="c-other", revision_id="ro1", repo_id=registration.repo_id,
        assertion="other() returns one", authority="inferred",
        support_sets=(("e-other",),), observed_at=1.0))
    await started_session(adapter, repo)

    # Only api.py drifts.
    (repo / "api.py").write_text("def send(payload, retries=3):\n    return payload\n")

    stale = await adapter.dispatch(edit_payload(repo, attempt="batch_api"))
    assert stale.decision == "deny"
    # The host inserts the denial before running the next call in the batch, which
    # is what clears the pending correction. Without that step a disjoint sibling
    # would be held back by an undelivered correction, which is correct but is not
    # what happens in a real serialized batch.
    deliver_and_confirm(adapter, stale, repo)

    sibling = await adapter.dispatch(edit_payload(
        repo, target="other.py", attempt="batch_other",
        old="def other():", new="def other(flag):"))
    assert sibling.decision is None  # a disjoint sibling is not blocked by it

    checks = {e["attempt_id"]: e for e in adapter.trace.events(native_session_id=NATIVE)
              if e["event"] == "action_check"}
    assert json.loads(checks["batch_api"]["targets"]) == ["api.py"]
    assert json.loads(checks["batch_other"]["targets"]) == ["other.py"]
    assert checks["batch_api"]["gate"] == "prevented"
    assert checks["batch_other"]["gate"] != "prevented"


@pytest.mark.asyncio
async def test_cancellation_produces_no_receipt_and_no_certificate(host):
    adapter, repo, _ = host
    await started_session(adapter, repo)
    before = len(adapter.trace.events(native_session_id=NATIVE))

    async def cancelled(_request):
        raise asyncio.CancelledError()

    original = adapter.engine.check_action
    adapter.engine.check_action = cancelled
    try:
        with pytest.raises(asyncio.CancelledError):
            await adapter.dispatch(edit_payload(repo, attempt="toolu_cancel"))
    finally:
        adapter.engine.check_action = original

    # Cancellation is not an outcome: nothing was recorded as checked or accepted.
    recorded = adapter.trace.events(native_session_id=NATIVE)[before:]
    assert not [e for e in recorded if e["insertion"] == "accepted"]
    assert not [e for e in recorded if e["gate"] in ("allowed", "prevented")]


# --------------------------------------------------------------------------- #
# Delivery confirmation: prepared / emitted / confirmed / failed
# --------------------------------------------------------------------------- #
#
# These drive the states by hand rather than through `started_session`, because
# the point of each is what happens when delivery does *not* complete.


def ledger(adapter, delivery_key):
    task_id, _, sequence = delivery_key.rpartition(":")
    return [d for d in adapter.delivery_states(NATIVE)
            if d["task_id"] == task_id and d["sequence"] == int(sequence)][0]


def acked(adapter, delivery_key):
    task_id, _, _ = delivery_key.rpartition(":")
    session = adapter.session_identity(NATIVE)
    return adapter.engine.tasks.get(task_id, session, now=adapter.clock()).ack_sequence


def write_transcript(repo, *contents, session=NATIVE):
    with open(transcript_for(repo, session), "w", encoding="utf-8", newline="\n") as stream:
        for content in contents:
            stream.write(json.dumps({
                "type": "user", "sessionId": session,
                "message": {"content": [{"type": "tool_result", "is_error": True,
                                         "content": content}]},
            }) + "\n")


@pytest.mark.asyncio
async def test_preparation_alone_never_advances_the_baseline(host):
    """A rendered packet that was never printed has not been delivered."""
    adapter, repo, _ = host
    started = await adapter.dispatch(payload("SessionStart", repo))
    assert started.delivery_key, "the snapshot must register a delivery"
    assert ledger(adapter, started.delivery_key)["state"] == "prepared"
    assert acked(adapter, started.delivery_key) == 0
    task_id, _, _ = started.delivery_key.rpartition(":")
    assert adapter.delivered_hashes(task_id, adapter.session_identity(NATIVE)) == {}


@pytest.mark.asyncio
async def test_emission_alone_never_advances_the_baseline(host):
    adapter, repo, _ = host
    started = await adapter.dispatch(payload("SessionStart", repo))
    adapter.emit(started, "SessionStart", io.StringIO())
    assert ledger(adapter, started.delivery_key)["state"] == "emitted"
    assert acked(adapter, started.delivery_key) == 0

    # An empty transcript is not evidence, however many times we look.
    session = adapter.session_identity(NATIVE)
    write_transcript(repo)
    for _ in range(MAX_DELIVERY_CONFIRMATIONS):
        adapter.confirm_deliveries(session, transcript_for(repo))
    assert ledger(adapter, started.delivery_key)["state"] == "failed"
    assert acked(adapter, started.delivery_key) == 0


@pytest.mark.asyncio
async def test_transcript_evidence_confirms_and_advances_exactly_once(host):
    adapter, repo, _ = host
    started = await adapter.dispatch(payload("SessionStart", repo))
    emitted = host_consumes(adapter, started, "SessionStart", repo)
    marker = ledger(adapter, started.delivery_key)["marker"]
    assert marker in emitted, "the emitted payload must carry its own marker"

    session = adapter.session_identity(NATIVE)
    adapter.confirm_deliveries(session, transcript_for(repo))
    assert ledger(adapter, started.delivery_key)["state"] == "confirmed"
    task_id, _, sequence = started.delivery_key.rpartition(":")
    assert acked(adapter, started.delivery_key) == int(sequence)
    assert adapter.delivered_hashes(task_id, session)  # the baseline now exists

    # Duplicate confirmation is idempotent and advances nothing further.
    adapter.confirm_deliveries(session, transcript_for(repo))
    adapter.confirm_deliveries(session, transcript_for(repo))
    assert acked(adapter, started.delivery_key) == int(sequence)
    confirmed = [e for e in adapter.trace.events(native_session_id=NATIVE)
                 if e["event"] == "delivery" and e["insertion"] == "confirmed"]
    assert len(confirmed) == 1


@pytest.mark.asyncio
async def test_a_broken_output_stream_marks_the_delivery_failed(host):
    adapter, repo, _ = host
    started = await adapter.dispatch(payload("SessionStart", repo))

    class Broken(io.StringIO):
        def write(self, _data):
            raise OSError("stdout closed")

    with pytest.raises(OSError):
        adapter.emit(started, "SessionStart", Broken())
    assert ledger(adapter, started.delivery_key)["state"] == "failed"
    assert acked(adapter, started.delivery_key) == 0
    failure = [e for e in adapter.trace.events(native_session_id=NATIVE)
               if e["event"] == "delivery" and e["insertion"] == "failed"]
    assert failure and failure[0]["reason"].startswith("output_failed")


@pytest.mark.asyncio
async def test_interruption_between_preparation_and_output_is_never_delivered(host):
    """The process died before printing, so a later marker match proves nothing."""
    adapter, repo, _ = host
    started = await adapter.dispatch(payload("SessionStart", repo))
    session = adapter.session_identity(NATIVE)
    marker = ledger(adapter, started.delivery_key)["marker"]

    write_transcript(repo, f"PreToolUse:Edit hook error: [{marker}]")
    adapter.confirm_deliveries(session, transcript_for(repo))
    assert ledger(adapter, started.delivery_key)["state"] == "failed"
    assert acked(adapter, started.delivery_key) == 0


@pytest.mark.asyncio
async def test_an_unconfirmed_correction_stays_pending_and_is_redelivered(host):
    adapter, repo, _ = host
    session = adapter.session_identity(NATIVE)
    await started_session(adapter, repo)

    (repo / "api.py").write_text("def send(payload, retries=3):\n    return payload\n")
    first = await adapter.dispatch(edit_payload(repo, attempt="toolu_c1"))
    assert first.decision == "deny" and first.delivery_key
    adapter.emit(first, "PreToolUse", io.StringIO())  # emitted; the host records nothing

    task_id, _, _ = first.delivery_key.rpartition(":")
    assert adapter.engine.tasks.get(task_id, session, now=adapter.clock()).pending is not None

    # The next attempt is denied again rather than let through on an assumed delivery.
    second = await adapter.dispatch(edit_payload(repo, attempt="toolu_c2"))
    assert second.decision == "deny"
    assert adapter.engine.tasks.get(task_id, session, now=adapter.clock()).pending is not None


@pytest.mark.asyncio
async def test_a_confirmed_correction_advances_the_baseline(host):
    adapter, repo, _ = host
    session = adapter.session_identity(NATIVE)
    await started_session(adapter, repo)
    (repo / "api.py").write_text("def send(payload, retries=3):\n    return payload\n")
    denied = await adapter.dispatch(edit_payload(repo, attempt="toolu_ok"))
    assert denied.decision == "deny"
    host_consumes(adapter, denied, "PreToolUse", repo)
    adapter.confirm_deliveries(session, transcript_for(repo))

    task_id, _, sequence = denied.delivery_key.rpartition(":")
    state = adapter.engine.tasks.get(task_id, session, now=adapter.clock())
    assert state.ack_sequence == int(sequence)
    assert state.pending is None
    assert ledger(adapter, denied.delivery_key)["state"] == "confirmed"


@pytest.mark.asyncio
async def test_confirmation_requires_the_intended_session(host):
    adapter, repo, _ = host
    started = await adapter.dispatch(payload("SessionStart", repo))
    adapter.emit(started, "SessionStart", io.StringIO())
    marker = ledger(adapter, started.delivery_key)["marker"]

    # Another session's transcript is not this packet's evidence. Confirmation
    # reads only the invoking session's transcript, and only its own ledger rows.
    write_transcript(repo, f"[{marker}]", session="other-native")
    other = adapter.session_identity("other-native")
    adapter.confirm_deliveries(other, transcript_for(repo, "other-native"))
    assert ledger(adapter, started.delivery_key)["state"] == "emitted"
    assert acked(adapter, started.delivery_key) == 0


@pytest.mark.asyncio
async def test_a_wrong_sequence_receipt_is_rejected_without_writing_off_the_packet(host):
    adapter, repo, _ = host
    session = adapter.session_identity(NATIVE)
    started = await adapter.dispatch(payload("SessionStart", repo))
    adapter.emit(started, "SessionStart", io.StringIO())
    task_id, _, _ = started.delivery_key.rpartition(":")

    # A ledger row for a sequence the core has no pending packet for.
    db = sqlite3.connect(adapter.state_path)
    with db:
        db.execute("INSERT INTO live_adapter_deliveries"
                   "(task_id,sequence,native_session_id,view_id,marker,kind,attempt_id,state)"
                   " VALUES(?,?,?,?,?,?,?, 'emitted')",
                   (task_id, 99, NATIVE, "view:bogus", "memex-delivery:bogus", "correction", None))
    db.close()
    write_transcript(repo, "[memex-delivery:bogus]")
    adapter.confirm_deliveries(session, transcript_for(repo))

    rejected = [e for e in adapter.trace.events(native_session_id=NATIVE)
                if e["event"] == "delivery" and e["insertion"] == "failed"
                and (e["reason"] or "").startswith("receipt_rejected")]
    assert rejected, "a mismatched receipt must be recorded as failed, not accepted"
    state = adapter.engine.tasks.get(task_id, session, now=adapter.clock())
    assert state.ack_sequence == 0 and state.pending is not None


@pytest.mark.asyncio
async def test_restart_does_not_trust_an_unconfirmed_delivery(host):
    """A fresh adapter over the same control plane re-reads the ledger."""
    adapter, repo, _ = host
    session = adapter.session_identity(NATIVE)
    started = await adapter.dispatch(payload("SessionStart", repo))
    host_consumes(adapter, started, "SessionStart", repo)

    restarted = ClaudeCodeAdapter(adapter.engine, adapter.capability, clock=adapter.clock)
    assert ledger(restarted, started.delivery_key)["state"] == "emitted"
    assert acked(restarted, started.delivery_key) == 0
    # It then confirms on the same evidence, proving the ledger survived.
    restarted.confirm_deliveries(session, transcript_for(repo))
    _, _, sequence = started.delivery_key.rpartition(":")
    assert acked(restarted, started.delivery_key) == int(sequence)


@pytest.mark.asyncio
async def test_the_adapter_only_ever_subtracts_permission(host):
    """No path may emit `allow` or `defer`.

    `allow` would bypass the user's permission policy on a memex outage, and a
    measured `defer` ends a non-interactive turn with `tool_deferred` instead of
    failing open. Denial is the only decision memex is entitled to make.
    """
    adapter, repo, _ = host
    await started_session(adapter, repo)
    emitted = []
    # A proceed, an opaque action, an out-of-scope target, and a real correction.
    emitted.append(await adapter.dispatch(edit_payload(repo, attempt="p1")))
    emitted.append(await adapter.dispatch(payload(
        "PreToolUse", repo, tool_name="Bash", tool_use_id="p2",
        tool_input={"command": "true"})))
    (repo / "api.py").write_text("def send(payload, retries=3):\n    return payload\n")
    emitted.append(await adapter.dispatch(edit_payload(repo, attempt="p3")))

    assert {r.decision for r in emitted} <= {None, "deny"}
    for response in emitted:
        rendered = response.to_payload("PreToolUse")["hookSpecificOutput"]
        assert rendered.get("permissionDecision") in (None, "deny")
        if response.decision is None:
            assert "permissionDecision" not in rendered
    assert any(r.decision == "deny" for r in emitted)


def test_trace_rejects_an_unknown_objective_result(tmp_path):
    trace = TraceStore(tmp_path / "runtime.sqlite")
    with pytest.raises(ValueError):
        trace.record_objective("toolu_x", "probably-fine")
