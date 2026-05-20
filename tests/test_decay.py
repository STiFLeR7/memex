"""Tests for the v0.3.0 nightly maintenance scheduler.

The scheduler keeps its historical class name (``DecayScheduler``) for daemon
wiring compatibility, but it NO LONGER decays confidence. v0.2.0's stored
confidence decay was a latent no-op (``last_touched`` was never written). The
job now (a) refreshes the cached ``stale`` boolean and (b) tombstones cold
nodes via :mod:`memex.graph.archive`.
"""

from __future__ import annotations


import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from memex.graph.decay import DecayScheduler


@pytest.mark.asyncio
async def test_decay_task_no_longer_mutates_confidence():
    """The Cypher must NOT subtract from r.confidence anywhere."""
    scheduler = DecayScheduler()
    mock_client = AsyncMock()
    mock_driver = AsyncMock()
    mock_result = MagicMock()
    mock_result.records = [{"updated_count": 0}]
    mock_driver.execute_query.return_value = mock_result
    mock_client.driver = mock_driver

    with patch("memex.graph.decay.get_graph_client", return_value=mock_client):
        await scheduler.decay_task()

        # At least one query was executed (the stale refresh).
        assert mock_driver.execute_query.called

        # NONE of the executed queries may decrement confidence.
        for call in mock_driver.execute_query.call_args_list:
            query_str = call[0][0] if call[0] else ""
            assert "r.confidence - 0.01" not in query_str, (
                "decay_task must not subtract from r.confidence in v0.3.0"
            )
            assert "n.confidence - 0.01" not in query_str
            assert "SET r.confidence" not in query_str, (
                "decay_task must not write r.confidence in v0.3.0"
            )


@pytest.mark.asyncio
async def test_decay_task_refreshes_stale_boolean():
    """The maintenance job must materialise the ``stale`` cache."""
    scheduler = DecayScheduler()
    mock_client = AsyncMock()
    mock_driver = AsyncMock()
    mock_result = MagicMock()
    mock_result.records = [{"updated_count": 7}]
    mock_driver.execute_query.return_value = mock_result
    mock_client.driver = mock_driver

    with patch("memex.graph.decay.get_graph_client", return_value=mock_client):
        await scheduler.decay_task()

        # Look for the SET n.stale = ... line in at least one query.
        found_stale_set = False
        for call in mock_driver.execute_query.call_args_list:
            query_str = call[0][0] if call[0] else ""
            if "SET n.stale" in query_str:
                found_stale_set = True
                break
        assert found_stale_set, "decay_task must refresh n.stale boolean"


@pytest.mark.asyncio
async def test_decay_task_invokes_archive_tombstoning_per_active_repo():
    """The maintenance job iterates every active repo and calls
    tombstone_cold_nodes(repo.path) for each. Mocks the registry + the
    canonical archive symbol."""
    scheduler = DecayScheduler()
    mock_client = AsyncMock()
    mock_driver = AsyncMock()
    mock_result = MagicMock()
    mock_result.records = [{"updated_count": 0}]
    mock_driver.execute_query.return_value = mock_result
    mock_client.driver = mock_driver

    tombstone = AsyncMock(return_value=4)
    repo_a = MagicMock(path="/repo/a", active=True)
    repo_b = MagicMock(path="/repo/b", active=True)

    with (
        patch("memex.graph.decay.get_graph_client", return_value=mock_client),
        patch("memex.graph.archive.tombstone_cold_nodes", tombstone),
        patch("memex.watcher.registry.get_active_repositories",
              return_value=[repo_a, repo_b]),
    ):
        await scheduler.decay_task()

    # Called once per repo, with repo.path as the single positional arg.
    assert tombstone.await_count == 2
    awaited_paths = [c.args[0] for c in tombstone.await_args_list]
    assert "/repo/a" in awaited_paths
    assert "/repo/b" in awaited_paths


@pytest.mark.asyncio
async def test_decay_task_production_symbol_exists_and_is_callable():
    """Integration-style check: import the REAL archive module (not a mock)
    and verify the symbol the decay scheduler looks for actually exists and
    is awaitable with the call signature decay.py uses.

    This is the test that would have caught the original B1 wiring bug
    (function-name mismatch between decay.py and archive.py)."""
    from memex.graph import archive

    # The exact symbol decay.py imports.
    assert hasattr(archive, "tombstone_cold_nodes"), (
        "memex.graph.archive must export tombstone_cold_nodes — the decay "
        "scheduler imports it by that exact name"
    )
    # And it must be an async callable accepting repo_root as first positional.
    import inspect
    sig = inspect.signature(archive.tombstone_cold_nodes)
    params = list(sig.parameters.values())
    assert len(params) >= 1, "tombstone_cold_nodes must accept at least one positional"
    assert params[0].name == "repo_root", (
        "tombstone_cold_nodes(repo_root, ...) — decay.py passes repo.path here"
    )
    assert inspect.iscoroutinefunction(archive.tombstone_cold_nodes), (
        "decay.py awaits the call; archive.tombstone_cold_nodes must be async"
    )


@pytest.mark.asyncio
async def test_decay_task_survives_archive_failure_for_one_repo():
    """If one repo's sweep fails, the others must still run; daemon must not
    crash."""
    scheduler = DecayScheduler()
    mock_client = AsyncMock()
    mock_driver = AsyncMock()
    mock_result = MagicMock()
    mock_result.records = [{"updated_count": 0}]
    mock_driver.execute_query.return_value = mock_result
    mock_client.driver = mock_driver

    tombstone = AsyncMock(side_effect=[Exception("bad repo"), 3])
    repos = [MagicMock(path="/bad"), MagicMock(path="/good")]

    with (
        patch("memex.graph.decay.get_graph_client", return_value=mock_client),
        patch("memex.graph.archive.tombstone_cold_nodes", tombstone),
        patch("memex.watcher.registry.get_active_repositories", return_value=repos),
    ):
        await scheduler.decay_task()  # must not raise

    assert tombstone.await_count == 2


@pytest.mark.asyncio
async def test_decay_task_swallows_stale_refresh_errors():
    """A Neo4j blip during the stale refresh must not crash the daemon."""
    scheduler = DecayScheduler()
    mock_client = AsyncMock()
    mock_driver = AsyncMock()
    mock_driver.execute_query.side_effect = Exception("transient")
    mock_client.driver = mock_driver

    with patch("memex.graph.decay.get_graph_client", return_value=mock_client):
        # Must not raise
        await scheduler.decay_task()
