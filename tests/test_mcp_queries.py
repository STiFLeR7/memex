import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from memex.mcp_server import queries
from memex.mcp_server.queries import MemexQueryError

@pytest.mark.asyncio
async def test_get_node_counts_returns_dict():
    mock_res = MagicMock()
    mock_record = MagicMock()
    mock_record.data.return_value = {"modules": 10, "symbols": 50, "decisions": 5, "problems": 2}
    mock_res.records = [mock_record]
    
    with patch("memex.mcp_server.queries.get_graph_client", new_callable=AsyncMock) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.driver.execute_query.return_value = mock_res
        mock_get_client.return_value = mock_client
        
        result = await queries.get_node_counts()
        assert result["modules"] == 10
        assert result["symbols"] == 50

@pytest.mark.asyncio
async def test_get_node_counts_repo_filter():
    mock_res = MagicMock(records=[])
    with patch("memex.mcp_server.queries.get_graph_client", new_callable=AsyncMock) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.driver.execute_query.return_value = mock_res
        mock_get_client.return_value = mock_client
        
        await queries.get_node_counts(repo="/fake/repo")
        call_args = mock_client.driver.execute_query.call_args
        assert "n.repo_path = $repo" in call_args[0][0]
        assert call_args[1]["params"]["repo"] == "/fake/repo"

@pytest.mark.asyncio
async def test_get_active_modules_filters_by_scope():
    mock_res = MagicMock()
    mock_record = MagicMock()
    mock_record.data.return_value = {"path": "test.py", "description": "desc", "symbols": 5}
    mock_res.records = [mock_record]
    
    with patch("memex.mcp_server.queries.get_graph_client", new_callable=AsyncMock) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.driver.execute_query.return_value = mock_res
        mock_get_client.return_value = mock_client
        
        result = await queries.get_active_modules(since_days=30, scope="memex/watcher")
        # Check that parameters were passed correctly
        call_args = mock_client.driver.execute_query.call_args
        assert call_args.kwargs["params"]["scope"] == "memex/watcher"
        assert isinstance(result, list)
        assert result[0]["path"] == "test.py"

@pytest.mark.asyncio
async def test_get_recent_decisions_raw_respects_limit():
    mock_rows = [MagicMock() for _ in range(25)]
    for r in mock_rows: r.data.return_value = {"text": "D"}
    mock_res = MagicMock(records=mock_rows)
    
    with patch("memex.mcp_server.queries.get_graph_client", new_callable=AsyncMock) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.driver.execute_query.return_value = mock_res
        mock_get_client.return_value = mock_client
        
        # limit is enforced in the query by LIMIT $limit
        result = await queries.get_recent_decisions_raw(since_days=7, module=None, limit=20)
        assert len(result) == 25 # In unit test, mock returns what it returns
        # But we verify the param was passed
        call_args = mock_client.driver.execute_query.call_args
        assert call_args.kwargs["params"]["limit"] == 20

@pytest.mark.asyncio
async def test_get_open_problems_raw_sorted_by_severity():
    mock_rows = [
        MagicMock(), MagicMock()
    ]
    mock_rows[0].data.return_value = {"text": "P1", "severity": "critical"}
    mock_rows[1].data.return_value = {"text": "P2", "severity": "low"}
    mock_res = MagicMock(records=mock_rows)
    
    with patch("memex.mcp_server.queries.get_graph_client", new_callable=AsyncMock) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.driver.execute_query.return_value = mock_res
        mock_get_client.return_value = mock_client
        
        result = await queries.get_open_problems_raw(module=None)
        assert result[0]["severity"] == "critical"

@pytest.mark.asyncio
async def test_get_stale_edges_threshold_applied():
    mock_res = MagicMock()
    mock_record = MagicMock()
    mock_record.data.return_value = {"id": "e1", "source": "A", "target": "B", "edge_type": "C", "confidence": 0.1, "sha": "S"}
    mock_res.records = [mock_record]
    
    with patch("memex.mcp_server.queries.get_graph_client", new_callable=AsyncMock) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.driver.execute_query.return_value = mock_res
        mock_get_client.return_value = mock_client
        
        result = await queries.get_stale_edges(threshold=0.4, limit=50)
        call_args = mock_client.driver.execute_query.call_args
        assert call_args.kwargs["params"]["threshold"] == 0.4
        assert len(result) == 1

@pytest.mark.asyncio
async def test_get_symbol_by_name_returns_none_if_missing():
    with patch("memex.mcp_server.queries.get_graph_client", new_callable=AsyncMock) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.driver.execute_query.return_value = MagicMock(records=[])
        mock_get_client.return_value = mock_client
        
        result = await queries.get_symbol_by_name("missing", file="main.py")
        assert result is None

@pytest.mark.asyncio
async def test_get_symbol_callers_returns_list():
    mock_rows = [MagicMock() for _ in range(3)]
    for i, r in enumerate(mock_rows): r.data.return_value = {"name": f"C{i}", "file": "f.py"}
    mock_res = MagicMock(records=mock_rows)
    
    with patch("memex.mcp_server.queries.get_graph_client", new_callable=AsyncMock) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.driver.execute_query.return_value = mock_res
        mock_get_client.return_value = mock_client
        
        result = await queries.get_symbol_callers("target")
        assert len(result) == 3
        assert result[0]["name"] == "C0"

@pytest.mark.asyncio
async def test_query_error_raises_memex_query_error():
    with patch("memex.mcp_server.queries.get_graph_client", new_callable=AsyncMock) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.driver.execute_query.side_effect = Exception("Neo4j Error")
        mock_get_client.return_value = mock_client
        
        with pytest.raises(MemexQueryError):
            await queries.get_node_counts()

@pytest.mark.asyncio
async def test_get_symbol_callees_returns_list():
    mock_rows = [MagicMock() for _ in range(2)]
    for i, r in enumerate(mock_rows): r.data.return_value = {"name": f"E{i}", "file": "f.py"}
    mock_res = MagicMock(records=mock_rows)
    
    with patch("memex.mcp_server.queries.get_graph_client", new_callable=AsyncMock) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.driver.execute_query.return_value = mock_res
        mock_get_client.return_value = mock_client
        
        result = await queries.get_symbol_callees("source")
        assert len(result) == 2

@pytest.mark.asyncio
async def test_get_symbol_problems_returns_list():
    mock_rows = [MagicMock()]
    mock_rows[0].__getitem__.return_value = "P1" # mock r['text']
    mock_res = MagicMock(records=mock_rows)
    
    with patch("memex.mcp_server.queries.get_graph_client", new_callable=AsyncMock) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.driver.execute_query.return_value = mock_res
        mock_get_client.return_value = mock_client
        
        result = await queries.get_symbol_problems("target")
        assert result == ["P1"]

@pytest.mark.asyncio
async def test_get_node_counts_empty_records():
    mock_res = MagicMock(records=[])
    with patch("memex.mcp_server.queries.get_graph_client", new_callable=AsyncMock) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.driver.execute_query.return_value = mock_res
        mock_get_client.return_value = mock_client

        result = await queries.get_node_counts()
        assert result["modules"] == 0


# ---------------------------------------------------------------------------
# v0.3.0 Phase 8 — count_unvalidated_decisions (surfaced by get_project_context)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_count_unvalidated_decisions_returns_count():
    mock_record = MagicMock()
    mock_record.__getitem__.side_effect = lambda k: 17 if k == "cnt" else None
    mock_res = MagicMock(records=[mock_record])

    with patch("memex.mcp_server.queries.get_graph_client", new_callable=AsyncMock) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.driver.execute_query.return_value = mock_res
        mock_get_client.return_value = mock_client

        result = await queries.count_unvalidated_decisions()
        assert result == 17

        # Query must filter on validated=false.
        call_args = mock_client.driver.execute_query.call_args
        query_str = call_args[0][0]
        assert "validated" in query_str
        assert "false" in query_str.lower()


@pytest.mark.asyncio
async def test_count_unvalidated_decisions_zero_on_empty():
    mock_res = MagicMock(records=[])
    with patch("memex.mcp_server.queries.get_graph_client", new_callable=AsyncMock) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.driver.execute_query.return_value = mock_res
        mock_get_client.return_value = mock_client

        result = await queries.count_unvalidated_decisions()
        assert result == 0


@pytest.mark.asyncio
async def test_count_unvalidated_decisions_safe_on_failure():
    """Must return 0 (not raise) so get_project_context() can't fail because
    of this auxiliary metric."""
    with patch("memex.mcp_server.queries.get_graph_client", new_callable=AsyncMock) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.driver.execute_query.side_effect = Exception("boom")
        mock_get_client.return_value = mock_client

        result = await queries.count_unvalidated_decisions()
        assert result == 0


@pytest.mark.asyncio
async def test_all_edge_traversing_queries_filter_expired_at():
    """B3 regression: every MCP query that traverses edges must filter
    `r.expired_at IS NULL` so Graphiti's auto-invalidated zombie edges
    never leak into agent context. ARCHITECTURE-v0.3.0 §8 step 1.

    Driven by introspecting the function bodies — calls each edge-traversing
    query with a mocked driver and asserts the Cypher contains the filter
    on a named edge variable."""
    mock_res = MagicMock(records=[])

    edge_traversing_calls = [
        ("get_recent_decisions_raw",
         lambda: queries.get_recent_decisions_raw(since_days=30, module=None, limit=10)),
        ("get_open_problems_raw",
         lambda: queries.get_open_problems_raw(module=None)),
        ("get_stale_edges",
         lambda: queries.get_stale_edges(threshold=0.5, limit=10)),
        ("get_symbol_callers",
         lambda: queries.get_symbol_callers(symbol_name="foo")),
        ("get_symbol_callees",
         lambda: queries.get_symbol_callees(symbol_name="foo")),
        ("get_symbol_decisions",
         lambda: queries.get_symbol_decisions(symbol_name="foo")),
        ("get_symbol_problems",
         lambda: queries.get_symbol_problems(symbol_name="foo")),
    ]

    with patch("memex.mcp_server.queries.get_graph_client", new_callable=AsyncMock) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.driver.execute_query.return_value = mock_res
        mock_get_client.return_value = mock_client

        for func_name, call in edge_traversing_calls:
            mock_client.driver.execute_query.reset_mock()
            await call()
            assert mock_client.driver.execute_query.called, f"{func_name} did not run a query"
            query_text = mock_client.driver.execute_query.call_args[0][0]
            assert "expired_at IS NULL" in query_text, (
                f"{func_name}: Cypher missing `r.expired_at IS NULL` filter — "
                f"Graphiti-invalidated edges will leak into agent context"
            )
