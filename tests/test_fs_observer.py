import asyncio
import pytest
import shutil
import tempfile
from pathlib import Path
from memex.watcher.fs_observer import FSObserver
from memex.watcher.events import FileChangeEvent

@pytest.mark.asyncio
async def test_fs_observer_creation():
    """
    Test that creating a file triggers a FileChangeEvent on the queue.
    """
    tmp_dir = tempfile.mkdtemp()
    queue = asyncio.Queue()
    observer = FSObserver(tmp_dir, queue)
    
    try:
        observer.start()
        
        # Give watchdog a tiny bit of time to settle
        await asyncio.sleep(0.1)
        
        test_file = Path(tmp_dir) / "hello.txt"
        test_file.write_text("hello memex")
        
        # Wait for event with 2s timeout
        try:
            event = await asyncio.wait_for(queue.get(), timeout=2.0)
            assert isinstance(event, FileChangeEvent)
            assert event.path == str(test_file.absolute())
            assert event.kind in ["created", "modified"]
        except asyncio.TimeoutError:
            pytest.fail("FSObserver failed to emit event within 2 seconds")
            
    finally:
        observer.stop()
        shutil.rmtree(tmp_dir)

@pytest.mark.asyncio
async def test_fs_observer_ignored():
    """
    Test that files in ignored directories do not trigger events.
    """
    tmp_dir = tempfile.mkdtemp()
    queue = asyncio.Queue()
    observer = FSObserver(tmp_dir, queue)
    
    try:
        observer.start()
        
        # Create ignored directory and file
        git_dir = Path(tmp_dir) / ".git"
        git_dir.mkdir()
        ignored_file = git_dir / "config"
        ignored_file.write_text("git config")
        
        # Wait a bit to see if anything pops up
        await asyncio.sleep(0.5)
        assert queue.empty(), "Ignored file should not have triggered an event"
        
    finally:
        observer.stop()
        shutil.rmtree(tmp_dir)
