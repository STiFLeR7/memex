import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from memex.watcher.events import CommitEvent
from memex.watcher.event_router import EventRouter


@pytest.mark.asyncio
async def test_queue_join_waits_for_commit_handler_and_spool_ack(tmp_path):
    queue = asyncio.Queue()
    with patch("memex.watcher.event_router.get_config") as config:
        config.return_value.debounce_window = 0.01
        router = EventRouter(queue)
    entered, release = asyncio.Event(), asyncio.Event()
    async def handler(event):
        entered.set()
        await release.wait()
    router.on_commit(handler)
    spool = tmp_path / ".memex" / "pending_commits" / "commit.json"
    spool.parent.mkdir(parents=True)
    spool.write_text("{}")
    event = CommitEvent("a", str(tmp_path), "msg", "", [], datetime.now(UTC), event_path=str(spool))
    run = asyncio.create_task(router.run())
    try:
        await queue.put(event)
        await asyncio.wait_for(entered.wait(), 1)
        joined = asyncio.create_task(queue.join())
        await asyncio.sleep(0.03)
        assert not joined.done() and spool.exists()
        release.set()
        await asyncio.wait_for(joined, 1)
        assert not spool.exists()
    finally:
        release.set()
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)


@pytest.mark.asyncio
async def test_failed_handler_keeps_durable_event(tmp_path):
    queue = asyncio.Queue()
    with patch("memex.watcher.event_router.get_config") as config:
        config.return_value.debounce_window = 0.01
        router = EventRouter(queue)
    async def fail(event):
        raise RuntimeError("graph unavailable")
    router.on_commit(fail)
    spool = tmp_path / ".memex" / "pending_commits" / "commit.json"
    spool.parent.mkdir(parents=True)
    spool.write_text("{}")
    run = asyncio.create_task(router.run())
    try:
        await queue.put(CommitEvent("a", str(tmp_path), "msg", "", [], datetime.now(UTC), event_path=str(spool)))
        await asyncio.wait_for(queue.join(), 1)
        assert Path(spool).exists()
    finally:
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)
