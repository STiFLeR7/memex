"""Tests for ``memex.graph.cluster_runner`` (Deliverable 3 + 6).

Persistence is exercised against a stub graph client so the suite stays
Neo4j-free. Integration tests against a real Neo4j live in
``tests/test_mcp_queries_integration.py`` and are excluded from the
default baseline run.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from memex.graph.cluster import ClusterAssignment
from memex.graph.cluster_runner import (
    CLUSTER_ACTOR,
    discover_modules_from_filesystem,
    run_cluster_command,
    run_init_cluster_pass,
    write_cluster_assignments,
)


# ---------------------------------------------------------------------------
# Stub client
# ---------------------------------------------------------------------------


class _StubGraphClient:
    """Records every ``add_episode`` + ``driver.execute_query`` call.

    Mimics Graphiti's episode-uuid return shape so the writer's post-hoc
    SET targets a stable id.
    """

    def __init__(self, has_clusters: bool = False) -> None:
        self.episodes: list[dict] = []
        self.queries: list[tuple[str, dict]] = []
        self.driver = self
        self._has_clusters = has_clusters

    async def add_episode(self, **kwargs) -> Any:  # noqa: D401
        self.episodes.append(kwargs)
        return SimpleNamespace(episode=SimpleNamespace(uuid=f"ep-{len(self.episodes)}"))

    async def execute_query(self, query: str, params: dict | None = None) -> Any:
        self.queries.append((query, params or {}))
        # Existing-clusters detection query
        if "RETURN count(c) as n" in query and self._has_clusters:
            return SimpleNamespace(records=[{"n": 5}])
        return SimpleNamespace(records=[])


# ---------------------------------------------------------------------------
# discover_modules_from_filesystem
# ---------------------------------------------------------------------------


def test_discover_modules_skips_vendored_dirs(tmp_path: Path) -> None:
    (tmp_path / "memex").mkdir()
    (tmp_path / "memex" / "x.py").write_text("pass\n")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "skipme.py").write_text("pass\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "skipme.py").write_text("pass\n")

    mods = discover_modules_from_filesystem(tmp_path)

    assert "memex/x.py" in mods
    assert not any(".venv" in m for m in mods)
    assert not any("node_modules" in m for m in mods)


# ---------------------------------------------------------------------------
# write_cluster_assignments
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_cluster_assignments_writes_episode_set_and_edges() -> None:
    client = _StubGraphClient()
    assignments = [
        ClusterAssignment(
            name="watcher",
            members={"watcher/handlers.py", "watcher/router.py"},
            leiden_id=1,
        ),
    ]

    out = await write_cluster_assignments(client, "/repo", assignments)

    assert out == {"clusters_written": 1, "contains_edges_written": 2}
    # One episode for the cluster, no other episodes.
    assert len(client.episodes) == 1
    assert client.episodes[0]["name"] == "cluster_watcher"

    queries_text = "\n".join(q for q, _ in client.queries)
    # Cluster upsert
    assert "MERGE (c:Entity {type: 'Cluster'" in queries_text
    # CONTAINS edges for each member
    assert queries_text.count("MERGE (c)-[r:CONTAINS]->") == 2
    # Stale-CONTAINS tombstone + stale-cluster tombstone
    assert "NOT m.name IN $members" in queries_text
    assert "NOT c.name IN $names" in queries_text


@pytest.mark.asyncio
async def test_write_cluster_assignments_skips_empty_clusters() -> None:
    client = _StubGraphClient()
    assignments = [
        ClusterAssignment(name="empty", members=set(), leiden_id=1),
        ClusterAssignment(name="real", members={"x.py", "y.py"}, leiden_id=2),
    ]

    out = await write_cluster_assignments(client, "/repo", assignments)

    # "empty" rejected by Pydantic (module_count is fine; empty members
    # still validates) — but its members iteration adds zero edges.
    # Either way we get exactly 2 contains edges for "real".
    assert out["contains_edges_written"] == 2


@pytest.mark.asyncio
async def test_write_cluster_assignments_blocks_unauthorised_actor(monkeypatch) -> None:
    """The CLUSTER_ACTOR constant must satisfy the Layer A ACL."""
    from memex.graph import schema as schema_mod

    captured: list[tuple[str, str]] = []
    original = schema_mod.check_write_policy

    def _fake(node_type: str, caller: str, owner=None) -> None:
        captured.append((node_type, caller))
        return original(node_type, caller, owner)

    monkeypatch.setattr(
        "memex.graph.cluster_runner.check_write_policy", _fake
    )
    client = _StubGraphClient()
    await write_cluster_assignments(
        client,
        "/repo",
        [ClusterAssignment(name="a", members={"x.py"}, leiden_id=1)],
    )
    assert ("Cluster", CLUSTER_ACTOR) in captured


# ---------------------------------------------------------------------------
# run_init_cluster_pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_init_cluster_pass_returns_none_for_empty_repo(tmp_path: Path) -> None:
    out = await run_init_cluster_pass(tmp_path)
    assert out is None


@pytest.mark.asyncio
async def test_run_init_cluster_pass_runs_without_neo4j(tmp_path: Path, monkeypatch) -> None:
    """When Neo4j is unreachable, init still produces an assignment list
    in memory — persistence is best-effort."""
    (tmp_path / "watcher").mkdir()
    (tmp_path / "watcher" / "handlers.py").write_text("pass\n")
    (tmp_path / "watcher" / "router.py").write_text("pass\n")
    (tmp_path / "graph").mkdir()
    (tmp_path / "graph" / "writer.py").write_text("pass\n")

    async def _no_client():
        raise RuntimeError("Neo4j down")

    monkeypatch.setattr(
        "memex.graph.client.get_graph_client", _no_client
    )

    out = await run_init_cluster_pass(tmp_path)
    assert out is not None
    assert len(out) >= 1
    # Persistence was attempted and failed silently
    placed = {m for a in out for m in a.members}
    assert "watcher/handlers.py" in placed


# ---------------------------------------------------------------------------
# run_cluster_command (CLI surface)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_cluster_command_dry_run_with_no_client(
    tmp_path: Path, monkeypatch
) -> None:
    """Dry-run with no Neo4j: must not raise and must not attempt writes."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("pass\n")
    (tmp_path / "pkg" / "b.py").write_text("pass\n")

    async def _no_client():
        raise RuntimeError("Neo4j down")

    monkeypatch.setattr(
        "memex.graph.client.get_graph_client", _no_client
    )

    # Should complete without exception.
    await run_cluster_command(tmp_path, dry_run=True)


@pytest.mark.asyncio
async def test_run_cluster_command_refuses_without_rerun_when_clusters_exist(
    tmp_path: Path, monkeypatch
) -> None:
    client = _StubGraphClient(has_clusters=True)

    async def _get_client():
        return client

    monkeypatch.setattr(
        "memex.graph.client.get_graph_client", _get_client
    )

    await run_cluster_command(tmp_path, rerun=False, dry_run=False)
    # Only the existence check ran — no episode added, no further queries.
    assert client.episodes == []
    assert len(client.queries) == 1
