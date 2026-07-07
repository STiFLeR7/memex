"""One-off idempotent backfill of `project_id` onto existing v0.6.x nodes.

Implements NET-02's migration path: every write site in
`memex/mcp_server/tools_write.py` now additively sets `n.project_id`
alongside `n.repo_path` for freshly-created nodes (Plan 00-02, Task 1), but
that leaves every node written before this phase carrying `repo_path` only.
`memex migrate project-id --repo <path>` fixes that up with a single
Cypher `MATCH...SET` pass, run through the existing
`client.driver.execute_query()` pattern (managed transaction) — per
00-RESEARCH.md, neither `apoc.periodic.iterate` nor Neo4j 5's native
`CALL {...} IN TRANSACTIONS` batching is needed at memex's dev-tool graph
scale (thousands, not millions, of nodes), and the latter is explicitly
incompatible with `execute_query()`'s managed-transaction wrapper anyway.

The migration is idempotent by construction — the Cypher's own
`WHERE ... AND n.project_id IS NULL` guard means running it twice never
re-touches an already-migrated node, and running it against zero matching
nodes is a no-op (prints "migrated 0 node(s)").
"""

import os
import sys
from typing import Optional

from memex.config import canonical_repo_path, resolve_project_id
from memex.graph.client import get_graph_client


async def run_migrate_project_id_command(repo_root: str) -> None:
    """Backfill `project_id` onto every existing `repo_path`-only node for
    ``repo_root``.

    Resolves `project_id` the same way the write path does
    (`memex.config.resolve_project_id`). If no `project_id` can be
    resolved (no git remote, no `.memex/project_id` file, `memex init
    --project-id` never run), prints an actionable error and exits
    non-zero without touching the graph.
    """
    repo_path = canonical_repo_path(os.path.abspath(repo_root))
    project_id: Optional[str] = resolve_project_id(repo_path)

    if not project_id:
        print(
            "memex migrate project-id: could not resolve a project_id for "
            f"{repo_path} — run `memex init --project-id <id>` first",
            file=sys.stderr,
        )
        sys.exit(1)

    client = await get_graph_client()

    query = (
        "MATCH (n:Entity) "
        "WHERE n.repo_path = $repo AND n.project_id IS NULL "
        "SET n.project_id = $project "
        "RETURN count(n) as updated"
    )
    res = await client.driver.execute_query(
        query, params={"repo": repo_path, "project": project_id}
    )

    updated = 0
    if res.records:
        updated = res.records[0]["updated"] or 0

    print(f"migrated {updated} node(s) in {repo_path} to project_id={project_id}")
