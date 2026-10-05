"""W09/W10: host binding, the deny gate and the trace, on real Git/SQLite/Neo4j.

These are deterministic adapter mechanism tests. The native-host ordering proof
lives in `test_phase3_native_loop.py`; passing here does not establish it.
"""
import asyncio
from hashlib import sha256
import json
import os
import subprocess

import pytest
import pytest_asyncio
from graphiti_core.driver.neo4j_driver import Neo4jDriver
from neo4j.exceptions import ServiceUnavailable

from memex.context.live import ClaimRevision, EvidenceRef, PacketBudget, OpenTaskRequest
from memex.integrations.claude_code import (
    ClaudeCodeAdapter, install_hooks, load_capability, register, uninstall_hooks,
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
    engine = LiveContextEngine(
        indexer, claims, tasks,
        authorize_view=lambda s, r: True,
        authorize_source=lambda s, p: p not in capability.denied_paths,
        clock=lambda: 100.0,
    )
    adapter = ClaudeCodeAdapter(engine, capability, clock=lambda: 100.0)
    yield adapter, repo, registration
    await driver.close()


def payload(event, repo, **extra):
    base = {"hook_event_name": event, "session_id": NATIVE, "cwd": str(repo),
            "permission_mode": "acceptEdits"}
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
        await adapter.on_session_start(payload("SessionStart", other))


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
    started = await adapter.on_session_start(payload("SessionStart", repo))
    assert "send() returns its payload unchanged" in started.additional_context

    # An unchanged target proceeds.
    first = await adapter.on_pre_tool_use(edit_payload(repo, attempt="toolu_ok"))
    assert first.decision is None  # memex abstains; it never grants permission

    # Supporting evidence changes after delivery.
    (repo / "api.py").write_text("def send(payload, retries=3):\n    return payload\n")

    denied = await adapter.on_pre_tool_use(edit_payload(repo, attempt="toolu_stale"))
    assert denied.decision == "deny"
    assert "outcome=replan" in denied.reason
    assert "api.py" in denied.reason
    # The correction names the delivered hash and the current one.
    assert "delivered sha256:" in denied.reason and "now sha256:" in denied.reason

    # A reconsidered attempt is a new attempt ID linked to the original. It is no
    # longer prevented, but memex also does not certify it: the claim's evidence
    # changed, so the honest answer is fail-open with unknown coverage until new
    # evidence is recorded.
    revised = await adapter.on_pre_tool_use(edit_payload(
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
    await adapter.on_session_start(payload("SessionStart", repo))
    (repo / "api.py").write_text("def send(payload, retries=3):\n    return payload\n")
    denied = await adapter.on_pre_tool_use(edit_payload(repo, attempt="toolu_a"))
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

    restated = await adapter.on_session_start(payload("SessionStart", repo))
    assert "retries count" in restated.additional_context
    allowed = await adapter.on_pre_tool_use(edit_payload(repo, attempt="toolu_b"))
    assert allowed.decision is None
    assert "outcome=proceed" in allowed.reason


@pytest.mark.asyncio
async def test_unrelated_change_does_not_interrupt_a_disjoint_action(host):
    adapter, repo, _ = host
    await adapter.on_session_start(payload("SessionStart", repo))
    (repo / "other.py").write_text("def other():\n    return 2\n")
    check = await adapter.on_pre_tool_use(edit_payload(repo, target="other.py", attempt="toolu_other",
                                                       old="def other():", new="def other(flag):"))
    assert check.decision is None


@pytest.mark.asyncio
async def test_opaque_shell_action_is_deferred_and_not_certified(host):
    adapter, repo, _ = host
    await adapter.on_session_start(payload("SessionStart", repo))
    response = await adapter.on_pre_tool_use(payload(
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
    await adapter.on_session_start(payload("SessionStart", repo))
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n")
    response = await adapter.on_pre_tool_use(payload(
        "PreToolUse", repo, tool_name="Edit", tool_use_id="toolu_out",
        tool_input={"file_path": str(outside), "old_string": "x = 1", "new_string": "x = 2"}))
    assert response.decision is None
    assert "outside the registered worktree" in response.reason


@pytest.mark.asyncio
async def test_revoked_source_access_refuses_without_disclosing_assertions(host):
    adapter, repo, _ = host
    await adapter.on_session_start(payload("SessionStart", repo))
    object.__setattr__(adapter.capability, "denied_paths", ("api.py",))
    response = await adapter.on_pre_tool_use(edit_payload(repo, attempt="toolu_revoked"))
    assert response.decision is None
    assert "not authorized" in response.reason
    assert "send() returns its payload unchanged" not in response.reason


# --------------------------------------------------------------------------- #
# Failure, bounds and replay
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_backend_outage_reports_unknown_freshness_without_a_receipt(host):
    adapter, repo, _ = host
    await adapter.on_session_start(payload("SessionStart", repo))
    before = len(adapter.trace.events(native_session_id=NATIVE))

    async def unavailable(*_a, **_k):
        raise ServiceUnavailable("isolated backend down")

    original = adapter.engine.claims.load
    adapter.engine.claims.load = unavailable
    try:
        response = await adapter.on_pre_tool_use(edit_payload(repo, attempt="toolu_outage"))
    finally:
        adapter.engine.claims.load = original
    assert response.decision is None
    assert "freshness unknown" in response.reason
    recorded = adapter.trace.events(native_session_id=NATIVE)[before:]
    check = [e for e in recorded if e["event"] == "action_check"][0]
    assert check["gate"] == "advisory"
    assert check["insertion"] == "none"  # no fabricated acceptance


@pytest.mark.asyncio
async def test_outage_preserves_an_unacknowledged_correction_and_still_requires_replan(host):
    """A correction whose insertion failed must survive the backend going away.

    Insertion failure is the case that matters: the host never took the
    correction, so it is still pending, and an outage must not let the stale
    pending edit through as merely "unknown freshness".
    """
    adapter, repo, _ = host
    await adapter.on_session_start(payload("SessionStart", repo))
    (repo / "api.py").write_text("def send(payload, retries=3):\n    return payload\n")

    def refuse(_receipt):
        raise ValueError("host refused the correction insertion")

    accepted = adapter.engine.ack_delivery
    adapter.engine.ack_delivery = refuse
    try:
        denied = await adapter.on_pre_tool_use(edit_payload(repo, attempt="toolu_known"))
    finally:
        adapter.engine.ack_delivery = accepted
    assert denied.decision == "deny"
    failed = [e for e in adapter.trace.events(native_session_id=NATIVE)
              if e["attempt_id"] == "toolu_known"][0]
    assert failed["insertion"] == "failed"  # never recorded as accepted

    async def unavailable(*_a, **_k):
        raise ServiceUnavailable("isolated backend down")

    original = adapter.engine.claims.load
    adapter.engine.claims.load = unavailable
    try:
        during = await adapter.on_pre_tool_use(edit_payload(repo, attempt="toolu_known_outage"))
    finally:
        adapter.engine.claims.load = original
    # A known correction must not be downgraded to unknown coverage.
    assert during.decision == "deny"


@pytest.mark.asyncio
async def test_adapter_deadline_fails_open_with_an_explicit_advisory(host):
    adapter, repo, _ = host
    await adapter.on_session_start(payload("SessionStart", repo))
    adapter.deadline_seconds = 0.01

    async def slow(_request):
        await asyncio.sleep(1.0)

    original = adapter.engine.check_action
    adapter.engine.check_action = slow
    try:
        response = await adapter.on_pre_tool_use(edit_payload(repo, attempt="toolu_slow"))
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
    await adapter.on_session_start(payload("SessionStart", repo))
    (repo / "api.py").write_text("def send(payload, retries=3):\n    return payload\n")
    first = await adapter.on_pre_tool_use(edit_payload(repo, attempt="toolu_replay"))
    second = await adapter.on_pre_tool_use(edit_payload(repo, attempt="toolu_replay"))
    assert first.decision == second.decision == "deny"
    assert first.reason == second.reason


@pytest.mark.asyncio
async def test_bounded_reconsideration_stops_retrying_the_same_mutation(host):
    adapter, repo, _ = host
    await adapter.on_session_start(payload("SessionStart", repo))
    (repo / "api.py").write_text("def send(payload, retries=3):\n    return payload\n")
    reasons = []
    for index in range(4):
        (repo / "api.py").write_text(f"def send(payload, retries={index}):\n    return payload\n")
        response = await adapter.on_pre_tool_use(edit_payload(repo, attempt=f"toolu_loop{index}"))
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
    await adapter.on_session_start(payload("SessionStart", repo))
    (repo / "api.py").write_text("def send(payload, retries=3):\n    return payload\n")
    await adapter.on_pre_tool_use(edit_payload(repo, attempt="toolu_trace"))
    await adapter.on_post_tool_use(payload("PostToolUse", repo, tool_name="Edit",
                                           tool_use_id="toolu_trace",
                                           tool_input={"file_path": str(repo / "api.py")}))
    adapter.trace.record_objective("toolu_trace", "passed", "fixture assertion")

    events = adapter.trace.events(native_session_id=NATIVE)
    start = [e for e in events if e["event"] == "session_start"][0]
    check = [e for e in events if e["event"] == "action_check"][0]
    executed = [e for e in events if e["event"] == "action_executed"][0]

    assert start["insertion"] == "accepted"      # delivery exposure
    assert check["gate"] == "prevented"          # what the adapter did
    assert check["objective"] == "passed"        # externally checked result
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
    await adapter.on_session_start(payload("SessionStart", repo))

    # Only api.py drifts.
    (repo / "api.py").write_text("def send(payload, retries=3):\n    return payload\n")

    stale = await adapter.on_pre_tool_use(edit_payload(repo, attempt="batch_api"))
    sibling = await adapter.on_pre_tool_use(edit_payload(
        repo, target="other.py", attempt="batch_other",
        old="def other():", new="def other(flag):"))
    assert stale.decision == "deny"
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
    await adapter.on_session_start(payload("SessionStart", repo))
    before = len(adapter.trace.events(native_session_id=NATIVE))

    async def cancelled(_request):
        raise asyncio.CancelledError()

    original = adapter.engine.check_action
    adapter.engine.check_action = cancelled
    try:
        with pytest.raises(asyncio.CancelledError):
            await adapter.on_pre_tool_use(edit_payload(repo, attempt="toolu_cancel"))
    finally:
        adapter.engine.check_action = original

    # Cancellation is not an outcome: nothing was recorded as checked or accepted.
    recorded = adapter.trace.events(native_session_id=NATIVE)[before:]
    assert not [e for e in recorded if e["insertion"] == "accepted"]
    assert not [e for e in recorded if e["gate"] in ("allowed", "prevented")]


@pytest.mark.asyncio
async def test_the_adapter_only_ever_subtracts_permission(host):
    """No path may emit `allow` or `defer`.

    `allow` would bypass the user's permission policy on a memex outage, and a
    measured `defer` ends a non-interactive turn with `tool_deferred` instead of
    failing open. Denial is the only decision memex is entitled to make.
    """
    adapter, repo, _ = host
    await adapter.on_session_start(payload("SessionStart", repo))
    emitted = []
    # A proceed, an opaque action, an out-of-scope target, and a real correction.
    emitted.append(await adapter.on_pre_tool_use(edit_payload(repo, attempt="p1")))
    emitted.append(await adapter.on_pre_tool_use(payload(
        "PreToolUse", repo, tool_name="Bash", tool_use_id="p2",
        tool_input={"command": "true"})))
    (repo / "api.py").write_text("def send(payload, retries=3):\n    return payload\n")
    emitted.append(await adapter.on_pre_tool_use(edit_payload(repo, attempt="p3")))

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
