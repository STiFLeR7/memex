"""Daemon composition with real Git, watchdog, SQLite and native Neo4j."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from memex.runtime.service import LiveContextRuntime
from memex.runtime.views import discover_repository
from memex.watcher import daemon
from memex.watcher.events import FileChangeEvent
from tests.test_live_graph import graph  # noqa: F401
from tests.test_live_repository import repository, git  # noqa: F401

pytestmark = pytest.mark.integration


async def wait_for(predicate, timeout=20):
    async def wait():
        while not await predicate():
            await asyncio.sleep(0.05)
    await asyncio.wait_for(wait(), timeout)


@pytest.mark.asyncio
async def test_runtime_rejects_unregistered_scope(repository, graph, tmp_path):
    from datetime import UTC, datetime
    runtime = LiveContextRuntime([repository], graph.driver)
    event = FileChangeEvent(str(tmp_path / "api.py"), str(tmp_path), "modified", datetime.now(UTC))
    with pytest.raises(ValueError, match="unregistered"):
        await runtime.on_event(event)


@pytest.mark.asyncio
async def test_opt_in_daemon_indexes_edits_and_rapid_commits_without_model_calls(repository, graph, monkeypatch):
    monkeypatch.setenv("MEMEX_LIVE_CONTEXT", "1")
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    monkeypatch.setattr(daemon, "get_graph_client", AsyncMock(return_value=SimpleNamespace(driver=graph.driver)))
    # Legacy semantic/model paths are outside P1; isolate those boundaries only.
    legacy_file, legacy_commit = AsyncMock(), AsyncMock()
    monkeypatch.setattr(daemon, "handle_file_change", legacy_file)
    monkeypatch.setattr(daemon, "handle_commit", legacy_commit)
    monkeypatch.setattr(daemon, "handle_lockfile_change", AsyncMock())
    monkeypatch.setattr(daemon, "initial_lockfile_index", AsyncMock())
    monkeypatch.setattr(daemon, "DecayScheduler", Mock())
    reg = discover_repository(repository)
    task = asyncio.create_task(daemon.run_daemon(str(repository)))
    async def published():
        return await graph.published_view(reg.repo_id, reg.worktree_id)
    try:
        await wait_for(published)
        first = await published()
        (repository / "api.py").write_text("import os\ndef api(): return os.getcwd()\n", encoding="utf-8")
        async def edited():
            return (await published()) != first
        await wait_for(edited)
        for number in range(3):
            git(repository, "commit", "-qam" if number == 0 else "--allow-empty", *(
                [f"change {number}"] if number == 0 else ["-qm", f"change {number}"]
            ))
        final_head = git(repository, "rev-parse", "HEAD")
        async def caught_up():
            view_id = await published()
            result = await graph.driver.execute_query(
                "MATCH (v:MemexView {view_id:$view}) RETURN v.head_commit AS head,v.completed AS done",
                params={"view": view_id},
            )
            return bool(result.records and result.records[0]["head"] == final_head
                        and result.records[0]["done"]
                        and not list((repository / ".memex/pending_commits").glob("*.json")))
        await wait_for(caught_up)
        legacy_file.assert_awaited()
        legacy_commit.assert_awaited()
    finally:
        task.cancel()
        await task
    assert not (repository / ".memex/daemon.pid").exists()
