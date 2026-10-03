"""Real Neo4j transactions; URI must designate an isolated test server."""
import os
import asyncio
import uuid
from dataclasses import replace

import pytest
import pytest_asyncio
from graphiti_core.driver.neo4j_driver import Neo4jDriver

from memex.context.revision import RepositoryView
from memex.runtime.graph import StructuralGraphStore
from memex.runtime.indexing import extract_structure


pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(loop_scope="function")
async def graph():
    uri = os.getenv("MEMEX_PHASE1_NEO4J_URI")
    if not uri:
        pytest.skip("set MEMEX_PHASE1_NEO4J_URI to an isolated native Neo4j server")
    # Avoid Graphiti's unrelated background index bootstrap racing fixture close.
    # The actual driver/session API is unchanged; Memex creates its own schema.
    driver = await asyncio.to_thread(Neo4jDriver, uri, None, None)
    store = StructuralGraphStore(driver)
    yield store
    await driver.close()


def view(generation=1, **updates):
    data = dict(repo_id="test-" + uuid.uuid4().hex, worktree_id="wt-1",
                head_commit="a" * 40, content_generation=generation,
                indexed_generation=0, manifest_hash="sha256:" + "b" * 64)
    data.update(updates)
    return RepositoryView(**data)


async def counts(graph, view_id):
    result = await graph.driver.execute_query(
        "MATCH (v:MemexView {view_id:$view}) OPTIONAL MATCH (v)-[:HAS_FILE]->(f) "
        "OPTIONAL MATCH (f)-[:HAS_CALL]->(c) RETURN count(DISTINCT f) AS files, count(c) AS calls",
        params={"view": view_id},
    )
    return result.records[0].data()


@pytest.mark.asyncio
async def test_atomic_body_change_empty_removal_and_replay(graph):
    first = view()
    old = extract_structure("api.py", b"def old(): return 1\ndef send(): return old()\n")
    assert await graph.publish(first, [old])
    assert await graph.completed(first.view_id)
    assert (await counts(graph, first.view_id))["calls"] == 1
    second = replace(first, content_generation=2, manifest_hash="sha256:" + "c" * 64)
    new = extract_structure("api.py", b"def send(): return 1\n")
    assert await graph.publish(second, [new])
    assert (await counts(graph, second.view_id))["calls"] == 0
    assert await graph.publish(second, [new])
    assert (await counts(graph, second.view_id))["files"] == 1
    third = replace(second, content_generation=3, manifest_hash="sha256:" + "d" * 64)
    assert await graph.publish(third, [])
    assert (await counts(graph, third.view_id))["files"] == 0
    assert (await counts(graph, first.view_id))["calls"] == 1
    assert not await graph.publish(first, [old])
    assert await graph.published_view(first.repo_id, first.worktree_id) == third.view_id


@pytest.mark.asyncio
async def test_failed_transaction_has_no_completion_or_partial_nodes(graph):
    candidate = view()
    structure = extract_structure("api.py", b"def api(): return 1\n")
    with pytest.raises(RuntimeError, match="injected"):
        await graph.publish(candidate, [structure], fail_before_complete=True)
    assert not await graph.completed(candidate.view_id)
    assert await graph.published_view(candidate.repo_id, candidate.worktree_id) is None
    assert await graph.publish(candidate, [structure])


@pytest.mark.asyncio
async def test_parse_failure_and_branch_are_not_current_structure(graph):
    a = view()
    b = replace(a, worktree_id="wt-2")
    await graph.publish(a, [extract_structure("api.py", b"def api(): return 1\n")])
    await graph.publish(b, [extract_structure("api.py", b"def api(): return 2\n")])
    bad = replace(a, content_generation=2, manifest_hash="sha256:" + "c" * 64)
    await graph.publish(bad, [extract_structure("api.py", b"def api(\n")])
    assert not await graph.coverage_complete(bad.view_id)
    assert await graph.published_view(a.repo_id, "wt-2") == b.view_id


@pytest.mark.asyncio
async def test_concurrent_initial_publication_has_unique_view_and_worktree(graph):
    candidate = view()
    structure = extract_structure("api.py", b"import os\ndef api(): return 1\n")
    assert all(await asyncio.gather(*(graph.publish(candidate, [structure]) for _ in range(5))))
    result = await graph.driver.execute_query(
        "MATCH (w:MemexWorktree {repo_id:$repo}) WITH count(w) AS worktrees "
        "MATCH (v:MemexView {view_id:$view}) RETURN worktrees,count(v) AS views",
        params={"repo": candidate.repo_id, "view": candidate.view_id},
    )
    assert result.records[0].data() == {"worktrees": 1, "views": 1}
    constraints = await graph.driver.execute_query("SHOW CONSTRAINTS YIELD name RETURN name")
    assert "memex_worktree_identity" in {row["name"] for row in constraints.records}


@pytest.mark.asyncio
async def test_final_import_removed_without_erasing_history(graph):
    first = view()
    await graph.publish(first, [extract_structure("api.py", b"import os\n")])
    second = replace(first, content_generation=2, manifest_hash="sha256:" + "c" * 64)
    await graph.publish(second, [extract_structure("api.py", b"# empty\n")])
    for candidate, expected in [(first, 1), (second, 0)]:
        result = await graph.driver.execute_query(
            "MATCH (:MemexFile {view_id:$view})-[r:IMPORTS]->() RETURN count(r) AS count",
            params={"view": candidate.view_id},
        )
        assert result.records[0]["count"] == expected


@pytest.mark.asyncio
async def test_attribute_call_does_not_fabricate_local_resolution(graph):
    candidate = view()
    await graph.publish(candidate, [extract_structure(
        "api.py", b"import os\ndef run(): return 1\ndef api(): return os.run()\n",
    )])
    result = await graph.driver.execute_query(
        "MATCH (:MemexSymbol {view_id:$view})-[r:CALLS]->() RETURN count(r) AS count",
        params={"view": candidate.view_id},
    )
    assert result.records[0]["count"] == 0
