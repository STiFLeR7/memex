"""Ordered capture → durable observation → graph commit → local acknowledgement."""
import asyncio
from dataclasses import dataclass

from memex.context.revision import RepositoryView
from memex.runtime.parsing import parse_sources
from memex.runtime.journal import ChangeJournal
from memex.runtime.views import SourceCapture, capture_sources, unchanged_since


@dataclass(frozen=True)
class IndexResult:
    view: RepositoryView
    coverage: dict[str, str]
    is_current: bool
    reason: str = ""
    capture: SourceCapture | None = None  # The stable capture this view was built from.


class RepositoryIndexer:
    def __init__(self, registration, store, journal=None):
        self.registration = registration
        self.store = store
        self.journal = journal or ChangeJournal(registration.runtime_path)
        self._lock = asyncio.Lock()

    async def refresh(self, *, verify_after: bool = True) -> IndexResult:
        """Index the current sources.

        `verify_after=False` is for callers that recheck the returned capture
        themselves once their own work is done, which covers this window too.
        """
        async with self._lock:
            for _ in range(2):
                # Listed once; the unchanged_since below (or the caller's) lists again.
                capture = await asyncio.to_thread(capture_sources, self.registration, relist=False)
                view = self.journal.observe(self.registration, capture)
                structures = await parse_sources(capture.files)
                coverage = {item.path: item.coverage for item in structures}
                # Already indexed and still the graph's published view: publishing
                # again would rewrite nothing, so the write transaction is skipped.
                indexed = view.indexed_generation == view.content_generation and (
                    await self.store.published_view(view.repo_id, view.worktree_id) == view.view_id)
                if not indexed:
                    published = await self.store.publish(view, structures)
                    if not published:
                        return IndexResult(view, coverage, False, "newer_view_published")
                    self.journal.acknowledge(view.view_id)
                current = self.journal.current(self.registration.worktree_id)
                if not verify_after or await asyncio.to_thread(unchanged_since, self.registration, capture):
                    if (current.view_id != view.view_id
                            or current.indexed_generation != current.content_generation
                            or await self.store.published_view(view.repo_id, view.worktree_id) != view.view_id):
                        return IndexResult(current, coverage, False, "concurrent_observation")
                    complete = all(status == "complete" for status in coverage.values())
                    return IndexResult(current, coverage, complete, "" if complete else "incomplete_coverage",
                                       capture)
            return IndexResult(current, coverage, False, "changed_during_indexing")
