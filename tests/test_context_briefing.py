"""Unit tests for the get_context_briefing MCP read tool.

Ensures proper section prioritization, token budget enforcement, scoping,
and telemetry recording without requiring a live Neo4j or Gemini backend.
"""

from __future__ import annotations
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import pytest

from memex.mcp_server.tools_read import get_context_briefing


# The mock decisions below carry fixed ``created_at`` dates (2026-06-08/09).
# ``get_context_briefing`` scores each decision via ``current_confidence`` and
# drops anything <= 0.5, so without a frozen clock the unvalidated decision
# decays below the cutoff once ~21 days of real wall-clock elapse — making
# ``test_briefing_includes_all_sections_when_budget_allows`` a time bomb.
# Freeze the confidence clock to just after the fixture dates so the intended
# scenario (validated 1-day-old, unvalidated 2-day-old, both fresh) is stable.
_FROZEN_NOW = datetime(2026, 6, 10, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _frozen_confidence_clock():
    with patch("memex.graph.confidence._utc_now", return_value=_FROZEN_NOW):
        yield


@pytest.fixture
def mock_queries():
    """Provides standard mock data for all briefing query functions."""
    # Priority 1: Cluster summaries
    mock_clusters = [
        {
            "name": "cluster-graph",
            "summary": "Graph client and operations.",
            "description": "Legacy graph description.",
            "members": ["memex/graph/client.py", "memex/graph/otel.py"],
            "module_count": 2,
        },
        {
            "name": "cluster-watcher",
            "summary": "Watcher daemon and file listener.",
            "description": "Legacy watcher description.",
            "members": ["memex/watcher/daemon.py"],
            "module_count": 1,
        }
    ]

    # Priority 2: Key Decisions
    mock_decisions = [
        {
            "text": "Adopt Streamable HTTP transport",
            "base_confidence": 0.9,
            "created_at": "2026-06-09T00:00:00Z",
            "validated": True,
        },
        {
            "text": "Implement OpenTelemetry metrics",
            "base_confidence": 0.8,
            "created_at": "2026-06-08T00:00:00Z",
            "validated": False,
        }
    ]

    # Priority 3: Open Problems
    mock_problems = [
        {
            "text": "Fix connection leakage in driver pool",
            "severity": "critical",
        },
        {
            "text": "Logging format consistency mismatch",
            "severity": "medium",
        }
    ]

    # Priority 4: Stale Edges
    mock_stale = [
        {
            "source": "memex/watcher/daemon.py",
            "target": "memex/graph/client.py",
            "edge_type": "DEPENDS_ON",
            "confidence": 0.25,
        }
    ]

    return mock_clusters, mock_decisions, mock_problems, mock_stale


def test_emit_validated_ratio_counts_validated_and_corroborated():
    """The validated_ratio gauge must count decisions that are validated OR
    corroborated (D3). Regression guard: get_recent_decisions_raw must surface
    the `corroborated` field, else corroborated-only decisions are undercounted."""
    from memex.mcp_server.tools_read import _emit_validated_ratio

    decisions = [
        {"validated": True, "corroborated": False},   # counts
        {"validated": False, "corroborated": True},    # counts (corroborated-only)
        {"validated": False, "corroborated": False},   # does not count
        {"validated": False},                          # missing key -> does not count
    ]
    with patch("memex.graph.otel.record_validated_ratio") as mock_ratio:
        _emit_validated_ratio(decisions)
        mock_ratio.assert_called_once_with(0.5)  # 2 of 4


def test_get_recent_decisions_raw_returns_corroborated_field():
    """The raw decisions query must RETURN `corroborated` so validation-health
    telemetry can distinguish corroborated-only decisions."""
    import inspect
    from memex.mcp_server import queries
    src = inspect.getsource(queries.get_recent_decisions_raw)
    assert "as corroborated" in src


@pytest.mark.asyncio
async def test_briefing_includes_all_sections_when_budget_allows(mock_queries):
    clusters, decisions, problems, stale = mock_queries

    with patch("memex.mcp_server.tools_read.get_cluster_level_context", new_callable=AsyncMock, return_value=clusters) as mock_get_clusters, \
         patch("memex.mcp_server.tools_read.get_recent_decisions_raw", new_callable=AsyncMock, return_value=decisions) as mock_get_decisions, \
         patch("memex.mcp_server.tools_read.get_open_problems_raw", new_callable=AsyncMock, return_value=problems) as mock_get_problems, \
         patch("memex.mcp_server.tools_read.get_stale_edges", new_callable=AsyncMock, return_value=stale) as mock_get_stale:

        briefing = await get_context_briefing(max_tokens=4000, repo="/fake")

        assert "# memex Context Briefing" in briefing
        assert "## Architecture Overview" in briefing
        assert "cluster-graph" in briefing
        assert "cluster-watcher" in briefing

        assert "## Key Decisions (last 7 days)" in briefing
        assert "Adopt Streamable HTTP transport" in briefing
        assert "Implement OpenTelemetry metrics" in briefing

        assert "## Open Problems" in briefing
        assert "Fix connection leakage in driver pool" in briefing

        assert "## Needs Attention" in briefing
        assert "memex/watcher/daemon.py" in briefing


@pytest.mark.asyncio
async def test_briefing_respects_token_budget(mock_queries):
    clusters, decisions, problems, stale = mock_queries

    with patch("memex.mcp_server.tools_read.get_cluster_level_context", new_callable=AsyncMock, return_value=clusters), \
         patch("memex.mcp_server.tools_read.get_recent_decisions_raw", new_callable=AsyncMock, return_value=decisions), \
         patch("memex.mcp_server.tools_read.get_open_problems_raw", new_callable=AsyncMock, return_value=problems), \
         patch("memex.mcp_server.tools_read.get_stale_edges", new_callable=AsyncMock, return_value=stale):

        # Extremely tight budget
        max_tokens = 70
        briefing = await get_context_briefing(max_tokens=max_tokens, repo="/fake")

        tokens = len(briefing) // 4
        assert tokens <= max_tokens, f"Expected tokens {tokens} to be <= {max_tokens}"
        # Since the budget is extremely tight, it should either truncate or skip later sections
        assert "## Needs Attention" not in briefing


@pytest.mark.asyncio
async def test_briefing_prioritizes_clusters_over_problems(mock_queries):
    clusters, decisions, problems, stale = mock_queries

    with patch("memex.mcp_server.tools_read.get_cluster_level_context", new_callable=AsyncMock, return_value=clusters), \
         patch("memex.mcp_server.tools_read.get_recent_decisions_raw", new_callable=AsyncMock, return_value=decisions), \
         patch("memex.mcp_server.tools_read.get_open_problems_raw", new_callable=AsyncMock, return_value=problems), \
         patch("memex.mcp_server.tools_read.get_stale_edges", new_callable=AsyncMock, return_value=stale):

        # A budget that fits clusters and maybe some decisions, but not problems/stale
        briefing = await get_context_briefing(max_tokens=120, repo="/fake")

        assert "## Architecture Overview" in briefing
        assert "## Key Decisions" in briefing or "truncated" in briefing
        # Problems and stale edges are deprioritized and should be absent/truncated out
        assert "## Open Problems" not in briefing
        assert "## Needs Attention" not in briefing


@pytest.mark.asyncio
async def test_briefing_scopes_to_module(mock_queries):
    clusters, decisions, problems, stale = mock_queries

    with patch("memex.mcp_server.tools_read.get_cluster_level_context", new_callable=AsyncMock, return_value=clusters) as mock_get_clusters, \
         patch("memex.mcp_server.tools_read.get_recent_decisions_raw", new_callable=AsyncMock, return_value=decisions) as mock_get_decisions, \
         patch("memex.mcp_server.tools_read.get_open_problems_raw", new_callable=AsyncMock, return_value=problems) as mock_get_problems, \
         patch("memex.mcp_server.tools_read.get_stale_edges", new_callable=AsyncMock, return_value=stale) as mock_get_stale:

        # Scope is "memex/graph"
        briefing = await get_context_briefing(max_tokens=4000, scope="memex/graph", repo="/fake")

        # Cluster summaries: cluster-watcher has members ["memex/watcher/daemon.py"], which doesn't match "memex/graph"
        # So only cluster-graph should be in the output!
        assert "cluster-graph" in briefing
        assert "cluster-watcher" not in briefing

        # Verify query functions were called with the correct module scope
        mock_get_decisions.assert_called_once_with(since_days=7, module="memex/graph", limit=10, repo="/fake")
        mock_get_problems.assert_called_once_with(module="memex/graph", repo="/fake")


@pytest.mark.asyncio
async def test_briefing_records_telemetry(mock_queries):
    clusters, decisions, problems, stale = mock_queries

    with patch("memex.mcp_server.tools_read.get_cluster_level_context", new_callable=AsyncMock, return_value=clusters), \
         patch("memex.mcp_server.tools_read.get_recent_decisions_raw", new_callable=AsyncMock, return_value=decisions), \
         patch("memex.mcp_server.tools_read.get_open_problems_raw", new_callable=AsyncMock, return_value=problems), \
         patch("memex.mcp_server.tools_read.get_stale_edges", new_callable=AsyncMock, return_value=stale), \
         patch("memex.graph.telemetry.record_tool_call", new_callable=AsyncMock) as mock_record:

        await get_context_briefing(max_tokens=2000, repo="/fake")

        mock_record.assert_called_once()
        args, kwargs = mock_record.call_args
        assert args[0] == "get_context_briefing"
        assert args[2] == "/fake"
        assert isinstance(args[1], int)  # tokens returned
