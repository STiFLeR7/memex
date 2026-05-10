import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock
from memex.mcp_server.tools_write import record_decision, record_problem, resolve_problem, invalidate_edge

@pytest.mark.asyncio
async def test_record_decision_creates_node():
    mock_result = MagicMock()
    mock_result.episode.uuid = "uuid-123"
    
    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.add_episode.return_value = mock_result
        mock_get_client.return_value = mock_client
        
        result = await record_decision(
            text="Use RS256 for JWT tokens.",
            rationale="Security compliance."
        )
        
        assert "decision recorded" in result
        assert "uuid-123" in result
        assert mock_client.add_episode.called

@pytest.mark.asyncio
async def test_record_decision_rejects_short_text():
    result = await record_decision(text="too short")
    assert "decision text too short" in result

@pytest.mark.asyncio
async def test_record_decision_missing_module_still_creates():
    mock_result = MagicMock()
    mock_result.episode.uuid = "uuid-456"
    
    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.add_episode.return_value = mock_result
        mock_get_client.return_value = mock_client
        
        result = await record_decision(
            text="Switched to EdDSA for key rotation."
        )
        
        assert "decision recorded" in result
        assert "uuid-456" in result
        # Should be called once for the decision, no extra calls for linking
        assert mock_client.add_episode.call_count == 1

@pytest.mark.asyncio
async def test_record_problem_creates_node():
    mock_result = MagicMock()
    mock_result.episode.uuid = "prob-123"
    
    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.search.return_value = [] # No duplicates
        mock_client.add_episode.return_value = mock_result
        mock_get_client.return_value = mock_client
        
        result = await record_problem(text="Memory leak in watcher daemon.")
        assert "problem recorded [medium]" in result
        assert "prob-123" in result

@pytest.mark.asyncio
async def test_record_problem_invalid_severity_coerced():
    mock_result = MagicMock()
    mock_result.episode.uuid = "prob-456"
    
    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.search.return_value = []
        mock_client.add_episode.return_value = mock_result
        mock_get_client.return_value = mock_client
        
        result = await record_problem(text="Broken git hook installer.", severity="super-critical")
        assert "coerced to medium" in result
        assert "[medium]" in result

@pytest.mark.asyncio
async def test_record_problem_duplicate_detection():
    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        # Mock a similar problem found in search
        mock_duplicate = MagicMock()
        mock_duplicate.type = "Problem"
        mock_duplicate.score = 0.95
        mock_duplicate.name = "Watcher memory leak"
        mock_duplicate.uuid = "old-prob-999"
        
        mock_client.search.return_value = [mock_duplicate]
        mock_get_client.return_value = mock_client
        
        result = await record_problem(text="Memory leak in watcher daemon.")
        assert "similar problem already recorded" in result
        assert "old-prob-999" in result
        assert not mock_client.add_episode.called

@pytest.mark.asyncio
async def test_resolve_problem_closes_node():
    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        # Mock finding the problem
        mock_res = MagicMock()
        mock_res.records = [{"text": "Broken auth", "resolved_at": None}]
        mock_client.driver.execute_query.side_effect = [mock_res, MagicMock()]
        
        mock_get_client.return_value = mock_client
        
        with patch("memex.mcp_server.tools_write._get_or_create_session", return_value="sess-1"):
            result = await resolve_problem(problem_id="prob-1", resolution_text="Fixed the bug in auth.")
            assert "problem resolved" in result
            assert "Broken auth" in result
            assert mock_client.add_episode.called

@pytest.mark.asyncio
async def test_resolve_problem_not_found_returns_message():
    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.driver.execute_query.return_value = MagicMock(records=[])
        mock_get_client.return_value = mock_client
        
        result = await resolve_problem(problem_id="missing", resolution_text="fixed it anyway")
        assert "not found" in result

@pytest.mark.asyncio
async def test_resolve_problem_already_resolved_returns_message():
    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_res = MagicMock()
        mock_res.records = [{"text": "P1", "resolved_at": datetime.now(), "resolution_summary": "Done"}]
        mock_client.driver.execute_query.return_value = mock_res
        mock_get_client.return_value = mock_client
        
        result = await resolve_problem(problem_id="p1", resolution_text="resolving again")
        assert "already resolved" in result

@pytest.mark.asyncio
async def test_invalidate_edge_sets_valid_until():
    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_res = MagicMock()
        mock_res.records = [{"source": "M1", "target": "S1", "edge_type": "EXPORTS", "valid_until": None}]
        mock_client.driver.execute_query.side_effect = [mock_res, MagicMock()]
        mock_get_client.return_value = mock_client
        
        result = await invalidate_edge(edge_id="edge-123", reason="Symbol moved to another file.")
        assert "edge invalidated" in result
        assert "M1" in result
        assert "S1" in result
        assert mock_client.driver.execute_query.call_count == 2

@pytest.mark.asyncio
async def test_invalidate_edge_not_found_returns_message():
    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.driver.execute_query.return_value = MagicMock(records=[])
        mock_get_client.return_value = mock_client
        
        result = await invalidate_edge(edge_id="missing", reason="invalid")
        assert "not found" in result

@pytest.mark.asyncio
async def test_invalidate_edge_already_invalidated_returns_message():
    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_res = MagicMock()
        mock_res.records = [{"valid_until": datetime.now(), "old_reason": "Outdated"}]
        mock_client.driver.execute_query.return_value = mock_res
        mock_get_client.return_value = mock_client
        
        result = await invalidate_edge(edge_id="e1", reason="new reason")
        assert "already invalidated" in result
