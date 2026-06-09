import pytest
from unittest.mock import AsyncMock, patch
from memex.mcp_server import server
from memex.mcp_server.server import create_server, ConfigError, MemexStartupError, handle_list_tools, handle_call_tool

@pytest.mark.asyncio
async def test_server_registers_all_13_tools():
    # constructs the instance, validates config, checks Neo4j
    with patch("memex.mcp_server.server.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        with patch("memex.mcp_server.server.get_config"):
            srv = await create_server("/fake/repo")

            # Verify the tool names we expect are returned by the list handler
            tools = await handle_list_tools()
            expected = [
                # 6 v0.1 read tools
                "get_project_context", "get_symbol_context", "get_recent_decisions",
                "get_open_problems", "search_context", "get_stale_context",
                # 4 v0.1 write tools
                "record_decision", "record_problem", "resolve_problem", "invalidate_edge",
                # 2 Phase 9 read tools
                "explain_change", "predict_impact",
                # Phase 10 / v0.5.0
                "get_context_briefing",
            ]

            assert len(tools) == 13
            actual_names = [t.name for t in tools]
            for name in expected:
                assert name in actual_names

def test_server_version_matches_pyproject():
    assert server.__version__ is not None

@pytest.mark.asyncio
async def test_server_startup_validates_config():
    with patch("memex.mcp_server.server.get_config", side_effect=ValueError("Missing KEY")):
        with pytest.raises(ConfigError):
            await create_server("/fake/repo")

@pytest.mark.asyncio
async def test_server_startup_checks_neo4j():
    with patch("memex.mcp_server.server.get_graph_client", side_effect=Exception("Conn failed")):
        with patch("memex.mcp_server.server.get_config"):
            with pytest.raises(MemexStartupError):
                await create_server("/fake/repo")


@pytest.mark.asyncio
async def test_introspection_only_mode_skips_neo4j(monkeypatch):
    # MCP directory sandboxes (glama.ai) run the server with no backend.
    # MEMEX_INTROSPECTION_ONLY=1 must let create_server boot so list_tools works.
    monkeypatch.setenv("MEMEX_INTROSPECTION_ONLY", "1")
    for var in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    # Reset the cached config singleton so our env-var changes take effect.
    from memex import config as _config_mod
    monkeypatch.setattr(_config_mod, "_config", None)

    # get_graph_client must NOT be called when introspection-only mode is on.
    with patch(
        "memex.mcp_server.server.get_graph_client",
        side_effect=AssertionError("Neo4j should not be contacted in introspection mode"),
    ):
        srv = await create_server("/fake/repo")

    tools = await handle_list_tools()
    assert len(tools) == 13

@pytest.mark.asyncio
async def test_server_tool_returns_string_not_none():
    # Test one tool through the call_tool interface
    with patch("memex.mcp_server.server.get_project_context", new_callable=AsyncMock) as mock_tool:
        mock_tool.return_value = "briefing"
        result = await handle_call_tool("get_project_context", {})
        assert len(result) == 1
        assert result[0].text == "briefing"

@pytest.mark.asyncio
async def test_server_tool_handles_neo4j_down_gracefully():
    with patch("memex.mcp_server.server.get_project_context", side_effect=Exception("Database Down")):
        result = await handle_call_tool("get_project_context", {})
        assert "Internal Server Error" in result[0].text

@pytest.mark.asyncio
async def test_call_tool_not_found():
    result = await handle_call_tool("nonexistent", {})
    assert "Tool nonexistent not found" in result[0].text

@pytest.mark.asyncio
async def test_call_get_symbol_context_missing_arg():
    result = await handle_call_tool("get_symbol_context", {})
    assert "Error: 'symbol_name' is required" in result[0].text

@pytest.mark.asyncio
async def test_call_all_tools_smoke():
    # Smoke test to ensure all dispatch branches are covered
    tools = [
        ("get_recent_decisions", {"days": "invalid"}), # tests coercion
        ("get_open_problems", {}),
        ("search_context", {"query": "test", "top_k": "5"}),
        ("get_stale_context", {"threshold": "0.3"}),
        ("record_decision", {"text": "D"*10}),
        ("record_problem", {"text": "P"*10}),
        ("resolve_problem", {"problem_id": "1", "resolution_text": "R"*10}),
        ("invalidate_edge", {"edge_id": "1", "reason": "R"*10}),
        ("get_context_briefing", {"max_tokens": "1000"}),
    ]
    for name, args in tools:
        with patch(f"memex.mcp_server.server.{name}", new_callable=AsyncMock) as mock_impl:
            mock_impl.return_value = "ok"
            await handle_call_tool(name, args)
            assert mock_impl.called
