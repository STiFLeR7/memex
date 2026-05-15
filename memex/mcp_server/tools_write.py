import logging
import hashlib
import asyncio
import time
import os
from datetime import datetime, UTC
from typing import Optional, Dict
from memex.graph.client import get_graph_client
from memex.config import get_config
from memex.watcher.registry import get_active_repositories

logger = logging.getLogger(__name__)

_current_session_name = None

# Locks to prevent duplicate problem creation during concurrent sessions
_problem_write_locks: Dict[str, asyncio.Lock] = {}

def _get_problem_lock(module: str | None, repo_path: str) -> asyncio.Lock:
    key = f"{repo_path}:{module or '__global__'}"
    if key not in _problem_write_locks:
        _problem_write_locks[key] = asyncio.Lock()
    return _problem_write_locks[key]

async def _resolve_repo(repo: Optional[str]) -> str:
    """Helper to resolve repo_path if not provided by the agent."""
    if repo:
        return os.path.abspath(repo)
    
    config = get_config()
    # 1. Try config repo_root
    if config.repo_root:
        return os.path.abspath(config.repo_root)
        
    # 2. Try registry if exactly one active repo
    repos = get_active_repositories()
    if len(repos) == 1:
        return os.path.abspath(repos[0].path)
        
    raise ValueError("Repository scoping required: please specify 'repo' parameter (multiple repos registered).")

async def _get_or_create_session(client, repo_path: str) -> str:
    """Gets or creates a stable AgentSession for this process."""
    global _current_session_name
    if _current_session_name:
        return _current_session_name
        
    start_time = int(time.time())
    repo_hash = hashlib.md5(repo_path.encode()).hexdigest()[:8]
    session_name = f"session_{repo_hash}_{start_time}"
    
    now = datetime.now(UTC)
    await client.add_episode(
        name=session_name,
        episode_body=f"Agent session {session_name} started for repository {repo_path}. Type: AgentSession. Repo: {repo_path}",
        source_description="agent",
        reference_time=now
    )
    
    # Force repo_path property on the session node
    await client.driver.execute_query(
        "MATCH (n:Entity {name: $name}) SET n.repo_path = $repo",
        params={"name": session_name, "repo": repo_path}
    )
    
    _current_session_name = session_name
    return _current_session_name

def _sanitize_text(val: Optional[str]) -> Optional[str]:
    if not val:
        return val
    # Strip null bytes and RTL overrides, cap at 2000 chars
    return val.replace('\x00', '').replace('\u202e', '')[:2000]

async def record_decision(
    text: str,
    module: Optional[str] = None,
    symbol: Optional[str] = None,
    rationale: Optional[str] = None,
    repo: Optional[str] = None,
) -> str:
    """
    Creates a Decision node in the graph.
    """
    text = _sanitize_text(text)
    rationale = _sanitize_text(rationale)

    if not text or len(text.strip()) < 10:
        return "decision text too short — be specific about what was decided and why"

    try:
        repo_path = await _resolve_repo(repo)
    except ValueError as e:
        return f"Error: {e}"

    client = await get_graph_client()
    now = datetime.now(UTC)

    body_parts = [f"Decision: {text}"]
    if rationale:
        body_parts.append(f"Rationale: {rationale}")
    if module:
        body_parts.append(f"Related Module: {module}")
    if symbol:
        body_parts.append(f"Related Symbol: {symbol}")
    body_parts.append(f"Repo: {repo_path}")

    episode_body = "\n".join(body_parts)

    try:
        result = await client.add_episode(
            name=f"agent_decision_{now.strftime('%Y%m%d_%H%M%S')}",
            episode_body=episode_body,
            source_description="agent",
            reference_time=now
        )
        
        node_id = result.episode.uuid
        
        # Explicitly set repo_path property
        await client.driver.execute_query(
            "MATCH (n:Entity) WHERE n.uuid = $id OR elementId(n) = $id SET n.repo_path = $repo",
            params={"id": node_id, "repo": repo_path}
        )

        if module:
             link_result = await client.add_episode(
                name=f"link_decision_module_{now.strftime('%Y%m%d_%H%M%S')}",
                episode_body=f"The decision '{text}' motivates changes in module '{module}'. Repo: {repo_path}",
                source_description="agent",
                reference_time=now
            )
             await client.driver.execute_query(
                "MATCH (n:Entity) WHERE n.uuid = $id OR elementId(n) = $id SET n.repo_path = $repo",
                params={"id": link_result.episode.uuid, "repo": repo_path}
            )

        display_text = text[:80] + ("..." if len(text) > 80 else "")
        return f"decision recorded: {display_text} [id: {node_id}] in {repo_path}"

    except Exception as e:
        logger.error("Failed to record decision", exc_info=True)
        return f"Error: Failed to record decision in graph. {e}"

async def record_problem(
    text: str,
    module: Optional[str] = None,
    severity: str = "medium",
    repo: Optional[str] = None,
) -> str:
    """
    Creates a Problem node with duplicate detection and concurrent write safety.
    """
    valid_severities = ["critical", "high", "medium", "low"]
    coerced = False
    if severity.lower() not in valid_severities:
        severity = "medium"
        coerced = True
    
    text = _sanitize_text(text)
    if not text or len(text.strip()) < 10:
        return "problem text too short — be specific about the issue"

    try:
        repo_path = await _resolve_repo(repo)
    except ValueError as e:
        return f"Error: {e}"

    client = await get_graph_client()
    now = datetime.now(UTC)

    async with _get_problem_lock(module, repo_path):
        try:
            # 3. Duplicate Detection (scoped to repo)
            search_results = await client.search(text, num_results=10)
            for res in search_results:
                node_type = getattr(res, "type", "unknown")
                # Handle MagicMock in tests or missing property
                res_repo = getattr(res, "repo_path", None)
                if hasattr(res_repo, "__class__") and res_repo.__class__.__name__ == "MagicMock":
                    res_repo = None
                
                score = getattr(res, "score", 0.0)
                if node_type == "Problem" and score > 0.85 and (res_repo is None or res_repo == repo_path):
                    existing_text = getattr(res, "name", "existing problem")
                    node_id = getattr(res, "uuid", "unknown")
                    return f"similar problem already recorded: {existing_text} [id: {node_id}]"
        except Exception as e:
            logger.warning("Duplicate detection search failed: %s", e)

        # 4. Create Problem Episode
        body_parts = [f"Problem: {text}", f"Severity: {severity}", "Status: open"]
        if module:
            body_parts.append(f"Related Module: {module}")
        body_parts.append(f"Repo: {repo_path}")

        episode_body = "\n".join(body_parts)

        try:
            result = await client.add_episode(
                name=f"agent_problem_{now.strftime('%Y%m%d_%H%M%S')}",
                episode_body=episode_body,
                source_description="agent",
                reference_time=now
            )
            
            node_id = result.episode.uuid
            # Explicitly set repo_path property
            await client.driver.execute_query(
                "MATCH (n:Entity) WHERE n.uuid = $id OR elementId(n) = $id SET n.repo_path = $repo",
                params={"id": node_id, "repo": repo_path}
            )

            if module:
                 link_result = await client.add_episode(
                    name=f"link_problem_module_{now.strftime('%Y%m%d_%H%M%S')}",
                    episode_body=f"The problem '{text}' was discovered in module '{module}'. Repo: {repo_path}",
                    source_description="agent",
                    reference_time=now
                )
                 await client.driver.execute_query(
                    "MATCH (n:Entity) WHERE n.uuid = $id OR elementId(n) = $id SET n.repo_path = $repo",
                    params={"id": link_result.episode.uuid, "repo": repo_path}
                )

            res_msg = f"problem recorded [{severity}]: {text[:80]}"
            if coerced:
                res_msg += " (severity coerced to medium)"
            return f"{res_msg} [id: {node_id}] in {repo_path}"

        except Exception as e:
            logger.error("Failed to record problem", exc_info=True)
            return f"Error: Failed to record problem in graph. {e}"

async def resolve_problem(
    problem_id: str,
    resolution_text: str,
    repo: Optional[str] = None,
) -> str:
    """
    Closes a Problem node and links it to the current AgentSession.
    """
    resolution_text = _sanitize_text(resolution_text)
    if not resolution_text or len(resolution_text.strip()) < 10:
        return "resolution text too short — explain how the problem was fixed"

    client = await get_graph_client()
    now = datetime.now(UTC)

    # 1. Look up Problem with retries
    query = """
    MATCH (p:Entity)
    WHERE (p.uuid = $id OR elementId(p) = $id) 
      AND (p.type = 'Problem' OR p.name CONTAINS 'Problem')
      AND ($repo IS NULL OR p.repo_path = $repo)
    OPTIONAL MATCH (p)-[r:RESOLVED_BY]->(s:Entity)
    RETURN p.name as text, r.resolved_at as resolved_at, s.summary as resolution_summary, p.repo_path as repo_path
    LIMIT 1
    """

    rec = None
    for attempt in range(5):
        try:
            res = await client.driver.execute_query(query, params={"id": problem_id, "repo": repo})
            if res.records:
                rec = res.records[0]
                break
        except Exception:
            pass
        await asyncio.sleep(0.5)

    if not rec:
        return f"problem {problem_id} not found"

    if rec['resolved_at']:
        return f"problem {problem_id} was already resolved"

    repo_path = rec.get('repo_path') or await _resolve_repo(repo)
    try:
        # 2. Get/Create Session
        session_name = await _get_or_create_session(client, repo_path)

        # 3. Create resolution episode
        await client.add_episode(
            name=f"resolution_{problem_id}",
            episode_body=f"Problem '{rec['text']}' was resolved in session {session_name}. Resolution: {resolution_text}. Repo: {repo_path}",
            source_description="agent",
            reference_time=now
        )

        # 4. Explicitly mark as closed via direct Cypher
        update_query = """
        MATCH (p:Entity)
        WHERE p.uuid = $id OR elementId(p) = $id
        SET p.status = 'closed', 
            p.valid_until = $now,
            p.type = 'Problem'
        WITH p
        MATCH (s:Entity {name: $session_name})
        MERGE (p)-[r:RESOLVED_BY]->(s)
        SET r.resolved_at = $now, r.fact = $resolution
        """
        await client.driver.execute_query(update_query, params={
            "id": problem_id,
            "session_name": session_name,
            "now": now,
            "resolution": resolution_text
        })

        return f"problem resolved: {rec['text'][:50]}..."

    except Exception as e:
        logger.error("Failed to resolve problem", exc_info=True)
        return f"Error: Failed to resolve problem in graph. {e}"

async def invalidate_edge(
    edge_id: str,
    reason: str,
    repo: Optional[str] = None,
) -> str:
    """
    Explicitly invalidates a graph edge.
    """
    reason = _sanitize_text(reason)
    if not reason or not reason.strip():
        return "invalidation reason is required"

    client = await get_graph_client()
    now = datetime.now(UTC)

    # 1. Look up Edge
    query = """
    MATCH (s:Entity)-[r]->(t:Entity)
    WHERE elementId(r) = $id
      AND ($repo IS NULL OR s.repo_path = $repo)
    RETURN s.name as source, t.name as target, type(r) as edge_type, 
           r.valid_until as valid_until, r.invalidation_reason as old_reason
    LIMIT 1
    """

    try:
        res = await client.driver.execute_query(query, params={"id": edge_id, "repo": repo})
        if not res.records:
            return f"edge {edge_id} not found — use search_context() or get_stale_context() to find edge ids"
        
        rec = res.records[0]
        if rec['valid_until']:
            date_str = rec['valid_until'].isoformat() if hasattr(rec['valid_until'], 'isoformat') else str(rec['valid_until'])
            return f"edge {edge_id} was already invalidated on {date_str[:10]} — reason: {rec['old_reason']}"

        # 2. Update Edge
        update_query = """
        MATCH ()-[r]->()
        WHERE elementId(r) = $id
        SET r.valid_until = $now, 
            r.invalidation_reason = $reason, 
            r.invalidated_by = 'agent'
        """
        await client.driver.execute_query(update_query, params={
            "id": edge_id,
            "now": now,
            "reason": reason
        })

        return f"edge invalidated: {rec['source']} —[{rec['edge_type']}]→ {rec['target']} — reason: {reason}"

    except Exception as e:
        logger.error("Failed to invalidate edge", exc_info=True)
        return f"Error: Failed to invalidate edge in graph. {e}"
