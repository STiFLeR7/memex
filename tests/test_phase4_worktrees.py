"""W13: worktree isolation and integration on real linked worktrees.

Two worktrees of one repository (`git worktree add`), one engine per worktree,
one shared control plane in the Git common directory, and the native Neo4j
graph. Integration is real Git: merge, conflict resolution, cherry-pick and
rebase. Nothing here is mocked; the claim is about views, not about hosts.
"""
import asyncio
import json
import os
import subprocess

import pytest
import pytest_asyncio
from graphiti_core.driver.neo4j_driver import Neo4jDriver

from memex.context.live import (
    ActionRequest, ClaimRevision, DeliveryReceipt, EvidenceRef, OpenTaskRequest, SessionIdentity,
)
from memex.runtime.actions import LiveContextEngine
from memex.runtime.coordinator import RepositoryIndexer
from memex.runtime.graph import StructuralGraphStore
from memex.runtime.guard import sha256
from memex.runtime.supports import ClaimStore
from memex.runtime.tasks import TaskStore
from memex.runtime.views import discover_repository

pytestmark = pytest.mark.integration

BASE = "def send(payload):\n    return payload\n"
CALLER = "from api import send\n\n\ndef relay(x):\n    return send(x)\n"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)


def commit(repo, message):
    result = git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", message)
    assert result.returncode == 0, result.stderr


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


@pytest_asyncio.fixture(loop_scope="function")
async def world(tmp_path):
    uri = os.getenv("MEMEX_PHASE1_NEO4J_URI")
    if not uri:
        pytest.skip("isolated native Neo4j required")
    a = tmp_path / "main"
    a.mkdir()
    assert git(tmp_path, "init", "-q", "-b", "main", str(a)).returncode == 0
    git(a, "config", "core.autocrlf", "false")  # byte-exact views on Windows
    (a / "api.py").write_text(BASE, newline="\n")
    (a / "caller.py").write_text(CALLER, newline="\n")
    git(a, "add", ".")
    commit(a, "base")
    b = tmp_path / "feature"
    assert git(a, "worktree", "add", "-q", "-b", "feature", str(b)).returncode == 0

    reg_a, reg_b = discover_repository(a), discover_repository(b)
    assert reg_a.repo_id == reg_b.repo_id and reg_a.worktree_id != reg_b.worktree_id
    assert reg_a.runtime_path == reg_b.runtime_path, "one control plane, in the common directory"

    driver = await asyncio.to_thread(Neo4jDriver, uri, None, None)
    store = StructuralGraphStore(driver)
    claims = ClaimStore(driver)
    tasks = TaskStore(reg_a.runtime_path, authenticate=lambda s: s.principal_id == "owner")
    clock = Clock()

    def engine(reg):
        return LiveContextEngine(RepositoryIndexer(reg, store), claims, tasks, clock=clock,
                                 authorize_view=lambda s, r: True, authorize_source=lambda s, p: True)

    sessions = {
        "A": SessionIdentity(harness="claude_code", native_session_id="n-a", memex_session_id="cc-a",
                             principal_id="owner"),
        "B": SessionIdentity(harness="codex", native_session_id="n-b", memex_session_id="cx-b",
                             principal_id="owner"),
    }
    yield {"A": (a, reg_a, engine(reg_a)), "B": (b, reg_b, engine(reg_b)),
           "claims": claims, "sessions": sessions, "clock": clock}
    await driver.close()


async def evidence(world, key, eid, path, *, scoped=True, **extra):
    repo, reg, _ = world[key]
    record = EvidenceRef(evidence_id=eid, repo_id=reg.repo_id, path=path, source_kind=extra.pop("kind", "source"),
                         content_hash=sha256((repo / path).read_bytes()), observed_at=1.0,
                         worktree_ids=(reg.worktree_id,) if scoped else (), **extra)
    await world["claims"].put_evidence(record, allow_approval=record.source_kind == "approval")
    return record


async def claim(world, cid, rid, text, support, *, worktree=None, authority="inferred", supersedes=()):
    reg = world["A"][1]
    worktrees = () if worktree is None else (world[worktree][1].worktree_id,)
    await world["claims"].put_claim(ClaimRevision(
        claim_id=cid, revision_id=rid, repo_id=reg.repo_id, assertion=text, authority=authority,
        support_sets=(support,), worktree_ids=worktrees, observed_at=1.0, supersedes=supersedes),
        allow_approval=authority == "human_approved")


async def open_task(world, key, intent="task"):
    repo, reg, engine = world[key]
    result = await engine.indexer.refresh()
    frame = await engine.open_task(OpenTaskRequest(session=world["sessions"][key], view=result.view, intent=intent))
    engine.ack_delivery(DeliveryReceipt(session=world["sessions"][key], task_id=frame.task_id,
                                        sequence=frame.sequence, view_id=frame.view_id, adapter_version="t",
                                        accepted_at=world["clock"](), outcome="host_accepted"))
    return frame


def items(frame):
    return {i.claim_id: i.status for i in frame.items}


async def check(world, key, frame, attempt, targets=("caller.py",)):
    repo, reg, engine = world[key]
    state = engine.tasks.get(frame.task_id, world["sessions"][key], now=world["clock"]())
    delivered = {}
    for item in state.baseline:
        for entry in item.verification.support_hashes:
            path, _, value = entry.partition("=")
            delivered[path] = value
    return await engine.check_action(ActionRequest(
        session=world["sessions"][key], task_id=frame.task_id, view=state.view, attempt_id=attempt,
        action_kind="Edit", targets=targets,
        expected_hashes=tuple((p, delivered[p]) for p in targets if p in delivered),
        last_acknowledged=state.ack_sequence, scope_complete=True, context_retained=True))


# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_same_path_different_api_gets_per_view_claims_and_no_leakage(world):
    """F08, and an unmerged edit in B leaves A's current facts alone."""
    (world["B"][0] / "api.py").write_text("def send(payload, timeout):\n    return payload\n", newline="\n")
    await evidence(world, "A", "e-api-a", "api.py")
    await evidence(world, "B", "e-api-b", "api.py")
    await evidence(world, "A", "e-api-base", "api.py", scoped=False)
    await claim(world, "c-arity-a", "a1", "send(payload) takes one argument", ("e-api-a",), worktree="A")
    await claim(world, "c-arity-b", "b1", "send() requires a timeout", ("e-api-b",), worktree="B")
    await claim(world, "c-shared", "s1", "send() returns its payload", ("e-api-base",))

    frame_a = await open_task(world, "A")
    frame_b = await open_task(world, "B")
    assert items(frame_a) == {"c-arity-a": "supported", "c-shared": "supported"}
    assert items(frame_b) == {"c-arity-b": "supported", "c-shared": "needs_revalidation"}
    assert frame_a.view.worktree_id != frame_b.view.worktree_id
    shared_a = next(i for i in frame_a.items if i.claim_id == "c-shared").verification
    shared_b = next(i for i in frame_b.items if i.claim_id == "c-shared").verification
    assert shared_a.view_id != shared_b.view_id and shared_a.support_hashes != shared_b.support_hashes

    # B's unmerged change does not reach A: A's affected action proceeds on A's facts.
    result = await check(world, "A", frame_a, "a-1", targets=("api.py",))
    assert result.outcome == "proceed", result.reason


@pytest.mark.asyncio
async def test_merge_into_a_revalidates_before_the_affected_action(world):
    """F09: integration triggers revalidation from A's actual merged contents."""
    await evidence(world, "A", "e-api", "api.py", scoped=False)
    await claim(world, "c-shared", "s1", "send() returns its payload unchanged", ("e-api",))
    frame_a = await open_task(world, "A")
    assert (await check(world, "A", frame_a, "a-before")).outcome == "proceed"

    b = world["B"][0]
    (b / "api.py").write_text("def send(payload):\n    return {'wrapped': payload}\n", newline="\n")
    commit(b, "wrap")
    assert git(world["A"][0], "merge", "-q", "feature").returncode == 0
    result = await check(world, "A", frame_a, "a-after")
    assert result.outcome == "replan", result.reason
    corrected = {c.item.claim_id: c.item.status for c in result.delta.changes if c.item}
    assert corrected == {"c-shared": "needs_revalidation"}
    merged = sha256((world["A"][0] / "api.py").read_bytes())
    assert any(h.endswith(merged) for c in result.delta.changes for h in c.item.verification.support_hashes)


@pytest.mark.asyncio
async def test_bs_test_result_does_not_certify_as_merge_even_with_identical_bytes(world):
    b = world["B"][0]
    (b / "api.py").write_text("def send(payload):\n    return payload  # tested\n", newline="\n")
    commit(b, "tested change")
    view_b = (await world["B"][2].indexer.refresh()).view
    await evidence(world, "B", "t-api", "api.py", scoped=False, kind="test", tested_view_id=view_b.view_id,
                   test_definition="tests/test_api.py::test_send", environment="py312", outcome="passed")
    await claim(world, "c-tested", "t1", "send() passes its test", ("t-api",))
    assert items(await open_task(world, "B")) == {"c-tested": "supported"}

    assert git(world["A"][0], "merge", "-q", "feature").returncode == 0
    assert (world["A"][0] / "api.py").read_bytes() == (b / "api.py").read_bytes()
    frame_a = await open_task(world, "A")
    assert items(frame_a) == {"c-tested": "needs_revalidation"}
    assert "test_scope_changed" in frame_a.items[0].reason


@pytest.mark.asyncio
async def test_conflict_resolution_is_verified_as_its_own_view(world):
    a, b = world["A"][0], world["B"][0]
    await evidence(world, "A", "e-api", "api.py", scoped=False)
    await claim(world, "c-shared", "s1", "send() returns its payload unchanged", ("e-api",))
    frame_a = await open_task(world, "A")

    (a / "api.py").write_text("def send(payload):\n    return payload.strip()\n", newline="\n")
    commit(a, "ours")
    (b / "api.py").write_text("def send(payload):\n    return payload.lower()\n", newline="\n")
    commit(b, "theirs")
    assert git(a, "merge", "-q", "feature").returncode != 0, "the fixture really conflicts"
    resolved = "def send(payload):\n    return payload.strip().lower()\n"
    (a / "api.py").write_text(resolved, newline="\n")
    git(a, "add", "api.py")
    commit(a, "resolve")

    result = await check(world, "A", frame_a, "a-resolved")
    assert result.outcome == "replan"
    hashes = {h for c in result.delta.changes for h in c.item.verification.support_hashes}
    assert hashes == {"api.py=" + sha256(resolved.encode())}, "verified against the resolution, not either side"


@pytest.mark.asyncio
async def test_cherry_pick_and_rebase_each_revalidate_the_receiving_view(world):
    a, b = world["A"][0], world["B"][0]
    await evidence(world, "A", "e-caller", "caller.py", scoped=False)
    await claim(world, "c-caller", "k1", "relay() forwards to send()", ("e-caller",))
    frame_a = await open_task(world, "A")

    (b / "caller.py").write_text(CALLER + "\n\ndef extra():\n    return 1\n", newline="\n")
    commit(b, "extra")
    picked = git(b, "rev-parse", "HEAD").stdout.strip()
    assert git(a, "cherry-pick", picked).returncode == 0
    assert (await check(world, "A", frame_a, "a-picked")).outcome == "replan"

    frame_b = await open_task(world, "B")
    (a / "api.py").write_text(BASE + "\n# main moved on\n", newline="\n")
    commit(a, "main moves")
    assert git(b, "-c", "user.email=t@t", "-c", "user.name=t", "rebase", "-q", "main").returncode == 0
    # The rebase did not change caller.py's bytes in B, so B's delivered status
    # for that claim still stands: no spurious correction for an unaffected claim.
    rebased = await check(world, "B", frame_b, "b-rebased")
    assert rebased.outcome != "replan" and rebased.delta is None, rebased.reason
    await evidence(world, "A", "e-api-main", "api.py", scoped=False)
    await claim(world, "c-api", "p1", "api.py carries the main-branch note", ("e-api-main",))
    assert items(await open_task(world, "B"))["c-api"] == "supported", "B now holds main's bytes after rebase"


@pytest.mark.asyncio
async def test_approved_rules_share_by_scope_and_proposals_never_promote(world):
    reg = world["A"][1]
    approval = EvidenceRef(evidence_id="approval-1", repo_id=reg.repo_id, source_kind="approval",
                           observed_at=1.0, approver="maintainer")
    await world["claims"].put_evidence(approval, allow_approval=True)
    await claim(world, "rule", "rule-1", "All network calls go through send()", ("approval-1",),
                authority="human_approved")
    await evidence(world, "B", "e-b", "api.py")
    with pytest.raises(ValueError):  # a branch-local proposal cannot supersede approved authority
        await claim(world, "rule", "rule-b", "Direct calls are fine", ("e-b",), worktree="B",
                    supersedes=("rule-1",))
    await claim(world, "proposal", "prop-b", "Direct calls are fine in this branch", ("e-b",), worktree="B")

    in_a, in_b = await open_task(world, "A"), await open_task(world, "B")
    assert items(in_a) == {"rule": "supported"}, "B's proposal is not A's fact"
    assert items(in_b) == {"rule": "supported", "proposal": "supported"}
    assert {i.claim_id: i.authority for i in in_b.items} == {"rule": "human_approved", "proposal": "inferred"}


@pytest.mark.asyncio
async def test_corrections_never_expose_another_tasks_scope(world):
    await evidence(world, "A", "e-api", "api.py", scoped=False)
    await claim(world, "c-shared", "s1", "send() returns its payload unchanged", ("e-api",))
    frame_a = await open_task(world, "A", intent="refactor the billing relay")
    frame_b = await open_task(world, "B", intent="SECRET-B: rewrite send for the payments launch")
    (world["A"][0] / "api.py").write_text("def send(payload):\n    return None\n", newline="\n")
    result = await check(world, "A", frame_a, "a-1")
    assert result.outcome == "replan"
    rendered = json.dumps(result.model_dump(mode="json"))
    for private in (frame_b.task_id, "SECRET-B", str(world["B"][0]), world["B"][1].worktree_id):
        assert private not in rendered
