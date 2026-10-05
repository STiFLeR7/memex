"""Deterministic core-contract demo for contributors: `python -m memex.evaluation.core_demo`.

CORE-CONTRACT DEMO. In-memory test doubles stand in for Neo4j and for a host
client, so this needs no graph server, no model and no login. It runs the real
v1 core: Git capture, the change journal, Python parsing, support
verification, the task store and the action check. It is not a native-client
proof and not a real-graph proof; for those, see the mutation-before-edit
demo in docs/v1/25_ONBOARDING.md.

The scenario: an agent receives a contract for validate(), a teammate changes
validate() while the agent works, and the agent's next edit is held for
reconsideration with a correction. An unrelated change interrupts nothing.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
from hashlib import sha256
from pathlib import Path

from memex.context.live import (ActionRequest, ClaimRevision, DeliveryReceipt, EvidenceRef, OpenTaskRequest,
                                PacketBudget, SessionIdentity)
from memex.runtime.actions import LiveContextEngine
from memex.runtime.coordinator import RepositoryIndexer
from memex.runtime.tasks import TaskStore
from memex.runtime.views import discover_repository

VALIDATE = 'def validate(payload):\n    """Return False for an unusable payload."""\n    return bool(payload)\n'
CHANGED = ('def validate(payload):\n    """Raise ValueError for an unusable payload."""\n    if not payload:\n'
           '        raise ValueError("unusable")\n    return True\n')


class MemoryStructureStore:
    """Test double for the Neo4j structural store: remembers the published view per worktree."""

    def __init__(self):
        self.published: dict[tuple[str, str], str] = {}

    async def publish(self, view, contributions, *, fail_before_complete=False) -> bool:
        self.published[(view.repo_id, view.worktree_id)] = view.view_id
        return True

    async def published_view(self, repo_id: str, worktree_id: str) -> str | None:
        return self.published.get((repo_id, worktree_id))


class MemoryClaimStore:
    """Test double for the Neo4j claim store."""

    def __init__(self):
        self.revisions, self.evidence = {}, {}

    async def put_evidence(self, evidence, *, allow_approval=False):
        self.evidence[evidence.evidence_id] = evidence

    async def put_claim(self, claim, *, allow_approval=False):
        self.revisions[claim.revision_id] = claim

    async def load(self, repo_id, *, max_claims=256, max_evidence=1024):
        return ({k: v for k, v in self.revisions.items() if v.repo_id == repo_id},
                {k: v for k, v in self.evidence.items() if v.repo_id == repo_id}, True)

    async def save_verification(self, repo_id, principal_id, verification):
        return None


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=demo@memex", "-c", "user.name=demo", *args],
                   check=True, capture_output=True)


async def run(root: Path, out=print) -> dict:
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "validate.py").write_text(VALIDATE, newline="\n")
    (repo / "api.py").write_text("from validate import validate\n", newline="\n")
    (repo / "notes.md").write_text("notes\n", newline="\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    registration = discover_repository(repo)
    claims = MemoryClaimStore()
    await claims.put_evidence(EvidenceRef(
        evidence_id="e-validate", repo_id=registration.repo_id, source_kind="source", path="validate.py",
        content_hash="sha256:" + sha256(VALIDATE.encode()).hexdigest(), observed_at=1.0))
    await claims.put_claim(ClaimRevision(
        claim_id="c-validate", revision_id="r1", repo_id=registration.repo_id, authority="inferred",
        assertion="validate() returns False for an unusable payload and never raises",
        support_sets=(("e-validate",),), observed_at=1.0))
    engine = LiveContextEngine(RepositoryIndexer(registration, MemoryStructureStore()), claims,
                               TaskStore(registration.runtime_path, authenticate=lambda s: True),
                               authorize_view=lambda s, r: True, authorize_source=lambda s, p: True,
                               clock=lambda: 10.0)
    session = SessionIdentity(harness="demo", native_session_id="session-1", memex_session_id="demo-1",
                              principal_id="contributor")
    view = (await engine.indexer.refresh()).view
    frame = await engine.open_task(OpenTaskRequest(session=session, view=view, intent="implement send()",
                                                   budget=PacketBudget()))
    out("1. The agent receives its working set:")
    for item in frame.items:
        out(f"     [{item.status}] {item.assertion}")
    engine.ack_delivery(DeliveryReceipt(session=session, task_id=frame.task_id, sequence=frame.sequence,
                                        view_id=frame.view_id, adapter_version="demo-double", accepted_at=10.0,
                                        outcome="host_accepted"))
    out("   (a test double acknowledges delivery; a real host must show the packet in its own transcript)")

    async def check(attempt: str):
        state = engine.tasks.get(frame.task_id, session, now=10.0)
        return await engine.check_action(ActionRequest(
            session=session, task_id=frame.task_id, view=state.view, attempt_id=attempt, action_kind="Edit",
            targets=("api.py",), last_acknowledged=state.ack_sequence, scope_complete=True,
            context_retained=True))

    first = await check("edit-1")
    out(f"2. The agent's first edit of api.py is checked: {first.outcome} ({first.reason})")
    (repo / "notes.md").write_text("notes, reworded\n", newline="\n")
    unrelated = await check("edit-2")
    out(f"3. An unrelated file changes; the next edit: {unrelated.outcome} ({unrelated.reason})")
    (repo / "validate.py").write_text(CHANGED, newline="\n")
    out("4. A teammate changes validate() to raise instead of returning False.")
    stale = await check("edit-3")
    out(f"5. The agent's next edit of api.py: {stale.outcome} ({stale.reason})")
    for change in (stale.delta.changes if stale.delta else ()):
        out(f"     correction: {change.operation} -> [{change.item.status if change.item else '-'}] {change.reason}")
    out("   The edit is held for reconsideration before it executes; the agent re-reads validate.py.")
    return {"first": first.outcome, "unrelated": unrelated.outcome, "stale": stale.outcome,
            "corrections": len(stale.delta.changes) if stale.delta else 0}


def main() -> int:
    print(__doc__.split("\n\n")[1].replace("\n", " "))
    print()
    with tempfile.TemporaryDirectory(prefix="memex-core-demo-") as scratch:
        result = asyncio.run(run(Path(scratch)))
    ok = result == {"first": "proceed", "unrelated": "proceed", "stale": "replan", "corrections": 1}
    print("\ncore contract demo:", "as expected" if ok else f"UNEXPECTED {result}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
