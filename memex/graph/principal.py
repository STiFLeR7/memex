"""Principal node bootstrap writes (Phase 02 / NET-08).

`write_principal_node()` is the answer to 02-RESEARCH.md Open Question #1:
how does the very first admin `Principal` node get created, given
`Principal.write_policy = "locked"` requires `role in ("admin", "system")`
to write it?

This module writes `:Entity{type:'Principal'}` nodes via direct Cypher
(`client.driver.execute_query(...)`), following the existing `:Entity` +
`type`-discriminator convention used by every other structured node type
(see `memex/graph/writer.py`) — never through `client.add_episode()`, which
would route structured identity metadata through Graphiti's LLM-driven
episode/entity extraction pipeline (wrong tool, unnecessary cost/latency).

`write_principal_node()` is called ONLY from the CLI's trusted `keys add`
handler (`memex/cli.py`) — never from `memex/mcp_server/tools_write.py`'s
agent-facing ACL-gated write path. It structurally bypasses
`check_write_policy()` exactly like the watcher's direct writes in
`writer.py` do, because the CLI operator IS the trusted local process
bootstrapping the system (see 02-RESEARCH.md Architecture Pattern 2 and
threat model T-02-05).
"""

from datetime import datetime
from typing import Optional

PRINCIPAL_MERGE_QUERY = """
MERGE (p:Entity {principal_id: $principal_id})
ON CREATE SET p.type = 'Principal',
              p.created_at = $now,
              p.write_policy = 'locked'
SET p.display_name = $display_name,
    p.role = $role,
    p.active = $active
RETURN elementId(p) as id
"""


async def write_principal_node(
    client,
    principal_id: str,
    display_name: Optional[str],
    role: str,
    active: bool = True,
) -> None:
    """Bootstrap-writes (MERGEs) a `:Entity{type:'Principal'}` node.

    Idempotent — re-running for the same `principal_id` updates
    `display_name`/`role`/`active` in place without duplicating the node
    (mirrors the existing MERGE-then-SET convention in `writer.py`).
    """
    now = datetime.now().isoformat()
    await client.driver.execute_query(
        PRINCIPAL_MERGE_QUERY,
        params={
            "principal_id": principal_id,
            "display_name": display_name,
            "role": role,
            "active": active,
            "now": now,
        },
    )
