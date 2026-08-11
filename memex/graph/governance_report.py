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

import asyncio
import json
import logging
import smtplib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Optional

import httpx

from memex.config import get_config, canonical_repo_path
from memex.graph.stats import get_stats_data
from memex.graph.confidence import current_confidence
from memex.cli_review import _fetch_pending_decisions

logger = logging.getLogger(__name__)

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


def reports_dir_for(repo_path: str) -> Path:
    """Return the canonicalized ``.memex/reports/`` directory for
    ``repo_path``, creating it if needed. Follows the exact ``.memex/port``
    file convention already established by
    ``memex/mcp_server/http.py::run_http_server()`` (canonicalize first,
    build the path from the canonical value, ``mkdir(parents=True,
    exist_ok=True)``), extended with a ``reports`` subdirectory since reports
    accumulate one pair per run rather than being a single ephemeral file."""
    repo_canon = canonical_repo_path(repo_path)
    reports_dir = Path(repo_canon) / ".memex" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    return reports_dir


def render_markdown(report: GovernanceReport) -> str:
    """Render ``report`` as a plain-Markdown document via f-strings /
    ``"\\n".join(...)`` only -- no templating library (a Jinja2 layer is
    disproportionate to a single manager-facing digest; matches
    ``stats.py::print_rich_stats``'s plain-formatting style, just targeting a
    string instead of a ``rich.Console``). The ``REPORT_NOTICE`` is rendered
    as a blockquote directly under the title (T-04-03 -- report files may be
    shared more casually than the graph itself)."""
    confidence = report.confidence_distribution
    lines = [
        "# Governance Report",
        "",
        f"> {REPORT_NOTICE}",
        "",
        "## Period",
        "",
        f"- Repo: {report.repo_path}",
        f"- Period (days): {report.period_days}",
        f"- Generated at: {report.generated_at}",
        "",
        "## Confidence Distribution",
        "",
        f"- High (>=0.7): {confidence.get('high', 0)}",
        f"- Mid (0.3-0.7): {confidence.get('mid', 0)}",
        f"- Stale (<0.3): {confidence.get('stale', 0)}",
        "",
        "## Modules Touched",
        "",
    ]
    if report.modules_touched:
        lines.extend(f"- {module}" for module in report.modules_touched)
    else:
        lines.append("- (none)")
    lines.extend(
        [
            "",
            "## Unvalidated Decisions",
            "",
            f"Count: {len(report.unvalidated_decisions)}",
            "",
        ]
    )
    for row in report.unvalidated_decisions:
        text = str(row.get("name") or row.get("text") or row.get("content") or "")
        lines.append(f"- {text[:120]}")
    return "\n".join(lines) + "\n"


def write_report(report: GovernanceReport) -> tuple[Path, Path]:
    """Persist ``report`` as a JSON + Markdown pair under
    ``.memex/reports/``. JSON is the machine-readable source of truth
    (serialized with ``default=str`` since ``unvalidated_decisions`` rows may
    carry non-JSON-native values such as ``datetime`` objects); Markdown is
    the human-readable companion rendered from the same data. Returns
    ``(json_path, md_path)``. No retention/pruning logic -- ship without it
    for v0.7.0 per research's Open Question 1 (explicitly deferred, not an
    oversight)."""
    reports_dir = reports_dir_for(report.repo_path)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    payload = {**asdict(report), "notice": REPORT_NOTICE}
    json_path = reports_dir / f"{stamp}.json"
    md_path = reports_dir / f"{stamp}.md"
    json_path.write_text(json.dumps(payload, default=str, indent=2))
    md_path.write_text(render_markdown(report))
    return json_path, md_path


def find_latest_report(repo_path: str) -> Optional[Path]:
    """Read-only lookup of the most recently written report JSON file for
    ``repo_path``. Canonicalizes ``repo_path`` via ``canonical_repo_path()``
    BEFORE any ``Path``/glob construction -- this is the single choke point
    Plan 04-03's ``GET /report`` endpoint will call with a caller-supplied
    ``repo`` query parameter, so the path-traversal defense lives here once
    rather than being duplicated at each call site (T-04-06). Does not create
    the directory (read-only) -- returns ``None`` gracefully when the
    directory or any report file is absent."""
    repo_canon = canonical_repo_path(repo_path)
    reports_dir = Path(repo_canon) / ".memex" / "reports"
    if not reports_dir.exists():
        return None
    candidates = sorted(reports_dir.glob("*.json"))
    if not candidates:
        return None
    return candidates[-1]


async def deliver_slack(report: GovernanceReport, webhook_url: str) -> bool:
    """POSTs the report's Markdown rendering to a Slack incoming webhook.
    Best-effort — returns False on any failure rather than raising, so a
    dead webhook can never crash the weekly report_task() cron job
    (mirrors decay_task's/report_task's own per-repo isolation pattern in
    memex/graph/decay.py)."""
    body = render_markdown(report)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(webhook_url, json={"text": body})
            response.raise_for_status()
        return True
    except Exception:
        logger.error(
            "Slack governance-report delivery failed for %s", report.repo_path, exc_info=True
        )
        return False


async def deliver_email(report: GovernanceReport, smtp_config: dict) -> bool:
    """Sends the report's rendered Markdown via SMTP. Runs the blocking
    smtplib call in a thread (asyncio.to_thread) so it never blocks the
    event loop — same pattern report_task() already uses for
    write_report() in memex/graph/decay.py. Best-effort, same as
    deliver_slack: returns False rather than raising."""

    def _send() -> None:
        body_md = render_markdown(report)
        msg = MIMEText(body_md, "plain")
        msg["Subject"] = f"memex Governance Report — {report.repo_path}"
        msg["From"] = smtp_config["user"]
        msg["To"] = ", ".join(smtp_config["to"])

        with smtplib.SMTP(smtp_config["host"], smtp_config["port"]) as server:
            server.starttls()
            server.login(smtp_config["user"], smtp_config["password"])
            server.sendmail(smtp_config["user"], smtp_config["to"], msg.as_string())

    try:
        await asyncio.to_thread(_send)
        return True
    except Exception:
        logger.error(
            "Email governance-report delivery failed for %s", report.repo_path, exc_info=True
        )
        return False
