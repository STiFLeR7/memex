"""Opt-in watcher composition. Notifications trigger capture, never supply facts."""
import asyncio
import logging
from pathlib import Path

from memex.runtime.coordinator import RepositoryIndexer
from memex.runtime.graph import StructuralGraphStore
from memex.runtime.views import discover_repository

logger = logging.getLogger(__name__)


class LiveContextRuntime:
    def __init__(self, repositories, driver, *, rescan_interval=5.0):
        if rescan_interval <= 0:
            raise ValueError("rescan_interval must be positive")
        self.rescan_interval = rescan_interval
        self.store = StructuralGraphStore(driver)
        self.indexers = {}
        for root in repositories:
            registration = discover_repository(root)
            self.indexers[Path(registration.root)] = RepositoryIndexer(registration, self.store)

    async def on_event(self, event):
        root = Path(event.repo_root).resolve()
        indexer = self.indexers.get(root)
        if indexer is None:
            raise ValueError("event refers to an unregistered worktree")
        if hasattr(event, "path") and not Path(event.path).resolve().is_relative_to(root):
            raise ValueError("event path escapes registered worktree")
        if not (root / ".memex" / "paused").exists():
            return await indexer.refresh()
        return None

    async def run(self):
        while True:
            for root, indexer in self.indexers.items():
                if (root / ".memex" / "paused").exists():
                    continue
                try:
                    await indexer.refresh()
                except Exception:
                    logger.exception("Live structural recovery remains pending for %s", root)
            await asyncio.sleep(self.rescan_interval)
