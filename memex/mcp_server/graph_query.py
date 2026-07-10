"""Shared graph-fetch helper for `/graph` (unauthenticated, Phase 02 bearer
auth) and `/team/graph` (session-gated, 05-02-PLAN.md NET-18).

Extracted verbatim from `memex.mcp_server.http.get_graph()`'s handler body —
the nodes/edges Cypher queries and node-type classification logic are moved
here unchanged so both routes return byte-for-byte identical shapes for the
same input. `http.py`'s `/graph` route still resolves `client` and
`canonical_repo` itself (via `get_graph_client()` / `canonical_repo_path()`)
so the existing `@patch("memex.mcp_server.http.get_graph_client")` mock
target used by `tests/test_http_transport.py::test_get_graph` needs no
changes.

Deviation from the plan's exact `fetch_graph_payload(client, canonical_repo)`
signature: a third `project: Optional[str] = None` parameter was added
(Rule 1/3 — required to preserve `/graph`'s existing, tested
`?project=<id>`-scoping behavior, e.g.
`test_get_graph_with_project_query_param`, byte-for-byte). It defaults to
`None`, so any caller written against the plan's two-argument shape still
works unchanged.
"""

from __future__ import annotations

from typing import Any, Optional


async def fetch_graph_payload(client: Any, canonical_repo: str, project: Optional[str] = None) -> dict:
    """Fetches nodes + edges for the graph view, scoped by `project` (if
    given) or `canonical_repo` otherwise. Returns `{"nodes": [...], "edges": [...]}`.

    Raises on query failure — callers are responsible for their own
    try/except + error response shaping (matches the pre-extraction inline
    behavior in `http.py`'s `/graph` route).
    """
    # Query nodes
    nodes_query = """
    MATCH (n:Entity)
    WHERE ($project IS NOT NULL AND n.project_id = $project) OR ($project IS NULL AND n.repo_path = $repo)
    RETURN
      elementId(n) as id,
      n.name as name,
      coalesce(n.type, '') as raw_type,
      coalesce(n.summary, n.description, '') as summary,
      coalesce(n.created_at, '') as created_at,
      coalesce(n.status, '') as status,
      coalesce(n.scope, '') as scope,
      coalesce(n.source_commit, '') as source_commit
    """

    # Query relationships
    edges_query = """
    MATCH (n1:Entity)-[r]->(n2:Entity)
    WHERE (($project IS NOT NULL AND n1.project_id = $project) OR ($project IS NULL AND n1.repo_path = $repo))
      AND (($project IS NOT NULL AND n2.project_id = $project) OR ($project IS NULL AND n2.repo_path = $repo))
      AND r.expired_at IS NULL
      AND r.valid_until IS NULL
    RETURN
      elementId(n1) as source,
      elementId(n2) as target,
      type(r) as type,
      coalesce(r.created_at, '') as created_at
    """

    nodes_res = await client.driver.execute_query(nodes_query, params={"repo": canonical_repo, "project": project})
    edges_res = await client.driver.execute_query(edges_query, params={"repo": canonical_repo, "project": project})

    nodes = []
    for record in nodes_res.records:
        data = record.data()
        name = data["name"]
        raw_type = data["raw_type"]

        # Determine classification
        if raw_type == 'Decision' or 'Decision' in name:
            node_type = 'Decision'
        elif raw_type == 'Problem':
            node_type = 'Problem'
        elif raw_type == 'Module' or any(name.endswith(ext) for ext in ['.py', '.js', '.ts', '.tsx', '.jsx', '.html', '.css', '.json']):
            node_type = 'Module'
        else:
            node_type = 'Symbol'

        # Format timestamps/datetimes to string if they are datetime objects
        created_at_val = data["created_at"]
        if hasattr(created_at_val, "isoformat"):
            created_at_val = created_at_val.isoformat()
        elif created_at_val and not isinstance(created_at_val, str):
            created_at_val = str(created_at_val)

        nodes.append({
            "id": data["id"],
            "name": name,
            "type": node_type,
            "summary": data["summary"],
            "created_at": created_at_val,
            "status": data["status"],
            "scope": data["scope"],
            "source_commit": data["source_commit"]
        })

    edges = []
    for record in edges_res.records:
        data = record.data()
        created_at_val = data["created_at"]
        if hasattr(created_at_val, "isoformat"):
            created_at_val = created_at_val.isoformat()
        elif created_at_val and not isinstance(created_at_val, str):
            created_at_val = str(created_at_val)

        edges.append({
            "source": data["source"],
            "target": data["target"],
            "type": data["type"],
            "created_at": created_at_val
        })

    return {"nodes": nodes, "edges": edges}
