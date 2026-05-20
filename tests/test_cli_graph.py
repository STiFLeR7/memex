"""Tests for ``memex graph`` (Phase 10).

Key invariants:
    - Output HTML is self-contained: D3 v7 ref + an ``<svg>`` element
    - Cluster overlay markup is present iff Cluster nodes exist
    - Every edge-traversing Cypher filters ``r.expired_at IS NULL``
      (B3 latent-zombie-edge bug fix)
    - Stale modules (``current_confidence < 0.3``) get a ``"stale": true``
      flag baked into the JSON data block
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memex import cli_graph


def _rec(data: dict) -> MagicMock:
    r = MagicMock()
    r.data.return_value = dict(data)
    return r


def _result(rows: list[dict]) -> MagicMock:
    res = MagicMock()
    res.records = [_rec(r) for r in rows]
    return res


def _make_client(per_query_rows: dict[str, list[dict]]):
    """Build a mock graph client whose driver dispatches by query keyword.

    Keys: 'modules' → MODULES_QUERY, 'edges' → EDGES_QUERY,
    'clusters' → CLUSTERS_QUERY. Anything else returns an empty record set.
    """
    mock_client = AsyncMock()
    captured_queries: list[str] = []

    async def fake_execute_query(query, params=None, **kw):
        captured_queries.append(query)
        if "(c:Entity)" in query and "Cluster" in query:
            return _result(per_query_rows.get("clusters", []))
        if "MATCH (a:Entity)-[r:" in query:
            return _result(per_query_rows.get("edges", []))
        if "MATCH (m:Entity)" in query and "RETURN m.name" in query:
            return _result(per_query_rows.get("modules", []))
        return _result([])

    mock_client.driver.execute_query.side_effect = fake_execute_query
    return mock_client, captured_queries


@pytest.mark.asyncio
async def test_run_graph_writes_html_to_output(tmp_path):
    """End-to-end: produces a valid HTML file containing D3 + <svg>."""
    mock_client, _ = _make_client({
        "modules": [
            {"id": "a.py", "label": "a.py", "symbol_count": 3,
             "cluster": "", "base_confidence": 1.0,
             "last_reinforced_at": datetime.now(timezone.utc),
             "validated": True, "created_at": datetime.now(timezone.utc)},
        ],
        "edges": [],
        "clusters": [],
    })

    out = tmp_path / "graph.html"
    with patch("memex.cli_graph.get_graph_client", new=AsyncMock(return_value=mock_client)):
        await cli_graph.run_graph_command(
            repo_root=str(tmp_path), output=str(out), open_browser=False,
        )

    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert "<svg" in html
    # D3 v7 reference (CDN URL)
    assert "d3" in html.lower()
    assert "d3.v7" in html
    # The JSON data island must contain the module we passed in.
    assert "a.py" in html


@pytest.mark.asyncio
async def test_run_graph_includes_cluster_hulls_when_clusters_exist(tmp_path):
    """When at least one Cluster node is returned, the rendered HTML
    must include a hull element (``<polygon>`` or ``<path>``) so the
    cluster overlay is detectable."""
    mock_client, _ = _make_client({
        "modules": [
            {"id": "x.py", "label": "x.py", "symbol_count": 2,
             "cluster": "core", "base_confidence": 1.0,
             "last_reinforced_at": datetime.now(timezone.utc),
             "validated": True, "created_at": datetime.now(timezone.utc)},
        ],
        "edges": [],
        "clusters": [
            {"name": "core", "description": "core stuff", "modules": ["x.py"]},
        ],
    })

    out = tmp_path / "graph.html"
    with patch("memex.cli_graph.get_graph_client", new=AsyncMock(return_value=mock_client)):
        await cli_graph.run_graph_command(
            repo_root=str(tmp_path), output=str(out), open_browser=False,
        )

    html = out.read_text(encoding="utf-8")
    assert "cluster-hulls" in html
    # Static hull marker for tests + screen-readers
    assert ("<polygon" in html) or ("<path" in html)


@pytest.mark.asyncio
async def test_run_graph_omits_cluster_hulls_when_no_clusters(tmp_path):
    """No Cluster nodes → no hull markup at all (cleanly omitted)."""
    mock_client, _ = _make_client({
        "modules": [
            {"id": "y.py", "label": "y.py", "symbol_count": 1,
             "cluster": "", "base_confidence": 1.0,
             "last_reinforced_at": datetime.now(timezone.utc),
             "validated": True, "created_at": datetime.now(timezone.utc)},
        ],
        "edges": [],
        "clusters": [],
    })

    out = tmp_path / "graph.html"
    with patch("memex.cli_graph.get_graph_client", new=AsyncMock(return_value=mock_client)):
        await cli_graph.run_graph_command(
            repo_root=str(tmp_path), output=str(out), open_browser=False,
        )

    html = out.read_text(encoding="utf-8")
    assert "cluster-hulls" not in html
    assert "cluster-hull-placeholder" not in html


@pytest.mark.asyncio
async def test_run_graph_filters_expired_edges(tmp_path):
    """B3 regression: the edge-traversing Cypher must contain
    ``r.expired_at IS NULL`` so Graphiti's auto-invalidated zombie edges
    never leak into the visualisation."""
    mock_client, captured = _make_client({"modules": [], "edges": [], "clusters": []})

    out = tmp_path / "graph.html"
    with patch("memex.cli_graph.get_graph_client", new=AsyncMock(return_value=mock_client)):
        await cli_graph.run_graph_command(
            repo_root=str(tmp_path), output=str(out), open_browser=False,
        )

    # Find the edges query — it's the one that matches MATCH (a)-[r:...]->(b)
    edge_queries = [q for q in captured if "MATCH (a:Entity)-[r:" in q]
    assert edge_queries, "edges query not issued"
    for q in edge_queries:
        assert "expired_at IS NULL" in q, (
            "edges Cypher missing `r.expired_at IS NULL` filter — "
            "Graphiti-invalidated edges will leak into the visualisation"
        )

    # And the cluster CONTAINS traversal too, when issued.
    cluster_queries = [q for q in captured if "(c:Entity)" in q and "Cluster" in q]
    for q in cluster_queries:
        assert "expired_at IS NULL" in q


@pytest.mark.asyncio
async def test_run_graph_renders_stale_nodes_grey(tmp_path):
    """A module whose computed confidence is below the staleness
    threshold must be flagged ``stale: true`` in the embedded JSON so
    the browser-side script can render it grey."""
    # last_reinforced_at far in the past + unvalidated → fully decayed.
    long_ago = datetime.now(timezone.utc) - timedelta(days=400)
    mock_client, _ = _make_client({
        "modules": [
            {"id": "old.py", "label": "old.py", "symbol_count": 0,
             "cluster": "",
             "base_confidence": 0.6,
             "last_reinforced_at": long_ago,
             "validated": False,
             "created_at": long_ago},
            {"id": "fresh.py", "label": "fresh.py", "symbol_count": 5,
             "cluster": "",
             "base_confidence": 1.0,
             "last_reinforced_at": datetime.now(timezone.utc),
             "validated": True,
             "created_at": datetime.now(timezone.utc)},
        ],
        "edges": [],
        "clusters": [],
    })

    out = tmp_path / "graph.html"
    with patch("memex.cli_graph.get_graph_client", new=AsyncMock(return_value=mock_client)):
        await cli_graph.run_graph_command(
            repo_root=str(tmp_path), output=str(out), open_browser=False,
        )

    html = out.read_text(encoding="utf-8")
    # Extract the embedded JSON island.
    m = re.search(
        r'<script id="graph-data"[^>]*>(.*?)</script>',
        html, re.DOTALL,
    )
    assert m, "graph-data JSON island missing"
    data = json.loads(m.group(1))
    nodes_by_id = {n["id"]: n for n in data["nodes"]}
    assert nodes_by_id["old.py"]["stale"] is True
    assert nodes_by_id["fresh.py"]["stale"] is False
