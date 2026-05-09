import asyncio
import json
import pytest
import shutil
import tempfile
from pathlib import Path
from datetime import datetime, UTC
from memex.watcher.commit_poller import CommitPoller
from memex.watcher.events import CommitEvent

@pytest.mark.asyncio
async def test_commit_poller_detects_file():
    """
    Test that the poller detects a pending_commit.json file, 
    emits an event, and deletes the file.
    """
    tmp_dir = tempfile.mkdtemp()
    memex_dir = Path(tmp_dir) / ".memex"
    memex_dir.mkdir()
    
    queue = asyncio.Queue()
    poller = CommitPoller(tmp_dir, queue)
    
    # Run the poller in the background
    task = asyncio.create_task(poller.run())
    
    try:
        # Create a fake pending commit
        event_data = {
            "sha": "abc123smoke",
            "message": "Smoke commit",
            "diff": "+ line",
            "files_changed": ["test.py"],
            "timestamp": datetime.now(UTC).isoformat()
        }
        
        pending_file = memex_dir / "pending_commit.json"
        pending_file.write_text(json.dumps(event_data))
        
        # Wait for the poller to pick it up
        event = await asyncio.wait_for(queue.get(), timeout=2.0)
        
        assert isinstance(event, CommitEvent)
        assert event.sha == "abc123smoke"
        assert not pending_file.exists(), "Poller should have deleted the file"
        
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        shutil.rmtree(tmp_dir)
