"""W16 arms: each arm's decision at the same boundaries, on the real runtime.

Real Git, SQLite and the native Neo4j fixture; hook events are simulated with
the payload shape Claude Code sends, so these tests establish the arms' rules,
not any agent's behavior. The initial packet's delivery is acknowledged through
the core directly, standing in for the transcript evidence a real host supplies.
"""
import os
import pathlib

import pytest
import pytest_asyncio

from memex.context.live import DeliveryReceipt
from memex.evaluation import fixtures
from memex.evaluation.arms import NoInvalidationEngine, adapter_class, set_arm
from memex.integrations.claude_code import ClaudeCodeAdapter, register
from memex.integrations.host_adapter import build_engine
from memex.runtime.views import discover_repository

pytestmark = pytest.mark.integration

D01 = fixtures.history_by_id("D01")   # affected: the dependency's contract changes in place
D05 = fixtures.history_by_id("D05")   # stable: an unrelated file changes
D06 = fixtures.history_by_id("D06")   # stable: one alternative support changes, the other holds


class Session:
    def __init__(self, adapter, repo):
        self.adapter, self.repo, self.native, self.count = adapter, repo, "native-1", 0

    def payload(self, event, **extra):
        return {"hook_event_name": event, "session_id": self.native, "cwd": str(self.repo), **extra}

    async def start(self):
        response = await self.adapter.dispatch(self.payload("SessionStart", source="startup"))
        binding = self.adapter._binding(self.native)
        session = self.adapter.session_identity(self.native)
        state = self.adapter.engine.tasks.get(binding["task_id"], session, now=self.adapter.clock())
        if state.pending is not None:  # what a host's own insertion record would confirm
            self.adapter.engine.ack_delivery(DeliveryReceipt(
                session=session, task_id=state.task_id, sequence=state.pending.sequence,
                view_id=state.pending.view_id, adapter_version="test", accepted_at=1.0, outcome="host_accepted"))
        return response

    async def edit(self, history):
        self.count += 1
        target = self.repo / history.spec.target
        return await self.adapter.dispatch(self.payload(
            "PreToolUse", tool_name="Edit", tool_use_id=f"toolu_{self.count}",
            tool_input={"file_path": str(target), "old_string": "raise", "new_string": "return None  #"}))

    async def executed(self, history):
        target = self.repo / history.spec.target
        return await self.adapter.dispatch(self.payload(
            "PostToolUse", tool_name="Edit", tool_use_id=f"toolu_{self.count}",
            tool_input={"file_path": str(target)}, tool_response={}))


@pytest_asyncio.fixture(loop_scope="function")
async def world(tmp_path):
    uri = os.getenv("MEMEX_PHASE1_NEO4J_URI")
    if not uri:
        pytest.skip("isolated native Neo4j required")

    async def make(history, arm):
        made = fixtures.materialize(history, tmp_path / f"{history.history_id}-{arm}")
        repo = pathlib.Path(made["repo"])
        await fixtures.seed_graph(history, repo, uri)
        registration = discover_repository(repo)
        set_arm(registration, arm)
        capability = register(repo, "owner")
        engine, driver = await build_engine(registration, capability, ClaudeCodeAdapter.HARNESS)
        if arm == "E-noinval":
            engine = NoInvalidationEngine(engine)
        adapter = adapter_class(ClaudeCodeAdapter, arm)(engine, capability)
        drivers.append(driver)
        return Session(adapter, repo), lambda: fixtures.apply_change(repo, made["plan"], uri=uri)

    drivers = []
    os.environ.setdefault("MEMEX_LIVE_NEO4J_URI", uri)
    yield make
    for driver in drivers:
        await driver.close()


ARM_RULES = {
    # arm: (denied before the change, denied after it, denied on the retry)
    "B": (False, False, False),
    "C": (False, True, False),
    "D": (False, True, False),
    "E": (False, True, None),       # the retry is the core's call: a resynchronizing re-check may hold again
    "E-noinval": (False, False, False),
    "E-norecon": (False, False, False),
    "E-full": (False, True, None),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("arm", sorted(ARM_RULES))
async def test_each_arm_decides_by_its_rule_when_the_contract_changes(world, arm):
    session, change = await world(D01, arm)
    start = await session.start()
    if arm == "D":
        assert "Note 1:" in start.additional_context and "validate.py@" in start.additional_context
    elif arm != "B":
        assert D01.spec.assertion[:40] in start.additional_context
    before, = [(await session.edit(D01)).decision == "deny"]
    change()
    after_response = await session.edit(D01)
    retry, = [(await session.edit(D01)).decision == "deny"]
    expected = ARM_RULES[arm]
    assert before is expected[0]
    assert (after_response.decision == "deny") is expected[1], after_response.reason
    if expected[2] is not None:
        assert retry is expected[2]
    if arm == "C":
        assert "fresh retrieval" in after_response.reason
    if arm == "D":
        assert "validate.py" in after_response.reason and "Re-read" in after_response.reason
    if arm == "E-full":
        assert "full current working set" in after_response.reason
    if arm == "E-norecon":
        deferred = await session.executed(D01)
        assert "outcome=replan" in deferred.additional_context, "the correction follows the action"


@pytest.mark.asyncio
@pytest.mark.parametrize("arm", ["C", "D", "E"])
async def test_an_unrelated_change_interrupts_no_arm(world, arm):
    session, change = await world(D05, arm)
    await session.start()
    change()
    assert (await session.edit(D05)).decision is None


@pytest.mark.asyncio
@pytest.mark.parametrize("arm,interrupts", [("C", False), ("D", True), ("E", False)])
async def test_a_surviving_alternative_support_separates_hash_notes_from_live_context(world, arm, interrupts):
    """F05: one alternative support changed; the claim still holds. Only D cannot tell."""
    session, change = await world(D06, arm)
    await session.start()
    change()
    assert ((await session.edit(D06)).decision == "deny") is interrupts
