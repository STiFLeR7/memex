"""``memex graph`` — static D3.js visualisation of the module dependency graph.

Renders a self-contained HTML file: Modules as nodes, IMPORTS / RELATES_TO
edges between modules, with an optional Cluster overlay drawn as faint
convex hulls behind the node layer. No backend, no JS build — the file
opens directly in any modern browser.

Design (per PLAN-v0.3.0 §"`memex graph` command (visual export)"):
    - Node size  = number of symbols defined in the module
      (``sqrt(symbol_count)*3 + 4`` so 0-symbol modules stay visible)
    - Edge opacity = ``current_confidence`` of the relationship
      (uses :func:`memex.graph.confidence.current_confidence`)
    - Stale modules (``current_confidence < 0.3``) are rendered grey
    - Cluster boundaries appear iff Cluster nodes exist in the graph
      (cluster engine is dev2's deliverable; omitting cleanly is required)
    - Every edge-traversing Cypher query filters ``r.expired_at IS NULL``
      — the v0.2.0 latent-zombie-edge bug fix (ARCHITECTURE §8 step 1).
"""

from __future__ import annotations

import logging
import webbrowser
from pathlib import Path
from typing import Any

from memex.cli_graph_template import render_html
from memex.graph.client import get_graph_client
from memex.graph.confidence import current_confidence

logger = logging.getLogger(__name__)

STALE_THRESHOLD = 0.3

# Truncation ceilings — sized so D3 still renders interactively on
# commodity hardware. When a query hits the ceiling the HTML legend gets
# a "graph truncated" banner so the user knows the view is partial.
MODULE_LIMIT = 5000
EDGE_LIMIT = 20000


# ---------------------------------------------------------------------------
# Cypher queries — every traversal filters `expired_at IS NULL` (Risk B3).
# Module-to-module edges accept `IMPORTS` or `RELATES_TO` because the
# IMPORTS edge writer is part of the deferred lockfile pipeline (dev2) and
# RELATES_TO is what Graphiti currently emits between modules.
# ---------------------------------------------------------------------------

_MODULES_QUERY = """
MATCH (m:Entity)
WHERE (coalesce(m.type, '') = 'Module'
       OR m.name ENDS WITH '.py'
       OR m.name ENDS WITH '.js'
       OR m.name ENDS WITH '.ts')
  AND ($repo IS NULL OR m.repo_path = $repo)
OPTIONAL MATCH (s:Entity)
WHERE (coalesce(s.type, '') = 'Symbol'
       OR (s.type IS NULL AND NOT s.name ENDS WITH '.py'))
  AND coalesce(s.file, '') = m.name
RETURN m.name                       AS id,
       coalesce(m.summary, m.name)  AS label,
       count(s)                     AS symbol_count,
       coalesce(m.cluster_name, '') AS cluster,
       coalesce(m.base_confidence, 1.0)            AS base_confidence,
       coalesce(m.last_reinforced_at, m.created_at) AS last_reinforced_at,
       coalesce(m.validated, false)                AS validated,
       coalesce(m.created_at, datetime())          AS created_at
LIMIT $module_limit
"""

_EDGES_QUERY = """
MATCH (a:Entity)-[r:IMPORTS|RELATES_TO]->(b:Entity)
WHERE r.expired_at IS NULL
  AND ($repo IS NULL OR a.repo_path = $repo)
  AND (coalesce(a.type, '') = 'Module' OR a.name ENDS WITH '.py'
       OR a.name ENDS WITH '.js'  OR a.name ENDS WITH '.ts')
  AND (coalesce(b.type, '') = 'Module' OR b.name ENDS WITH '.py'
       OR b.name ENDS WITH '.js'  OR b.name ENDS WITH '.ts')
RETURN a.name AS source,
       b.name AS target,
       coalesce(r.base_confidence, r.confidence, 1.0) AS base_confidence,
       coalesce(r.last_reinforced_at, r.valid_from, r.created_at) AS last_reinforced_at,
       coalesce(r.validated, false) AS validated
LIMIT $edge_limit
"""

_CLUSTERS_QUERY = """
MATCH (c:Entity)
WHERE c.type = 'Cluster'
  AND ($repo IS NULL OR c.repo_path = $repo)
OPTIONAL MATCH (c)-[rc:CONTAINS]->(m:Entity)
WHERE rc.expired_at IS NULL
  AND (coalesce(m.type, '') = 'Module' OR m.name ENDS WITH '.py')
RETURN c.name                       AS name,
       coalesce(c.description, '')  AS description,
       collect(DISTINCT m.name)     AS modules
"""


async def _fetch_graph_data(repo: str | None) -> dict[str, Any]:
    """Pull modules + edges + (optional) clusters from Neo4j.

    Returns a dict that matches the shape expected by
    :func:`memex.cli_graph_template.render_html`. Each branch is wrapped
    in its own try/except so a single failing query (e.g. Cluster type
    absent) doesn't kill the whole export.
    """
    client = await get_graph_client()

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    clusters: list[dict[str, Any]] = []
    truncated = {"modules": False, "edges": False}

    # --- Modules ----------------------------------------------------------
    try:
        res = await client.driver.execute_query(
            _MODULES_QUERY, params={"repo": repo, "module_limit": MODULE_LIMIT}
        )
        for rec in res.records:
            row = rec.data()
            stale = current_confidence(row) < STALE_THRESHOLD
            nodes.append({
                "id":           row.get("id"),
                "label":        row.get("label") or row.get("id"),
                "symbol_count": int(row.get("symbol_count") or 0),
                "cluster":      row.get("cluster") or None,
                "stale":        bool(stale),
            })
        if len(nodes) >= MODULE_LIMIT:
            truncated["modules"] = True
    except Exception:
        logger.error("graph: failed to fetch modules", exc_info=True)

    # --- Edges ------------------------------------------------------------
    try:
        res = await client.driver.execute_query(
            _EDGES_QUERY, params={"repo": repo, "edge_limit": EDGE_LIMIT}
        )
        for rec in res.records:
            row = rec.data()
            conf = current_confidence(row)
            edges.append({
                "source":     row.get("source"),
                "target":     row.get("target"),
                "confidence": float(conf),
            })
        if len(edges) >= EDGE_LIMIT:
            truncated["edges"] = True
    except Exception:
        logger.error("graph: failed to fetch edges", exc_info=True)

    # --- Clusters ---------------------------------------------------------
    try:
        res = await client.driver.execute_query(_CLUSTERS_QUERY, params={"repo": repo})
        for rec in res.records:
            row = rec.data()
            mods = [m for m in (row.get("modules") or []) if m]
            clusters.append({
                "name":        row.get("name"),
                "description": row.get("description") or "",
                "modules":     mods,
            })
    except Exception:
        # Cluster type may not exist yet (engine deferred to dev2).
        logger.debug("graph: cluster query empty / not supported", exc_info=True)

    return {
        "nodes":     nodes,
        "edges":     edges,
        "clusters":  clusters,
        "truncated": truncated,
        "limits":    {"modules": MODULE_LIMIT, "edges": EDGE_LIMIT},
    }


async def run_graph_command(repo_root: str, output: str, open_browser: bool) -> None:
    """Generate a static D3.js HTML visualisation of the module dependency
    graph. Writes ``output``; optionally opens it in the user's browser.

    Works even on an empty graph — the resulting HTML still renders the
    legend and empty SVG canvas.
    """
    repo_path: str | None
    try:
        repo_path = str(Path(repo_root).resolve()) if repo_root else None
    except Exception:
        repo_path = repo_root or None

    # Pass repo=None on best-effort so a misconfigured repo_path doesn't
    # silently filter every node away.
    data = await _fetch_graph_data(repo_path)
    if not data["nodes"] and not data["edges"]:
        # One retry without the repo filter, in case repo_path doesn't
        # match any node's `repo_path` (common when nodes were created
        # before per-repo tagging).
        fallback = await _fetch_graph_data(None)
        if fallback["nodes"] or fallback["edges"]:
            data = fallback

    html = render_html(data)

    out_path = Path(output).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")

    n_modules  = len(data["nodes"])
    n_edges    = len(data["edges"])
    n_clusters = len(data["clusters"])

    trunc = data.get("truncated") or {}
    trunc_note = ""
    if trunc.get("modules") or trunc.get("edges"):
        parts = []
        if trunc.get("modules"):
            parts.append(f"modules capped at {MODULE_LIMIT}")
        if trunc.get("edges"):
            parts.append(f"edges capped at {EDGE_LIMIT}")
        trunc_note = " [TRUNCATED: " + ", ".join(parts) + "]"

    if n_clusters:
        print(f"graph exported to {out_path} "
              f"({n_modules} modules, {n_edges} edges, {n_clusters} clusters)"
              f"{trunc_note}")
    else:
        print(f"graph exported to {out_path} "
              f"({n_modules} modules, {n_edges} edges){trunc_note}")

    if open_browser:
        try:
            webbrowser.open(out_path.as_uri())
        except Exception:
            logger.warning("graph: failed to open browser", exc_info=True)
