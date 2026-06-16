"""Unit tests for the stats aggregation service layer (memex/graph/stats.py)."""

from __future__ import annotations
import os
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
from types import SimpleNamespace

from memex.graph.telemetry import TelemetryDB, normalize_repo_path
from memex.graph.stats import get_stats_data


@pytest.fixture
def temp_db_path(tmp_path):
    """Fixture returning a temporary telemetry database path."""
    return tmp_path / "telemetry_stats.db"


@pytest.mark.asyncio
async def test_stats_aggregation_empty(temp_db_path):
    """Verify statistics return zero/default values when telemetry DB is empty."""
    with patch("memex.graph.telemetry.get_telemetry_db_path", return_value=temp_db_path), \
         patch("memex.graph.stats.get_graph_client") as mock_graph_client:
        
        # Mock Neo4j driver
        mock_driver = MagicMock()
        mock_driver.execute_query = AsyncMock(return_value=MagicMock(records=[]))
        mock_graph_client.return_value = MagicMock(driver=mock_driver)
        
        stats = await get_stats_data()
        
        # Schema assertions
        assert stats["today"]["tool_calls"] == 0
        assert stats["today"]["tokens_saved"] == 0
        assert stats["last_7_days"]["avg_savings_per_call"] == 0.0
        assert stats["last_30_days"]["avg_daily_savings"] == 0.0
        assert stats["lifetime"]["tool_calls"] == 0
        assert stats["top_tools"] == []
        assert stats["agents"] == []
        assert stats["validation_health"]["validated"] == 0


@pytest.mark.asyncio
async def test_stats_aggregation_periods_and_filtering(temp_db_path):
    """Verify stats filter by date periods and repository paths correctly."""
    with patch("memex.graph.telemetry.get_telemetry_db_path", return_value=temp_db_path), \
         patch("memex.graph.stats.get_graph_client") as mock_graph_client:
        
        mock_driver = MagicMock()
        mock_driver.execute_query = AsyncMock(return_value=MagicMock(records=[
            MagicMock(data=lambda: {
                "validated": 2,
                "unvalidated": 1,
                "corroborated": 1,
                "last_validated_at": "2026-06-16T00:00:00Z"
            })
        ]))
        mock_graph_client.return_value = MagicMock(driver=mock_driver)
        
        db = TelemetryDB(db_path=temp_db_path)
        
        # 1. Seed calls for repo A
        repo_a = normalize_repo_path("/repo/a")
        repo_b = normalize_repo_path("/repo/b")
        
        # Today
        db.record_call("get_context_briefing", repo_a, "claude-code", 200, 1000) # Saved 800
        
        # 5 days ago
        five_days_ago = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO tool_calls (tool_name, called_at, repo_path, agent, tokens_returned, tokens_naive, tokens_saved) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("search_context", five_days_ago, repo_a, "gemini-cli", 100, 500, 400)
            )
            conn.commit()
            
        # 25 days ago
        twenty_five_days_ago = (datetime.now(timezone.utc) - timedelta(days=25)).isoformat()
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO tool_calls (tool_name, called_at, repo_path, agent, tokens_returned, tokens_naive, tokens_saved) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("get_symbol_context", twenty_five_days_ago, repo_a, "cursor", 50, 250, 200)
            )
            conn.commit()
            
        # 45 days ago (Lifetime only)
        forty_five_days_ago = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO tool_calls (tool_name, called_at, repo_path, agent, tokens_returned, tokens_naive, tokens_saved) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("get_project_context", forty_five_days_ago, repo_a, "unknown-agent", 100, 200, 100)
            )
            conn.commit()
            
        # Call for repo B (should be filtered out when querying repo A)
        db.record_call("search_context", repo_b, "claude-code", 100, 1000) # Saved 900
        
        # Fetch stats global
        global_stats = await get_stats_data()
        assert global_stats["lifetime"]["tool_calls"] == 5
        
        # Fetch stats for repo A
        stats = await get_stats_data(repo_path=repo_a)
        
        assert stats["today"]["tool_calls"] == 1
        assert stats["today"]["tokens_saved"] == 800
        
        # 5 days ago + Today = 2 calls in last 7 days
        assert stats["last_7_days"]["tool_calls"] == 2
        assert stats["last_7_days"]["tokens_saved"] == 1200 # 800 + 400
        assert stats["last_7_days"]["avg_savings_per_call"] == 600.0
        
        # Today + 5d + 25d = 3 calls in last 30 days
        assert stats["last_30_days"]["tool_calls"] == 3
        assert stats["last_30_days"]["tokens_saved"] == 1400 # 800 + 400 + 200
        assert stats["last_30_days"]["avg_daily_savings"] == round(1400 / 30.0, 1)
        
        # Today + 5d + 25d + 45d = 4 calls lifetime for repo A
        assert stats["lifetime"]["tool_calls"] == 4
        assert stats["lifetime"]["tokens_saved"] == 1500 # 800 + 400 + 200 + 100
        
        # Top tools check
        assert len(stats["top_tools"]) == 4
        assert stats["top_tools"][0]["tool_name"] == "get_context_briefing"
        assert stats["top_tools"][0]["tokens_saved"] == 800
        
        # Agent clients share check
        # Total calls = 4 (claude-code: 1, gemini-cli: 1, cursor: 1, unknown-agent: 1)
        # unknown-agent should map to Other.
        agents = {a["agent"]: a for a in stats["agents"]}
        assert agents["Claude Code"]["calls"] == 1
        assert agents["Claude Code"]["pct"] == 25.0
        assert agents["Gemini CLI"]["calls"] == 1
        assert agents["Cursor"]["calls"] == 1
        assert agents["Other"]["calls"] == 1
        assert agents["Other"]["pct"] == 25.0
