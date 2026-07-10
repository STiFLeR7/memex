"""Tests for the NET-16 governance-report composition module
(memex/graph/governance_report.py).

Per the plan: generate_report() must compose the three already-existing
computations (get_stats_data / _fetch_pending_decisions / current_confidence)
without writing any new Cypher query. These tests mock the composed
functions and assert on call shape + derived-field behavior only.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import pytest

from memex.graph.governance_report import (
    GovernanceReport,
    generate_report,
    reports_dir_for,
    render_markdown,
    write_report,
    find_latest_report,
    REPORT_NOTICE,
)
from memex.config import canonical_repo_path


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


# ---------------------------------------------------------------------------
# Plan 04-02 Task 1 — write_report() / render_markdown() / reports_dir_for()
# ---------------------------------------------------------------------------


def _make_report(repo_path: str, **overrides) -> GovernanceReport:
    """Build a minimal, real (non-mocked) GovernanceReport for persistence
    tests, which exercise real tmp_path filesystem I/O (no mocking needed —
    pure pathlib/json/string logic, same style as tests/test_config.py's
    canonical_repo_path tests)."""
    defaults = dict(
        repo_path=repo_path,
        period_days=7,
        generated_at=datetime.now(timezone.utc).isoformat(),
        telemetry={"today": {"tool_calls": 1}},
        period_telemetry={"tool_calls": 1},
        confidence_distribution={"high": 2, "mid": 1, "stale": 3},
        unvalidated_decisions=[{"name": "Use Postgres for X", "created_at": "2026-01-01"}],
        modules_touched=["a.py", "b.py"],
    )
    defaults.update(overrides)
    return GovernanceReport(**defaults)


def test_reports_dir_for_returns_canonicalized_path_and_creates_it(tmp_path):
    repo_path = str(tmp_path)
    reports_dir = reports_dir_for(repo_path)

    expected = Path(canonical_repo_path(repo_path)) / ".memex" / "reports"
    assert reports_dir == expected
    assert reports_dir.exists()
    assert reports_dir.is_dir()


def test_write_report_writes_json_and_markdown_pair(tmp_path):
    report = _make_report(str(tmp_path))
    json_path, md_path = write_report(report)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert json_path.name == f"{stamp}.json"
    assert md_path.name == f"{stamp}.md"
    assert json_path.exists()
    assert md_path.exists()

    payload = json.loads(json_path.read_text())
    assert payload["repo_path"] == report.repo_path
    assert payload["period_days"] == report.period_days
    assert payload["generated_at"] == report.generated_at
    assert payload["telemetry"] == report.telemetry
    assert payload["period_telemetry"] == report.period_telemetry
    assert payload["confidence_distribution"] == report.confidence_distribution
    assert payload["unvalidated_decisions"] == report.unvalidated_decisions
    assert payload["modules_touched"] == report.modules_touched
    assert payload["notice"] == REPORT_NOTICE


def test_write_report_serializes_non_json_native_values_with_default_str(tmp_path):
    report = _make_report(
        str(tmp_path),
        unvalidated_decisions=[
            {"name": "Use Postgres for X", "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}
        ],
    )

    # Must not raise (a bare json.dumps() would raise TypeError on the
    # datetime value).
    json_path, _ = write_report(report)
    payload = json.loads(json_path.read_text())
    assert payload["unvalidated_decisions"][0]["created_at"] == str(
        datetime(2026, 1, 1, tzinfo=timezone.utc)
    )


def test_render_markdown_contains_key_fields_and_no_jinja2():
    report = _make_report("/some/repo")
    markdown = render_markdown(report)

    assert report.repo_path in markdown
    assert str(report.period_days) in markdown
    assert str(report.confidence_distribution["high"]) in markdown
    assert str(report.confidence_distribution["mid"]) in markdown
    assert str(report.confidence_distribution["stale"]) in markdown
    for module in report.modules_touched:
        assert module in markdown
    assert REPORT_NOTICE in markdown
    # REPORT_NOTICE should appear near the top of the document (within the
    # first few lines), not buried at the end.
    top_of_doc = "\n".join(markdown.splitlines()[:5])
    assert REPORT_NOTICE in top_of_doc

    import subprocess

    grep_result = subprocess.run(
        ["grep", "-c", "jinja2", "memex/graph/governance_report.py"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    # grep -c returns exit code 1 when count is 0 (no matches) — either way,
    # stdout must report zero matches.
    assert grep_result.stdout.strip() == "0"


# ---------------------------------------------------------------------------
# Plan 04-02 Task 2 — find_latest_report() path-confinement and selection
# ---------------------------------------------------------------------------


def test_find_latest_report_picks_true_latest_by_date_string_sort(tmp_path):
    repo_path = str(tmp_path)
    reports_dir = reports_dir_for(repo_path)
    for name in ["2026-07-01.json", "2026-07-07.json", "2026-06-30.json"]:
        (reports_dir / name).write_text("{}")

    latest = find_latest_report(repo_path)
    assert latest is not None
    assert latest.name == "2026-07-07.json"


def test_find_latest_report_returns_none_when_reports_dir_absent(tmp_path):
    # tmp_path itself has no .memex/reports/ directory created.
    result = find_latest_report(str(tmp_path))
    assert result is None


def test_find_latest_report_resolves_traversal_path_to_same_canonical_dir(tmp_path):
    (tmp_path / "sub").mkdir()
    clean_path = str(tmp_path)
    traversal_path = str(tmp_path / "sub" / "..")

    reports_dir = reports_dir_for(clean_path)
    (reports_dir / "2026-07-01.json").write_text("{}")

    result_clean = find_latest_report(clean_path)
    result_traversal = find_latest_report(traversal_path)

    assert result_clean is not None
    assert result_traversal is not None
    assert result_clean == result_traversal
