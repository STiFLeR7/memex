import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

from memex.config import get_config
from memex.watcher.events import CommitEvent

logger = logging.getLogger(__name__)


class CommitPoller:
    """At-least-once durable spool delivery; the router acknowledges success."""

    def __init__(self, repo_root: str, queue: asyncio.Queue):
        self.repo_root = os.path.abspath(repo_root)
        self.queue = queue
        self.pending_file = Path(self.repo_root) / ".memex" / "pending_commit.json"
        self.spool = self.pending_file.parent / "pending_commits"
        self.poll_interval = get_config().poll_interval
        self.retry_interval = 30.0
        self._inflight: dict[Path, float] = {}

    def _read(self, path):
        data = json.loads(path.read_text(encoding="utf-8"))
        return CommitEvent(
            sha=data["sha"], repo_root=self.repo_root, message=data["message"],
            diff=data["diff"], files_changed=data["files_changed"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            event_path=str(path) if path.parent == self.spool else None,
        )

    async def poll_once(self):
        now = time.monotonic()
        self._inflight = {path: sent for path, sent in self._inflight.items() if path.exists()}
        durable_shas = set()
        for path in sorted(self.spool.glob("*.json")):
            try:
                event = self._read(path)
                durable_shas.add(event.sha)
                if now - self._inflight.get(path, float("-inf")) >= self.retry_interval:
                    await self.queue.put(event)
                    self._inflight[path] = now
            except (ValueError, KeyError, OSError):
                logger.warning("Unreadable durable event retained: %s", path)
        if self.pending_file.exists():
            try:
                event = self._read(self.pending_file)
                if event.sha not in durable_shas:
                    await self.queue.put(event)
                self.pending_file.unlink(missing_ok=True)
            except (ValueError, KeyError):
                logger.warning("Malformed legacy event: %s", self.pending_file)
                self.pending_file.unlink(missing_ok=True)

    async def run(self) -> None:
        logger.info("CommitPoller started (interval: %.1fs)", self.poll_interval)
        while True:
            try:
                await self.poll_once()
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("Error in CommitPoller", exc_info=True)
                await asyncio.sleep(1.0)
