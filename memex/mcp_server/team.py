"""NET-18 (05-02-PLAN.md) — `/team/*` read endpoints for the team dashboard.

Four session+role-gated routes, each wrapping an existing computational core
rather than reimplementing logic (05-RESEARCH.md "Don't Hand-Roll" table):

- `GET /team/activity`   — wraps `memex.graph.telemetry.TelemetryDB.get_stats()`
  (tool-client call volume) AND a defensive Cypher query over Phase 01's
  `harness`/`agent_id` node properties (real per-principal write attribution).
  These are two DISTINCT data sources (05-RESEARCH.md Pitfall 2) — never
  conflated into one field. `attribution_available` tells the caller which
  kind of data `by_principal` actually represents.
- `GET /team/confidence` — wraps `memex.graph.confidence.current_confidence()`,
  grouped by module. No re-derived decay formula.
- `GET /team/conflicts`  — wraps `memex.mcp_server.conflict.detect_decision_conflicts()`,
  called once PER MODULE BATCH (not once over the full team-wide list) to
  avoid the existing >50-item O(n^2) safety cap silently zeroing out results
  once combined team-wide volume exceeds 50 (05-RESEARCH.md Pitfall 1).
- `GET /team/graph`      — session-gated equivalent of the unauthenticated
  `/graph` route (05-RESEARCH.md Security Domain flag); shares
  `fetch_graph_payload()` with `/graph` so the two never drift in shape.

All four routes are gated by `Depends(require_role("viewer"))` (T-05-08 —
the accepted, documented Phase 02-role-check-pending posture), never a bare
`Depends(require_session)`.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from memex.config import canonical_repo_path
from memex.graph.client import get_graph_client
from memex.graph.confidence import current_confidence, is_stale
from memex.graph.telemetry import TelemetryDB
from memex.mcp_server.auth_session import require_role
from memex.mcp_server.conflict import detect_decision_conflicts
from memex.mcp_server.graph_query import fetch_graph_payload
from memex.mcp_server.queries import get_recent_decisions_raw

logger = logging.getLogger(__name__)

# Decisions/Problems with no linked module fall into this bucket rather than
# being silently dropped from /team/confidence and /team/conflicts.
_UNSCOPED_MODULE = "_unscoped"

# Same defensive ceiling used by detect_decision_conflicts() itself
# (memex/mcp_server/conflict.py) — applied HERE per-module, not once
# globally, which is the explicit fix for 05-RESEARCH.md Pitfall 1.
_CONFLICT_MODULE_CAP = 50


# ---------------------------------------------------------------------------
# Response contracts
# ---------------------------------------------------------------------------


class ActivityByTool(BaseModel):
    """Tool-client call volume from `telemetry.py` — NOT per-person
    attribution (05-RESEARCH.md Pitfall 2)."""

    tool_client: str
    calls: int
    tokens_returned: int
    tokens_saved: Optional[int] = None


class ActivityByPrincipal(BaseModel):
    """Real per-principal write attribution, from Phase 01's `harness`/
    `agent_id` graph node properties. Only populated when
    `attribution_available` is True."""

    principal: str
    decision_count: int
    problem_count: int


class TeamActivityResponse(BaseModel):
    """WARNING for API consumers (incl. the v0.8.0 Task 7 dashboard):
    `by_tool_client` and `by_principal` can reflect DIFFERENT time windows.
    `by_tool_client` always uses the rolling `period_days` window — the
    `since`/`until` query params never reach `TelemetryDB.get_stats()`.
    `by_principal` uses `since`/`until` when either is given, and only falls
    back to `period_days` otherwise. `period_days` itself only describes
    `by_tool_client`'s window; when since/until are set it does not describe
    `by_principal`'s actual range."""

    period_days: int
    attribution_available: bool
    by_tool_client: List[ActivityByTool]
    by_principal: List[ActivityByPrincipal]


class ConfidenceByModule(BaseModel):
    module: str
    avg_confidence: float
    node_count: int
    stale_count: int


class TeamConfidenceResponse(BaseModel):
    period_days: int
    by_module: List[ConfidenceByModule]


class ConflictEntry(BaseModel):
    """One entry per decision flagged `conflict=True` by
    `detect_decision_conflicts()`, carrying the ids of the other flagged
    decisions in its own module batch it conflicts with.

    Pairing-shape decision: `detect_decision_conflicts()` only sets a
    boolean `conflict` flag per row (it doesn't return explicit pairs), so
    re-deriving exact pairwise matches would mean re-running the same
    same-module + overlapping-window + below-threshold check a second time.
    Instead, each flagged decision lists every OTHER flagged decision in its
    batch as a `conflicts_with` id list — this is a conservative
    over-approximation when 3+ decisions in one module are mutually flagged
    (not all of those pairs individually failed the similarity check), but
    it keeps the response schema simple and never requires a second
    similarity pass. Chosen over re-deriving exact pairs for this reason.
    """

    decision_id: str
    text: str
    module: str
    conflicts_with: List[str]


class TeamConflictsResponse(BaseModel):
    period_days: int
    modules_scanned: int
    decisions_scanned: int
    capped_modules: List[str]
    conflicts: List[ConflictEntry]


class GraphPayload(BaseModel):
    """Loose passthrough shape — mirrors `/graph`'s existing untyped dict
    response (nodes/edges are heterogeneous dicts already shaped by
    `fetch_graph_payload`)."""

    nodes: List[dict]
    edges: List[dict]


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_team_router(repo_root: str) -> APIRouter:
    """Builds the `/team/*` router. Mirrors the `create_app(server, repo_root)`
    factory pattern already used in `http.py` — this is the project's first
    `APIRouter` split (05-RESEARCH.md Open Question 1), chosen because four
    new routes plus the existing session/login routes is enough surface to
    warrant it.
    """
    router = APIRouter(prefix="/team", tags=["team"])

    @router.get("/activity", response_model=TeamActivityResponse)
    async def team_activity(
        days: int = 30,
        # v0.8.0 Pillar C: explicit range narrows the per-principal query to
        # a fixed window instead of the rolling `days` window, for teams with
        # months of history who want e.g. "last sprint" rather than "last N days".
        since: Optional[str] = None,
        until: Optional[str] = None,
        principal: str = Depends(require_role("viewer")),
    ) -> TeamActivityResponse:
        canonical_repo = canonical_repo_path(repo_root)

        # Tool-client call volume (05-RESEARCH.md Pitfall 2 — this answers
        # "which tool clients were used," not "who wrote what"). Always uses
        # the rolling `days` window — since/until intentionally do NOT reach
        # get_stats() (out of scope; TelemetryDB has its own windowing
        # semantics). See TeamActivityResponse's docstring: this means
        # by_tool_client and by_principal can represent different windows
        # when since/until are set.
        stats = TelemetryDB().get_stats(canonical_repo, days)
        by_tool_client = [
            ActivityByTool(
                tool_client=row["agent"],
                calls=row["calls"],
                tokens_returned=row.get("tokens_returned", 0) or 0,
                tokens_saved=row.get("tokens_saved"),
            )
            for row in stats.get("by_agent", [])
        ]

        # Real per-principal write attribution (Phase 01's harness/agent_id
        # node properties). Defensive: filters out null groups; an empty
        # result set means Phase 01 hasn't populated attribution data for
        # this repo yet, NOT that zero people are active — hence the
        # explicit flag rather than a silently-empty chart passed off as
        # real data.
        client = await get_graph_client()

        # Explicit since/until wins over the rolling `days` window when
        # provided (v0.8.0 Pillar C). `n.created_at` is a native Neo4j
        # datetime (same convention as the `days`-window clause below, which
        # compares it directly against `datetime() - duration(...)`), so the
        # incoming ISO date strings are cast with `datetime($since)` /
        # `datetime($until)` rather than compared as raw strings.
        if since or until:
            time_filter = (
                "AND ($since IS NULL OR n.created_at >= datetime($since))"
                " AND ($until IS NULL OR n.created_at <= datetime($until))"
            )
        else:
            time_filter = "AND coalesce(n.created_at, datetime()) >= datetime() - duration({days: $days})"

        attribution_query = f"""
        MATCH (n:Entity)
        WHERE (n.type = 'Decision' OR n.name CONTAINS 'Decision' OR n.type = 'Problem' OR n.name CONTAINS 'Problem')
          AND n.repo_path = $repo
          {time_filter}
          AND coalesce(n.harness, n.agent_id) IS NOT NULL
        RETURN
          coalesce(n.harness, n.agent_id) as principal,
          count(CASE WHEN n.type = 'Decision' OR n.name CONTAINS 'Decision' THEN 1 END) as decision_count,
          count(CASE WHEN n.type = 'Problem' OR n.name CONTAINS 'Problem' THEN 1 END) as problem_count
        ORDER BY principal ASC
        """
        attribution_res = await client.driver.execute_query(
            attribution_query,
            params={"repo": canonical_repo, "days": days, "since": since, "until": until},
        )
        attribution_rows = [r.data() for r in attribution_res.records]

        attribution_available = len(attribution_rows) > 0
        by_principal = [
            ActivityByPrincipal(
                principal=row["principal"],
                decision_count=row.get("decision_count", 0) or 0,
                problem_count=row.get("problem_count", 0) or 0,
            )
            for row in attribution_rows
        ]

        return TeamActivityResponse(
            period_days=days,
            attribution_available=attribution_available,
            by_tool_client=by_tool_client,
            by_principal=by_principal,
        )

    @router.get("/confidence", response_model=TeamConfidenceResponse)
    async def team_confidence(
        days: int = 90,
        principal: str = Depends(require_role("viewer")),
    ) -> TeamConfidenceResponse:
        canonical_repo = canonical_repo_path(repo_root)

        # Reuses get_recent_decisions_raw (memex.mcp_server.queries) — this
        # already returns base_confidence/last_reinforced_at/validated/module,
        # exactly what current_confidence() needs. No new aggregation query.
        rows = await get_recent_decisions_raw(
            since_days=days, module=None, limit=500, repo=canonical_repo
        )

        by_module: dict[str, list[dict]] = {}
        for row in rows:
            module = row.get("module") or _UNSCOPED_MODULE
            by_module.setdefault(module, []).append(row)

        confidence_entries = []
        for module, module_rows in sorted(by_module.items()):
            confidences = [current_confidence(row) for row in module_rows]
            stale_count = sum(1 for row in module_rows if is_stale(row))
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
            confidence_entries.append(
                ConfidenceByModule(
                    module=module,
                    avg_confidence=avg_confidence,
                    node_count=len(module_rows),
                    stale_count=stale_count,
                )
            )

        return TeamConfidenceResponse(period_days=days, by_module=confidence_entries)

    @router.get("/conflicts", response_model=TeamConflictsResponse)
    async def team_conflicts(
        days: int = 90,
        principal: str = Depends(require_role("viewer")),
    ) -> TeamConflictsResponse:
        canonical_repo = canonical_repo_path(repo_root)

        rows = await get_recent_decisions_raw(
            since_days=days, module=None, limit=500, repo=canonical_repo
        )

        # Group into per-module batches BEFORE calling
        # detect_decision_conflicts (05-RESEARCH.md Pitfall 1 fix): the
        # existing function's >50 cap applies to whatever list it receives,
        # so passing one global list silently drops everything past 50
        # regardless of module distribution. Passing one list per module
        # means each call's cap applies only to that module's own decisions.
        by_module: dict[str, list[dict]] = {}
        for row in rows:
            module = row.get("module") or _UNSCOPED_MODULE
            by_module.setdefault(module, []).append(row)

        client = await get_graph_client()
        capped_modules: List[str] = []
        conflicts: List[ConflictEntry] = []

        for module, batch in sorted(by_module.items()):
            # Already sorted newest-first per get_recent_decisions_raw's
            # `ORDER BY d.created_at DESC` — relative order is preserved by
            # the per-module grouping above, so truncation keeps the newest
            # 50 for this module, not an arbitrary slice.
            if len(batch) > _CONFLICT_MODULE_CAP:
                batch = batch[:_CONFLICT_MODULE_CAP]
                capped_modules.append(module)

            flagged_batch = await detect_decision_conflicts(list(batch), client)
            flagged_ids = [
                row.get("id") for row in flagged_batch if row.get("conflict")
            ]
            for row in flagged_batch:
                if not row.get("conflict"):
                    continue
                own_id = row.get("id")
                conflicts.append(
                    ConflictEntry(
                        decision_id=own_id,
                        text=row.get("text", "") or "",
                        module=module,
                        conflicts_with=[fid for fid in flagged_ids if fid != own_id],
                    )
                )

        return TeamConflictsResponse(
            period_days=days,
            modules_scanned=len(by_module),
            decisions_scanned=len(rows),
            capped_modules=capped_modules,
            conflicts=conflicts,
        )

    @router.get("/graph", response_model=GraphPayload)
    async def team_graph(
        project: Optional[str] = None,
        # v0.8.0 Pillar C: cluster-level view is O(clusters) not O(modules) —
        # keeps the dashboard graph responsive on large repos instead of
        # rendering every Entity node.
        cluster_only: bool = False,
        principal: str = Depends(require_role("viewer")),
    ) -> dict:
        # Session-gated equivalent of the unauthenticated /graph route
        # (05-RESEARCH.md Security Domain flag) — the dashboard's graph view
        # must call this, never the bare /graph endpoint.
        client = await get_graph_client()
        canonical_repo = canonical_repo_path(repo_root)
        return await fetch_graph_payload(client, canonical_repo, project, cluster_only=cluster_only)

    return router
