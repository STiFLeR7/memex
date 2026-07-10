"""Tests for the NET-16 governance-report composition module
(memex/graph/governance_report.py).

Per the plan: generate_report() must compose the three already-existing
computations (get_stats_data / _fetch_pending_decisions / current_confidence)
without writing any new Cypher query. These tests mock the composed
functions and assert on call shape + derived-field behavior only.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import pytest

from memex.graph.governance_report import GovernanceReport, generate_report


def _make_stats() -> dict:
    """A minimal, distinguishable get_stats_data()-shaped return value —
    each bucket carries a unique marker so tests can assert which bucket
    period_telemetry selected."""
    return {
        "today": {"marker": "today", "tool_calls": 1},
        "last_7_days": {"marker": "last_7_days", "tool_calls": 7},
        "last_30_days": {"marker": "last_30_days", "tool_calls": 30},
        "lifetime": {"marker": "lifetime", "tool_calls": 999},
        "top_tools": [],
        "agents": [],
        "validation_health": {"validated": 0, "unvalidated": 0, "corroborated": 0, "last_review_days_ago": None},
    }


# ---------------------------------------------------------------------------
# Test 1 — composition only, no direct Neo4j calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_report_composes_without_reinventing_queries():
    stats = _make_stats()
    with (
        patch(
            "memex.graph.governance_report.get_stats_data",
            new=AsyncMock(return_value=stats),
        ) as mock_get_stats,
        patch(
            "memex.graph.governance_report._fetch_pending_decisions",
            new=AsyncMock(return_value=[]),
        ) as mock_fetch_pending,
    ):
        report = await generate_report("/repo", period_days=7)

    mock_get_stats.assert_awaited_once_with("/repo")
    mock_fetch_pending.assert_awaited_once_with("/repo")
    assert isinstance(report, GovernanceReport)
    assert report.repo_path == "/repo"
    assert report.telemetry == stats


# ---------------------------------------------------------------------------
# Test 2 — confidence bucketing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confidence_distribution_buckets_from_computed_confidence():
    stats = _make_stats()
    rows = [
        {"_computed_confidence": 0.8},
        {"_computed_confidence": 0.5},
        {"_computed_confidence": 0.2},
    ]
    with (
        patch("memex.graph.governance_report.get_stats_data", new=AsyncMock(return_value=stats)),
        patch("memex.graph.governance_report._fetch_pending_decisions", new=AsyncMock(return_value=rows)),
    ):
        report = await generate_report("/repo", period_days=7)

    assert report.confidence_distribution == {"high": 1, "mid": 1, "stale": 1}


@pytest.mark.asyncio
async def test_confidence_distribution_falls_back_to_current_confidence_when_missing():
    stats = _make_stats()
    row_without_computed = {"base_confidence": 0.9, "validated": True}
    with (
        patch("memex.graph.governance_report.get_stats_data", new=AsyncMock(return_value=stats)),
        patch(
            "memex.graph.governance_report._fetch_pending_decisions",
            new=AsyncMock(return_value=[row_without_computed]),
        ),
        patch("memex.graph.governance_report.current_confidence", return_value=0.75) as mock_cc,
    ):
        report = await generate_report("/repo", period_days=7)

    mock_cc.assert_called_once_with(row_without_computed)
    assert report.confidence_distribution == {"high": 1, "mid": 0, "stale": 0}


# ---------------------------------------------------------------------------
# Test 3 — modules_touched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_modules_touched_sorted_deduplicated_union():
    stats = _make_stats()
    rows = [
        {"_computed_confidence": 0.5, "modules": ["b.py", "a.py"]},
        {"_computed_confidence": 0.5, "modules": ["a.py"]},
        {"_computed_confidence": 0.5},  # missing modules key
        {"_computed_confidence": 0.5, "modules": []},  # empty modules list
    ]
    with (
        patch("memex.graph.governance_report.get_stats_data", new=AsyncMock(return_value=stats)),
        patch("memex.graph.governance_report._fetch_pending_decisions", new=AsyncMock(return_value=rows)),
    ):
        report = await generate_report("/repo", period_days=7)

    assert report.modules_touched == ["a.py", "b.py"]


# ---------------------------------------------------------------------------
# Test 4 — generated_at + period_days resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generated_at_is_iso_utc_and_period_days_uses_explicit_arg():
    stats = _make_stats()
    with (
        patch("memex.graph.governance_report.get_stats_data", new=AsyncMock(return_value=stats)),
        patch("memex.graph.governance_report._fetch_pending_decisions", new=AsyncMock(return_value=[])),
    ):
        report = await generate_report("/repo", period_days=14)

    # Round-trips through fromisoformat without raising.
    datetime.fromisoformat(report.generated_at)
    assert report.period_days == 14


@pytest.mark.asyncio
async def test_period_days_falls_back_to_config_when_none():
    stats = _make_stats()
    mock_cfg = SimpleNamespace(report_period_days=7)
    with (
        patch("memex.graph.governance_report.get_config", return_value=mock_cfg),
        patch("memex.graph.governance_report.get_stats_data", new=AsyncMock(return_value=stats)),
        patch("memex.graph.governance_report._fetch_pending_decisions", new=AsyncMock(return_value=[])),
    ):
        report = await generate_report("/repo", period_days=None)

    assert report.period_days == 7


# ---------------------------------------------------------------------------
# Test 5 — period_telemetry bucket selection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "period_days,expected_key",
    [
        (1, "today"),
        (7, "last_7_days"),
        (30, "last_30_days"),
        (45, "lifetime"),
    ],
)
async def test_period_telemetry_selects_nearest_bucket(period_days, expected_key):
    stats = _make_stats()
    with (
        patch("memex.graph.governance_report.get_stats_data", new=AsyncMock(return_value=stats)),
        patch("memex.graph.governance_report._fetch_pending_decisions", new=AsyncMock(return_value=[])),
    ):
        report = await generate_report("/repo", period_days=period_days)

    assert report.period_telemetry == stats[expected_key]
