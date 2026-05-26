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


MAX_COUPLED_MODULES = 25

# Each dimension is its own focused query returning DISTINCT coupled module
# names. They were previously fused into one query that re-`UNWIND`'d the source
# set per stage even though the import/decision stages didn't correlate to it —
# wasteful and hard to verify. Split + aggregated in Python (audit Q3). Each
# kind contributes at most 1 to a module's score (DISTINCT), preserving the
# original scoring semantics.

# Symbols in `file` that call / are called by symbols in OTHER files.
_CALL_COUPLING_QUERY = """
MATCH (src:Entity)
WHERE (src.file = $file OR src.name = $file)
  AND ($repo IS NULL OR src.repo_path = $repo)
MATCH (src)-[r:CALLS|RELATES_TO]-(coupled:Entity)
WHERE r.expired_at IS NULL
  AND (coupled.type = 'Symbol' OR coupled.type IS NULL)
  AND coalesce(coupled.file, '') <> ''
  AND coalesce(coupled.file, '') <> $file
RETURN DISTINCT coalesce(coupled.file, coupled.name) AS module
"""

# Module-level import/dependency/export edges from `file`. Not repo-scoped —
# matches the prior behaviour (module names are globally unique by path).
_IMPORT_COUPLING_QUERY = """
MATCH (m_src:Entity)-[r:IMPORTS|DEPENDS_ON|EXPORTS]-(m_other:Entity)
WHERE r.expired_at IS NULL
  AND (coalesce(m_src.name, '') = $file OR coalesce(m_src.path, '') = $file)
  AND coalesce(m_other.name, m_other.path, '') <> ''
  AND coalesce(m_other.name, m_other.path, '') <> $file
RETURN DISTINCT coalesce(m_other.name, m_other.path) AS module
"""

# Modules whose Decisions also mention the file's symbols.
_DECISION_COUPLING_QUERY = """
MATCH (src:Entity)
WHERE (src.file = $file OR src.name = $file)
  AND ($repo IS NULL OR src.repo_path = $repo)
MATCH (src)-[r3:MOTIVATES|RELATES_TO|MENTIONS]-(d:Entity)
WHERE r3.expired_at IS NULL
  AND (d.type = 'Decision' OR d.name CONTAINS 'Decision')
MATCH (d)-[r4:MOTIVATES|RELATES_TO|MENTIONS]-(other_mod:Entity)
WHERE r4.expired_at IS NULL
  AND (coalesce(other_mod.type, '') = 'Module'
       OR other_mod.name ENDS WITH '.py'
       OR other_mod.name ENDS WITH '.js'
       OR other_mod.name ENDS WITH '.ts')
  AND coalesce(other_mod.name, '') <> $file
RETURN DISTINCT other_mod.name AS module
"""


async def _coupled_modules_for(client, query: str, file_path: str, repo: Optional[str]) -> List[str]:
    """Run one dimension query and return its DISTINCT coupled module names."""
    res = await client.driver.execute_query(query, params={"file": file_path, "repo": repo})
    return [r["module"] for r in res.records if r.get("module")]


async def _query_coupled_modules(file_path: str, repo: Optional[str]) -> List[Dict[str, Any]]:
    """Find modules coupled to `file_path` via calls, imports, or decision
    links, and rank them by how many of those dimensions couple.

    Returns rows with: `module`, `call_count`, `import_count`,
    `decision_count`, `total_score` — ranked by `total_score` desc, then
    module name asc, capped at ``MAX_COUPLED_MODULES``.
    """
    client = await get_graph_client()
    try:
        calls = await _coupled_modules_for(client, _CALL_COUPLING_QUERY, file_path, repo)
        imports = await _coupled_modules_for(client, _IMPORT_COUPLING_QUERY, file_path, repo)
        decisions = await _coupled_modules_for(client, _DECISION_COUPLING_QUERY, file_path, repo)
    except Exception:
        logger.error("predict_impact graph query failed", exc_info=True)
        return []

    rows: Dict[str, Dict[str, Any]] = {}

    def _bump(modules: List[str], key: str) -> None:
        for module in modules:
            row = rows.setdefault(
                module,
                {"module": module, "call_count": 0, "import_count": 0, "decision_count": 0},
            )
            row[key] += 1  # DISTINCT per dimension → at most 1 (preserves prior scoring)

    _bump(calls, "call_count")
    _bump(imports, "import_count")
    _bump(decisions, "decision_count")

    for row in rows.values():
        row["total_score"] = row["call_count"] + row["import_count"] + row["decision_count"]

    ranked = sorted(rows.values(), key=lambda r: (-r["total_score"], r["module"]))
    return ranked[:MAX_COUPLED_MODULES]


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
    else:
        # Canonicalize an agent-supplied repo so it matches the stored
        # write-side join key regardless of spelling (B1).
        try:
            from memex.config import canonical_repo_path
            repo = canonical_repo_path(repo)
        except Exception:
            pass

    try:
        rows = await _query_coupled_modules(file_path, repo=repo)
        result = _format_impact_report(file_path, rows)
    except Exception as e:
        logger.error("predict_impact failed", exc_info=True)
        result = f"Error: predict_impact graph query failed. {e}"

    try:
        from memex.graph.telemetry import record_tool_call
        await record_tool_call("predict_impact", len(result) // 4, repo)
    except Exception:
        pass
    return result
