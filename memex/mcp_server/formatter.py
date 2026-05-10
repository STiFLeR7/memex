import logging
from datetime import datetime, UTC
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# Token approximation constants
TOKEN_LIMIT_BRIEFING = 2000
TOKEN_LIMIT_STANDARD = 1500
CHARS_PER_TOKEN = 4

def _truncate_if_needed(lines: List[str], limit_tokens: int) -> str:
    """
    Assembles lines into a string and truncates if it exceeds token limit.
    Approximates tokens as len(text) / 4.
    """
    limit_chars = limit_tokens * CHARS_PER_TOKEN
    full_text = "\n".join(lines)
    
    if len(full_text) <= limit_chars:
        return full_text
    
    # Simple truncation: keep as many lines as fit
    truncated_lines = []
    current_len = 0
    # Reserve room for the truncation message
    msg = "\n[truncated — use scope= or module= parameter to narrow]"
    max_body_chars = limit_chars - len(msg)
    
    for line in lines:
        if current_len + len(line) + 1 > max_body_chars:
            # If even the header doesn't fit, this logic is tough. 
            # But for our use case, we usually truncate lists.
            break
        truncated_lines.append(line)
        current_len += len(line) + 1
        
    if not truncated_lines:
        # Fallback if first line was too long
        return lines[0][:max_body_chars] + msg

    truncated_lines.append(msg)
    return "\n".join(truncated_lines)

def format_project_context(repo_root: str, counts: Dict[str, int], modules: List[Dict], decisions: List[Dict], problems: List[Dict], stale_count: int) -> str:
    """Assembles the primary project briefing."""
    now = datetime.now(UTC)
    lines = [
        "# memex project context",
        f"generated: {now.isoformat()}",
        f"repo: {repo_root}",
        f"neo4j nodes: {counts['modules']} modules, {counts['symbols']} symbols, {counts['decisions']} decisions, {counts['problems']} open problems"
    ]
    
    lines.append("\n## active modules (last 30 days)")
    if modules:
        for r in modules:
            desc = r['description'][:100] + "..." if r['description'] and len(r['description']) > 100 else (r['description'] or 'no description')
            lines.append(f"- {r['path']} — {desc} — {r['symbols']} symbols")
    else:
        lines.append("- no active modules found.")

    lines.append("\n## recent decisions (last 7 days)")
    if decisions:
        for r in decisions:
            lines.append(f"- {r['text']} — {r['scope']} — {r['sha'][:8] if r['sha'] else 'n/a'}")
    else:
        lines.append("- no recent decisions.")

    lines.append("\n## open problems")
    if problems:
        for r in problems:
            mod_str = f" ({r['module']})" if r['module'] else ""
            lines.append(f"- {r['text']} — {r['severity']}{mod_str}")
    else:
        lines.append("- no open problems.")

    lines.append("\n## stale warnings")
    if stale_count > 0:
        lines.append(f"- {stale_count} edges below confidence 0.3 — run `get_stale_context()` for details")
    else:
        lines.append("- no stale edges detected.")
        
    return _truncate_if_needed(lines, TOKEN_LIMIT_BRIEFING)

def format_symbol_context(symbol: Dict[str, Any], callers: List[Dict], callees: List[Dict], decisions: List[str], problems: List[str]) -> str:
    """Assembles detailed symbol information."""
    lines = [
        f"# symbol: {symbol['name']}",
        f"kind: {symbol['kind']}",
        f"file: {symbol['file']}:{symbol['line']}",
        f"signature: {symbol['signature'] or 'n/a'}",
        f"confidence: {symbol['confidence']:.2f}",
        f"stale: {'yes' if symbol['stale'] else 'no'}"
    ]

    lines.append("\n## called by")
    if callers:
        for c in callers:
            lines.append(f"- {c['name']} ({c['file']})")
    else:
        lines.append("- no known callers.")

    lines.append("\n## calls")
    if callees:
        for c in callees:
            lines.append(f"- {c['name']} ({c['file']})")
    else:
        lines.append("- no known callees.")

    lines.append("\n## linked decisions")
    if decisions:
        for d in decisions:
            lines.append(f"- {d}")
    else:
        lines.append("- no linked decisions.")

    lines.append("\n## linked problems")
    if problems:
        for p in problems:
            lines.append(f"- {p}")
    else:
        lines.append("- no linked problems.")
        
    return _truncate_if_needed(lines, TOKEN_LIMIT_STANDARD)

def format_decisions(decisions: List[Dict], days: int, module: Optional[str], total_count: int) -> str:
    """Assembles chronological decision list."""
    if not decisions:
        return f"no decisions recorded in the last {days} days"

    lines = [f"# recent decisions (last {days} days)"]
    if module:
        lines.append(f"filter: module starts with `{module}`")
        
    for r in decisions[:20]:
        dt = r['date']
        date_str = dt.isoformat()[:10] if hasattr(dt, 'isoformat') else str(dt)[:10]
        lines.append(f"\n[{date_str}] {r['text']}")
        lines.append(f"  scope: {r['scope']}")
        lines.append(f"  rationale: {r['rationale']}")
        lines.append(f"  commit: {r['sha'][:8]}")
        if r['module_paths']:
            lines.append(f"  affects: {', '.join(r['module_paths'])}")
        else:
            lines.append("  affects: unknown")

    if total_count > 20:
        lines.append(f"\n*showing 20 of {total_count} — narrow with module= parameter*")
        
    return _truncate_if_needed(lines, TOKEN_LIMIT_STANDARD)

def format_problems(problems: List[Dict], module: Optional[str]) -> str:
    """Assembles unresolved problem list."""
    if not problems:
        return "no open problems recorded"

    lines = ["# open problems"]
    if module:
        lines.append(f"filter: module starts with `{module}`")
        
    for r in problems:
        dt = r['date']
        date_str = dt.isoformat()[:10] if hasattr(dt, 'isoformat') else str(dt)[:10]
        lines.append(f"\n[{r['severity'].upper()}] {r['text']}")
        lines.append(f"  module: {r['module'] or 'unknown'}")
        lines.append(f"  discovered: {date_str}")
        lines.append(f"  surfaced by: {r['agent']}")
        lines.append(f"  id: {r['id']}")
        
    return _truncate_if_needed(lines, TOKEN_LIMIT_STANDARD)

def format_search_results(query: str, results: List[Any]) -> str:
    """Assembles search result list."""
    if not results:
        return f"no relevant context found for query: '{query}'"

    lines = [f"# search results for: '{query}'"]
    for res in results:
        node_type = getattr(res, "type", "unknown")
        name = getattr(res, "name", "unknown")
        if not name or name == "unknown":
            name = getattr(res, "fact", "no excerpt")
            
        file_path = getattr(res, "file", None)
        confidence = getattr(res, "confidence", 1.0)
        stale = getattr(res, "stale", False)
        relevance = getattr(res, "score", 0.0)
        
        lines.append(f"\n[{node_type}] {name}")
        if file_path:
            lines.append(f"  file: {file_path}")
        lines.append(f"  confidence: {confidence:.2f}")
        lines.append(f"  stale: {'yes' if stale else 'no'}")
        lines.append(f"  relevance: {relevance:.4f}")
        
    return _truncate_if_needed(lines, TOKEN_LIMIT_STANDARD)

def format_stale_edges(edges: List[Dict], threshold: float, total_found: int) -> str:
    """Assembles stale context report."""
    if not edges:
        return f"no stale edges below threshold {threshold:.2f}"

    lines = [f"# stale context report (threshold: {threshold:.2f})"]
    lines.append("The following knowledge has decayed and may be outdated:")
    
    for r in edges[:50]:
        dt = r['date']
        date_str = dt.isoformat()[:10] if hasattr(dt, 'isoformat') else str(dt)[:10]
        lines.append(f"\n[conf: {r['confidence']:.2f}] {r['source']} —[{r['edge_type']}]→ {r['target']}")
        lines.append(f"  last valid: {date_str}")
        lines.append(f"  source commit: {r['sha'][:8]}")
        lines.append(f"  id: {r['id']}")

    if total_found > 50:
        lines.append(f"\n*noting: total {total_found} stale edges found, showing first 50*")
        
    return _truncate_if_needed(lines, TOKEN_LIMIT_STANDARD)
