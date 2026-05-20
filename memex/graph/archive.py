"""Tombstone-based cold-storage mover for Neo4j nodes.

Implements the pruning strategy from ARCHITECTURE-v0.3.0 §10:

* Eligibility uses *computed* confidence (not stored): a node is "cold"
  when ``current_confidence < 0.05`` AND ``validated == false`` AND
  ``last_reinforced_at`` is older than 90 days.
* Cold nodes are copied to a local SQLite archive (``.memex/archive.db``)
  AND relabelled in Neo4j with a ``Tombstoned`` label — NOT destructively
  deleted. ``memex archive --restore <node_id>`` lifts the tombstone.
* Validated nodes are never archived, regardless of computed confidence.

The actual ``current_confidence`` helper is owned by Phase 8
(``memex/graph/confidence.py``). When that module exists we import from
it; otherwise we fall back to a faithful local TempValid implementation
so this module is testable in isolation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

ARCHIVE_DB_PATH = ".memex/archive.db"

COLD_CONFIDENCE_THRESHOLD = 0.05
COLD_AGE_DAYS = 90

# Canonical TempValid helper lives in memex.graph.confidence (Phase 8).
# We import the function here; do NOT re-implement the lambda constants —
# any drift between the two files becomes a silent correctness bug.
from memex.graph.confidence import current_confidence as _current_confidence  # noqa: E402


# ---------------------------------------------------------------------------
# Datetime helpers
# ---------------------------------------------------------------------------


def _parse_dt(value: Any) -> datetime | None:
    """Best-effort coercion of a Neo4j/JSON timestamp into an aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            # Accept trailing "Z"
            iso = value.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            return None
    # Neo4j DateTime exposes .to_native() on driver objects
    to_native = getattr(value, "to_native", None)
    if callable(to_native):
        try:
            dt = to_native()
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except Exception:
            return None
    return None


def _serialise_dt(value: Any) -> str | None:
    dt = _parse_dt(value)
    return dt.isoformat() if dt else None


# ---------------------------------------------------------------------------
# Cold-node predicate
# ---------------------------------------------------------------------------


def is_cold(node: dict[str, Any]) -> bool:
    """Predicate from ARCHITECTURE §10: cold = low confidence ∧ unvalidated ∧ stale."""
    if node.get("validated", False):
        return False
    last_reinforced = _parse_dt(node.get("last_reinforced_at"))
    if last_reinforced is None:
        last_reinforced = _parse_dt(node.get("created_at"))
    if last_reinforced is None:
        # No temporal info: be conservative, do NOT archive
        return False
    days_since = (datetime.now(UTC) - last_reinforced).total_seconds() / 86400.0
    if days_since <= COLD_AGE_DAYS:
        return False
    return _current_confidence(node) < COLD_CONFIDENCE_THRESHOLD


# ---------------------------------------------------------------------------
# SQLite archive store
# ---------------------------------------------------------------------------


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS archived_nodes (
    node_id        TEXT PRIMARY KEY,
    labels         TEXT NOT NULL,           -- JSON list
    properties     TEXT NOT NULL,           -- JSON object
    base_confidence REAL,
    validated      INTEGER NOT NULL DEFAULT 0,
    last_reinforced_at TEXT,
    archived_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS archived_edges (
    node_id        TEXT NOT NULL,
    direction      TEXT NOT NULL,           -- 'in' | 'out'
    rel_type       TEXT NOT NULL,
    other_id       TEXT NOT NULL,
    properties     TEXT NOT NULL,           -- JSON object
    FOREIGN KEY (node_id) REFERENCES archived_nodes(node_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_archived_at ON archived_nodes(archived_at);
"""


def _archive_db_path(repo_root: str | Path) -> Path:
    p = Path(repo_root) / ARCHIVE_DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _connect(repo_root: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(_archive_db_path(repo_root)))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_SQL)
    return conn


def _serialise_props(props: dict[str, Any]) -> str:
    """JSON-encode a property dict, converting datetimes to ISO strings."""

    def _default(obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        to_native = getattr(obj, "to_native", None)
        if callable(to_native):
            try:
                return to_native().isoformat()
            except Exception:
                return str(obj)
        return str(obj)

    return json.dumps(props, default=_default, sort_keys=True)


def _store_node(
    conn: sqlite3.Connection,
    node_id: str,
    labels: Iterable[str],
    properties: dict[str, Any],
    edges: Iterable[dict[str, Any]],
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO archived_nodes
        (node_id, labels, properties, base_confidence, validated,
         last_reinforced_at, archived_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            node_id,
            json.dumps(list(labels)),
            _serialise_props(properties),
            float(properties.get("base_confidence", 0.6) or 0.6),
            1 if properties.get("validated") else 0,
            _serialise_dt(properties.get("last_reinforced_at")),
            datetime.now(UTC).isoformat(),
        ),
    )
    # Replace any previously stored edges for this node so re-archiving is idempotent
    conn.execute("DELETE FROM archived_edges WHERE node_id = ?", (node_id,))
    for edge in edges:
        conn.execute(
            """
            INSERT INTO archived_edges (node_id, direction, rel_type, other_id, properties)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                node_id,
                edge.get("direction", "out"),
                edge.get("rel_type", "RELATED_TO"),
                str(edge.get("other_id", "")),
                _serialise_props(edge.get("properties", {})),
            ),
        )


# ---------------------------------------------------------------------------
# Neo4j integration (best-effort; absent driver is handled cleanly)
# ---------------------------------------------------------------------------


_COLD_CANDIDATE_QUERY = """
MATCH (n:Entity)
WHERE coalesce(n.validated, false) = false
RETURN
    coalesce(n.uuid, toString(elementId(n))) AS node_id,
    labels(n) AS labels,
    properties(n) AS props
"""


_NODE_EDGES_QUERY = """
MATCH (n:Entity)
WHERE coalesce(n.uuid, toString(elementId(n))) = $node_id
OPTIONAL MATCH (n)-[r_out]->(m)
WITH n, collect({
    direction: 'out',
    rel_type: type(r_out),
    other_id: coalesce(m.uuid, toString(elementId(m))),
    properties: properties(r_out)
}) AS out_edges
OPTIONAL MATCH (n)<-[r_in]-(m2)
RETURN out_edges + collect({
    direction: 'in',
    rel_type: type(r_in),
    other_id: coalesce(m2.uuid, toString(elementId(m2))),
    properties: properties(r_in)
}) AS edges
"""


_TOMBSTONE_QUERY = """
MATCH (n:Entity)
WHERE coalesce(n.uuid, toString(elementId(n))) = $node_id
SET n:Tombstoned,
    n.archived_at = $now,
    n.archive_db_path = $db_path
"""


_RESTORE_FETCH_QUERY = """
MATCH (n:Tombstoned)
WHERE coalesce(n.uuid, toString(elementId(n))) = $node_id
REMOVE n:Tombstoned
REMOVE n.archived_at
REMOVE n.archive_db_path
RETURN coalesce(n.uuid, toString(elementId(n))) AS node_id
"""


async def _query(client: Any, cypher: str, params: dict[str, Any] | None = None) -> Any:
    """Adapter around the various execute_query shapes a Graphiti driver may expose."""
    result = await client.driver.execute_query(cypher, params=params or {})
    # Newer drivers return an EagerResult with .records
    records = getattr(result, "records", None)
    if records is None and isinstance(result, tuple):
        records = result[0]
    return records or []


async def _load_records(repo_root: str | Path) -> list[dict[str, Any]]:
    """Pull candidate cold nodes from Neo4j. Returns ``[]`` if Neo4j is unavailable.

    The Graphiti client is imported lazily so test environments without a live
    Neo4j (and without the full graphiti dependency) can still exercise the
    SQLite half of this module.
    """
    try:
        from memex.graph.client import get_graph_client
    except Exception:
        logger.warning("archive: graph client import failed — skipping Neo4j read")
        return []

    try:
        client = await get_graph_client()
        records = await _query(client, _COLD_CANDIDATE_QUERY)
    except Exception:
        logger.warning("archive: could not fetch candidate nodes from Neo4j", exc_info=True)
        return []

    out: list[dict[str, Any]] = []
    for r in records:
        try:
            # Neo4j records support both index and key access
            out.append(
                {
                    "node_id": r["node_id"],
                    "labels": list(r["labels"] or []),
                    "props": dict(r["props"] or {}),
                }
            )
        except (KeyError, TypeError):
            continue
    return out


async def _fetch_edges(client: Any, node_id: str) -> list[dict[str, Any]]:
    try:
        records = await _query(client, _NODE_EDGES_QUERY, {"node_id": node_id})
    except Exception:
        return []
    edges: list[dict[str, Any]] = []
    for r in records:
        for edge in (r["edges"] or []):
            if not edge or not edge.get("rel_type"):
                continue
            edges.append(edge)
    return edges


async def _tombstone(client: Any, node_id: str, db_path: str) -> None:
    await _query(
        client,
        _TOMBSTONE_QUERY,
        {
            "node_id": node_id,
            "now": datetime.now(UTC).isoformat(),
            "db_path": db_path,
        },
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def archive_cold_nodes(
    repo_root: str | Path,
    dry_run: bool = False,
    *,
    candidate_records: list[dict[str, Any]] | None = None,
    client: Any | None = None,
) -> int:
    """Archive every cold node under ``repo_root`` to ``.memex/archive.db``.

    Returns the number of nodes archived (or that *would* be archived when
    ``dry_run=True``). The two keyword-only parameters (``candidate_records``,
    ``client``) are test-injection seams — production callers leave them
    ``None`` and the function reaches for Neo4j on its own.
    """
    db_path = _archive_db_path(repo_root)

    if candidate_records is None:
        records = await _load_records(repo_root)
    else:
        records = candidate_records

    if client is None:
        try:
            from memex.graph.client import get_graph_client
            client = await get_graph_client() if records and not dry_run else None
        except Exception:
            client = None

    cold = [r for r in records if is_cold(r.get("props") or {})]
    if not cold:
        logger.info("archive: 0 cold nodes found")
        return 0

    if dry_run:
        logger.info("archive (dry-run): %d cold nodes would be archived", len(cold))
        return len(cold)

    def _write_all(rows: list[tuple[dict[str, Any], list[dict[str, Any]]]]) -> int:
        with _connect(repo_root) as conn:
            with conn:
                for record, edges in rows:
                    _store_node(
                        conn,
                        record["node_id"],
                        record["labels"],
                        record["props"],
                        edges,
                    )
        return len(rows)

    rows: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for record in cold:
        edges: list[dict[str, Any]] = []
        if client is not None:
            edges = await _fetch_edges(client, record["node_id"])
        rows.append((record, edges))

    written = await asyncio.to_thread(_write_all, rows)

    if client is not None:
        for record, _edges in rows:
            try:
                await _tombstone(client, record["node_id"], str(db_path))
            except Exception:
                logger.warning(
                    "archive: tombstone failed for %s", record["node_id"], exc_info=True
                )

    logger.info("archive: %d nodes archived to %s", written, db_path)
    return written


# Alias under the name the decay scheduler looks for (decay.py uses
# getattr(_archive, "tombstone_cold_nodes")). Spec vocabulary in
# TRASH/ARCHITECTURE-v0.3.0.md §10 calls this the tombstone step.
tombstone_cold_nodes = archive_cold_nodes


async def restore_archived_node(node_id: str, repo_root: str | Path) -> bool:
    """Lift the tombstone for ``node_id`` and re-insert from SQLite if needed.

    Fast path: if Neo4j still has the node under the ``Tombstoned`` label we
    just remove the label. Slow path: re-insert from ``archive.db`` with the
    original properties. Returns ``True`` on success.
    """
    def _read() -> dict[str, Any] | None:
        with _connect(repo_root) as conn:
            row = conn.execute(
                "SELECT * FROM archived_nodes WHERE node_id = ?", (node_id,)
            ).fetchone()
            if row is None:
                return None
            edges = conn.execute(
                "SELECT direction, rel_type, other_id, properties FROM archived_edges WHERE node_id = ?",
                (node_id,),
            ).fetchall()
            return {
                "node_id": row["node_id"],
                "labels": json.loads(row["labels"]),
                "properties": json.loads(row["properties"]),
                "edges": [
                    {
                        "direction": e["direction"],
                        "rel_type": e["rel_type"],
                        "other_id": e["other_id"],
                        "properties": json.loads(e["properties"]),
                    }
                    for e in edges
                ],
            }

    archived = await asyncio.to_thread(_read)
    if archived is None:
        logger.warning("archive: node %s not found in archive.db", node_id)
        return False

    try:
        from memex.graph.client import get_graph_client

        client = await get_graph_client()
    except Exception:
        logger.warning(
            "archive: no graph client available — node %s remains in archive.db", node_id
        )
        # SQLite half succeeded; consider this a soft success only if dry_run
        return False

    # Fast path
    try:
        records = await _query(client, _RESTORE_FETCH_QUERY, {"node_id": node_id})
        if records:
            await _delete_archived(repo_root, node_id)
            logger.info("archive: restored %s via tombstone removal", node_id)
            return True
    except Exception:
        logger.warning("archive: fast-path restore failed for %s", node_id, exc_info=True)

    # Slow path — reinsert from SQLite
    props = archived["properties"]
    labels = archived["labels"] or ["Entity"]
    label_str = ":".join(labels)
    try:
        await client.driver.execute_query(
            f"CREATE (n:{label_str}) SET n = $props",
            params={"props": props},
        )
        await _delete_archived(repo_root, node_id)
        logger.info("archive: re-inserted %s from archive.db", node_id)
        return True
    except Exception:
        logger.error("archive: slow-path restore failed for %s", node_id, exc_info=True)
        return False


async def _delete_archived(repo_root: str | Path, node_id: str) -> None:
    def _delete() -> None:
        with _connect(repo_root) as conn:
            with conn:
                conn.execute("DELETE FROM archived_edges WHERE node_id = ?", (node_id,))
                conn.execute("DELETE FROM archived_nodes WHERE node_id = ?", (node_id,))

    await asyncio.to_thread(_delete)


async def archive_stats(repo_root: str | Path) -> dict[str, Any]:
    """Summarise the contents of ``.memex/archive.db``."""

    def _read() -> dict[str, Any]:
        path = _archive_db_path(repo_root)
        size_bytes = path.stat().st_size if path.exists() else 0
        with _connect(repo_root) as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM archived_nodes"
            ).fetchone()["n"]
            oldest_row = conn.execute(
                "SELECT MIN(archived_at) AS oldest FROM archived_nodes"
            ).fetchone()
        oldest = oldest_row["oldest"] if oldest_row else None
        oldest_dt: datetime | None = None
        if oldest:
            try:
                oldest_dt = datetime.fromisoformat(oldest)
                if oldest_dt.tzinfo is None:
                    oldest_dt = oldest_dt.replace(tzinfo=UTC)
            except ValueError:
                oldest_dt = None
        return {
            "total_archived": int(total or 0),
            "oldest_archived_at": oldest_dt,
            "size_bytes": int(size_bytes),
        }

    return await asyncio.to_thread(_read)


# ---------------------------------------------------------------------------
# CLI entrypoint (wired from memex/cli.py)
# ---------------------------------------------------------------------------


async def run_archive_command(
    repo_root: str,
    restore: str | None,
    stats: bool,
) -> None:
    """Dispatch for ``memex archive`` — handles the three CLI modes."""
    if stats:
        info = await archive_stats(repo_root)
        oldest = info["oldest_archived_at"]
        oldest_str = oldest.isoformat() if oldest else "(none)"
        print(f"memex archive — {repo_root}")
        print(f"  total archived nodes: {info['total_archived']}")
        print(f"  oldest archived at:   {oldest_str}")
        print(f"  archive.db size:      {info['size_bytes']} bytes")
        return

    if restore:
        ok = await restore_archived_node(restore, repo_root)
        if ok:
            print(f"memex archive: restored node {restore}")
        else:
            print(f"memex archive: could not restore {restore}")
        return

    try:
        count = await archive_cold_nodes(repo_root)
    except Exception as exc:
        logger.error("memex archive failed", exc_info=True)
        print(f"memex archive failed: {exc}")
        return
    print(f"memex archive: {count} cold node(s) archived")
