import asyncio
import logging
from typing import Callable, Awaitable, Dict
from memex.watcher.events import FileChangeEvent, CommitEvent
from memex.config import get_config

logger = logging.getLogger(__name__)

class EventRouter:
    """
    Reads from the raw queue. Debounces FileChangeEvents by path.
    Passes CommitEvents through immediately.
    """
    def __init__(self, queue: asyncio.Queue):
        self.queue = queue
        self.file_handlers: list[Callable[[FileChangeEvent], Awaitable[None]]] = []
        self.commit_handlers: list[Callable[[CommitEvent], Awaitable[None]]] = []
        self.debounce_tasks: Dict[str, asyncio.Task] = {}
        config = get_config()
        self.debounce_window = config.debounce_window

    def on_file_change(self, handler: Callable[[FileChangeEvent], Awaitable[None]]):
        self.file_handlers.append(handler)

    def on_commit(self, handler: Callable[[CommitEvent], Awaitable[None]]):
        self.commit_handlers.append(handler)

    async def _dispatch_file(self, event: FileChangeEvent):
        for handler in self.file_handlers:
            try:
                await handler(event)
            except Exception:
                logger.error("Error in file handler", exc_info=True)

    async def _dispatch_commit(self, event: CommitEvent):
        for handler in self.commit_handlers:
            try:
                await handler(event)
            except Exception:
                logger.error("Error in commit handler", exc_info=True)

    async def _debounce_worker(self, path: str, event: FileChangeEvent):
        """
        Wait for the debounce window, then dispatch.
        """
        try:
            await asyncio.sleep(self.debounce_window)
            await self._dispatch_file(event)
            # Cleanup task reference
            if self.debounce_tasks.get(path) == asyncio.current_task():
                del self.debounce_tasks[path]
        except asyncio.CancelledError:
            pass

    async def run(self) -> None:
        logger.info("EventRouter started (debounce window: %.1fs)", self.debounce_window)
        while True:
            try:
                event = await self.queue.get()
                
                if isinstance(event, CommitEvent):
                    # Immediate dispatch, but track task to prevent GC
                    task = asyncio.create_task(self._dispatch_commit(event))
                    # We don't strictly need to track one-off tasks unless we need to await them later,
                    # but it's good practice. Here we just let it run.
                    
                elif isinstance(event, FileChangeEvent):
                    # Debounce by path
                    path = event.path
                    if path in self.debounce_tasks:
                        self.debounce_tasks[path].cancel()
                    
                    self.debounce_tasks[path] = asyncio.create_task(
                        self._debounce_worker(path, event)
                    )
                
                self.queue.task_done()
                
            except asyncio.CancelledError:
                for task in self.debounce_tasks.values():
                    task.cancel()
                break
            except Exception:
                logger.error("Error in EventRouter", exc_info=True)
