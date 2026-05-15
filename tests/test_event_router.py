import asyncio
import pytest
from datetime import datetime, UTC
from memex.watcher.events import FileChangeEvent, CommitEvent
from memex.watcher.event_router import EventRouter

@pytest.mark.asyncio
async def test_event_router_debounce():
    """
    Assert that multiple rapid FileChangeEvents for the same path
    trigger the handler exactly once.
    """
    queue = asyncio.Queue()
    router = EventRouter(queue)
    
    call_count = 0
    async def handler(event):
        nonlocal call_count
        call_count += 1
        
    router.on_file_change(handler)
    router_task = asyncio.create_task(router.run())
    
    try:
        # Send 5 rapid events for the same file
        path = "/tmp/test.py"
        repo_root = "/tmp"
        for _ in range(5):
            event = FileChangeEvent(
                path=path, 
                repo_root=repo_root,
                kind="modified", 
                timestamp=datetime.now(UTC)
            )
            await queue.put(event)
            await asyncio.sleep(0.1) # Total 0.4s, well within 0.8s window
            
        # Wait for debounce window to pass (0.8s + buffer)
        await asyncio.sleep(1.0)
        
        assert call_count == 1
        
    finally:
        router_task.cancel()
        try:
            await router_task
        except asyncio.CancelledError:
            pass

@pytest.mark.asyncio
async def test_event_router_commit_immediate():
    """
    Assert that CommitEvents bypass debounce and fire immediately.
    """
    queue = asyncio.Queue()
    router = EventRouter(queue)
    
    commit_fired = False
    async def handler(event):
        nonlocal commit_fired
        commit_fired = True
        
    router.on_commit(handler)
    router_task = asyncio.create_task(router.run())
    
    try:
        event = CommitEvent(
            sha="123", 
            repo_root="/tmp",
            message="msg", 
            diff="", 
            files_changed=[], 
            timestamp=datetime.now(UTC)
        )
        await queue.put(event)
        
        # Give a tiny bit of time for task scheduling
        await asyncio.sleep(0.05)
        assert commit_fired is True
        
    finally:
        router_task.cancel()
        try:
            await router_task
        except asyncio.CancelledError:
            pass
