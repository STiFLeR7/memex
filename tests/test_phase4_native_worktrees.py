"""P4 native gate: Claude Code and Codex live together in SEPARATE worktrees.

Worktree A (Claude) and worktree B (Codex) belong to one repository. Both
sessions receive the same claim about validate()'s contract and are held after
their first read, so both are live at once. Then, in the order under test:

* Codex, in B, changes validate()'s failure contract. That change is unmerged.
* Claude, in A, implements send(). Its edit must NOT be interrupted: B's
  unmerged observation is not A's current fact.

Claude is then held after that first edit while B's change is committed and
merged into A. Claude's next edit to api.py must be corrected against A's
actual merged contents, and its revision must pass the post-merge contract that
its denied proposal fails. B's own context is never replaced by A's.

Opt-in: drives the installed claude and codex clients with real model turns.
"""
import asyncio
import json
import os
import shutil
import time

import pytest
import pytest_asyncio
from graphiti_core.driver.neo4j_driver import Neo4jDriver

from memex.context.live import ClaimRevision, EvidenceRef
from memex.integrations import codex
from memex.integrations.claude_code import register as register_claude
from memex.runtime.coordinator import RepositoryIndexer
from memex.runtime.graph import StructuralGraphStore
from memex.runtime.supports import ClaimStore
from memex.runtime.views import discover_repository
from tests import phase4_native_harness as harness
from tests import phase4_support as support

pytestmark = pytest.mark.integration

CLAUDE_PROMPT = (
    "Implement send(payload) in api.py. It must return {'ok': True, 'value': payload} for a usable "
    "payload and {'ok': False, 'error': 'invalid'} for an unusable one, and it must not raise. Use "
    "validate() from validate.py. Start from the memex engineering context already provided for "
    "validate()'s contract instead of reading validate.py up front, but do re-read it if memex tells "
    "you it changed. Edit only api.py. Do not run any commands. Work in two separate edits: first "
    "implement send(); then, as a second separate edit, add a one-line docstring to send() that says "
    "what it returns for an unusable payload."
)

CODEX_PROMPT = (
    "In validate.py, change validate(payload) so that it raises ValueError('payload is unusable') for an "
    "unusable payload instead of returning False, and still returns True for a usable payload. Edit only "
    "validate.py, using apply_patch. You may read files with read-only shell commands; do not run or test "
    "code, and do not write files with shell commands."
)


@pytest_asyncio.fixture(loop_scope="function")
async def worktrees(tmp_path):
    if os.getenv("MEMEX_PHASE4_NATIVE") != "1":
        pytest.skip("native host run is opt-in; set MEMEX_PHASE4_NATIVE=1")
    uri = os.getenv("MEMEX_PHASE1_NEO4J_URI")
    if not uri:
        pytest.skip("isolated native Neo4j required")
    if shutil.which("codex") is None or shutil.which("claude") is None:
        pytest.skip("both native clients are required")
    a = support.fixture_repo(tmp_path / "main")
    support.git(a, "config", "core.autocrlf", "false")
    b = tmp_path / "feature"
    support.git(a, "worktree", "add", "-q", "-b", "feature", str(b))
    reg_a, reg_b = discover_repository(a), discover_repository(b)
    assert reg_a.repo_id == reg_b.repo_id and reg_a.worktree_id != reg_b.worktree_id

    driver = await asyncio.to_thread(Neo4jDriver, uri, None, None)
    store = StructuralGraphStore(driver)
    for registration in (reg_a, reg_b):
        await RepositoryIndexer(registration, store).refresh()
    claims = ClaimStore(driver)
    await claims.put_evidence(EvidenceRef(evidence_id="e-validate", repo_id=reg_a.repo_id, path="validate.py",
                                          content_hash=support.digest(a / "validate.py"),
                                          source_kind="source", observed_at=1.0))
    await claims.put_claim(ClaimRevision(
        claim_id="c-validate-returns-false", revision_id="r1", repo_id=reg_a.repo_id,
        assertion=support.ASSERTION, authority="inferred", support_sets=(("e-validate",),), observed_at=1.0))
    await driver.close()
    register_claude(a, "owner")
    codex.register(a, "owner", backend={"neo4j_uri": uri})  # one repository, one capability per host
    yield a, b, reg_a, reg_b, codex.write_launcher(reg_a, pythonpath=str(support.CHECKOUT))


def view_ids(registration, harness_name):
    return {e["checked_view_id"] for e in harness.events(registration, harness_name) if e["checked_view_id"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("order", [("claude", "codex"), ("codex", "claude")], ids=["claude-first", "codex-first"])
async def test_unmerged_change_stays_out_until_integration_then_corrects(worktrees, tmp_path, order):
    a, b, reg_a, reg_b, launcher = worktrees
    state = tmp_path / "state"
    hooks = support.script_launcher(tmp_path / "bin", "phase4-hooks", support.FIXTURE_HOOKS)
    harness.claude_settings(a, hooks, state, "api.py", barrier="claude")
    # A second barrier holds Claude after its first completed edit, for integration.
    settings_path = a / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text())
    settings["hooks"]["PostToolUse"].append({"matcher": "Edit|Write|MultiEdit", "hooks": [{
        "type": "command", "timeout": 1200,
        "command": support.fixture_command(hooks, "barrier", state, "claude-edit1", "api.py", 1170)}]})
    settings_path.write_text(json.dumps(settings, indent=2))
    flags = codex.session_flags(launcher, extra=harness.codex_extra(hooks, state, "validate.py", barrier="codex"))
    original_validate = (a / "validate.py").read_text()

    server = support.AppServer(flags, log_path=tmp_path / "app-server.jsonl")
    claude = None
    try:
        thread_id = server.start_thread(b)
        turn_id = server.start_turn(thread_id, CODEX_PROMPT)
        claude = harness.ClaudeRun(a, CLAUDE_PROMPT, tmp_path / "claude-stream.jsonl")

        def codex_alive():
            done = [m for m in server.messages if m.get("method") == "turn/completed"]
            assert not done or done[0]["params"]["turn"].get("status") == "completed", \
                f"codex turn failed: {done[0]['params']['turn'].get('error')}"
            return not done

        ready = harness.wait_ready(state, ["claude", "codex"],
                                   alive=[("claude", claude.alive), ("codex", codex_alive)])
        released = {}
        for host in order:
            released[host] = harness.release(state, host)
            if host == "codex":
                harness.wait_until(lambda: not codex_alive(), what="codex's unmerged change in B")
                assert "raise ValueError" in (b / "validate.py").read_text()
            else:
                harness.wait_ready(state, ["claude-edit1"], alive=[("claude", claude.alive)])
        assert claude.alive(), "Claude must still be live for integration"

        # Isolation, before integration: A's first edit went through, and A's bytes are A's.
        first_edit = [e for e in harness.events(reg_a, "claude_code") if e["event"] == "action_check"
                      and "api.py" in json.loads(e["targets"] or "[]")]
        assert first_edit and all(e["gate"] != "prevented" for e in first_edit), \
            f"B's unmerged change interrupted A: {[(e['gate'], e['reason']) for e in first_edit]}"
        assert (a / "validate.py").read_text() == original_validate
        pre_merge_api = (a / "api.py").read_text()
        assert support.run_contract(a, pre_merge_api, tmp_path, "pre-merge").returncode == 0

        # Integration: B's change is committed and merged into A, while Claude waits live.
        support.git(b, "-c", "user.email=p4@memex.test", "-c", "user.name=p4", "commit", "-qam", "raise on invalid")
        support.git(a, "-c", "user.email=p4@memex.test", "-c", "user.name=p4", "merge", "-q", "--no-edit", "feature")
        merged_at = time.time()
        assert "raise ValueError" in (a / "validate.py").read_text()
        harness.release(state, "claude-edit1")
        assert claude.wait() == 0, claude.stderr[-2000:]
        turn = server.wait_turn(thread_id, turn_id)
    finally:
        server.close()
        if claude and claude.alive():
            claude.proc.kill()
    assert turn["status"] == "completed" and server.declined == []

    records = claude.records()
    result = harness.assert_reconsidered(
        host="claude_code", registration=reg_a, repo=a, target="api.py", original=pre_merge_api,
        contract="contract_check.py", scratch=tmp_path, state=state,
        reread=lambda marker: harness.claude_reread(records, marker))
    assert result["denial"]["at"] > merged_at, "the correction came from integration, not from B's edit"
    assert support.run_contract(a, pre_merge_api, tmp_path, "pre-merge-after").returncode != 0, \
        "the pre-merge implementation is wrong for the merged contract"

    # B's own session was never corrected for A's work, and its view is its own.
    codex_rows = harness.events(reg_b, "codex")
    assert any(e["event"] == "action_executed" and "validate.py" in json.loads(e["targets"] or "[]")
               for e in codex_rows)
    assert not any(e["gate"] == "prevented" for e in codex_rows)
    assert view_ids(reg_a, "claude_code").isdisjoint(view_ids(reg_b, "codex"))
    assert max(r["at"] for r in ready.values()) < min(released.values())
    (tmp_path / "order.json").write_text(json.dumps({"order": order, "ready": ready, "released": released,
                                                     "merged_at": merged_at}))
