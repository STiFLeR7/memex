"""Ambient per-request Principal identity for the /mcp HTTP transport.

Lives in its own small module (not `http.py` or `server.py`) specifically so
both files can import it without a circular-import risk: `http.py` sets it
per-request (in `mcp_asgi_app`, after resolving the caller's bearer token),
`server.py`'s `handle_call_tool` (wired in Plan 02-03) reads it.

This only works because Plan 02-02 Task 1 migrated `/mcp`'s transport wiring
to `StreamableHTTPSessionManager(stateless=True)`: the per-request MCP
dispatch task is spawned via `task_group.start(...)` called directly from
the same coroutine that sets `principal_ctx`, so `contextvars.Context` —
copied at task-creation time — carries the value across correctly (see
02-RESEARCH.md Pitfall #3). Setting this ContextVar against the old,
single-persistent-transport wiring would silently never propagate.
"""

from contextvars import ContextVar
from typing import Optional

from memex.graph.schema import Principal

principal_ctx: ContextVar[Optional[Principal]] = ContextVar("principal_ctx", default=None)
