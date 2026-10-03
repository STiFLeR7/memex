"""Deterministic reproductions of the independent final review findings."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from memex.runtime.indexing import extract_structure
from memex.runtime.views import capture_sources, discover_repository
from memex.watcher.commit_poller import CommitPoller
from memex.watcher.event_router import EventRouter
from memex.watcher.events import CommitEvent
from memex.watcher.git_hook import install_hooks
from memex.watcher.handlers import handle_commit
from tests.test_live_repository import repository  # noqa: F401


def test_concurrent_hook_installation_preserves_original(repository):
    from memex.watcher import git_hook
    hook = repository / ".git/hooks/post-commit"
    original = b"#!/bin/sh\nexit 0\n"
    hook.write_bytes(original)
    entered, release = threading.Event(), threading.Event()
    write = git_hook._atomic_write
    def blocked(path, content):
        if path.name == "post-commit" and not entered.is_set():
            entered.set()
            assert release.wait(3)
        write(path, content)
    with patch.object(git_hook, "_atomic_write", side_effect=blocked), ThreadPoolExecutor(2) as pool:
        first = pool.submit(install_hooks, str(repository))
        assert entered.wait(3)
        second = pool.submit(install_hooks, str(repository))
        try:
            time.sleep(0.1)
            assert not second.done(), "another installer entered the preservation transaction"
        finally:
            release.set()
            first.result(timeout=3)
            second.result(timeout=3)
    assert hook.with_name("post-commit.memex-original").read_bytes() == original


def spool_event(root):
    spool = root / ".memex/pending_commits/event.json"
    spool.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(sha="a", message="test", diff="", files_changed=[], timestamp=datetime.now(UTC).isoformat())
    spool.write_text(json.dumps(payload))
    return spool, CommitEvent(**(payload | {"repo_root": str(root), "timestamp": datetime.now(UTC), "event_path": str(spool)}))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["extraction", "partial_write"])
async def test_actual_commit_handler_failure_remains_durable(tmp_path, failure):
    spool, event = spool_event(tmp_path)
    queue = asyncio.Queue()
    router = EventRouter(queue)
    router.on_commit(handle_commit)
    extract = AsyncMock(side_effect=RuntimeError("extraction failed")) if failure == "extraction" else AsyncMock(
        return_value=[SimpleNamespace(text="first"), SimpleNamespace(text="second")])
    write = AsyncMock(side_effect=[None, RuntimeError("partial write failed")])
    with (
        patch("memex.watcher.handlers.extract_decisions", extract),
        patch("memex.watcher.handlers.write_decision", write),
        patch("memex.watcher.handlers.corroborate_decisions", AsyncMock(return_value=0)),
        patch("memex.watcher.handlers.notify_local_server"),
    ):
        run = asyncio.create_task(router.run())
        try:
            await queue.put(event)
            await asyncio.wait_for(queue.join(), 2)
            assert spool.exists()
        finally:
            run.cancel()
            await run


@pytest.mark.asyncio
async def test_slow_commit_retry_is_coalesced_but_failure_can_retry(tmp_path):
    spool, _ = spool_event(tmp_path)
    queue = asyncio.Queue()
    router = EventRouter(queue)
    poller = CommitPoller(str(tmp_path), queue)
    poller.retry_interval = 0
    entered, release = asyncio.Event(), asyncio.Event()
    attempts = 0
    async def handler(event):
        nonlocal attempts
        attempts += 1
        entered.set()
        await release.wait()
        if attempts == 1:
            raise RuntimeError("retryable failure")
    router.on_commit(handler)
    run = asyncio.create_task(router.run())
    try:
        await poller.poll_once()
        await asyncio.wait_for(entered.wait(), 1)
        await poller.poll_once()
        await asyncio.sleep(0.03)
        assert attempts == 1
        release.set()
        await asyncio.wait_for(queue.join(), 1)
        assert spool.exists()
        await poller.poll_once()
        await asyncio.wait_for(queue.join(), 1)
        assert attempts == 2 and not spool.exists()
    finally:
        release.set()
        run.cancel()
        await run


def test_unknown_sources_and_manifests_participate_in_capture(repository):
    for name in ["App.java", "client.cpp", "custom.build"]:
        (repository / name).write_text("unsupported content\n", encoding="utf-8")
    reg = discover_repository(repository)
    first = capture_sources(reg)
    assert {"App.java", "client.cpp", "custom.build"} <= first.files.keys()
    assert all(extract_structure(name, first.files[name]).coverage == "unsupported"
               for name in ["App.java", "client.cpp", "custom.build"])
    (repository / "App.java").write_text("changed content\n", encoding="utf-8")
    assert capture_sources(reg).manifest_hash != first.manifest_hash


def test_python_headers_and_nonfunction_calls_are_captured():
    structure = extract_structure("api.py", b"bootstrap()\n@decorate(factory())\ndef f(x=default()):\n    return api()\nclass C(base()):\n    ready = setup()\n")
    assert structure.coverage == "complete"
    assert {(call.caller, call.callee) for call in structure.calls} == {
        ("<module>", "bootstrap"), ("<module>", "decorate"),
        ("<module>", "factory"), ("<module>", "default"),
        ("f", "api"), ("<module>", "base"), ("C.<body>", "setup"),
    }


@pytest.mark.asyncio
async def test_prestart_commit_cancellation_does_not_leak_queue_accounting(tmp_path):
    spool, event = spool_event(tmp_path)
    queue = asyncio.Queue()
    router = EventRouter(queue)
    start = router._start
    def cancel_before_start(coroutine):
        task = start(coroutine)
        task.cancel()
        return task
    with patch.object(router, "_start", side_effect=cancel_before_start):
        run = asyncio.create_task(router.run())
        await queue.put(event)
        await asyncio.sleep(0.03)
        run.cancel()
        await run
    await asyncio.wait_for(queue.join(), 0.2)
    assert spool.exists()
