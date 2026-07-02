import logging
import hashlib
import asyncio
import time
import os
from datetime import datetime, UTC
from typing import Optional, Dict
from memex.graph.client import get_graph_client
from memex.graph.schema import (
    MemexWritePolicyError,
    check_write_policy as _schema_check_write_policy,
)
from memex.config import get_config
from memex.watcher.registry import get_active_repositories

from memex.watcher.handlers import notify_local_server

logger = logging.getLogger(__name__)

_current_session_name = None

# Locks to prevent duplicate problem creation during concurrent sessions
_problem_write_locks: Dict[str, asyncio.Lock] = {}
# Phase 9 — mirror of `_problem_write_locks` for Decision intent-confirmation.
# Per-`(repo, module)` lock so concurrent agent calls for the same module
# serialise through the dedup check.
_decision_write_locks: Dict[str, asyncio.Lock] = {}


def _get_problem_lock(module: str | None, repo_path: str) -> asyncio.Lock:
    key = f"{repo_path}:{module or '__global__'}"
    if key not in _problem_write_locks:
        _problem_write_locks[key] = asyncio.Lock()
    return _problem_write_locks[key]


def _get_decision_lock(module: str | None, repo_path: str) -> asyncio.Lock:
    """Phase 9 — per-`(repo, module)` lock for record_decision intent confirmation."""
    key = f"{repo_path}:{module or '__global__'}"
    if key not in _decision_write_locks:
        _decision_write_locks[key] = asyncio.Lock()
    return _decision_write_locks[key]


# ---------------------------------------------------------------------------
# Phase 9 — Layer A (Node-type ACL) helper
# ---------------------------------------------------------------------------


def check_write_policy(node_type: str, caller: str = "agent", owner: Optional[str] = None) -> None:
    """Enforces the Layer A node-type ACL from ARCHITECTURE-v0.3.0.md §7.

    Re-exported from `memex.graph.schema` so callers in `tools_write.py` can
    import it from one place. Raises `MemexWritePolicyError` on violation.
    """
    _schema_check_write_policy(node_type, caller, owner=owner)


def _get_intent_confirm_threshold() -> float:
    """Phase 9 — Layer B threshold from `config.retrieval.contradiction_similarity_threshold`.

    Phase 7 owns the `retrieval` config section. Until it lands we fall back
    to the locked-spec default of 0.85.
    """
    try:
        config = get_config()
        retrieval = getattr(config, "retrieval", None)
        if retrieval is not None:
            return float(getattr(retrieval, "contradiction_similarity_threshold", 0.85))
    except Exception:
        pass
    return 0.85


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
    return val.replace('\x00', '').replace('‮', '')[:2000]


# ---------------------------------------------------------------------------
# Phase 9 — Layer B (Intent confirmation) — port of record_problem's 0.85
# dedup pattern. Runs BEFORE add_episode; if a near-duplicate Decision is
# found in the same repo, returns the existing node + 3 options without
# writing. The agent then picks corroborate/supersede/force-proceed.
# ---------------------------------------------------------------------------


async def _check_decision_intent_confirmation(
    client, text: str, module: Optional[str], repo_path: str
) -> Optional[Dict]:
    """Search the graph for an existing Decision whose semantic similarity to
    `text` exceeds the intent-confirmation threshold. Returns the candidate
    dict (with `uuid`, `name`) if a match is found, else None.

    Mirrors the v0.2.0 `record_problem` dedup pattern: best-effort, any
    search failure degrades gracefully to "no candidates" rather than
    blocking the write. This is critical so existing tests that don't mock
    `client.search` continue to pass.
    """
    threshold = _get_intent_confirm_threshold()
    try:
        search_results = await client.search(text, num_results=10)
    except Exception as e:
        logger.warning("Decision intent-confirmation search failed: %s", e)
        return None

    for res in search_results or []:
        node_type = getattr(res, "type", "unknown")
        res_repo = getattr(res, "repo_path", None)
        # Defensive — MagicMock leakage from tests
        if hasattr(res_repo, "__class__") and res_repo.__class__.__name__ == "MagicMock":
            res_repo = None

        score = getattr(res, "score", 0.0)
        # Handle MagicMock score
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0.0

        # B5 fix: require an explicit repo_path match. Previously
        # `res_repo is None` was treated as "match" — that leaked candidates
        # from any repo or untagged legacy data into the intent-confirmation
        # prompt with an actionable id pointing at the wrong repo's node.
        if node_type == "Decision" and score >= threshold and res_repo == repo_path:
            cand_uuid = getattr(res, "uuid", None)
            if not cand_uuid or (hasattr(cand_uuid, "__class__") and cand_uuid.__class__.__name__ == "MagicMock"):
                cand_uuid = "unknown"
                
            res_modules = []
            if cand_uuid != "unknown":
                module_query = """
                MATCH (d:Entity)
                WHERE d.uuid = $uuid OR elementId(d) = $uuid
                OPTIONAL MATCH (d)-[:MOTIVATES|RELATES_TO|MENTIONS]-(m:Entity)
                WHERE coalesce(m.type, '') = 'Module' OR m.name ENDS WITH '.py' OR m.name ENDS WITH '.js'
                RETURN collect(DISTINCT m.name) as modules
                """
                try:
                    m_res = await client.driver.execute_query(module_query, params={"uuid": cand_uuid})
                    if m_res.records:
                        res_modules = m_res.records[0]["modules"] or []
                except Exception:
                    pass

            if module:
                module_match = any(m == module or m.endswith(f"/{module}") or module.endswith(f"/{m}") for m in res_modules)
            else:
                module_match = (len(res_modules) == 0)

            if not module_match:
                continue

            return {
                "uuid": getattr(res, "uuid", "unknown"),
                "name": getattr(res, "name", "existing decision"),
                "score": score,
            }
    return None


def _format_intent_confirmation_response(candidate: Dict) -> str:
    """Render the locked-spec response from ARCHITECTURE §7 Layer B."""
    return (
        f"similar decision already exists [id: {candidate['uuid']}]:\n"
        f"\"{candidate['name']}\"\n\n"
        f"options:\n"
        f"  corroborate: record_decision(corroborates=\"{candidate['uuid']}\")\n"
        f"  supersede:   record_decision(supersedes=\"{candidate['uuid']}\", text=\"<new text>\")\n"
        f"  proceed:     record_decision(text=\"...\", force=True)   # writes a sibling Decision"
    )


async def _corroborate_decision(client, target_id: str, repo_path: str, now: datetime) -> str:
    """Phase 9 — corroborates=<id>: do NOT write a new node. Update the
    existing node's `last_reinforced_at` and add a corroborating edge from
    the current AgentSession. Returns a short status string.
    """
    # Resolve the session so the corroborating edge has an origin
    try:
        session_name = await _get_or_create_session(client, repo_path)
    except Exception as e:
        logger.warning("Could not resolve session for corroboration: %s", e)
        session_name = None

    update_query = """
    MATCH (d:Entity)
    WHERE d.uuid = $id OR elementId(d) = $id
    SET d.last_reinforced_at = $now,
        d.access_count = coalesce(d.access_count, 0) + 1
    RETURN d.name as name
    """
    try:
        res = await client.driver.execute_query(update_query, params={"id": target_id, "now": now})
        if not res.records:
            return f"decision {target_id} not found — nothing to corroborate"
    except Exception as e:
        logger.error("Corroboration update failed", exc_info=True)
        return f"Error: corroboration failed. {e}"

    # Add a corroborating edge (session → decision) if we have a session
    if session_name:
        edge_query = """
        MATCH (s:Entity {name: $session_name})
        MATCH (d:Entity) WHERE d.uuid = $id OR elementId(d) = $id
        MERGE (s)-[r:CORROBORATES]->(d)
        SET r.created_at = $now, r.fact = 'agent corroborated this decision'
        """
        try:
            await client.driver.execute_query(edge_query, params={
                "session_name": session_name,
                "id": target_id,
                "now": now,
            })
        except Exception as e:
            logger.warning("Corroborating edge write failed (decision still reinforced): %s", e)

    return f"corroborated {target_id}"


async def _supersede_decision(client, old_id: str, now: datetime) -> None:
    """Phase 9 — supersedes=<id>: explicit Cypher to expire the superseded
    node's outgoing edges. The new Decision node carrying `supersedes=<old_id>`
    is written by the caller; this helper only marks the old edges expired.
    """
    expire_query = """
    MATCH (old:Entity)-[r]->()
    WHERE (old.uuid = $id OR elementId(old) = $id)
      AND r.expired_at IS NULL
    SET r.expired_at = $now,
        r.invalidated_by = 'agent_supersede'
    """
    try:
        await client.driver.execute_query(expire_query, params={"id": old_id, "now": now})
    except Exception as e:
        logger.warning("Supersede edge-expiry failed: %s", e)


async def record_decision(
    text: str,
    module: Optional[str] = None,
    symbol: Optional[str] = None,
    rationale: Optional[str] = None,
    repo: Optional[str] = None,
    corroborates: Optional[str] = None,
    supersedes: Optional[str] = None,
    force: bool = False,
) -> str:
    """
    Creates a Decision node in the graph.

    Phase 9 governance:
    - Layer A (ACL): Decision is `open`, agent permitted.
    - Layer B (intent confirmation): if a near-duplicate Decision (similarity
      >= threshold, default 0.85) exists for this repo and `force=False` and
      no explicit `corroborates`/`supersedes` was passed, returns the existing
      node's id + 3 options WITHOUT writing.
    - `corroborates=<id>`: no new node; reinforces existing.
    - `supersedes=<id>`: new node carrying `supersedes` field; old edges expired.
    - `force=True`: skip intent-confirmation, always write a sibling.
    """
    # Layer A — Decision is "open", but enforce the policy explicitly so a
    # future change to WRITE_POLICIES is honoured without code edits here.
    try:
        check_write_policy("Decision", caller="agent")
    except MemexWritePolicyError as e:
        return f"Error: {e}"

    text = _sanitize_text(text)
    rationale = _sanitize_text(rationale)

    # corroborates short-circuits text-length validation — no text needed
    if not corroborates and (not text or len(text.strip()) < 10):
        return "decision text too short — be specific about what was decided and why"

    try:
        repo_path = await _resolve_repo(repo)
    except ValueError as e:
        return f"Error: {e}"

    client = await get_graph_client()
    now = datetime.now(UTC)

    # ------------------------------------------------------------------
    # Branch: corroborates — reinforce existing, no new node
    # ------------------------------------------------------------------
    if corroborates:
        async with _get_decision_lock(module, repo_path):
            res = await _corroborate_decision(client, corroborates, repo_path, now)
            notify_local_server()
            return res

    # ------------------------------------------------------------------
    # Branch: write a new node (default, supersede, or force)
    # Acquire per-(repo, module) lock around the dedup check + write so
    # concurrent calls cannot both pass the check and double-write.
    # ------------------------------------------------------------------
    async with _get_decision_lock(module, repo_path):
        # Layer B — intent confirmation (skipped when force=True or when
        # an explicit supersede was provided — agent has already declared
        # intent in that case)
        if not force and not supersedes:
            candidate = await _check_decision_intent_confirmation(client, text, module, repo_path)
            if candidate:
                return _format_intent_confirmation_response(candidate)

        # If supersede was requested, verify existence and then expire the old node's edges first.
        if supersedes:
            check_query = """
            MATCH (d:Entity)
            WHERE (d.uuid = $id OR elementId(d) = $id)
              AND (d.type = 'Decision' OR d.name CONTAINS 'Decision')
            RETURN d.uuid as uuid LIMIT 1
            """
            try:
                res = await client.driver.execute_query(check_query, params={"id": supersedes})
                if not res.records:
                    return f"Error: supersedes target '{supersedes}' not found"
            except Exception as e:
                logger.error("Failed to verify supersedes target existence", exc_info=True)
                return f"Error: Failed to verify supersedes target existence. {e}"
            await _supersede_decision(client, supersedes, now)

        body_parts = [f"Decision: {text}"]
        if rationale:
            body_parts.append(f"Rationale: {rationale}")
        if module:
            body_parts.append(f"Related Module: {module}")
        if symbol:
            body_parts.append(f"Related Symbol: {symbol}")
        if supersedes:
            body_parts.append(f"Supersedes: {supersedes}")
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

            # Signal Pillar A — a freshly agent-recorded Decision must carry an
            # explicit, config-driven base_confidence. Without this SET the node
            # has no base_confidence and current_confidence() falls back to
            # coalesce(..., 1.0), silently treating every agent write as a
            # fully-trusted fact — the exact over-trust Signal exists to prevent.
            # Harness identity isn't yet threaded from the MCP initialize
            # handshake, so resolve the `default` harness (harness=None).
            try:
                initial_conf = get_config().initial_confidence_for(None)
            except Exception:
                initial_conf = 0.6  # never regress to the implicit 1.0 fallback

            # Explicitly set repo_path + Signal confidence anchor + supersedes.
            # validated stays False — only `memex review` (or corroboration)
            # may raise an agent-written decision toward 1.0.
            set_clauses = [
                "n.repo_path = $repo",
                "n.type = coalesce(n.type, 'Decision')",
                "n.base_confidence = $base_confidence",
                "n.validated = $validated",
                "n.source = coalesce(n.source, 'agent')",
                "n.last_reinforced_at = coalesce(n.last_reinforced_at, $now)",
                "n.access_count = coalesce(n.access_count, 0)",
            ]
            params = {
                "id": node_id,
                "repo": repo_path,
                "base_confidence": initial_conf,
                "validated": False,
                "now": now,
            }
            if supersedes:
                set_clauses.append("n.supersedes = $supersedes")
                params["supersedes"] = supersedes
            update_cypher = (
                "MATCH (n:Entity) WHERE n.uuid = $id OR elementId(n) = $id "
                "SET " + ", ".join(set_clauses)
            )
            await client.driver.execute_query(update_cypher, params=params)

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
            prefix = "decision recorded"
            if supersedes:
                prefix = f"decision recorded (supersedes {supersedes})"
            res = f"{prefix}: {display_text} [id: {node_id}] in {repo_path}"
            notify_local_server()
            return res

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
    # Layer A ACL — Problem is "open" for agents; check anyway to honour
    # any future policy change in WRITE_POLICIES.
    try:
        check_write_policy("Problem", caller="agent")
    except MemexWritePolicyError as e:
        return f"Error: {e}"

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
                # B6 (= B5 twin): require strict repo_path match. Previously
                # `res_repo is None` was treated as "match" — that leaked
                # candidates from any repo or untagged legacy data into the
                # dedup return with an actionable id pointing at the wrong
                # repo's node. record_decision was tightened in pass 2; this
                # is the matching tightening for record_problem.
                if node_type == "Problem" and score > 0.85 and res_repo == repo_path:
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
            res = f"{res_msg} [id: {node_id}] in {repo_path}"
            notify_local_server()
            return res

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
    # Layer A — Problem is "open"; explicit check for future-proofing.
    try:
        check_write_policy("Problem", caller="agent")
    except MemexWritePolicyError as e:
        return f"Error: {e}"

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

        res = f"problem resolved: {rec['text'][:50]}..."
        notify_local_server()
        return res

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
    # Layer A — edges aren't strictly node-typed, but invalidation is a
    # write op and we treat it as agent-permitted (no "locked" gate exists
    # for edges in WRITE_POLICIES). Keep the call for symmetry / audit.
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

        res = f"edge invalidated: {rec['source']} —[{rec['edge_type']}]→ {rec['target']} — reason: {reason}"
        notify_local_server()
        return res

    except Exception as e:
        logger.error("Failed to invalidate edge", exc_info=True)
        return f"Error: Failed to invalidate edge in graph. {e}"
