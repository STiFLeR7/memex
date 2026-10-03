import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable

from memex.config import get_config
from memex.watcher.events import CommitEvent, FileChangeEvent

logger = logging.getLogger(__name__)


class EventRouter:
    """Debounce files; acknowledge queue items only after their handlers finish."""

    def __init__(self, queue: asyncio.Queue):
        self.queue = queue
        self.file_handlers: list[Callable[[FileChangeEvent], Awaitable[None]]] = []
        self.commit_handlers: list[Callable[[CommitEvent], Awaitable[bool | None]]] = []
        self.debounce_tasks: dict[str, asyncio.Task] = {}
        self._pending_counts: dict[str, int] = {}
        self._workers: set[asyncio.Task] = set()
        self._commit_tasks: dict[object, asyncio.Task] = {}
        self._commit_counts: dict[object, int] = {}
        self.debounce_window = get_config().debounce_window

    def on_file_change(self, handler):
        self.file_handlers.append(handler)

    def on_commit(self, handler):
        self.commit_handlers.append(handler)

    async def _dispatch(self, handlers, event) -> bool:
        succeeded = True
        for handler in handlers:
            try:
                if await handler(event) is False:
                    succeeded = False
            except Exception:
                succeeded = False
                logger.error("Error in event handler", exc_info=True)
        return succeeded

    async def _commit_worker(self, event):
        try:
            if await self._dispatch(self.commit_handlers, event) and event.event_path:
                path = Path(event.event_path).resolve()
                spool = (Path(event.repo_root) / ".memex" / "pending_commits").resolve()
                if path.parent == spool and path.suffix == ".json":
                    path.unlink(missing_ok=True)
        except Exception:
            logger.exception("Commit acknowledgement failed; durable event retained")

    def _commit_finished(self, key, task):
        self._commit_tasks.pop(key, None)
        for _ in range(self._commit_counts.pop(key, 0)):
            self.queue.task_done()

    async def _debounce_worker(self, path, event):
        count = 0
        try:
            await asyncio.sleep(self.debounce_window)
            count = self._pending_counts.pop(path, 0)
            await self._dispatch(self.file_handlers, event)
        finally:
            for _ in range(count):
                self.queue.task_done()
            if self.debounce_tasks.get(path) is asyncio.current_task():
                self.debounce_tasks.pop(path, None)

    def _start(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._workers.add(task)
        task.add_done_callback(self._workers.discard)
        return task

    async def run(self) -> None:
        logger.info("EventRouter started (debounce window: %.1fs)", self.debounce_window)
        try:
            while True:
                event = await self.queue.get()
                if isinstance(event, CommitEvent):
                    if event.event_path and not Path(event.event_path).exists():
                        self.queue.task_done()
                        continue
                    key = str(Path(event.event_path).resolve()) if event.event_path else object()
                    self._commit_counts[key] = self._commit_counts.get(key, 0) + 1
                    if key not in self._commit_tasks:
                        task = self._start(self._commit_worker(event))
                        self._commit_tasks[key] = task
                        task.add_done_callback(lambda task, key=key: self._commit_finished(key, task))
                elif isinstance(event, FileChangeEvent):
                    path = str(Path(event.path).resolve())
                    self._pending_counts[path] = self._pending_counts.get(path, 0) + 1
                    previous = self.debounce_tasks.get(path)
                    if previous:
                        previous.cancel()
                    self.debounce_tasks[path] = self._start(self._debounce_worker(path, event))
                else:
                    self.queue.task_done()
        except asyncio.CancelledError:
            pass
        finally:
            workers = list(self._workers)
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            for count in self._pending_counts.values():
                for _ in range(count):
                    self.queue.task_done()
            self._pending_counts.clear()
