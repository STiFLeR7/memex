"""Phase 9 — `predict_impact` MCP tool (tool 12).

Pure graph traversal. Returns modules likely affected by changes to
`file_path` based on historical coupling (calls + exports + imports +
decision links). NO LLM call — by contract this is CPU-bound only.
"""

import logging
from typing import Optional, List, Dict, Any

from memex.config import get_config
from memex.graph.client import get_graph_client

logger = logging.getLogger(__name__)

TOKEN_BUDGET = 2000
CHAR_BUDGET = TOKEN_BUDGET * 4


def _truncate(text: str, char_budget: int = CHAR_BUDGET) -> str:
    if len(text) <= char_budget:
        return text
    return text[: char_budget - 60] + "\n[truncated — too many coupled modules]"


async def _query_coupled_modules(file_path: str, repo: Optional[str]) -> List[Dict[str, Any]]:
    """For every symbol in `file_path`, find symbols that call it / are
    called by it (n-hop=1 for now), then aggregate the owning files /
    modules and count edges per coupled module.

    Returns rows with: `module`, `call_count`, `import_count`,
    `decision_count`, `total_score`.
    """
    client = await get_graph_client()
    # We treat the file_path itself as both a Module name (Graphiti's
    # convention: module nodes are stored with their relative path as `name`)
    # and a `file` property on Symbol nodes.
    query = """
    // Find the source symbols living in the file
    MATCH (src:Entity)
    WHERE (src.file = $file OR src.name = $file)
      AND ($repo IS NULL OR src.repo_path = $repo)
    WITH collect(src) AS sources

    // Coupled symbols — symbols that call or are called by anything in sources
    UNWIND sources AS s
    OPTIONAL MATCH (s)-[r1:CALLS|RELATES_TO]-(coupled:Entity)
    WHERE r1.expired_at IS NULL
      AND (coupled.type = 'Symbol' OR coupled.type IS NULL)
      AND coalesce(coupled.file, '') <> ''
      AND coalesce(coupled.file, '') <> $file
    WITH sources, collect(DISTINCT {
        file: coalesce(coupled.file, coupled.name),
        kind: 'call'
    }) AS call_edges

    // Module-level import edges
    UNWIND sources AS s2
    OPTIONAL MATCH (m_src:Entity)-[r2:IMPORTS|DEPENDS_ON|EXPORTS]-(m_other:Entity)
    WHERE r2.expired_at IS NULL
      AND (coalesce(m_src.name, '') = $file OR coalesce(m_src.path, '') = $file)
      AND coalesce(m_other.name, m_other.path, '') <> ''
      AND coalesce(m_other.name, m_other.path, '') <> $file
    WITH sources, call_edges, collect(DISTINCT {
        file: coalesce(m_other.name, m_other.path),
        kind: 'import'
    }) AS import_edges

    // Decision linkage — modules whose Decisions mention the file's symbols
    UNWIND sources AS s3
    OPTIONAL MATCH (s3)-[r3:MOTIVATES|RELATES_TO|MENTIONS]-(d:Entity)
    WHERE r3.expired_at IS NULL
      AND (d.type = 'Decision' OR d.name CONTAINS 'Decision')
    OPTIONAL MATCH (d)-[r4:MOTIVATES|RELATES_TO|MENTIONS]-(other_mod:Entity)
    WHERE r4.expired_at IS NULL
      AND (coalesce(other_mod.type, '') = 'Module'
           OR other_mod.name ENDS WITH '.py'
           OR other_mod.name ENDS WITH '.js'
           OR other_mod.name ENDS WITH '.ts')
      AND coalesce(other_mod.name, '') <> $file
    WITH call_edges, import_edges, collect(DISTINCT {
        file: other_mod.name,
        kind: 'decision'
    }) AS decision_edges

    WITH call_edges + import_edges + decision_edges AS all_edges
    UNWIND all_edges AS edge
    WITH edge.file AS module, edge.kind AS kind
    WHERE module IS NOT NULL
    RETURN module,
           sum(CASE kind WHEN 'call' THEN 1 ELSE 0 END) AS call_count,
           sum(CASE kind WHEN 'import' THEN 1 ELSE 0 END) AS import_count,
           sum(CASE kind WHEN 'decision' THEN 1 ELSE 0 END) AS decision_count,
           count(*) AS total_score
    ORDER BY total_score DESC, module ASC
    LIMIT 25
    """
    try:
        res = await client.driver.execute_query(query, params={"file": file_path, "repo": repo})
        return [r.data() for r in res.records]
    except Exception:
        logger.error("predict_impact graph query failed", exc_info=True)
        return []


def _format_impact_report(file_path: str, rows: List[Dict[str, Any]]) -> str:
    lines: List[str] = [f"# predict_impact: `{file_path}`", ""]
    if not rows:
        lines.append("no historically-coupled modules found in the graph.")
        lines.append("")
        lines.append("either the file is new / unindexed, or the watcher hasn't built call edges yet.")
        return _truncate("\n".join(lines))

    lines.append(f"_top {len(rows)} likely-affected modules, ranked by coupling strength_")
    lines.append("")
    for i, row in enumerate(rows, 1):
        module = row.get("module") or "unknown"
        calls = int(row.get("call_count") or 0)
        imports = int(row.get("import_count") or 0)
        decisions = int(row.get("decision_count") or 0)
        total = int(row.get("total_score") or 0)
        # Build the basis explanation
        basis_parts: List[str] = []
        if calls:
            basis_parts.append(f"{calls} calls")
        if imports:
            basis_parts.append(f"{imports} imports")
        if decisions:
            basis_parts.append(f"{decisions} decision links")
        basis = ", ".join(basis_parts) if basis_parts else "indirect coupling"
        lines.append(f"{i}. **{module}** — score {total} — based on {basis}")

    return _truncate("\n".join(lines))


async def predict_impact(file_path: str, repo: Optional[str] = None) -> str:
    """Returns modules likely affected by changes to `file_path` based on
    historical coupling in the graph. PURE GRAPH TRAVERSAL — no LLM call.

    Returns a ranked Markdown list under ~2000 tokens. Never raises into
    the MCP protocol.
    """
    if not file_path or not file_path.strip():
        return "Error: file_path is required"

    file_path = file_path.strip()

    # repo defaults to config.repo_root when not provided, so multi-repo
    # deployments don't accidentally aggregate coupling across repos.
    if repo is None:
        try:
            config = get_config()
            repo = config.repo_root
        except Exception:
            repo = None

    try:
        rows = await _query_coupled_modules(file_path, repo=repo)
    except Exception as e:
        logger.error("predict_impact failed", exc_info=True)
        return f"Error: predict_impact graph query failed. {e}"

    return _format_impact_report(file_path, rows)
