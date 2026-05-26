"""Tests for ``memex.graph.archive``."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, UTC
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from memex.graph.archive import (
    ARCHIVE_DB_PATH,
    archive_cold_nodes,
    archive_stats,
    is_cold,
    restore_archived_node,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _record(
    node_id: str,
    *,
    base_confidence: float,
    validated: bool,
    days_old: int,
    labels: list[str] | None = None,
    extra: dict | None = None,
) -> dict:
    """Build a candidate-node dict mirroring what Neo4j would return."""
    last = (datetime.now(UTC) - timedelta(days=days_old)).isoformat()
    props = {
        "uuid": node_id,
        "name": f"node-{node_id}",
        "base_confidence": base_confidence,
        "validated": validated,
        "last_reinforced_at": last,
        "created_at": last,
    }
    if extra:
        props.update(extra)
    return {
        "node_id": node_id,
        "labels": labels or ["Entity"],
        "props": props,
    }


# ---------------------------------------------------------------------------
# is_cold predicate
# ---------------------------------------------------------------------------


def test_is_cold_predicate_matches_architecture_spec() -> None:
    cold = _record("cold", base_confidence=0.6, validated=False, days_old=400)
    fresh = _record("fresh", base_confidence=0.6, validated=False, days_old=5)
    validated = _record("val", base_confidence=0.6, validated=True, days_old=400)
    not_old_enough = _record("not_old_enough", base_confidence=1.0, validated=False, days_old=80)

    assert is_cold(cold["props"]) is True
    assert is_cold(fresh["props"]) is False
    assert is_cold(validated["props"]) is False
    # Under the new 3-regime decay math, any unvalidated node older than 90 days
    # decays to < 0.05 (even with base_confidence = 1.0) and is thus cold.
    # An unvalidated node with age <= 90 days is never cold because of the age threshold.
    assert is_cold(not_old_enough["props"]) is False



# ---------------------------------------------------------------------------
# archive_cold_nodes — SQLite write path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_tombstones_cold_nodes_to_sqlite(tmp_path: Path) -> None:
    cold_a = _record("cold-a", base_confidence=0.4, validated=False, days_old=400)
    cold_b = _record("cold-b", base_confidence=0.3, validated=False, days_old=200)
    fresh = _record("fresh", base_confidence=0.6, validated=False, days_old=5)
    candidates = [cold_a, cold_b, fresh]

    client = MagicMock()
    client.driver = MagicMock()
    client.driver.execute_query = AsyncMock(
        return_value=MagicMock(records=[{"edges": []}])
    )

    count = await archive_cold_nodes(
        tmp_path, candidate_records=candidates, client=client
    )

    assert count == 2

    db_path = tmp_path / ARCHIVE_DB_PATH
    assert db_path.exists()

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT node_id, labels, properties, validated FROM archived_nodes ORDER BY node_id"
        ).fetchall()

    assert [r["node_id"] for r in rows] == ["cold-a", "cold-b"]
    for r in rows:
        assert json.loads(r["labels"]) == ["Entity"]
        props = json.loads(r["properties"])
        assert props["uuid"] in {"cold-a", "cold-b"}
        assert r["validated"] == 0

    # Tombstone Cypher must have been issued — once per cold node.
    tombstone_calls = [
        c for c in client.driver.execute_query.call_args_list
        if "Tombstoned" in str(c.args[0])
    ]
    assert len(tombstone_calls) == 2


@pytest.mark.asyncio
async def test_archive_does_not_tombstone_validated_nodes(tmp_path: Path) -> None:
    validated_cold = _record(
        "v1", base_confidence=0.6, validated=True, days_old=400
    )
    candidates = [validated_cold]

    client = MagicMock()
    client.driver = MagicMock()
    client.driver.execute_query = AsyncMock(
        return_value=MagicMock(records=[{"edges": []}])
    )

    count = await archive_cold_nodes(
        tmp_path, candidate_records=candidates, client=client
    )

    assert count == 0
    # No DB write -> no .memex/archive.db file should be touched with data
    db_path = tmp_path / ARCHIVE_DB_PATH
    if db_path.exists():
        with sqlite3.connect(str(db_path)) as conn:
            n = conn.execute("SELECT COUNT(*) FROM archived_nodes").fetchone()[0]
        assert n == 0


@pytest.mark.asyncio
async def test_archive_dry_run_does_not_write(tmp_path: Path) -> None:
    cold = _record("c", base_confidence=0.3, validated=False, days_old=200)

    count = await archive_cold_nodes(
        tmp_path, dry_run=True, candidate_records=[cold], client=MagicMock()
    )

    assert count == 1
    db_path = tmp_path / ARCHIVE_DB_PATH
    if db_path.exists():
        with sqlite3.connect(str(db_path)) as conn:
            n = conn.execute("SELECT COUNT(*) FROM archived_nodes").fetchone()[0]
        assert n == 0


# ---------------------------------------------------------------------------
# restore_archived_node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_restore_reinserts_with_temporal_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cold = _record("restore-me", base_confidence=0.3, validated=False, days_old=200)
    original_last_reinforced = cold["props"]["last_reinforced_at"]

    archive_client = MagicMock()
    archive_client.driver = MagicMock()
    archive_client.driver.execute_query = AsyncMock(
        return_value=MagicMock(records=[{"edges": []}])
    )
    await archive_cold_nodes(
        tmp_path, candidate_records=[cold], client=archive_client
    )

    # Simulate a Neo4j environment with NO live tombstone -> slow path fires
    restore_client = MagicMock()
    restore_client.driver = MagicMock()
    create_calls: list[tuple[str, dict]] = []

    async def fake_execute_query(cypher: str, params=None):
        params = params or {}
        if "Tombstoned" in cypher and "REMOVE" in cypher:
            return MagicMock(records=[])  # fast-path miss
        if cypher.strip().startswith("CREATE"):
            create_calls.append((cypher, params))
            return MagicMock(records=[])
        return MagicMock(records=[])

    restore_client.driver.execute_query = AsyncMock(side_effect=fake_execute_query)

    async def fake_get_graph_client() -> MagicMock:
        return restore_client

    monkeypatch.setattr(
        "memex.graph.client.get_graph_client", fake_get_graph_client
    )

    ok = await restore_archived_node("restore-me", tmp_path)
    assert ok is True

    # CREATE happened with the original properties
    assert len(create_calls) == 1
    _cypher, params = create_calls[0]
    props = params["props"]
    assert props["uuid"] == "restore-me"
    assert props["last_reinforced_at"] == original_last_reinforced
    assert props["base_confidence"] == 0.3

    # SQLite entry must be gone after successful restore
    with sqlite3.connect(str(tmp_path / ARCHIVE_DB_PATH)) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM archived_nodes WHERE node_id = ?",
            ("restore-me",),
        ).fetchone()[0]
    assert n == 0


@pytest.mark.asyncio
async def test_restore_missing_node_returns_false(tmp_path: Path) -> None:
    ok = await restore_archived_node("does-not-exist", tmp_path)
    assert ok is False


# ---------------------------------------------------------------------------
# archive_stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_stats_returns_counts(tmp_path: Path) -> None:
    # Empty archive
    stats = await archive_stats(tmp_path)
    assert stats["total_archived"] == 0
    assert stats["oldest_archived_at"] is None
    assert stats["size_bytes"] >= 0

    # Seed two archived nodes
    cold_a = _record("a", base_confidence=0.3, validated=False, days_old=120)
    cold_b = _record("b", base_confidence=0.2, validated=False, days_old=300)
    client = MagicMock()
    client.driver = MagicMock()
    client.driver.execute_query = AsyncMock(
        return_value=MagicMock(records=[{"edges": []}])
    )
    await archive_cold_nodes(
        tmp_path, candidate_records=[cold_a, cold_b], client=client
    )

    stats = await archive_stats(tmp_path)
    assert stats["total_archived"] == 2
    assert isinstance(stats["oldest_archived_at"], datetime)
    assert stats["size_bytes"] > 0


# ---------------------------------------------------------------------------
# Edge serialisation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_stores_outgoing_and_incoming_edges(tmp_path: Path) -> None:
    cold = _record("with-edges", base_confidence=0.3, validated=False, days_old=200)

    edge_records = [
        {
            "edges": [
                {
                    "direction": "out",
                    "rel_type": "RELATES_TO",
                    "other_id": "neighbour-1",
                    "properties": {"weight": 0.5},
                },
                {
                    "direction": "in",
                    "rel_type": "MENTIONS",
                    "other_id": "neighbour-2",
                    "properties": {},
                },
            ]
        }
    ]

    client = MagicMock()
    client.driver = MagicMock()
    client.driver.execute_query = AsyncMock(
        return_value=MagicMock(records=edge_records)
    )

    count = await archive_cold_nodes(
        tmp_path, candidate_records=[cold], client=client
    )
    assert count == 1

    with sqlite3.connect(str(tmp_path / ARCHIVE_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT direction, rel_type, other_id FROM archived_edges WHERE node_id = ? ORDER BY direction",
            ("with-edges",),
        ).fetchall()

    assert {(r["direction"], r["rel_type"], r["other_id"]) for r in rows} == {
        ("in", "MENTIONS", "neighbour-2"),
        ("out", "RELATES_TO", "neighbour-1"),
    }
