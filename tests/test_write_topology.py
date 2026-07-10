"""Phase 03 (NET-12/13/14) — write-topology concurrent-safety validation.

Validates that the existing per-`(repo, module)` `asyncio.Lock` design in
`memex/mcp_server/tools_write.py` (`_decision_write_locks`/`_problem_write_locks`)
actually serializes the check-then-act critical section for `record_decision`/
`record_problem` under Topology A (one shared process, many concurrent
coroutines) — and that the fine-grained per-module lock keying still lets
DIFFERENT modules proceed in parallel (no unintended global serialization).

This file intentionally contains no production-code changes — it is
validation + documentation of existing behavior only (see
`.planning/phases/03-write-topology/03-01-PLAN.md`).

Tests 1-3 are unmarked (CI-runnable, no live Neo4j — a mocked `client.search`
with a deterministic `asyncio.sleep`-based interleaving proves lock ordering
without needing a real graph). Tests 4-5 are `@pytest.mark.integration` and
require a live Neo4j + `GEMINI_API_KEY`, same precondition as every other
integration test in this repo — they are not required to pass in a sandbox
with no Docker/Neo4j available.
"""

import asyncio
import uuid

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from memex.mcp_server import tools_write
from memex.mcp_server.tools_write import record_decision, record_problem
from memex.graph.client import get_graph_client, reset_graph_client  # noqa: F401 — re-exported for integration tests


@pytest.fixture(autouse=True)
def _clear_write_locks():
    """Hermetic isolation: clear the module-level lock dicts before (and
    after) every test so no test's lock state can leak into another's."""
    tools_write._decision_write_locks.clear()
    tools_write._problem_write_locks.clear()
    yield
    tools_write._decision_write_locks.clear()
    tools_write._problem_write_locks.clear()


def _make_mock_client(call_order: list[str]) -> AsyncMock:
    """Build an AsyncMock graph client whose `.search` records a
    "check-start"/sleep/"check-end" triple into the shared `call_order` list
    — this is the deterministic interleaving probe. `.add_episode` returns a
    distinct fake uuid per call and `.driver.execute_query` is a no-op
    AsyncMock, so both concurrent calls complete without exceptions."""

    async def fake_search(text, num_results=10):
        call_order.append("check-start")
        await asyncio.sleep(0.01)  # yield control — this is where a race would show
        call_order.append("check-end")
        return []

    mock_client = AsyncMock()
    mock_client.search = fake_search
    mock_client.add_episode = AsyncMock(
        side_effect=[
            MagicMock(episode=MagicMock(uuid=f"fake-uuid-{i}")) for i in range(10)
        ]
    )
    mock_client.driver.execute_query = AsyncMock()
    return mock_client


@pytest.mark.asyncio
async def test_decision_lock_serializes_same_module_key():
    """Two concurrent record_decision() calls for the SAME (repo, module)
    key must serialize through the dedup check — proven by contiguous
    check-start/check-end pairs, not interleaved ones."""
    call_order: list[str] = []
    mock_client = _make_mock_client(call_order)

    with patch(
        "memex.mcp_server.tools_write.get_graph_client",
        return_value=mock_client,
    ):
        results = await asyncio.gather(
            record_decision(text="decision A" * 3, module="same_module.py", repo="."),
            record_decision(text="decision B" * 3, module="same_module.py", repo="."),
        )

    for r in results:
        assert r.startswith("decision recorded"), f"unexpected result: {r}"

    # If the lock serializes correctly, each call's check-start/check-end
    # pair is contiguous — interleaved "check-start, check-start, check-end,
    # check-end" would indicate the lock failed to serialize the critical
    # section for the same module key.
    assert call_order == ["check-start", "check-end", "check-start", "check-end"]


@pytest.mark.asyncio
async def test_problem_lock_serializes_same_module_key():
    """Twin of the above for record_problem() — record_problem calls
    client.search directly inside _get_problem_lock's `async with` block
    (no separate helper), so the same fake-search-with-sleep technique
    applies directly."""
    call_order: list[str] = []
    mock_client = _make_mock_client(call_order)

    with patch(
        "memex.mcp_server.tools_write.get_graph_client",
        return_value=mock_client,
    ):
        results = await asyncio.gather(
            record_problem(text="problem one report", module="same_module.py", repo="."),
            record_problem(text="problem two report", module="same_module.py", repo="."),
        )

    for r in results:
        assert r.startswith("problem recorded"), f"unexpected result: {r}"

    assert call_order == ["check-start", "check-end", "check-start", "check-end"]


@pytest.mark.asyncio
async def test_decision_locks_do_not_serialize_different_modules():
    """Concurrent record_decision() calls for DIFFERENT module keys must
    NOT serialize against each other — the per-module lock granularity is
    a deliberate design choice (ARCHITECTURE-v0.3.0.md §15: "a global lock
    would serialize all agent writes across all modules, unacceptable
    latency"). This directly validates that fine-grained locking still
    delivers the intended parallelism."""
    call_order: list[str] = []
    mock_client = _make_mock_client(call_order)

    with patch(
        "memex.mcp_server.tools_write.get_graph_client",
        return_value=mock_client,
    ):
        results = await asyncio.gather(
            record_decision(text="decision for module A", module="module_a.py", repo="."),
            record_decision(text="decision for module B", module="module_b.py", repo="."),
        )

    for r in results:
        assert r.startswith("decision recorded"), f"unexpected result: {r}"

    # Both calls must have entered their critical section (appended
    # "check-start") before either exited ("check-end") — i.e. the calls
    # interleave rather than serialize. If the lock were incorrectly
    # shared across different module keys, call_order would instead be
    # the fully-serialized ["check-start", "check-end", "check-start",
    # "check-end"] pattern seen in the same-module tests above.
    assert len(call_order) == 4
    assert call_order[1] != "check-end", (
        f"expected different-module calls to interleave, got serialized order: {call_order}"
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_concurrent_record_decision_no_duplicates():
    """Live-Neo4j proof: N concurrent record_decision() calls for the SAME
    (repo, module) key produce exactly ONE written Decision node — not N.
    Requires a live Neo4j + GEMINI_API_KEY (same precondition as every
    other @pytest.mark.integration test in this repo); run locally via
    `docker compose -f docker/docker-compose.yml up -d`.
    """
    await reset_graph_client()
    text = "concurrent-write-safety probe: switch retry backoff to exponential"
    # Unique module name per invocation avoids dedup collisions against
    # leftover nodes from prior runs.
    module = f"topology_test_{uuid.uuid4().hex[:8]}.py"

    results = await asyncio.gather(*[
        record_decision(text=text, module=module, repo=".")
        for _ in range(10)
    ])

    written = [r for r in results if r.startswith("decision recorded")]
    deduped = [r for r in results if r.startswith("similar decision already exists")]

    assert len(written) == 1, f"expected exactly 1 write, got {len(written)}: {results}"
    assert len(deduped) == len(results) - 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_concurrent_record_problem_no_duplicates():
    """Live-Neo4j proof: N concurrent record_problem() calls for the SAME
    (repo, module) key produce exactly ONE written Problem node — not N."""
    await reset_graph_client()
    text = "concurrent-write-safety probe: watcher daemon leaks file descriptors"
    module = f"topology_test_{uuid.uuid4().hex[:8]}.py"

    results = await asyncio.gather(*[
        record_problem(text=text, module=module, repo=".")
        for _ in range(10)
    ])

    written = [r for r in results if r.startswith("problem recorded")]
    deduped = [r for r in results if r.startswith("similar problem already recorded")]

    assert len(written) == 1, f"expected exactly 1 write, got {len(written)}: {results}"
    assert len(deduped) == len(results) - 1
