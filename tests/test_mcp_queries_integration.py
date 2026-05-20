import pytest
import asyncio
from datetime import datetime, UTC
from memex.mcp_server.queries import (
    get_node_counts,
    get_active_modules,
    get_recent_decisions_raw,
    get_open_problems_raw,
    get_stale_edges,
    get_symbol_by_name
)
from memex.graph.client import get_graph_client, reset_graph_client

@pytest.fixture(autouse=True)
async def cleanup_client():
    """Ensure a fresh graph client for every test."""
    await reset_graph_client()
    yield
    await reset_graph_client()

@pytest.mark.asyncio
@pytest.mark.integration
async def test_queries_integration():
    """
    Integration test for raw Cypher queries.
    Seeds minimal data and verifies results.
    """
    client = await get_graph_client()
    
    # 1. Seed minimal data using episodes
    # We use very explicit text to help the LLM extractor
    now = datetime.now(UTC)
    
    await client.add_episode(
        name="seed_module",
        episode_body="The file 'query_test.py' is a Python module in this project.",
        source_description="integration test",
        reference_time=now
    )
    
    await client.add_episode(
        name="seed_symbol",
        episode_body="The function 'query_fn' is defined in 'query_test.py'. It is a symbol.",
        source_description="integration test",
        reference_time=now
    )

    # 2. Test Node Counts
    # It might take a few seconds for extraction to finish
    counts = None
    for _ in range(15):
        await asyncio.sleep(1.0)
        counts = await get_node_counts()
        if counts.get('modules', 0) >= 1:
            break
            
    assert isinstance(counts, dict)
    assert counts['modules'] >= 1
    
    # 3. Test Active Modules
    modules = await get_active_modules(since_days=1, scope=None)
    assert any("query_test.py" in m['path'] for m in modules)
    
    # 4. Test Symbol Retrieval (with retry for indexing lag)
    symbol = None
    for _ in range(10):
        await asyncio.sleep(1.0)
        symbol = await get_symbol_by_name("query_fn", file=None) # Don't filter by file in test yet as LLM might not link it perfectly
        if symbol:
            break
            
    assert symbol is not None
    assert "query_fn" in symbol['name']
    
    # 5. Test Recent Decisions
    decisions = await get_recent_decisions_raw(since_days=1, module=None, limit=10)
    assert isinstance(decisions, list)
    
    # 6. Test Open Problems
    problems = await get_open_problems_raw(module=None)
    assert isinstance(problems, list)
    
    # 7. Test Stale Edges
    stale = await get_stale_edges(threshold=1.0, limit=10)
    assert isinstance(stale, list)
