import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from memex.mcp_server.tools_read import (
    get_project_context,
    get_symbol_context,
    get_recent_decisions,
    get_open_problems,
    search_context,
    get_stale_context
)
from memex.mcp_server.formatter import CHARS_PER_TOKEN

@pytest.mark.asyncio
async def test_get_project_context_returns_markdown():
    with patch("memex.mcp_server.tools_read.get_node_counts", return_value={"modules": 1, "symbols": 1, "decisions": 1, "problems": 1}), \
         patch("memex.mcp_server.tools_read.get_active_modules", return_value=[]), \
         patch("memex.mcp_server.tools_read.get_recent_decisions_raw", return_value=[]), \
         patch("memex.mcp_server.tools_read.get_open_problems_raw", return_value=[]), \
         patch("memex.mcp_server.tools_read.get_stale_edges", return_value=[]):
        
        result = await get_project_context()
        assert "# memex project context" in result
        assert "neo4j nodes: 1 modules" in result

@pytest.mark.asyncio
async def test_get_project_context_scope_filters_correctly():
    with patch("memex.mcp_server.tools_read.get_node_counts", return_value={"modules": 1, "symbols": 1, "decisions": 1, "problems": 1}), \
         patch("memex.mcp_server.tools_read.get_active_modules") as mock_modules, \
         patch("memex.mcp_server.tools_read.get_recent_decisions_raw"), \
         patch("memex.mcp_server.tools_read.get_open_problems_raw"), \
         patch("memex.mcp_server.tools_read.get_stale_edges", return_value=[]):
        
        await get_project_context(scope="src/auth")
        mock_modules.assert_called_with(since_days=30, scope="src/auth")

@pytest.mark.asyncio
async def test_get_project_context_empty_graph_returns_gracefully():
    with patch("memex.mcp_server.tools_read.get_node_counts", return_value={"modules": 0, "symbols": 0, "decisions": 0, "problems": 0}), \
         patch("memex.mcp_server.tools_read.get_active_modules", return_value=[]), \
         patch("memex.mcp_server.tools_read.get_recent_decisions_raw", return_value=[]), \
         patch("memex.mcp_server.tools_read.get_open_problems_raw", return_value=[]), \
         patch("memex.mcp_server.tools_read.get_stale_edges", return_value=[]):
        
        result = await get_project_context()
        assert "no active modules found" in result
        assert "no recent decisions" in result

@pytest.mark.asyncio
async def test_get_symbol_context_found():
    symbol_data = {
        "name": "login", "kind": "fn", "file": "auth.py", "line": 10, 
        "signature": "def login()", "confidence": 1.0, "stale": False
    }
    with patch("memex.mcp_server.tools_read.get_symbol_by_name", return_value=symbol_data), \
         patch("memex.mcp_server.tools_read.get_symbol_callers", return_value=[]), \
         patch("memex.mcp_server.tools_read.get_symbol_callees", return_value=[]), \
         patch("memex.mcp_server.tools_read.get_symbol_decisions", return_value=[]), \
         patch("memex.mcp_server.tools_read.get_symbol_problems", return_value=[]):
        
        result = await get_symbol_context("login")
        assert "# symbol: login" in result
        assert "kind: fn" in result
        assert "file: auth.py:10" in result

@pytest.mark.asyncio
async def test_get_symbol_context_not_found_returns_fuzzy_match_message():
    with patch("memex.mcp_server.tools_read.get_symbol_by_name", return_value=None), \
         patch("memex.mcp_server.tools_read.get_graph_client") as mock_client:
        
        # We must use a separate mock for the search result to avoid MagicMock recursing into .name
        mock_result = MagicMock()
        mock_result.name = "login_v2"
        
        mock_search = AsyncMock()
        mock_search.return_value = [mock_result]
        mock_client.return_value.search = mock_search
        
        result = await get_symbol_context("loginn")
        assert "Symbol 'loginn' not found" in result
        assert "Did you mean 'login_v2'?" in result

@pytest.mark.asyncio
async def test_get_recent_decisions_caps_at_20():
    mock_data = [{"text": f"D{i}", "date": MagicMock(), "scope": "local", "rationale": "R", "sha": "S", "module_paths": []} for i in range(25)]
    with patch("memex.mcp_server.tools_read.get_recent_decisions_raw", return_value=mock_data):
        result = await get_recent_decisions()
        # count occurrences of "  scope:" as a proxy for entries
        assert result.count("  scope:") == 20
        assert "showing 20 of 25" in result

@pytest.mark.asyncio
async def test_get_recent_decisions_no_results_returns_message():
    with patch("memex.mcp_server.tools_read.get_recent_decisions_raw", return_value=[]):
        result = await get_recent_decisions(days=5)
        assert "no decisions recorded in the last 5 days" in result

@pytest.mark.asyncio
async def test_get_open_problems_sorted_by_severity():
    mock_problems = [
        {"severity": "low", "text": "P1", "module": "M1", "date": MagicMock(), "agent": "W"},
        {"severity": "critical", "text": "P2", "module": "M2", "date": MagicMock(), "agent": "W"}
    ]
    with patch("memex.mcp_server.tools_read.get_open_problems_raw", return_value=mock_problems):
        result = await get_open_problems()
        # [CRITICAL] should appear before [LOW]
        assert result.index("[CRITICAL]") < result.index("[LOW]")

@pytest.mark.asyncio
async def test_search_context_empty_query_guard():
    result = await search_context("   ")
    assert "query must be non-empty" in result

@pytest.mark.asyncio
async def test_search_context_top_k_clamped_at_20():
    with patch("memex.mcp_server.tools_read.get_graph_client") as mock_client:
        mock_search = AsyncMock(return_value=[])
        mock_client.return_value.search = mock_search
        await search_context("test", top_k=50)
        mock_search.assert_called_with("test", num_results=20)

@pytest.mark.asyncio
async def test_search_context_graphiti_error_returns_fallback():
    with patch("memex.mcp_server.tools_read.get_graph_client") as mock_client:
        mock_client.return_value.search.side_effect = Exception("API Down")
        result = await search_context("test")
        assert "search temporarily unavailable" in result

@pytest.mark.asyncio
async def test_get_stale_context_threshold_clamped():
    with patch("memex.mcp_server.tools_read.get_stale_edges", return_value=[]) as mock_stale:
        await get_stale_context(threshold=1.5)
        mock_stale.assert_called_with(threshold=1.0, limit=51)

@pytest.mark.asyncio
async def test_get_stale_context_no_stale_returns_message():
    with patch("memex.mcp_server.tools_read.get_stale_edges", return_value=[]):
        result = await get_stale_context(threshold=0.2)
        assert "no stale edges below threshold 0.20" in result

def test_formatter_truncates_at_token_budget():
    from memex.mcp_server.formatter import format_project_context
    counts = {"modules": 0, "symbols": 0, "decisions": 0, "problems": 0}
    # 2000 tokens * 4 chars/token = 8000 chars
    # We need > 8000 chars total. Each module line is capped at ~150 chars.
    # 100 modules * 150 chars = 15,000 chars total.
    long_modules = [{"path": f"module_{i}.py", "description": "X"*200, "symbols": 0} for i in range(100)]
    
    result = format_project_context("repo", counts, long_modules, [], [], 0)
    assert "[truncated — use scope= or module= parameter to narrow]" in result
    assert len(result) <= 2000 * CHARS_PER_TOKEN
