"""CLI orchestrator for the v0.3.1 cluster engine.

Two entry points:

* :func:`run_init_cluster_pass` — invoked from ``memex init``. Discovers
  modules by walking the filesystem (the watcher hasn't populated Neo4j
  yet) and runs a one-shot cluster pass, persisting if Neo4j is
  reachable.
* :func:`run_cluster_command` — invoked from ``memex cluster``. Reads
  Module / Cluster / Decision state from Neo4j so re-runs benefit from
  prior pinning and TF-IDF naming. Supports ``--dry-run`` (print only)
  and ``--rerun`` (force overwrite even if Cluster nodes already exist).

Persistence (Deliverable 6) is implemented as a pair of helpers:
:func:`write_cluster_assignments` MERGEs the Cluster + CONTAINS edges,
:func:`tombstone_stale_clusters` expires clusters that no longer appear
in the latest assignment.
"""

from __future__ import annotations

import logging
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Optional

from memex.extractor.lockfile import _python_files
from memex.graph.cluster import ClusterAssignment, run_cluster_pass
from memex.graph.schema import Cluster, check_write_policy

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Filesystem module discovery (used by `memex init` before watcher runs)
# ---------------------------------------------------------------------------


def discover_modules_from_filesystem(repo_root: str | Path) -> list[str]:
    """Walk ``repo_root`` and return repo-relative paths of every Python
    module (``foo/bar.py`` form). Mirrors the path-filter logic in
    :func:`memex.extractor.lockfile._python_files`.
    """
    root = Path(repo_root)
    out: list[str] = []
    for path in _python_files(root):
        rel = path.relative_to(root)
        out.append(str(rel).replace("\\", "/"))
    return out


# ---------------------------------------------------------------------------
# init pass — no Neo4j read, persist best-effort
# ---------------------------------------------------------------------------


async def run_init_cluster_pass(repo_root: str | Path) -> Optional[list[ClusterAssignment]]:
    """One-shot cluster pass during ``memex init``.

    Returns the assignment list on success or ``None`` if no modules were
    discovered. Persistence is best-effort — connection / write failures
    are logged but do not raise, so ``memex init`` succeeds even when
    Neo4j isn't running yet.
    """
    repo = str(Path(repo_root).resolve())
    modules = discover_modules_from_filesystem(repo)

    if not modules:
        logger.info("init: no Python modules found; skipping cluster pass")
        return None

    assignments = await run_cluster_pass(
        repo_root=repo, module_paths=modules
    )
    if not assignments:
        logger.info("init: cluster pass produced no clusters")
        return assignments

    logger.info(
        "init: clustered %d modules into %d clusters",
        len(modules),
        len(assignments),
    )

    # Best-effort persist. Falls through quietly if Neo4j isn't reachable
    # — first `memex watch` / `memex cluster` will re-persist.
    try:
        from memex.graph.client import get_graph_client
        client = await get_graph_client()
        summary = await write_cluster_assignments(client, repo, assignments)
        logger.info(
            "init: persisted %d Cluster nodes (%d CONTAINS edges)",
            summary["clusters_written"],
            summary["contains_edges_written"],
        )
    except Exception:
        logger.info(
            "init: Cluster nodes not persisted (Neo4j unreachable). "
            "Run `memex cluster` once the daemon is up.",
            exc_info=True,
        )

    return assignments


# ---------------------------------------------------------------------------
# `memex cluster` command — Neo4j-backed, supports dry-run / rerun
# ---------------------------------------------------------------------------


async def run_cluster_command(
    repo_root: str | Path,
    *,
    rerun: bool = False,
    dry_run: bool = False,
    refresh_summaries: bool = False,
) -> None:
    """Implementation behind ``memex cluster [--rerun] [--dry-run] [--refresh-summaries]``."""
    repo = str(Path(repo_root).resolve())

    if refresh_summaries:
        if dry_run:
            print("memex cluster: dry run for refresh-summaries is not supported")
            return
        try:
            from memex.graph.client import get_graph_client
            client = await get_graph_client()
        except Exception as exc:
            print(f"memex cluster: Neo4j not reachable — {exc}")
            return
        from memex.graph.cluster_summary import refresh_cluster_summaries
        summaries = await refresh_cluster_summaries(repo, force=True)
        print(f"memex cluster: generated {len(summaries)} cluster summaries")
        return

    if dry_run:
        # Dry-run still wants prior-cluster pinning for an honest preview,
        # so we connect read-only and skip the write phase.
        try:
            from memex.graph.client import get_graph_client
            client = await get_graph_client()
        except Exception:
            logger.warning(
                "cluster: Neo4j unreachable — falling back to filesystem-only dry run",
                exc_info=True,
            )
            client = None

        if client is None:
            modules = discover_modules_from_filesystem(repo)
            assignments = await run_cluster_pass(
                repo_root=repo, module_paths=modules
            )
        else:
            assignments = await run_cluster_pass(repo_root=repo, client=client)

        _print_assignments(assignments)
        return

    # Real run — require Neo4j; surface a helpful error if it's down.
    try:
        from memex.graph.client import get_graph_client
        client = await get_graph_client()
    except Exception as exc:
        print(f"memex cluster: Neo4j not reachable — {exc}")
        return

    if not rerun and await _has_existing_clusters(client, repo):
        print(
            "memex cluster: Cluster nodes already exist for this repo. "
            "Pass --rerun to re-cluster, or --dry-run to preview without writing."
        )
        return

    assignments = await run_cluster_pass(repo_root=repo, client=client)
    summary = await write_cluster_assignments(client, repo, assignments)
    print(
        f"memex cluster: wrote {summary['clusters_written']} clusters "
        f"({summary['contains_edges_written']} CONTAINS edges)"
    )

    # Synthesize summaries for new clusters
    try:
        from memex.graph.cluster_summary import refresh_cluster_summaries
        summaries = await refresh_cluster_summaries(repo)
        if summaries:
            print(f"memex cluster: generated {len(summaries)} cluster summaries")
    except Exception as e:
        logger.error(f"Failed to generate cluster summaries: {e}", exc_info=True)

    _print_assignments(assignments)


def _print_assignments(assignments: list[ClusterAssignment]) -> None:
    if not assignments:
        print("(no clusters)")
        return
    print(f"\nClusters ({len(assignments)}):")
    for a in assignments:
        flags = []
        if a.pinned_by_user:
            flags.append("user-pinned")
        elif a.pinned_from_prior:
            flags.append("inherited")
        flag_str = f" [{','.join(flags)}]" if flags else ""
        print(f"  - {a.name}{flag_str} ({len(a.members)} modules)")


# ---------------------------------------------------------------------------
# Persistence (Deliverable 6) — Cluster nodes + CONTAINS edges
# ---------------------------------------------------------------------------


CLUSTER_ACTOR = "cluster"


async def _has_existing_clusters(client: Any, repo_path: str) -> bool:
    query = """
    MATCH (c:Entity)
    WHERE c.type = 'Cluster' AND c.repo_path = $repo
    RETURN count(c) as n
    """
    try:
        res = await client.driver.execute_query(query, params={"repo": repo_path})
        return bool(res.records and res.records[0]["n"] > 0)
    except Exception:
        return False


async def write_cluster_assignments(
    client: Any,
    repo_path: str,
    assignments: list[ClusterAssignment],
) -> dict[str, int]:
    """Persist a cluster assignment to Neo4j as Cluster nodes + CONTAINS edges.

    Pattern (Q1 hybrid per ARCHITECTURE §4):

    1. ACL check via :func:`memex.graph.schema.check_write_policy` —
       Cluster nodes have ``write_policy='locked'`` and only the
       ``cluster`` caller may mutate them.
    2. ``add_episode`` for each Cluster so the NL Graphiti pipeline can
       surface the cluster in search.
    3. Post-hoc Cypher SET for the v0.3.0 cross-node fields
       (``write_policy``, ``last_reinforced_at``, ``access_count``) and
       the cluster-specific fields (``module_count``, ``repo_path``,
       ``description``).
    4. MERGE CONTAINS edges from the Cluster to each member Module.
    5. ``last_reinforced_at`` on existing CONTAINS edges (Q4 corroboration
       semantics — re-clustering reinforces).
    6. Tombstone CONTAINS edges to modules no longer in this cluster
       (``expired_at = now``).

    Returns ``{"clusters_written": N, "contains_edges_written": M}``.
    """
    # Layer A ACL — raises if the caller isn't allowed to mutate Cluster nodes.
    check_write_policy("Cluster", CLUSTER_ACTOR)

    now = datetime.now(UTC)
    clusters_written = 0
    edges_written = 0

    for a in assignments:
        # Pydantic validation — guards against empty names / silly inputs.
        try:
            Cluster(
                name=a.name,
                repo_path=repo_path,
                module_count=len(a.members),
                created_at=now,
            )
        except Exception:
            logger.warning(
                "cluster: schema validation failed for %s — skipping",
                a.name,
                exc_info=True,
            )
            continue

        episode_name = f"cluster_{a.name}"
        try:
            result = await client.add_episode(
                name=episode_name,
                episode_body=(
                    f"Cluster {a.name} groups {len(a.members)} modules "
                    f"under {repo_path}: "
                    f"{', '.join(sorted(a.members)[:5])}"
                    f"{'...' if len(a.members) > 5 else ''}."
                ),
                source_description="memex cluster engine (hybrid Leiden)",
                reference_time=now,
            )
        except Exception:
            logger.warning(
                "cluster: add_episode failed for %s",
                episode_name,
                exc_info=True,
            )
            continue

        episode_uuid = getattr(getattr(result, "episode", None), "uuid", None)
        # Always upsert the cluster's canonical row by (type, name, repo_path)
        # so two episodes for the same logical cluster collapse into one node.
        upsert_query = """
        MERGE (c:Entity {type: 'Cluster', name: $name, repo_path: $repo})
          ON CREATE SET c.created_at = $now,
                        c.access_count = 0
        SET c.module_count = $module_count,
            c.last_reinforced_at = $now,
            c.write_policy = 'locked',
            c.description = $description
        WITH c
        OPTIONAL MATCH (e:Entity)
        WHERE e.uuid = $episode_uuid OR elementId(e) = $episode_uuid
        FOREACH (_ IN CASE WHEN e IS NULL OR e = c THEN [] ELSE [1] END |
            MERGE (c)-[r:DESCRIBED_BY]->(e)
              ON CREATE SET r.created_at = $now, r.expired_at = NULL
              ON MATCH SET  r.last_reinforced_at = $now, r.expired_at = NULL
        )
        RETURN elementId(c) as cluster_id
        """
        description = _describe_cluster(a)
        try:
            await client.driver.execute_query(
                upsert_query,
                params={
                    "name": a.name,
                    "repo": repo_path,
                    "module_count": len(a.members),
                    "now": now,
                    "description": description,
                    "episode_uuid": episode_uuid,
                },
            )
            clusters_written += 1
        except Exception:
            logger.warning(
                "cluster: upsert failed for %s",
                a.name,
                exc_info=True,
            )
            continue

        # CONTAINS edges — MERGE per member.
        for member in sorted(a.members):
            edge_query = """
            MATCH (c:Entity {type: 'Cluster', name: $name, repo_path: $repo})
            MERGE (m:Entity {name: $member, repo_path: $repo})
              ON CREATE SET m.type = 'Module',
                            m.created_at = $now,
                            m.write_policy = 'locked',
                            m.access_count = 0
            MERGE (c)-[r:CONTAINS]->(m)
              ON CREATE SET r.created_at = $now,
                            r.expired_at = NULL,
                            r.last_reinforced_at = $now
              ON MATCH  SET r.last_reinforced_at = $now,
                            r.expired_at = NULL
            SET m.cluster_name = $name
            """
            try:
                await client.driver.execute_query(
                    edge_query,
                    params={
                        "name": a.name,
                        "repo": repo_path,
                        "member": member,
                        "now": now,
                    },
                )
                edges_written += 1
            except Exception:
                logger.warning(
                    "cluster: CONTAINS edge failed %s -> %s",
                    a.name,
                    member,
                    exc_info=True,
                )

        # Tombstone CONTAINS edges to members no longer in this cluster.
        try:
            await client.driver.execute_query(
                """
                MATCH (c:Entity {type: 'Cluster', name: $name, repo_path: $repo})
                      -[r:CONTAINS]->(m:Entity)
                WHERE r.expired_at IS NULL
                  AND NOT m.name IN $members
                SET r.expired_at = $now
                """,
                params={
                    "name": a.name,
                    "repo": repo_path,
                    "members": sorted(a.members),
                    "now": now,
                },
            )
        except Exception:
            logger.debug(
                "cluster: stale-CONTAINS tombstone skipped for %s",
                a.name,
                exc_info=True,
            )

    # Tombstone clusters that this run no longer produces.
    try:
        await client.driver.execute_query(
            """
            MATCH (c:Entity)
            WHERE c.type = 'Cluster'
              AND c.repo_path = $repo
              AND NOT c.name IN $names
            OPTIONAL MATCH (c)-[r:CONTAINS]->(:Entity)
            SET c.expired_at = $now,
                r.expired_at = $now
            """,
            params={
                "repo": repo_path,
                "names": [a.name for a in assignments],
                "now": now,
            },
        )
    except Exception:
        logger.debug("cluster: stale-cluster tombstone skipped", exc_info=True)

    return {
        "clusters_written": clusters_written,
        "contains_edges_written": edges_written,
    }


def _describe_cluster(a: ClusterAssignment) -> str:
    """Build the ``description`` blurb stored on Cluster nodes."""
    flags = []
    if a.pinned_by_user:
        flags.append("user-pinned")
    elif a.pinned_from_prior:
        flags.append("inherited")
    flag_str = f" [{', '.join(flags)}]" if flags else ""
    return f"{len(a.members)} modules{flag_str}"
