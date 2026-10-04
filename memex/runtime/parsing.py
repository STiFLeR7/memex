"""Bounded CPU workers keep Python AST parsing off the coordinator event loop."""
import asyncio
from concurrent.futures import ProcessPoolExecutor
from contextvars import ContextVar
import multiprocessing
import threading
import time
import weakref

from memex.runtime.indexing import extract_structure

parse_deadline = ContextVar("memex_parse_deadline", default=None)
_pool = None
_pool_lock = threading.Lock()
_slots = weakref.WeakKeyDictionary()


def _extract_batch(files, deadline):
    structures = []
    for path, content in files.items():
        if time.monotonic() >= deadline:
            raise TimeoutError("structural parsing deadline exceeded")
        structures.append(extract_structure(path, content))
    return structures


async def parse_sources(files):
    """At most two submitted jobs; cancellation never frees a still-running slot.

    A running parser may finish its current file after the caller times out, but
    cannot publish a view or certificate. Its process holds the CPU work instead
    of blocking other sessions; deadline checks stop subsequent files.
    """
    global _pool
    loop = asyncio.get_running_loop()
    slots = _slots.setdefault(loop, asyncio.Semaphore(2))
    await slots.acquire()
    try:
        with _pool_lock:
            if _pool is None:
                _pool = ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn"))
        deadline = parse_deadline.get()
        job = _pool.submit(_extract_batch, files, deadline if deadline is not None else time.monotonic()+30)
    except BaseException:
        slots.release()
        raise

    def release(_):
        if not loop.is_closed():
            try:
                loop.call_soon_threadsafe(slots.release)
            except RuntimeError:
                pass  # This request's event loop already closed.

    job.add_done_callback(release)
    return await asyncio.wrap_future(job, loop=loop)
