import logging
from typing import Optional
from memex.graph.client import get_graph_client
from memex.config import get_config
from memex.mcp_server.queries import (
    get_node_counts,
    get_active_modules,
    get_recent_decisions_raw,
    get_open_problems_raw,
    get_stale_edges,
    get_symbol_by_name,
    get_symbol_callers,
    get_symbol_callees,
    get_symbol_decisions,
    get_symbol_problems,
    count_unvalidated_decisions,
    get_cluster_level_context,  # v0.3.1 Deliverable 4
    composite_search,  # Phase 7
)
from memex.mcp_server.formatter import (
    format_project_context,
    format_symbol_context,
    format_decisions,
    format_problems,
    format_search_results_with_breakdown,  # Phase 7
    format_stale_edges
)

logger = logging.getLogger(__name__)


def _emit_decision_confidence(decisions: list) -> None:
    """Best-effort: annotate the active OTel span with the mean computed
    confidence of the decisions being returned (Signal D3). Never raises —
    observability must not break a read tool."""
    try:
        if not decisions:
            return
        from memex.graph.confidence import current_confidence
        from memex.graph.otel import set_decision_confidence
        confs = [current_confidence(d) for d in decisions]
        if confs:
            set_decision_confidence(sum(confs) / len(confs))
    except Exception:
        logger.debug("failed to emit memex.decision.confidence", exc_info=True)


def _emit_validated_ratio(decisions: list) -> None:
    """Best-effort: update the validated_ratio gauge with the fraction of
    decisions that are validated or corroborated (Signal D3). Never raises."""
    try:
        from memex.graph.otel import record_validated_ratio
        if not decisions:
            record_validated_ratio(0.0)
            return
        ok = sum(1 for d in decisions if (d.get("validated") or d.get("corroborated")))
        record_validated_ratio(ok / len(decisions))
    except Exception:
        logger.debug("failed to emit memex.decision.validated_ratio", exc_info=True)


async def get_project_context(scope: Optional[str] = None, repo: Optional[str] = None) -> str:
    """
    Returns a structured markdown briefing of the project.
    """
    try:
        config = get_config()
        
        counts = await get_node_counts(repo=repo)
        modules = await get_active_modules(since_days=30, scope=scope, repo=repo)
        decisions = await get_recent_decisions_raw(since_days=7, module=scope, limit=10, repo=repo)
        problems = await get_open_problems_raw(module=scope, repo=repo)
        stale_list = await get_stale_edges(threshold=0.3, limit=1, repo=repo)
        # v0.3.0 Phase 8 — surface unvalidated decision count as a leading
        # warning so the gap between watcher-synthesised and human-reviewed
        # decisions stays visible.
        try:
            unvalidated_count = await count_unvalidated_decisions(repo=repo)
        except Exception:
            unvalidated_count = 0

        # v0.3.1 Deliverable 4: prefer cluster-level summary when Cluster
        # nodes exist for this repo. Scope=<cluster-name> still drills into
        # modules — when scope is set, skip the cluster summary so the
        # agent gets module-level detail for that scope.
        clusters: list = []
        if not scope:
            try:
                clusters = await get_cluster_level_context(repo=repo)
            except Exception:
                logger.debug("cluster-level context fetch failed", exc_info=True)

        result = format_project_context(
            repo_root=repo or config.repo_root,
            counts=counts,
            modules=modules,
            decisions=decisions,
            problems=problems,
            stale_count=len(stale_list),
            unvalidated_count=unvalidated_count,
            clusters=clusters,
        )
        try:
            module_files = [m['path'] for m in modules if m.get('path')]
            from memex.graph.telemetry import record_tool_call
            await record_tool_call("get_project_context", len(result) // 4, repo or config.repo_root, module_files)
        except Exception:
            pass
        return result

    except Exception as e:
        logger.error("Failed to generate project context", exc_info=True)
        return f"Error: Failed to retrieve project context from Neo4j. {e}"

async def get_symbol_context(symbol_name: str, file: Optional[str] = None, repo: Optional[str] = None) -> str:
    """
    Returns everything the graph knows about a specific symbol.
    """
    try:
        symbol = await get_symbol_by_name(symbol_name, file, repo=repo)
        
        if not symbol:
            client = await get_graph_client()
            search_results = await client.search(symbol_name, num_results=1)
            # Filter search results if repo is provided
            if repo:
                search_results = [r for r in search_results if getattr(r, 'repo_path', None) == repo]
            
            suggestion = ""
            if search_results:
                best = search_results[0]
                suggestion = f"\n\nDid you mean '{getattr(best, 'name', 'unknown')}'?"
            result = f"Symbol '{symbol_name}' not found.{suggestion}"
            try:
                from memex.graph.telemetry import record_tool_call
                await record_tool_call("get_symbol_context", len(result) // 4, repo)
            except Exception:
                pass
            return result

        callers = await get_symbol_callers(symbol_name, repo=repo)
        callees = await get_symbol_callees(symbol_name, repo=repo)
        decisions = await get_symbol_decisions(symbol_name, repo=repo)
        problems = await get_symbol_problems(symbol_name, repo=repo)

        result = format_symbol_context(
            symbol=symbol,
            callers=callers,
            callees=callees,
            decisions=decisions,
            problems=problems
        )
        try:
            module_files = [symbol['file']] if symbol.get('file') and symbol.get('file') != 'unknown' else None
            from memex.graph.telemetry import record_tool_call
            await record_tool_call("get_symbol_context", len(result) // 4, repo, module_files)
        except Exception:
            pass
        return result

    except Exception as e:
        logger.error("Failed to fetch symbol context", exc_info=True)
        return f"Error: Failed to retrieve symbol context for '{symbol_name}'. {e}"

async def get_recent_decisions(days: int = 30, module: Optional[str] = None, repo: Optional[str] = None, corroborated_only: bool = False) -> str:
    """
    Returns Decision nodes created within the last days days, newest first.
    Phase 7: runs `detect_decision_conflicts` so contradictory Decisions with
    overlapping validity windows get a `conflict: true` flag the formatter
    surfaces to the agent.
    """
    try:
        decisions = await get_recent_decisions_raw(since_days=days, module=module, limit=21, repo=repo, corroborated_only=corroborated_only)
        # Phase 7 conflict detection — opportunistic. Falls back silently if
        # the graph client / similarity function isn't usable so an LLM
        # hiccup doesn't break the read tool.
        try:
            from memex.mcp_server.conflict import detect_decision_conflicts
            client = await get_graph_client()
            decisions = await detect_decision_conflicts(decisions, client)
        except Exception:
            logger.debug("conflict detection skipped this run", exc_info=True)

        result = format_decisions(
            decisions=decisions,
            days=days,
            module=module,
            total_count=len(decisions)
        )
        _emit_decision_confidence(decisions)
        try:
            from memex.graph.telemetry import record_tool_call
            await record_tool_call("get_recent_decisions", len(result) // 4, repo)
        except Exception:
            pass
        return result

    except Exception as e:
        logger.error("Failed to fetch recent decisions", exc_info=True)
        return f"Error: Failed to retrieve decisions from Neo4j. {e}"

async def get_open_problems(module: Optional[str] = None, repo: Optional[str] = None) -> str:
    """
    Returns Problem nodes with no resolved_by edge.
    """
    try:
        problems = await get_open_problems_raw(module=module, repo=repo)
        if not problems:
            result = "no open problems recorded"
            try:
                from memex.graph.telemetry import record_tool_call
                await record_tool_call("get_open_problems", len(result) // 4, repo, [module] if module else None)
            except Exception:
                pass
            return result
            
        # Sort in Python as well for mock consistency
        def sev_to_score(s):
            return {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(s.lower(), 0)
        
        problems.sort(key=lambda x: sev_to_score(x.get('severity', 'medium')), reverse=True)
            
        result = format_problems(problems=problems, module=module)
        try:
            from memex.graph.telemetry import record_tool_call
            await record_tool_call("get_open_problems", len(result) // 4, repo, [module] if module else None)
        except Exception:
            pass
        return result

    except Exception as e:
        logger.error("Failed to fetch open problems", exc_info=True)
        return f"Error: Failed to retrieve problems from Neo4j. {e}"

async def search_context(query: str, top_k: int = 8, repo: Optional[str] = None) -> str:
    """
    Semantic + keyword + graph traversal search across all node types.

    Phase 7: delegates to ``queries.composite_search`` which runs the
    multiplicative composite reranker (memex/mcp_server/reranker.py) + RRF +
    access_count bump, then renders the result with the per-factor breakdown
    (ARCHITECTURE-v0.3.0 §8).

    We resolve the graph client here and pass it explicitly to
    ``composite_search(client=...)`` so existing tests that patch
    ``tools_read.get_graph_client`` keep working — the production wiring is
    a single shared implementation in queries.py.
    """
    if not query or not query.strip():
        return "query must be non-empty"

    top_k = min(max(1, top_k), 20)

    try:
        client = await get_graph_client()
        merged = await composite_search(
            query=query,
            num_results=top_k,
            repo=repo,
            client=client,
        )

        if not merged:
            return f"no relevant context found for query: '{query}'"

        result = format_search_results_with_breakdown(query=query, results=merged)
        try:
            from memex.graph.telemetry import record_tool_call
            await record_tool_call("search_context", len(result) // 4, repo)
        except Exception:
            pass
        return result

    except Exception:
        logger.error("Graphiti search failed", exc_info=True)
        return "search temporarily unavailable — try get_project_context() instead"

async def get_stale_context(threshold: float = 0.5, repo: Optional[str] = None) -> str:
    """
    Returns edges whose confidence field is below threshold.
    """
    threshold = min(max(0.0, threshold), 1.0)
    
    try:
        edges = await get_stale_edges(threshold=threshold, limit=51, repo=repo)
        result = format_stale_edges(
            edges=edges,
            threshold=threshold,
            total_found=len(edges)
        )
        try:
            from memex.graph.telemetry import record_tool_call
            await record_tool_call("get_stale_context", len(result) // 4, repo)
        except Exception:
            pass
        return result

    except Exception as e:
        logger.error("Failed to fetch stale context", exc_info=True)
        return f"Error: Failed to retrieve stale context from Neo4j. {e}"


# ---------------------------------------------------------------------------
# Phase 9 — new read tools (explain_change, predict_impact)
# Re-exported here so server.py and tests can import them from a single
# `memex.mcp_server.tools_read` surface alongside the v0.1 read tools.
# ---------------------------------------------------------------------------

from memex.mcp_server.tools_explain import explain_change  # noqa: E402, F401
from memex.mcp_server.tools_impact import predict_impact   # noqa: E402, F401

async def get_context_briefing(
    max_tokens: int = 2000,
    scope: Optional[str] = None,
    repo: Optional[str] = None,
) -> str:
    """Token-budgeted context briefing for session priming.

    Assembles context in priority order, stopping when the budget is hit:
    1. Cluster summaries (if available)
    2. High-confidence recent decisions (last 7 days, conf > 0.5)
    3. Open problems
    4. Stale edges needing attention

    Each section is added only if budget remains.
    """
    sections: list[str] = []
    used_tokens = 0

    header = "# memex Context Briefing\n\n"
    footer_template = "\n\n---\n*Budget: {used_tokens}/{max_tokens} tokens used*"

    used_tokens += len(header) // 4

    def _budget_remaining() -> int:
        footer_est = len(footer_template.format(used_tokens=max_tokens, max_tokens=max_tokens)) // 4
        return max_tokens - used_tokens - footer_est

    def _add_section(title: str, content: str) -> bool:
        nonlocal used_tokens
        section = f"## {title}\n{content}\n\n"
        section_tokens = len(section) // 4
        rem = _budget_remaining()
        if rem <= 0:
            return False
        if section_tokens > rem:
            available_chars = rem * 4
            if available_chars < 50:
                return False
            section = section[:available_chars] + "\n...(truncated)\n\n"
            section_tokens = len(section) // 4
        sections.append(section)
        used_tokens += section_tokens
        return True

    # 1. Cluster summaries (cheapest, highest density)
    try:
        cluster_ctx = await get_cluster_level_context(repo=repo)
        if scope:
            cluster_ctx = [
                c for c in cluster_ctx
                if any(m.startswith(scope) or scope in m for m in c.get("members", []))
            ]
        if cluster_ctx:
            lines = []
            for c in cluster_ctx:
                desc = c.get("summary") or c.get("description") or "no summary available"
                lines.append(f"- **{c.get('name', 'unknown')}** ({c.get('module_count', 0)} modules): {desc}")
            _add_section("Architecture Overview", "\n".join(lines))
    except Exception as e:
        logger.warning("Failed to include cluster summaries in briefing: %s", e)

    # 2. High-confidence recent decisions
    if _budget_remaining() > 50:
        try:
            # get decisions for the last 7 days.
            decisions = await get_recent_decisions_raw(since_days=7, module=scope, limit=10, repo=repo)
            from memex.graph.confidence import current_confidence
            scored = [(d, current_confidence(d)) for d in decisions]
            scored.sort(key=lambda x: x[1], reverse=True)
            high_conf = [d for d, c in scored if c > 0.5]
            # Signal D3 — surface confidence + validation health on this span /
            # metrics pipeline alongside the token-saving attributes.
            _emit_decision_confidence(decisions)
            _emit_validated_ratio(decisions)
            if high_conf:
                lines = []
                for d in high_conf:
                    conf = current_confidence(d)
                    lines.append(f"- **{d.get('text', 'unnamed')}** (conf: {conf:.2f})")
                _add_section("Key Decisions (last 7 days)", "\n".join(lines))
        except Exception as e:
            logger.warning("Failed to include recent decisions in briefing: %s", e)

    # 3. Open problems
    if _budget_remaining() > 50:
        try:
            problems = await get_open_problems_raw(module=scope, repo=repo)
            if problems:
                lines = [f"- **{p.get('text', 'unnamed')}** ({p.get('severity', 'medium')})" for p in problems[:5]]
                _add_section("Open Problems", "\n".join(lines))
        except Exception as e:
            logger.warning("Failed to include open problems in briefing: %s", e)

    # 4. Stale edges
    if _budget_remaining() > 50:
        try:
            # stale edges threshold 0.3
            stale = await get_stale_edges(threshold=0.3, limit=3, repo=repo)
            if stale:
                lines = [
                    f"- ⚠️ **{s.get('source')}** -[{s.get('edge_type')}]-> **{s.get('target')}** (conf: {s.get('confidence'):.2f})"
                    for s in stale
                ]
                _add_section("Needs Attention", "\n".join(lines))
        except Exception as e:
            logger.warning("Failed to include stale edges in briefing: %s", e)

    briefing = header + "".join(sections) + footer_template.format(used_tokens=used_tokens, max_tokens=max_tokens)

    # Telemetry
    try:
        from memex.graph.telemetry import record_tool_call
        await record_tool_call("get_context_briefing", used_tokens, repo)
    except Exception:
        pass

    return briefing
