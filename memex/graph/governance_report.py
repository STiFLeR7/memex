"""Governance-report composition module (Phase 04 / NET-16).

Composes the three already-existing computations in this codebase —
``get_stats_data()`` (telemetry aggregation), ``_fetch_pending_decisions()``
(unvalidated Decision rows), and ``current_confidence()`` (query-time
confidence) — into a single ``GovernanceReport`` object. No new Cypher is
written here: this module performs zero direct ``get_graph_client()`` /
``execute_query()`` calls of its own, deliberately reusing the review-TUI's
pending-decision query and the stats module's telemetry aggregation rather
than reinventing either (NET-16's "composition, not reinvention" mandate).

This plan defines the data contract only. File/markdown persistence is
Plan 04-02's concern; scheduler + HTTP wiring is Plan 04-03's concern.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

from memex.config import get_config
from memex.graph.stats import get_stats_data
from memex.graph.confidence import current_confidence
from memex.cli_review import _fetch_pending_decisions

# Consumed by Plan 04-02's file/markdown writers to flag the report as
# containing aggregated, more-sensitive-than-any-single-node decision
# history (threat T-04-01). Not used for anything in this plan.
REPORT_NOTICE = "Internal — contains project decision history"

_HIGH_THRESHOLD = 0.7
_MID_THRESHOLD = 0.3


@dataclass
class GovernanceReport:
    repo_path: str
    period_days: int
    generated_at: str
    telemetry: dict[str, Any]
    period_telemetry: dict[str, Any]
    confidence_distribution: dict[str, int]
    unvalidated_decisions: list[dict[str, Any]]
    modules_touched: list[str]


def _bucket_confidence(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Bucket ``rows`` into high/mid/stale using each row's already-computed
    ``_computed_confidence`` field, falling back to ``current_confidence(row)``
    only when that field is missing or ``None``."""
    buckets = {"high": 0, "mid": 0, "stale": 0}
    for row in rows:
        confidence = row.get("_computed_confidence")
        if confidence is None:
            confidence = current_confidence(row)
        if confidence >= _HIGH_THRESHOLD:
            buckets["high"] += 1
        elif confidence >= _MID_THRESHOLD:
            buckets["mid"] += 1
        else:
            buckets["stale"] += 1
    return buckets


def _select_period_telemetry(telemetry: dict[str, Any], period_days: int) -> dict[str, Any]:
    """Select the nearest telemetry bucket for ``period_days``."""
    if period_days <= 1:
        return telemetry["today"]
    if period_days <= 7:
        return telemetry["last_7_days"]
    if period_days <= 30:
        return telemetry["last_30_days"]
    return telemetry["lifetime"]


async def generate_report(repo_path: str, period_days: Optional[int] = None) -> GovernanceReport:
    """Compose a ``GovernanceReport`` for ``repo_path`` from the existing
    telemetry-stats and pending-decisions computations. Performs no direct
    Neo4j access itself."""
    resolved_period_days = (
        period_days if period_days is not None else get_config().report_period_days
    )

    telemetry = await get_stats_data(repo_path)
    pending = await _fetch_pending_decisions(repo_path)

    confidence_distribution = _bucket_confidence(pending)
    modules_touched = sorted({m for row in pending for m in (row.get("modules") or [])})
    period_telemetry = _select_period_telemetry(telemetry, resolved_period_days)

    return GovernanceReport(
        repo_path=repo_path,
        period_days=resolved_period_days,
        generated_at=datetime.now(timezone.utc).isoformat(),
        telemetry=telemetry,
        period_telemetry=period_telemetry,
        confidence_distribution=confidence_distribution,
        unvalidated_decisions=pending,
        modules_touched=modules_touched,
    )
