import logging
from typing import Optional, List, Dict, Any
from memex.graph.client import get_graph_client

logger = logging.getLogger(__name__)

class MemexQueryError(Exception):
    """Custom error for Neo4j queries in memex."""
    def __init__(self, message: str, query: str, original_error: Exception):
        super().__init__(f"{message}: {original_error}")
        self.query = query
        self.original_error = original_error

async def get_node_counts(repo: Optional[str] = None) -> Dict[str, int]:
    """Returns counts of core node types."""
    client = await get_graph_client()
    query = """
    MATCH (n:Entity)
    WHERE ($repo IS NULL OR n.repo_path = $repo)
    RETURN 
      count(CASE WHEN n.name ENDS WITH '.py' OR n.name ENDS WITH '.js' OR n.name ENDS WITH '.ts' OR coalesce(n.type, '') = 'Module' THEN 1 END) as modules,
      count(CASE WHEN n.type = 'Symbol' OR (NOT n.name ENDS WITH '.py' AND n.type IS NULL) THEN 1 END) as symbols,
      count(CASE WHEN n.type = 'Decision' OR n.name CONTAINS 'Decision' THEN 1 END) as decisions,
      count(CASE WHEN n.type = 'Problem' AND coalesce(n.status, 'open') = 'open' THEN 1 END) as problems
    """
    try:
        res = await client.driver.execute_query(query, params={"repo": repo})
        if not res.records:
            return {"modules": 0, "symbols": 0, "decisions": 0, "problems": 0}
        return res.records[0].data()
    except Exception as e:
        raise MemexQueryError("Failed to get node counts", query, e)

async def get_active_modules(since_days: int, scope: Optional[str], repo: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns modules modified recently."""
    client = await get_graph_client()
    query = """
    MATCH (m:Entity)
    WHERE (coalesce(m.type, '') = 'Module' OR m.name ENDS WITH '.py' OR m.name ENDS WITH '.js')
      AND ($repo IS NULL OR m.repo_path = $repo)
      AND ($scope IS NULL OR m.name STARTS WITH $scope)
      AND coalesce(m.created_at, datetime()) >= datetime() - duration({days: $days})
    OPTIONAL MATCH (s:Entity) WHERE coalesce(s.file, '') = m.name OR (s.type = 'Symbol' AND s.file = m.name)
    RETURN m.name as path, coalesce(m.summary, '') as description, count(s) as symbols
    ORDER BY m.name ASC
    LIMIT 20
    """
    try:
        res = await client.driver.execute_query(query, params={"scope": scope, "days": since_days, "repo": repo})
        return [r.data() for r in res.records]
    except Exception as e:
        raise MemexQueryError("Failed to get active modules", query, e)

async def get_recent_decisions_raw(since_days: int, module: Optional[str], limit: int, repo: Optional[str] = None, corroborated_only: bool = False) -> List[Dict[str, Any]]:
    """Returns recent decision nodes and their affected modules."""
    client = await get_graph_client()
    query = """
    MATCH (d:Entity)
    WHERE (d.type = 'Decision' OR d.name CONTAINS 'Decision')
      AND ($repo IS NULL OR d.repo_path = $repo)
      AND coalesce(d.created_at, datetime()) >= datetime() - duration({days: $days})
      AND ($corroborated_only = false OR d.corroborated = true OR d.validated = true)
    
    OPTIONAL MATCH (d)-[r:MOTIVATES|RELATES_TO|MENTIONS]-(m:Entity)
    WHERE (r.expired_at IS NULL)
      AND (coalesce(m.type, '') = 'Module' OR m.name ENDS WITH '.py' OR m.name ENDS WITH '.js')
 
    WITH d, collect(DISTINCT m.name) as module_paths
    WHERE ($module IS NULL OR any(path IN module_paths WHERE path STARTS WITH $module))
    
    RETURN
      d.name as text,
      coalesce(d.created_at, datetime()) as date,
      coalesce(d.scope, 'local') as scope,
      coalesce(d.summary, 'n/a') as rationale,
      coalesce(d.source_commit, 'n/a') as sha,
      module_paths,
      // Phase 7: a single canonical `module` field so conflict detection in
      // tools_read.get_recent_decisions can group by it. Picks the first
      // module path (Decisions usually link to one primary module).
      CASE WHEN size(module_paths) > 0 THEN module_paths[0] ELSE NULL END as module,
      // Phase 7: validity-window fields so conflict detection can check
      // whether two same-module Decisions have overlapping windows.
      coalesce(d.valid_from, d.created_at) as valid_from,
      d.valid_until as valid_until,
      coalesce(d.base_confidence, 0.6) as base_confidence,
      d.last_reinforced_at as last_reinforced_at,
      d.created_at as created_at,
      d.validated as validated,
      d.corroborated as corroborated,
      coalesce(d.uuid, elementId(d)) as id
    ORDER BY d.created_at DESC
    LIMIT $limit
    """
    try:
        res = await client.driver.execute_query(query, params={"days": since_days, "module": module, "limit": limit, "repo": repo, "corroborated_only": corroborated_only})
        return [r.data() for r in res.records]
    except Exception as e:
        raise MemexQueryError("Failed to get recent decisions", query, e)

async def get_open_problems_raw(module: Optional[str], repo: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns unresolved problem nodes."""
    client = await get_graph_client()
    # We match anything that looks like a Problem and is NOT resolved
    query = """
    MATCH (p:Entity)
    WHERE (p.type = 'Problem' OR p.name CONTAINS 'Problem') 
      AND ($repo IS NULL OR p.repo_path = $repo)
      AND coalesce(p.status, 'open') = 'open'
      AND NOT (p)-[:RESOLVED_BY|RESOLVES]->()
    
    OPTIONAL MATCH (p)-[r:RELATES_TO|CAUSED_BY]-(m:Entity)
    WHERE (r.expired_at IS NULL)
      AND (coalesce(m.type, '') = 'Module' OR m.name ENDS WITH '.py')
    
    WITH p, m,
         CASE coalesce(p.severity, 'medium')
           WHEN 'critical' THEN 4
           WHEN 'high' THEN 3
           WHEN 'medium' THEN 2
           WHEN 'low' THEN 1
           ELSE 0
         END as sev_score
    
    WHERE ($module IS NULL OR m.name STARTS WITH $module)
    
    RETURN p.name as text, coalesce(p.severity, 'medium') as severity, 
           coalesce(m.name, 'unknown') as module, coalesce(p.created_at, datetime()) as date,
           coalesce(p.surfaced_by, 'watcher') as agent,
           coalesce(p.uuid, elementId(p)) as id
    ORDER BY sev_score DESC, date DESC
    LIMIT 20
    """
    try:
        res = await client.driver.execute_query(query, params={"module": module, "repo": repo})
        return [r.data() for r in res.records]
    except Exception as e:
        raise MemexQueryError("Failed to get open problems", query, e)

async def get_stale_edges(threshold: float, limit: int, repo: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns relationships with low confidence."""
    client = await get_graph_client()
    query = """
    MATCH (s:Entity)-[r]->(t:Entity)
    WHERE r.expired_at IS NULL
      AND coalesce(r.confidence, 1.0) < $threshold
      AND ($repo IS NULL OR s.repo_path = $repo)
    RETURN s.name as source, t.name as target, type(r) as edge_type,
           coalesce(r.confidence, 1.0) as confidence, coalesce(r.valid_from, r.created_at, datetime()) as date,
           coalesce(r.source_commit, 'unknown') as sha,
           elementId(r) as id
    ORDER BY confidence ASC
    LIMIT $limit
    """
    try:
        res = await client.driver.execute_query(query, params={"threshold": threshold, "limit": limit, "repo": repo})
        return [r.data() for r in res.records]
    except Exception as e:
        raise MemexQueryError("Failed to get stale edges", query, e)

async def get_symbol_by_name(name: str, file: Optional[str], repo: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Finds a single symbol by name and optional file."""
    client = await get_graph_client()
    query = """
    MATCH (s:Entity {name: $name})
    WHERE (coalesce(s.type, '') = 'Symbol' OR (s.type IS NULL AND NOT s.name ENDS WITH '.py'))
    AND ($file IS NULL OR coalesce(s.file, '') = $file)
    AND ($repo IS NULL OR s.repo_path = $repo)
    RETURN s.name as name, coalesce(s.kind, 'fn') as kind, coalesce(s.file, 'unknown') as file, 
           coalesce(s.line, 0) as line, coalesce(s.signature, 'n/a') as signature, 
           coalesce(s.confidence, 1.0) as confidence, coalesce(s.stale, false) as stale,
           elementId(s) as id
    LIMIT 1
    """
    try:
        res = await client.driver.execute_query(query, params={"name": name, "file": file, "repo": repo})
        return res.records[0].data() if res.records else None
    except Exception as e:
        raise MemexQueryError(f"Failed to find symbol '{name}'", query, e)

async def get_symbol_callers(symbol_name: str, repo: Optional[str] = None) -> List[Dict[str, Any]]:
    """Finds symbols that call the target symbol."""
    client = await get_graph_client()
    query = """
    MATCH (caller:Entity)-[r:CALLS|RELATES_TO]->(s:Entity {name: $name})
    WHERE r.expired_at IS NULL
      AND (caller.type = 'Symbol' OR caller.type IS NULL)
      AND (s.type = 'Symbol' OR s.type IS NULL)
      AND ($repo IS NULL OR s.repo_path = $repo)
    RETURN caller.name as name, coalesce(caller.file, 'unknown') as file
    """
    try:
        res = await client.driver.execute_query(query, params={"name": symbol_name, "repo": repo})
        return [r.data() for r in res.records]
    except Exception as e:
        raise MemexQueryError(f"Failed to get callers for '{symbol_name}'", query, e)

async def get_symbol_callees(symbol_name: str, repo: Optional[str] = None) -> List[Dict[str, Any]]:
    """Finds symbols called by the target symbol."""
    client = await get_graph_client()
    query = """
    MATCH (s:Entity {name: $name})-[r:CALLS|RELATES_TO]->(callee:Entity)
    WHERE r.expired_at IS NULL
      AND (callee.type = 'Symbol' OR callee.type IS NULL)
      AND (s.type = 'Symbol' OR s.type IS NULL)
      AND ($repo IS NULL OR s.repo_path = $repo)
    RETURN callee.name as name, coalesce(callee.file, 'unknown') as file
    """
    try:
        res = await client.driver.execute_query(query, params={"name": symbol_name, "repo": repo})
        return [r.data() for r in res.records]
    except Exception as e:
        raise MemexQueryError(f"Failed to get callees for '{symbol_name}'", query, e)

async def get_symbol_decisions(symbol_name: str, repo: Optional[str] = None) -> List[str]:
    """Finds decisions linked to a symbol."""
    client = await get_graph_client()
    query = """
    MATCH (d:Entity)-[r:MOTIVATES|RELATES_TO]-(s:Entity {name: $name})
    WHERE r.expired_at IS NULL
      AND (d.type = 'Decision' OR d.name CONTAINS 'Decision')
      AND (s.type = 'Symbol' OR s.type IS NULL)
      AND ($repo IS NULL OR s.repo_path = $repo)
    RETURN d.name as text
    """
    try:
        res = await client.driver.execute_query(query, params={"name": symbol_name, "repo": repo})
        return [r['text'] for r in res.records]
    except Exception as e:
        raise MemexQueryError(f"Failed to get decisions for '{symbol_name}'", query, e)

async def get_symbol_problems(symbol_name: str, repo: Optional[str] = None) -> List[str]:
    """Finds open problems linked to a symbol."""
    client = await get_graph_client()
    query = """
    MATCH (p:Entity)-[r:CAUSED_BY|RELATES_TO]-(s:Entity {name: $name})
    WHERE r.expired_at IS NULL
      AND (p.type = 'Problem' OR p.name CONTAINS 'Problem')
      AND (s.type = 'Symbol' OR s.type IS NULL)
      AND coalesce(p.status, 'open') = 'open'
      AND ($repo IS NULL OR s.repo_path = $repo)
    RETURN p.name as text
    """
    try:
        res = await client.driver.execute_query(query, params={"name": symbol_name, "repo": repo})
        return [r['text'] for r in res.records]
    except Exception as e:
        raise MemexQueryError(f"Failed to get problems for '{symbol_name}'", query, e)


# ---------------------------------------------------------------------------
# v0.3.0 placeholder query functions (Phase 5.9 scaffolding)
# Implementations land in Phase 6 (cluster) / Phase 7 (composite scoring) /
# Phase 8 (unvalidated count) / per the locked plan.
# Returning empty / safe defaults so callers don't break before phases land.
# ---------------------------------------------------------------------------


async def count_unvalidated_decisions(repo: Optional[str] = None) -> int:
    """Phase 8: returns count of Decision nodes with validated=False.
    Surfaced in `get_project_context()` as a warning.
    Scaffolded — returns 0 until Phase 8 lands."""
    client = await get_graph_client()
    query = """
    MATCH (d:Entity)
    WHERE (d.type = 'Decision' OR d.name CONTAINS 'Decision')
      AND coalesce(d.validated, false) = false
      AND ($repo IS NULL OR d.repo_path = $repo)
    RETURN count(d) as cnt
    """
    try:
        res = await client.driver.execute_query(query, params={"repo": repo})
        return res.records[0]["cnt"] if res.records else 0
    except Exception:
        return 0


async def get_cluster_level_context(repo: Optional[str] = None) -> List[Dict[str, Any]]:
    """Phase 6 (dev2): returns one row per Cluster with aggregate counts.
    Scaffolded — returns empty list until Cluster nodes exist."""
    client = await get_graph_client()
    query = """
    MATCH (c:Entity)
    WHERE c.type = 'Cluster'
      AND c.expired_at IS NULL
      AND ($repo IS NULL OR c.repo_path = $repo)
    OPTIONAL MATCH (c)-[rc:CONTAINS]->(m:Entity)
    WHERE rc.expired_at IS NULL AND m.type = 'Module'
    RETURN c.name as name,
           coalesce(c.summary, c.description, '') as description,
           c.summary as summary,
           collect(DISTINCT m.name) as members,
           count(DISTINCT m) as module_count
    ORDER BY c.name ASC
    """
    try:
        res = await client.driver.execute_query(query, params={"repo": repo})
        return [r.data() for r in res.records]
    except Exception:
        return []


async def increment_access_count(node_ids: List[str]) -> None:
    """Phase 7: bumps `access_count` on every node returned by a retrieval.
    Feeds the rehearsal_boost in composite scoring.
    Scaffolded — does the update; safe to call before reranker exists."""
    if not node_ids:
        return
    client = await get_graph_client()
    query = """
    MATCH (n:Entity)
    WHERE n.uuid IN $ids OR elementId(n) IN $ids
    SET n.access_count = coalesce(n.access_count, 0) + 1
    """
    try:
        await client.driver.execute_query(query, params={"ids": node_ids})
    except Exception:
        # Best-effort: access_count being stale doesn't break retrieval,
        # but log so the failure is diagnosable rather than silent.
        logger.debug("increment_access_count failed for %d ids", len(node_ids), exc_info=True)


# ---------------------------------------------------------------------------
# Phase 7 — composite_search()
# Wraps Graphiti's `client.search()` with the multiplicative composite
# reranker + RRF (memex/mcp_server/reranker.py). All existing query
# functions above are intentionally untouched.
#
# Why a wrapper instead of editing client.search() directly: Graphiti's
# public `search()` returns a SINGLE modality list (typically edges). The
# RRF-across-4-modalities merge in ARCHITECTURE §8 will be wired up once
# the lower-level `_search()` surface is exercised (Phase 8+). Until then
# `composite_search()` runs the composite over the single list returned by
# `client.search()` and the RRF merge degrades gracefully (each result's
# rrf_score equals 1/(rank+60)).
# ---------------------------------------------------------------------------


async def composite_search(
    query: str,
    num_results: int = 8,
    tau_days: Optional[int] = None,
    conf_floor: Optional[float] = None,
    rehearsal_weight: Optional[float] = None,
    rrf_k: Optional[int] = None,
    repo: Optional[str] = None,
    client: Any = None,
):
    """Search Graphiti and apply the Phase 7 composite reranker.

    Returns a list of ``ScoredResult`` (see ``memex.mcp_server.reranker``)
    sorted by composite ``final_score`` (descending). Drops any results with
    ``expired_at`` set (§8 Step 1). Bumps ``access_count`` on every returned
    node so the rehearsal_boost has data to consume on subsequent retrievals.

    Configuration knobs default to ``RetrievalConfig`` values (which in turn
    default to the constants in ``reranker.py``). Explicit kwargs override
    config — useful for tests and future tuning paths.
    """
    # Local imports to avoid a circular dependency between queries.py and
    # reranker.py (reranker imports confidence; both are reachable from the
    # server module graph).
    from memex.mcp_server.reranker import (
        RECENCY_TAU_DAYS,
        CONF_FLOOR,
        REHEARSAL_WEIGHT,
        RRF_K,
        score_and_rank,
        merge_modalities,
    )

    # Resolve config; fall back to module constants if RetrievalConfig
    # isn't present on the running config (older configs).
    try:
        from memex.config import get_config
        cfg = get_config()
        retrieval = getattr(cfg, "retrieval", None)
    except Exception:
        retrieval = None

    if tau_days is None:
        tau_days = getattr(retrieval, "recency_tau_days", RECENCY_TAU_DAYS) if retrieval else RECENCY_TAU_DAYS
    if conf_floor is None:
        conf_floor = getattr(retrieval, "conf_floor", CONF_FLOOR) if retrieval else CONF_FLOOR
    if rehearsal_weight is None:
        rehearsal_weight = getattr(retrieval, "rehearsal_weight", REHEARSAL_WEIGHT) if retrieval else REHEARSAL_WEIGHT
    if rrf_k is None:
        rrf_k = getattr(retrieval, "rrf_k", RRF_K) if retrieval else RRF_K

    # Injectable client — production callers leave None and we resolve via the
    # singleton; tests/wrappers can pass a pre-resolved client so their patched
    # `get_graph_client` mock is honoured even when this function is called
    # from a different module.
    if client is None:
        client = await get_graph_client()
    raw_results = await client.search(query, num_results=num_results)

    # Optional repo filter — applied BEFORE composite scoring so we don't
    # waste rerank work on out-of-scope hits.
    if repo:
        raw_results = [r for r in raw_results if getattr(r, "repo_path", None) == repo]

    # Single-modality flow: score + sort, then run RRF (no-op for one list
    # but preserves the contract so plumbing the other 3 modalities later
    # is additive).
    edges_ranked = score_and_rank(
        raw_results,
        tau_days=tau_days,
        conf_floor=conf_floor,
        rehearsal_weight=rehearsal_weight,
        modality="edge",
    )
    merged = merge_modalities(edges_ranked, k=rrf_k)

    # Best-effort access_count bump — never block the returned results.
    uuids = [sr.uuid for sr in merged if sr.uuid]
    if uuids:
        try:
            await increment_access_count(uuids)
        except Exception:
            logger.debug("composite_search: access_count bump failed", exc_info=True)

    return merged
