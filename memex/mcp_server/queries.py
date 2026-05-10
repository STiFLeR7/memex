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

async def get_node_counts() -> Dict[str, int]:
    """Returns counts of core node types."""
    client = await get_graph_client()
    query = """
    MATCH (n:Entity)
    RETURN 
      count(CASE WHEN n.name ENDS WITH '.py' OR n.name ENDS WITH '.js' OR n.name ENDS WITH '.ts' OR coalesce(n.type, '') = 'Module' THEN 1 END) as modules,
      count(CASE WHEN n.type = 'Symbol' OR (NOT n.name ENDS WITH '.py' AND n.type IS NULL) THEN 1 END) as symbols,
      count(CASE WHEN n.type = 'Decision' OR n.name CONTAINS 'Decision' THEN 1 END) as decisions,
      count(CASE WHEN n.type = 'Problem' AND coalesce(n.status, 'open') = 'open' THEN 1 END) as problems
    """
    try:
        res = await client.driver.execute_query(query)
        if not res.records:
            return {"modules": 0, "symbols": 0, "decisions": 0, "problems": 0}
        return res.records[0].data()
    except Exception as e:
        raise MemexQueryError("Failed to get node counts", query, e)

async def get_active_modules(since_days: int, scope: Optional[str]) -> List[Dict[str, Any]]:
    """Returns modules modified recently."""
    client = await get_graph_client()
    query = """
    MATCH (m:Entity)
    WHERE (coalesce(m.type, '') = 'Module' OR m.name ENDS WITH '.py' OR m.name ENDS WITH '.js')
      AND ($scope IS NULL OR m.name STARTS WITH $scope)
      AND coalesce(m.created_at, datetime()) >= datetime() - duration({days: $days})
    OPTIONAL MATCH (s:Entity) WHERE coalesce(s.file, '') = m.name OR (s.type = 'Symbol' AND s.file = m.name)
    RETURN m.name as path, coalesce(m.summary, '') as description, count(s) as symbols
    ORDER BY m.name ASC
    LIMIT 20
    """
    try:
        res = await client.driver.execute_query(query, params={"scope": scope, "days": since_days})
        return [r.data() for r in res.records]
    except Exception as e:
        raise MemexQueryError("Failed to get active modules", query, e)

async def get_recent_decisions_raw(since_days: int, module: Optional[str], limit: int) -> List[Dict[str, Any]]:
    """Returns recent decision nodes and their affected modules."""
    client = await get_graph_client()
    query = """
    MATCH (d:Entity)
    WHERE (d.type = 'Decision' OR d.name CONTAINS 'Decision')
      AND coalesce(d.created_at, datetime()) >= datetime() - duration({days: $days})
    
    OPTIONAL MATCH (d)-[:MOTIVATES|RELATES_TO|MENTIONS]-(m:Entity)
    WHERE coalesce(m.type, '') = 'Module' OR m.name ENDS WITH '.py' OR m.name ENDS WITH '.js'
    
    WITH d, collect(DISTINCT m.name) as module_paths
    WHERE ($module IS NULL OR any(path IN module_paths WHERE path STARTS WITH $module))
    
    RETURN 
      d.name as text, 
      coalesce(d.created_at, datetime()) as date, 
      coalesce(d.scope, 'local') as scope, 
      coalesce(d.summary, 'n/a') as rationale, 
      coalesce(d.source_commit, 'n/a') as sha, 
      module_paths
    ORDER BY d.created_at DESC
    LIMIT $limit
    """
    try:
        res = await client.driver.execute_query(query, params={"days": since_days, "module": module, "limit": limit})
        return [r.data() for r in res.records]
    except Exception as e:
        raise MemexQueryError("Failed to get recent decisions", query, e)

async def get_open_problems_raw(module: Optional[str]) -> List[Dict[str, Any]]:
    """Returns unresolved problem nodes."""
    client = await get_graph_client()
    # We match anything that looks like a Problem and is NOT resolved
    query = """
    MATCH (p:Entity)
    WHERE (coalesce(p.type, '') = 'Problem' OR p.name CONTAINS 'Problem') 
      AND coalesce(p.status, 'open') = 'open'
      AND NOT (p)-[:RESOLVED_BY|RESOLVES]->()
    
    OPTIONAL MATCH (p)-[:RELATES_TO|CAUSED_BY]-(m:Entity)
    WHERE coalesce(m.type, '') = 'Module' OR m.name ENDS WITH '.py'
    
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
        res = await client.driver.execute_query(query, params={"module": module})
        return [r.data() for r in res.records]
    except Exception as e:
        raise MemexQueryError("Failed to get open problems", query, e)

async def get_stale_edges(threshold: float, limit: int) -> List[Dict[str, Any]]:
    """Returns relationships with low confidence."""
    client = await get_graph_client()
    query = """
    MATCH (s:Entity)-[r]->(t:Entity)
    WHERE coalesce(r.confidence, 1.0) < $threshold
    RETURN s.name as source, t.name as target, type(r) as edge_type, 
           coalesce(r.confidence, 1.0) as confidence, coalesce(r.valid_from, r.created_at, datetime()) as date, 
           coalesce(r.source_commit, 'unknown') as sha,
           elementId(r) as id
    ORDER BY confidence ASC
    LIMIT $limit
    """
    try:
        res = await client.driver.execute_query(query, params={"threshold": threshold, "limit": limit})
        return [r.data() for r in res.records]
    except Exception as e:
        raise MemexQueryError("Failed to get stale edges", query, e)

async def get_symbol_by_name(name: str, file: Optional[str]) -> Optional[Dict[str, Any]]:
    """Finds a single symbol by name and optional file."""
    client = await get_graph_client()
    query = """
    MATCH (s:Entity {name: $name})
    WHERE coalesce(s.type, '') = 'Symbol' OR (s.type IS NULL AND NOT s.name ENDS WITH '.py')
    AND ($file IS NULL OR coalesce(s.file, '') = $file)
    RETURN s.name as name, coalesce(s.kind, 'fn') as kind, coalesce(s.file, 'unknown') as file, 
           coalesce(s.line, 0) as line, coalesce(s.signature, 'n/a') as signature, 
           coalesce(s.confidence, 1.0) as confidence, coalesce(s.stale, false) as stale,
           elementId(s) as id
    LIMIT 1
    """
    try:
        res = await client.driver.execute_query(query, params={"name": name, "file": file})
        return res.records[0].data() if res.records else None
    except Exception as e:
        raise MemexQueryError(f"Failed to find symbol '{name}'", query, e)

async def get_symbol_callers(symbol_name: str) -> List[Dict[str, Any]]:
    """Finds symbols that call the target symbol."""
    client = await get_graph_client()
    query = """
    MATCH (caller:Entity)-[:CALLS|RELATES_TO]->(s:Entity {name: $name})
    WHERE (caller.type = 'Symbol' OR caller.type IS NULL) AND (s.type = 'Symbol' OR s.type IS NULL)
    RETURN caller.name as name, coalesce(caller.file, 'unknown') as file
    """
    try:
        res = await client.driver.execute_query(query, params={"name": symbol_name})
        return [r.data() for r in res.records]
    except Exception as e:
        raise MemexQueryError(f"Failed to get callers for '{symbol_name}'", query, e)

async def get_symbol_callees(symbol_name: str) -> List[Dict[str, Any]]:
    """Finds symbols called by the target symbol."""
    client = await get_graph_client()
    query = """
    MATCH (s:Entity {name: $name})-[:CALLS|RELATES_TO]->(callee:Entity)
    WHERE (callee.type = 'Symbol' OR callee.type IS NULL) AND (s.type = 'Symbol' OR s.type IS NULL)
    RETURN callee.name as name, coalesce(callee.file, 'unknown') as file
    """
    try:
        res = await client.driver.execute_query(query, params={"name": symbol_name})
        return [r.data() for r in res.records]
    except Exception as e:
        raise MemexQueryError(f"Failed to get callees for '{symbol_name}'", query, e)

async def get_symbol_decisions(symbol_name: str) -> List[str]:
    """Finds decisions linked to a symbol."""
    client = await get_graph_client()
    query = """
    MATCH (d:Entity)-[:MOTIVATES|RELATES_TO]-(s:Entity {name: $name})
    WHERE (d.type = 'Decision' OR d.name CONTAINS 'Decision') AND (s.type = 'Symbol' OR s.type IS NULL)
    RETURN d.name as text
    """
    try:
        res = await client.driver.execute_query(query, params={"name": symbol_name})
        return [r['text'] for r in res.records]
    except Exception as e:
        raise MemexQueryError(f"Failed to get decisions for '{symbol_name}'", query, e)

async def get_symbol_problems(symbol_name: str) -> List[str]:
    """Finds open problems linked to a symbol."""
    client = await get_graph_client()
    query = """
    MATCH (p:Entity)-[:CAUSED_BY|RELATES_TO]-(s:Entity {name: $name})
    WHERE (p.type = 'Problem' OR p.name CONTAINS 'Problem') AND (s.type = 'Symbol' OR s.type IS NULL) AND coalesce(p.status, 'open') = 'open'
    RETURN p.name as text
    """
    try:
        res = await client.driver.execute_query(query, params={"name": symbol_name})
        return [r['text'] for r in res.records]
    except Exception as e:
        raise MemexQueryError(f"Failed to get problems for '{symbol_name}'", query, e)
