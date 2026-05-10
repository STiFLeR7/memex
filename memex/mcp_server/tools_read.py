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
    get_symbol_problems
)
from memex.mcp_server.formatter import (
    format_project_context,
    format_symbol_context,
    format_decisions,
    format_problems,
    format_search_results,
    format_stale_edges
)

logger = logging.getLogger(__name__)

async def get_project_context(scope: Optional[str] = None) -> str:
    """
    Returns a structured markdown briefing of the project.
    """
    try:
        config = get_config()
        
        counts = await get_node_counts()
        modules = await get_active_modules(since_days=30, scope=scope)
        decisions = await get_recent_decisions_raw(since_days=7, module=scope, limit=10)
        problems = await get_open_problems_raw(module=scope)
        stale_list = await get_stale_edges(threshold=0.3, limit=1)
        
        return format_project_context(
            repo_root=config.repo_root,
            counts=counts,
            modules=modules,
            decisions=decisions,
            problems=problems,
            stale_count=len(stale_list)
        )

    except Exception as e:
        logger.error("Failed to generate project context", exc_info=True)
        return f"Error: Failed to retrieve project context from Neo4j. {e}"

async def get_symbol_context(symbol_name: str, file: Optional[str] = None) -> str:
    """
    Returns everything the graph knows about a specific symbol.
    """
    try:
        symbol = await get_symbol_by_name(symbol_name, file)
        
        if not symbol:
            client = await get_graph_client()
            search_results = await client.search(symbol_name, num_results=1)
            suggestion = ""
            if search_results:
                best = search_results[0]
                suggestion = f"\n\nDid you mean '{getattr(best, 'name', 'unknown')}'?"
            return f"Symbol '{symbol_name}' not found.{suggestion}"

        callers = await get_symbol_callers(symbol_name)
        callees = await get_symbol_callees(symbol_name)
        decisions = await get_symbol_decisions(symbol_name)
        problems = await get_symbol_problems(symbol_name)

        return format_symbol_context(
            symbol=symbol,
            callers=callers,
            callees=callees,
            decisions=decisions,
            problems=problems
        )

    except Exception as e:
        logger.error("Failed to fetch symbol context", exc_info=True)
        return f"Error: Failed to retrieve symbol context for '{symbol_name}'. {e}"

async def get_recent_decisions(days: int = 30, module: Optional[str] = None) -> str:
    """
    Returns Decision nodes created within the last days days, newest first.
    """
    try:
        decisions = await get_recent_decisions_raw(since_days=days, module=module, limit=21)
        return format_decisions(
            decisions=decisions,
            days=days,
            module=module,
            total_count=len(decisions)
        )

    except Exception as e:
        logger.error("Failed to fetch recent decisions", exc_info=True)
        return f"Error: Failed to retrieve decisions from Neo4j. {e}"

async def get_open_problems(module: Optional[str] = None) -> str:
    """
    Returns Problem nodes with no resolved_by edge.
    """
    try:
        problems = await get_open_problems_raw(module=module)
        if not problems:
            return "no open problems recorded"
            
        # Sort in Python as well for mock consistency
        def sev_to_score(s):
            return {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(s.lower(), 0)
        
        problems.sort(key=lambda x: sev_to_score(x.get('severity', 'medium')), reverse=True)
            
        return format_problems(problems=problems, module=module)

    except Exception as e:
        logger.error("Failed to fetch open problems", exc_info=True)
        return f"Error: Failed to retrieve problems from Neo4j. {e}"

async def search_context(query: str, top_k: int = 8) -> str:
    """
    Semantic + keyword + graph traversal search across all node types.
    """
    if not query or not query.strip():
        return "query must be non-empty"

    top_k = min(max(1, top_k), 20)
    client = await get_graph_client()
    
    try:
        results = await client.search(query, num_results=top_k)
        
        if not results:
            return f"no relevant context found for query: '{query}'"
            
        return format_search_results(query=query, results=results)

    except Exception as e:
        logger.error("Graphiti search failed", exc_info=True)
        return "search temporarily unavailable — try get_project_context() instead"

async def get_stale_context(threshold: float = 0.5) -> str:
    """
    Returns edges whose confidence field is below threshold.
    """
    threshold = min(max(0.0, threshold), 1.0)
    
    try:
        edges = await get_stale_edges(threshold=threshold, limit=51)
        return format_stale_edges(
            edges=edges,
            threshold=threshold,
            total_found=len(edges)
        )

    except Exception as e:
        logger.error("Failed to fetch stale context", exc_info=True)
        return f"Error: Failed to retrieve stale context from Neo4j. {e}"
