import asyncio
import logging
import os
from datetime import datetime, UTC
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from memex.watcher.events import FileChangeEvent
from memex.config import get_config

logger = logging.getLogger(__name__)

class MemexHandler(FileSystemEventHandler):
    def __init__(self, repo_root: str, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop, ignored_patterns: list[str]):
        self.repo_root = repo_root
        self.queue = queue
        self.loop = loop
        self.ignored_patterns = ignored_patterns

    def is_ignored(self, path: str) -> bool:
        p = Path(path)
        # Check if any part of the path matches ignored patterns
        for part in p.parts:
            if part in self.ignored_patterns:
                return True
        # Check extensions
        if p.suffix.lower() == ".pyc":
            return True
        return False

    def on_any_event(self, event: FileSystemEvent):
        if event.is_directory:
            return
        
        if self.is_ignored(event.src_path):
            return

        kind_map = {
            "created": "created",
            "modified": "modified",
            "deleted": "deleted",
            "moved": "modified"
        }
        
        kind = kind_map.get(event.event_type)
        if not kind:
            return

        file_event = FileChangeEvent(
            path=os.path.abspath(event.src_path),
            repo_root=self.repo_root,
            kind=kind,
            timestamp=datetime.now(UTC)
        )

        # Bridge thread to asyncio loop
        try:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, file_event)
        except RuntimeError:
            # Loop might be closing
            pass

class FSObserver:
    def __init__(self, repo_root: str, queue: asyncio.Queue):
        self.repo_root = os.path.abspath(repo_root)
        self.queue = queue
        self.loop = asyncio.get_running_loop()
        self.observer = Observer()
        config = get_config()
        self.ignored_patterns = config.ignored_patterns

    def start(self) -> None:
        handler = MemexHandler(self.repo_root, self.queue, self.loop, self.ignored_patterns)
        self.observer.schedule(handler, self.repo_root, recursive=True)
        self.observer.start()
        logger.info("FSObserver started on %s", self.repo_root)

    def stop(self) -> None:
        self.observer.stop()
        self.observer.join()
        logger.info("FSObserver stopped")
