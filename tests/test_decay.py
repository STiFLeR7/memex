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


# --- Phase 04 / NET-15: weekly governance report cron job ---


def _fake_config(decay_hour=2, report_hour=3, report_day_of_week="mon"):
    mock_cfg = MagicMock()
    mock_cfg.decay_hour = decay_hour
    mock_cfg.decay_minute = 0
    mock_cfg.report_hour = report_hour
    mock_cfg.report_minute = 0
    mock_cfg.report_day_of_week = report_day_of_week
    return mock_cfg


def test_start_registers_two_distinct_cron_jobs():
    """DecayScheduler.start() must register both the nightly decay job and
    the new weekly report job on the same AsyncIOScheduler instance, at
    distinct configured times/trigger shapes."""
    scheduler = DecayScheduler()
    fake_config = _fake_config(decay_hour=2, report_hour=3, report_day_of_week="mon")

    with (
        patch("memex.graph.decay.get_config", return_value=fake_config),
        patch("apscheduler.schedulers.asyncio.AsyncIOScheduler.start"),
    ):
        scheduler.start()

        jobs = scheduler.scheduler.get_jobs()
        assert len(jobs) == 2

        decay_job = next(j for j in jobs if j.func == scheduler.decay_task)
        report_job = next(j for j in jobs if j.func == scheduler.report_task)

        assert decay_job.trigger.fields[
            [f.name for f in decay_job.trigger.fields].index("hour")
        ].expressions[0].first == 2
        assert report_job.trigger.fields[
            [f.name for f in report_job.trigger.fields].index("hour")
        ].expressions[0].first == 3

        # The report job's trigger carries a day_of_week restriction the
        # decay job's does not (decay job has no day_of_week kwarg passed).
        report_dow_field = report_job.trigger.fields[
            [f.name for f in report_job.trigger.fields].index("day_of_week")
        ]
        decay_dow_field = decay_job.trigger.fields[
            [f.name for f in decay_job.trigger.fields].index("day_of_week")
        ]
        assert str(report_dow_field) != str(decay_dow_field)

        # scheduler.scheduler.start() itself is mocked above (no real event
        # loop is running in this sync test), so the scheduler never
        # transitions to `running` -- skip calling scheduler.stop(), which
        # would raise SchedulerNotRunningError against the real shutdown().


@pytest.mark.asyncio
async def test_report_task_invokes_generate_and_write_per_active_repo():
    """report_task() must call generate_report() + write_report() once per
    active repo, passing repo.path -- mirrors
    test_decay_task_invokes_archive_tombstoning_per_active_repo."""
    scheduler = DecayScheduler()

    fake_report_a = MagicMock(name="report-a")
    fake_report_b = MagicMock(name="report-b")
    generate_report_mock = AsyncMock(side_effect=[fake_report_a, fake_report_b])
    write_report_mock = MagicMock()

    repo_a = MagicMock(path="/repo/a", active=True)
    repo_b = MagicMock(path="/repo/b", active=True)

    with (
        patch("memex.graph.governance_report.generate_report", generate_report_mock),
        patch("memex.graph.governance_report.write_report", write_report_mock),
        patch("memex.watcher.registry.get_active_repositories", return_value=[repo_a, repo_b]),
    ):
        await scheduler.report_task()

    assert generate_report_mock.await_count == 2
    awaited_paths = [c.args[0] for c in generate_report_mock.await_args_list]
    assert "/repo/a" in awaited_paths
    assert "/repo/b" in awaited_paths

    assert write_report_mock.call_count == 2
    written = [c.args[0] for c in write_report_mock.call_args_list]
    assert fake_report_a in written
    assert fake_report_b in written


@pytest.mark.asyncio
async def test_report_task_survives_generate_report_failure_for_one_repo():
    """If generate_report() raises for one repo, the other must still be
    attempted; report_task() must not raise -- mirrors
    test_decay_task_survives_archive_failure_for_one_repo."""
    scheduler = DecayScheduler()

    fake_report = MagicMock(name="good-report")
    generate_report_mock = AsyncMock(side_effect=[Exception("bad repo"), fake_report])
    write_report_mock = MagicMock()

    repos = [MagicMock(path="/bad"), MagicMock(path="/good")]

    with (
        patch("memex.graph.governance_report.generate_report", generate_report_mock),
        patch("memex.graph.governance_report.write_report", write_report_mock),
        patch("memex.watcher.registry.get_active_repositories", return_value=repos),
    ):
        await scheduler.report_task()  # must not raise

    assert generate_report_mock.await_count == 2
    # write_report is only called for the repo whose generate_report succeeded.
    assert write_report_mock.call_count == 1
    assert write_report_mock.call_args.args[0] is fake_report
