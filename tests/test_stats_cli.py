"""CLI tests for the `memex stats` command."""

from __future__ import annotations
import json
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from memex.cli import run_stats_command


def test_run_stats_command_human_readable():
    """Verify that human-readable console stats output aggregates periods, top tools, and agents."""
    mock_stats = {
        "today": {"tool_calls": 1, "tokens_returned": 100, "tokens_naive": 500, "tokens_saved": 400, "reduction_pct": 80.0},
        "last_7_days": {"tool_calls": 2, "tokens_returned": 200, "tokens_naive": 1000, "tokens_saved": 800, "reduction_pct": 80.0, "avg_savings_per_call": 400.0},
        "last_30_days": {"tool_calls": 3, "tokens_returned": 300, "tokens_naive": 1500, "tokens_saved": 1200, "reduction_pct": 80.0, "avg_daily_savings": 40.0},
        "lifetime": {"tool_calls": 4, "tokens_returned": 400, "tokens_naive": 2000, "tokens_saved": 1600, "reduction_pct": 80.0},
        "top_tools": [{"tool_name": "search_context", "calls": 2, "tokens_saved": 1000}],
        "agents": [{"agent": "Claude Code", "calls": 2, "pct": 50.0, "tokens_saved": 1000}],
        "validation_health": {"validated": 5, "unvalidated": 2, "corroborated": 3, "last_review_days_ago": 1}
    }
    
    with patch("memex.graph.stats.get_stats_data", new_callable=AsyncMock, return_value=mock_stats):
        import io
        from contextlib import redirect_stdout
        f = io.StringIO()
        with redirect_stdout(f):
            asyncio.run(run_stats_command(repo_root="/fake/repo", days=30, as_json=False))
        output = f.getvalue()
        
        assert "memex statistics" in output
        assert "today" in output
        assert "last 7 days" in output
        assert "last 30 days" in output
        assert "lifetime" in output
        assert "search_context" in output
        assert "Claude Code" in output
        assert "validation health" in output
        assert "validated:       5" in output


def test_run_stats_command_json():
    """Verify that --json output prints valid raw JSON serialization."""
    mock_stats = {
        "today": {"tool_calls": 0, "tokens_returned": 0, "tokens_naive": 0, "tokens_saved": 0, "reduction_pct": 0.0},
        "last_7_days": {"tool_calls": 0, "tokens_returned": 0, "tokens_naive": 0, "tokens_saved": 0, "reduction_pct": 0.0, "avg_savings_per_call": 0.0},
        "last_30_days": {"tool_calls": 0, "tokens_returned": 0, "tokens_naive": 0, "tokens_saved": 0, "reduction_pct": 0.0, "avg_daily_savings": 0.0},
        "lifetime": {"tool_calls": 0, "tokens_returned": 0, "tokens_naive": 0, "tokens_saved": 0, "reduction_pct": 0.0},
        "top_tools": [],
        "agents": [],
        "validation_health": {"validated": 0, "unvalidated": 0, "corroborated": 0, "last_review_days_ago": None}
    }
    
    with patch("memex.graph.stats.get_stats_data", new_callable=AsyncMock, return_value=mock_stats):
        import io
        from contextlib import redirect_stdout
        f = io.StringIO()
        with redirect_stdout(f):
            asyncio.run(run_stats_command(repo_root="/fake/repo", days=30, as_json=True))
        raw_json = json.loads(f.getvalue())
        
        assert raw_json["today"]["tool_calls"] == 0
        assert raw_json["lifetime"]["tool_calls"] == 0
        assert raw_json["validation_health"]["validated"] == 0
