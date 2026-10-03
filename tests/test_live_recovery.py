"""Native graph + real Git + durable SQLite recovery, without model calls."""
from unittest.mock import patch

import pytest

from tests.test_live_graph import graph  # noqa: F401
from tests.test_live_repository import repository, git  # noqa: F401
from memex.runtime.coordinator import RepositoryIndexer
from memex.runtime.views import discover_repository
from memex.runtime.journal import ChangeJournal


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_missed_event_body_edit_revert_deletion_and_restart(repository, graph):
    reg = discover_repository(repository)
    indexer = RepositoryIndexer(reg, graph)
    initial = await indexer.refresh()
    assert initial.is_current
    assert initial.view.indexed_generation == initial.view.content_generation
    (repository / "api.py").write_text("def api():\n    return 4\n", encoding="utf-8")
    changed = await RepositoryIndexer(reg, graph).refresh()  # No watcher event.
    assert changed.is_current and changed.view.view_id != initial.view.view_id
    git(repository, "restore", "api.py")
    reverted = await indexer.refresh()
    assert reverted.is_current and reverted.view.view_id != changed.view.view_id
    (repository / "api.py").unlink()
    assert (await indexer.refresh()).is_current


@pytest.mark.asyncio
async def test_graph_commit_before_local_ack_recovers_without_duplicate(repository, graph):
    reg = discover_repository(repository)
    journal = ChangeJournal(reg.runtime_path)
    indexer = RepositoryIndexer(reg, graph, journal)
    with patch.object(journal, "acknowledge", side_effect=RuntimeError("simulated crash")):
        with pytest.raises(RuntimeError, match="simulated crash"):
            await indexer.refresh()
    pending = journal.pending(reg.worktree_id)
    assert len(pending) == 1 and await graph.completed(pending[0])
    recovered = await RepositoryIndexer(reg, graph).refresh()
    assert recovered.is_current
    assert recovered.view.view_id == pending[0]
    assert journal.pending(reg.worktree_id) == []


@pytest.mark.asyncio
async def test_parse_failure_does_not_certify_fresh_and_checkout_change_reindexes(repository, graph):
    reg = discover_repository(repository)
    indexer = RepositoryIndexer(reg, graph)
    good = await indexer.refresh()
    (repository / "api.py").write_text("def broken(\n", encoding="utf-8")
    bad = await indexer.refresh()
    assert not bad.is_current and bad.coverage["api.py"] == "parse_error"
    git(repository, "restore", "api.py")
    git(repository, "checkout", "-qb", "next")
    (repository / "api.py").write_text("def api():\n    return 9\n", encoding="utf-8")
    git(repository, "commit", "-qam", "change")
    fixed = await indexer.refresh()
    assert fixed.is_current and fixed.view.head_commit != good.view.head_commit


@pytest.mark.asyncio
async def test_changing_source_during_publish_cannot_certify_old_view(repository, graph):
    reg = discover_repository(repository)
    indexer = RepositoryIndexer(reg, graph)
    original = graph.publish
    count = 0
    async def changing(view, contributions, **kwargs):
        nonlocal count
        result = await original(view, contributions, **kwargs)
        count += 1
        (repository / "api.py").write_text(f"def api():\n    return {count + 10}\n", encoding="utf-8")
        return result
    with patch.object(graph, "publish", side_effect=changing):
        result = await indexer.refresh()
    assert not result.is_current and result.reason == "changed_during_indexing"


@pytest.mark.asyncio
async def test_other_observation_cannot_be_reported_as_indexed(repository, graph):
    from memex.runtime.views import capture_sources
    reg = discover_repository(repository)
    indexer = RepositoryIndexer(reg, graph)
    acknowledge = indexer.journal.acknowledge
    def concurrent_observation(view_id):
        acknowledge(view_id)
        original = (repository / "api.py").read_bytes()
        (repository / "api.py").write_bytes(b"def other(): return 2\n")
        indexer.journal.observe(reg, capture_sources(reg))
        (repository / "api.py").write_bytes(original)
    with patch.object(indexer.journal, "acknowledge", side_effect=concurrent_observation):
        result = await indexer.refresh()
    assert not result.is_current
