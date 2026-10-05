"""W12 native gate: a real Codex app-server session reconsiders a stale mutation.

The same discriminating fixture as Phase 3, now through the Codex adapter:

1. the session receives a packet, confirmed from its own rollout,
2. validate()'s failure contract changes after the agent reads api.py,
3. the agent proposes an api.py patch from its earlier context,
4/5. memex checks it and the pending patch does not execute,
6. the correction reaches that thread, confirmed and bound to its turn,
7. the agent re-reads validate.py and proposes a semantically different patch,
8. the revision executes and passes an objective contract that the original
   proposal, reconstructed and run in isolation, fails.

Opt-in: it drives the installed codex-cli and consumes a real model turn.
"""
import asyncio
import json
import os
import pathlib
import shutil
import sqlite3

import pytest
import pytest_asyncio
from graphiti_core.driver.neo4j_driver import Neo4jDriver

from memex.context.live import ClaimRevision, EvidenceRef
from memex.integrations import codex
from memex.runtime.coordinator import RepositoryIndexer
from memex.runtime.graph import StructuralGraphStore
from memex.runtime.supports import ClaimStore
from memex.runtime.trace import TraceStore
from memex.runtime.views import discover_repository
from tests import phase4_support as support

pytestmark = pytest.mark.integration

PROMPT = (
    "Implement send(payload) in api.py. It must return {'ok': True, 'value': payload} for a "
    "usable payload and {'ok': False, 'error': 'invalid'} for an unusable one, and it must not "
    "raise. Use validate() from validate.py. Start from the memex engineering context already "
    "provided for validate()'s contract instead of reading validate.py up front, but do re-read "
    "it if memex tells you it changed. Edit only api.py, using apply_patch. You may read files "
    "with read-only shell commands; do not run or test code, and do not write files with shell commands."
)


async def seed(repo: pathlib.Path, uri: str):
    registration = discover_repository(repo)
    driver = await asyncio.to_thread(Neo4jDriver, uri, None, None)
    await RepositoryIndexer(registration, StructuralGraphStore(driver)).refresh()
    claims = ClaimStore(driver)
    for name in ("api.py", "validate.py"):
        await claims.put_evidence(EvidenceRef(
            evidence_id=f"e-{name}", repo_id=registration.repo_id, path=name,
            content_hash=support.digest(repo / name), source_kind="source", observed_at=1.0))
    await claims.put_claim(ClaimRevision(
        claim_id="c-validate-returns-false", revision_id="r1", repo_id=registration.repo_id,
        assertion=support.ASSERTION, authority="inferred",
        support_sets=(("e-api.py", "e-validate.py"),), observed_at=1.0))
    await driver.close()
    return registration


@pytest_asyncio.fixture(loop_scope="function")
async def native(tmp_path):
    if os.getenv("MEMEX_PHASE4_NATIVE") != "1":
        pytest.skip("native host run is opt-in; set MEMEX_PHASE4_NATIVE=1")
    uri = os.getenv("MEMEX_PHASE1_NEO4J_URI")
    if not uri:
        pytest.skip("isolated native Neo4j required")
    if shutil.which("codex") is None:
        pytest.skip("Codex CLI not installed")
    repo = support.fixture_repo(tmp_path / "repo")
    registration = await seed(repo, uri)
    codex.register(repo, "owner", backend={"neo4j_uri": uri})
    launcher = codex.write_launcher(registration, pythonpath=str(support.CHECKOUT))
    yield repo, registration, launcher


@pytest.mark.asyncio
async def test_native_codex_semantically_reconsiders_a_stale_mutation(native, tmp_path):
    repo, registration, launcher = native
    state = tmp_path / "fixture-state"
    hooks = support.script_launcher(tmp_path / "bin", "phase4-hooks", support.FIXTURE_HOOKS)
    extra = {
        "PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "timeout": 30,
                         "command": support.fixture_command(hooks, "writer", state, "api.py", "codex")}]}],
        "PreToolUse": [{"matcher": "apply_patch", "hooks": [{"type": "command", "timeout": 30,
                        "command": support.fixture_command(hooks, "record", state)}]}],
    }
    api_before = (repo / "api.py").read_text()
    api_digest_before = support.digest(repo / "api.py")
    validate_before = support.digest(repo / "validate.py")

    server = support.AppServer(codex.session_flags(launcher, extra=extra),
                               log_path=tmp_path / "app-server.jsonl")
    try:
        thread_id = server.start_thread(repo)
        turn_id = server.start_turn(thread_id, PROMPT)
        turn = server.wait_turn(thread_id, turn_id)
        rollout = server.rollout(thread_id)
    finally:
        server.close()
    shutil.copyfile(rollout, tmp_path / "rollout.jsonl")
    assert turn["status"] == "completed", turn
    assert server.declined == [], "the harness was asked to approve something; nothing may be approved"

    # --- 2. the dependency contract changed after delivery
    assert support.digest(repo / "validate.py") != validate_before
    assert "raise ValueError" in (repo / "validate.py").read_text()

    trace = TraceStore(registration.runtime_path).events()
    assert trace, "memex hooks did not run; the thread never reached the adapter"
    assert {e["harness"] for e in trace} == {"codex"}

    # --- 1. the packet was delivered and confirmed from the thread's own rollout
    starts = [e for e in trace if e["event"] == "session_start"]
    assert starts and starts[0]["insertion"] == "prepared"
    session = starts[0]["native_session_id"]
    assert session == thread_id and all(e["native_session_id"] == session for e in trace)
    confirmed = [e for e in trace if e["event"] == "delivery" and e["insertion"] == "confirmed"]
    assert starts[0]["sequence"] in [e["sequence"] for e in confirmed], "initial packet never confirmed"

    # --- 4/5. the affected patch was checked and prevented
    checks = [e for e in trace if e["event"] == "action_check"]
    prevented = [e for e in checks if e["gate"] == "prevented"]
    assert prevented, f"no mutation was prevented; gates={[e['gate'] for e in checks]}"
    denial = prevented[0]
    assert denial["outcome"] in ("replan", "resync_required")
    assert "api.py" in json.loads(denial["targets"])
    assert json.loads(denial["observed_hashes"]).get("api.py") == api_digest_before
    executed = [e for e in trace if e["event"] == "action_executed"]
    assert all(e["attempt_id"] != denial["attempt_id"] for e in executed), "the denied patch executed"
    assert all(e["ordering"] > denial["ordering"] for e in executed)

    # --- 6. the correction reached the thread, and memex confirmed that insertion
    correction = [e for e in confirmed if e["sequence"] == denial["sequence"]]
    assert correction and correction[0]["attempt_id"] == denial["attempt_id"]
    db = sqlite3.connect(registration.runtime_path)
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM live_adapter_deliveries WHERE task_id=? AND sequence=?",
                     (denial["task_id"], denial["sequence"])).fetchone()
    db.close()
    records = support.rollout_records(rollout)
    index = support.denial_index(records, row["marker"])
    assert index is not None, "the correction never entered the model-visible history"
    passthrough = records[index]["payload"].get("internal_chat_message_metadata_passthrough") or {}
    assert row["binding"] == passthrough.get("turn_id") == turn_id

    # --- 7. the dependency was re-read after the correction, then a linked revision followed
    later = support.rollout_texts_after(records, index)
    assert any("validate.py" in text for text in later), "the agent never re-read the changed dependency"
    assert denial["reconsidered"] == 1
    revised = [e for e in checks if e["original_attempt_id"] == denial["attempt_id"]]
    assert revised and all(e["attempt_id"] != denial["attempt_id"] for e in revised)

    # --- 8. the objective contract separates the two implementations
    proposal = [p for p in support.proposals(state) if p["tool_use_id"] == denial["attempt_id"]]
    assert proposal, "the denied proposal was not recorded"
    original = support.proposed_api(proposal[0]["tool"], proposal[0]["input"], api_before)
    before = support.run_contract(repo, original, tmp_path, "proposed")
    assert before.returncode != 0, f"the denied proposal still passes; fixture does not discriminate\n{original}"
    assert "ValueError" in before.stderr or "AssertionError" in before.stderr
    shipped = (repo / "api.py").read_text()
    after = support.run_contract(repo, shipped, tmp_path, "shipped")
    assert after.returncode == 0, f"shipped implementation fails\n{shipped}\n{after.stderr}"
    assert shipped != original
    assert "raise ValueError" in (repo / "validate.py").read_text()

    TraceStore(registration.runtime_path).record_objective(
        revised[0]["attempt_id"], "passed",
        "contract_check passes for the shipped implementation and fails for the denied proposal")
