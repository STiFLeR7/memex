"""P4 native gate: Claude Code and Codex live together in ONE checkout.

Both clients run at the same time against the same files. Claude implements
send() in api.py; Codex implements relay() in relay.py. Both receive the same
claim about validate()'s contract. Each session is held by a barrier after it
reads its own target, so both are demonstrably live with their initial packets.
Then the concurrent contributor changes validate()'s failure contract, and the
sessions are released in the order under test.

Each host must independently: have its pending mutation prevented, receive and
confirm its own correction (the other host's acknowledgement suppresses
nothing), re-read the changed dependency, and ship a revision that passes its
objective contract while its original proposal fails it.

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
from tests.phase4_fixture_hooks import CHANGED

pytestmark = pytest.mark.integration

HOSTS = {
    "claude": {"harness": "claude_code", "target": "api.py", "contract": "contract_check.py"},
    "codex": {"harness": "codex", "target": "relay.py", "contract": "relay_check.py"},
}


@pytest_asyncio.fixture(loop_scope="function")
async def checkout(tmp_path, monkeypatch):
    if os.getenv("MEMEX_PHASE4_NATIVE") != "1":
        pytest.skip("native host run is opt-in; set MEMEX_PHASE4_NATIVE=1")
    support.select_clients(monkeypatch)
    uri = os.getenv("MEMEX_PHASE1_NEO4J_URI")
    if not uri:
        pytest.skip("isolated native Neo4j required")
    if shutil.which("codex") is None or shutil.which("claude") is None:
        pytest.skip("both native clients are required")
    repo = support.fixture_repo(tmp_path / "repo")
    (repo / "relay.py").write_text(harness.RELAY, newline="\n")
    (repo / "relay_check.py").write_text(harness.RELAY_CONTRACT, newline="\n")
    support.git(repo, "add", ".")
    support.git(repo, "-c", "user.email=p4@memex.test", "-c", "user.name=p4", "commit", "-qm", "relay")
    registration = discover_repository(repo)
    driver = await asyncio.to_thread(Neo4jDriver, uri, None, None)
    await RepositoryIndexer(registration, StructuralGraphStore(driver)).refresh()
    claims = ClaimStore(driver)
    await claims.put_evidence(EvidenceRef(evidence_id="e-validate", repo_id=registration.repo_id,
                                          path="validate.py", content_hash=support.digest(repo / "validate.py"),
                                          source_kind="source", observed_at=1.0))
    await claims.put_claim(ClaimRevision(
        claim_id="c-validate-returns-false", revision_id="r1", repo_id=registration.repo_id,
        assertion=support.ASSERTION, authority="inferred", support_sets=(("e-validate",),), observed_at=1.0))
    await driver.close()
    register_claude(repo, "owner")
    codex.register(repo, "owner", backend={"neo4j_uri": uri})
    yield repo, registration, codex.write_launcher(registration, pythonpath=str(support.CHECKOUT))


@pytest.mark.asyncio
@pytest.mark.parametrize("order", [("claude", "codex"), ("codex", "claude")], ids=["claude-first", "codex-first"])
async def test_both_hosts_live_in_one_checkout_are_corrected_independently(checkout, tmp_path, order):
    repo, registration, launcher = checkout
    state = tmp_path / "state"
    hooks = support.script_launcher(tmp_path / "bin", "phase4-hooks", support.FIXTURE_HOOKS)
    harness.claude_settings(repo, hooks, state, "api.py", barrier="claude")
    flags = codex.session_flags(launcher, extra=harness.codex_extra(hooks, state, "relay.py", barrier="codex"))
    originals = {h: (repo / spec["target"]).read_text() for h, spec in HOSTS.items()}

    server = support.AppServer(flags, log_path=tmp_path / "app-server.jsonl")
    claude = None
    try:
        thread_id = server.start_thread(repo)
        turn_id = server.start_turn(thread_id, harness.CODEX_PROMPT)
        claude = harness.ClaudeRun(repo, harness.CLAUDE_PROMPT, tmp_path / "claude-stream.jsonl")

        def codex_alive():
            done = [m for m in server.messages if m.get("method") == "turn/completed"]
            assert not done or done[0]["params"]["turn"].get("status") == "completed", \
                f"codex turn failed before its barrier: {done[0]['params']['turn'].get('error')}"
            return not done

        ready = harness.wait_ready(state, ["claude", "codex"],
                                   alive=[("claude", claude.alive), ("codex", codex_alive)])
        # Both sessions are live and holding their packets. Now the contributor acts.
        (repo / "validate.py").write_text(CHANGED, newline="\n")
        changed_at = time.time()

        first, second = order
        released = {first: harness.release(state, first)}
        first_done = harness.wait_until(
            lambda: harness.executed_on(registration, HOSTS[first]["harness"], HOSTS[first]["target"]),
            what=f"{first}'s revised mutation")
        still_live = {"claude": claude.alive(), "codex": codex_alive()}
        assert still_live[second], f"{second} must still be a live session when {first} finishes its mutation"
        released[second] = harness.release(state, second)

        assert claude.wait() == 0, claude.stderr[-2000:]
        turn = server.wait_turn(thread_id, turn_id)
        rollout = server.rollout(thread_id)
    finally:
        server.close()
        if claude and claude.alive():
            claude.proc.kill()
    shutil.copyfile(rollout, tmp_path / "codex-rollout.jsonl")
    assert turn["status"] == "completed", turn
    assert server.declined == []

    records = claude.records()
    rereads = {"claude": lambda marker: harness.claude_reread(records, marker),
               "codex": lambda marker: harness.codex_reread(rollout, marker)}
    results = {}
    for host, spec in HOSTS.items():
        results[host] = harness.assert_reconsidered(
            host=spec["harness"], registration=registration, repo=repo, target=spec["target"],
            original=originals[host], contract=spec["contract"], scratch=tmp_path, state=state,
            reread=rereads[host])

    # Both sessions were live together: each held its packet before either was corrected.
    first, second = order
    assert max(ready[h]["at"] for h in ready) < changed_at < min(results[h]["denial"]["at"] for h in results)
    # And the release order is the action order: the second host's correction came
    # after the first host's revised mutation had already executed.
    assert results[second]["denial"]["ordering"] > first_done["ordering"]
    # Independent streams: two hosts, two tasks, each correction confirmed in its own ledger.
    denials = {h: r["denial"] for h, r in results.items()}
    assert denials["claude"]["task_id"] != denials["codex"]["task_id"]
    assert {r["harness"] for r in harness.events(registration)} == {"claude_code", "codex"}
    assert "raise ValueError" in (repo / "validate.py").read_text(), "the contributor's change survived"
    (tmp_path / "order.json").write_text(json.dumps({"order": order, "ready": ready, "released": released,
                                                     "changed_at": changed_at}))
