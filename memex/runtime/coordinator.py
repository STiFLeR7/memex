"""Ordered capture → durable observation → graph commit → local acknowledgement."""
import asyncio
from dataclasses import dataclass

from memex.context.revision import RepositoryView
from memex.runtime.parsing import parse_sources
from memex.runtime.journal import ChangeJournal
from memex.runtime.views import capture_sources


@dataclass(frozen=True)
class IndexResult:
    view: RepositoryView
    coverage: dict[str, str]
    is_current: bool
    reason: str = ""


class RepositoryIndexer:
    def __init__(self, registration, store, journal=None):
        self.registration = registration
        self.store = store
        self.journal = journal or ChangeJournal(registration.runtime_path)
        self._lock = asyncio.Lock()

    async def refresh(self) -> IndexResult:
        async with self._lock:
            for _ in range(2):
                capture = await asyncio.to_thread(capture_sources, self.registration)
                view = self.journal.observe(self.registration, capture)
                structures = await parse_sources(capture.files)
                coverage = {item.path: item.coverage for item in structures}
                published = await self.store.publish(view, structures)
                if not published:
                    return IndexResult(view, coverage, False, "newer_view_published")
                self.journal.acknowledge(view.view_id)
                current = self.journal.current(self.registration.worktree_id)
                after = await asyncio.to_thread(capture_sources, self.registration)
                if (after.head_commit, after.manifest_hash) == (capture.head_commit, capture.manifest_hash):
                    if (current.view_id != view.view_id
                            or current.indexed_generation != current.content_generation
                            or await self.store.published_view(view.repo_id, view.worktree_id) != view.view_id):
                        return IndexResult(current, coverage, False, "concurrent_observation")
                    complete = all(status == "complete" for status in coverage.values())
                    return IndexResult(current, coverage, complete, "" if complete else "incomplete_coverage")
            return IndexResult(current, coverage, False, "changed_during_indexing")
