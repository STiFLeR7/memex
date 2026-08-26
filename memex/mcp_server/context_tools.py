"""Thin MCP projections over the protocol-neutral context selector."""

from __future__ import annotations

import logging
from typing import Optional

from memex.context.packet import PacketBudget, validate_packet_metadata
from memex.context.selection import select_context

logger = logging.getLogger(__name__)
DEFAULT_MAX_ITEMS = 8
DEFAULT_MAX_CHARS = 12_000


async def get_engineering_context(
    query: str,
    top_k: int = DEFAULT_MAX_ITEMS,
    repo: Optional[str] = None,
    project: Optional[str] = None,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    allow_historical: bool = False,
) -> str:
    """Return the same bounded packet projection used by the provider path."""

    if not query or not query.strip():
        return "query must be non-empty"
    if not repo and not project:
        return "repository or project scope is required"
    try:
        limit = min(max(int(top_k), 1), DEFAULT_MAX_ITEMS)
    except (TypeError, ValueError):
        limit = DEFAULT_MAX_ITEMS

    try:
        packet = await select_context(
            query,
            repo=repo,
            project=project,
            task_id=task_id,
            session_id=session_id,
            harness="mcp",
            budget=PacketBudget(max_items=limit, max_chars=DEFAULT_MAX_CHARS),
            allow_historical=allow_historical,
        )
        if validate_packet_metadata(packet):
            return "engineering context temporarily unavailable — invalid context metadata"
        return packet.render_text()
    except Exception:
        logger.debug("MCP engineering context projection failed", exc_info=True)
        return "engineering context temporarily unavailable — try search_context() instead"
