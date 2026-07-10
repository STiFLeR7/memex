import contextlib
import logging
import asyncio
import json
import os

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles
import uvicorn

from memex.watcher.registry import validate_key, resolve_principal
from memex.graph.client import get_graph_client
from memex.graph.schema import Principal
from memex.config import canonical_repo_path
from memex.mcp_server.principal_ctx import principal_ctx
from memex.mcp_server.auth_session import create_auth_router, get_session_secret
from memex.mcp_server.graph_query import fetch_graph_payload
from memex.mcp_server.team import create_team_router

logger = logging.getLogger(__name__)

_subscribers: list[asyncio.Queue] = []

async def broadcast_event(event_type: str, data: dict):
    """
    Broadcasts an event to all connected SSE clients.
    """
    for queue in list(_subscribers):
        try:
            await queue.put({"event": event_type, "data": data})
        except Exception as e:
            logger.warning(f"Failed to push to subscriber queue: {e}")

async def verify_auth_token(token: str) -> bool:
    """
    Validates the Bearer token against the registry.

    Kept for the SSE branch only (deprecated, legacy boolean auth model —
    see threat_model T-02-09). The streamable-http branch and the ordinary
    FastAPI routes (/graph, /stats) use `resolve_principal_from_headers` /
    `require_principal` instead (NET-10, 02-RESEARCH.md Pitfall #4).
    """
    if not token:
        return False
    return validate_key(token)


def _extract_bearer_token(headers) -> "str | None":
    """Extracts the bearer token from an `Authorization` header, stripping
    only the leading `Bearer ` scheme — a token that itself contains the
    substring `Bearer ` must survive intact (Audit B6). ``headers`` accepts
    anything with a `.get(...)` method (a Starlette `Headers` object or a
    plain dict), so this is usable both from FastAPI `Request.headers` and
    from `mcp_asgi_app`'s raw ASGI-derived `Request.headers`.
    """
    auth_header = headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header.removeprefix("Bearer ")
    return None


async def resolve_principal_from_headers(headers) -> "Principal | None":
    """Shared token-extraction + resolution helper (02-RESEARCH.md
    Pitfall #4). Used by the `require_principal` FastAPI dependency
    (/graph, /stats — ordinary routes with full `Depends()` support) AND by
    `mcp_asgi_app`'s streamable-http branch directly (a raw ASGI callable
    mounted via `app.mount()` — Starlette's `Mount` bypasses FastAPI's DI
    system entirely, so `Depends()` cannot be used there). This is the
    single place the bearer-token extraction + resolution logic lives, so
    neither caller hand-rolls its own copy.
    """
    token = _extract_bearer_token(headers)
    return await resolve_principal(token)


async def require_principal(request: Request) -> Principal:
    """FastAPI dependency for ordinary routes (/graph, /stats). Raises the
    same 401 detail message the pre-existing manual header-parsing blocks
    used, for behavioral continuity."""
    principal = await resolve_principal_from_headers(request.headers)
    if principal is None:
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    return principal


def create_app(server: Server, repo_root: str):
    """
    Creates the FastAPI application for memex.
    """
    transport_mode = os.environ.get("MEMEX_MCP_TRANSPORT", "streamable-http")

    # session_manager is only set for the (default) streamable-http branch.
    # SSE is deprecated (02-RESEARCH.md Open Question #3) and is explicitly
    # left on its own, structurally different code path (SseServerTransport)
    # — out of scope for this migration and for Task 3's new auth model
    # (see threat_model T-02-09).
    session_manager = None
    sse = None

    if transport_mode == "sse":
        import warnings
        warnings.warn(
            "SSE transport is deprecated and will be removed in v0.6.0. "
            "Migrate to Streamable HTTP by removing MEMEX_MCP_TRANSPORT env var.",
            DeprecationWarning,
            stacklevel=1,
        )
        sse = SseServerTransport("/mcp/messages")
    else:
        # Streamable HTTP transport (default). Migrated from a single,
        # startup-created `StreamableHTTPServerTransport` + one persistent
        # `asyncio.create_task(run_transport())` background task, to
        # `StreamableHTTPSessionManager(stateless=True)`, which spins up a
        # *fresh* transport + task per HTTP request, spawned via
        # `task_group.start(...)` called directly from the request's own
        # coroutine (`mcp_asgi_app`, below). This is a precondition (not
        # optional) for ambient per-request identity (`principal_ctx`,
        # Task 2) to propagate correctly into `handle_call_tool` — under the
        # old wiring, the one persistent dispatch task was created once, at
        # app startup, long before any request (and its Authorization
        # header) existed, so a ContextVar set per-request could never be
        # visible inside it (02-RESEARCH.md Pitfall #3). `stateless=True`
        # preserves today's `mcp_session_id=None` / no-session-tracking
        # behavior — this is a like-for-like transport swap, not a protocol
        # change for existing clients.
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        session_manager = StreamableHTTPSessionManager(app=server, stateless=True)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        # FastAPI's `lifespan=` context manager and the deprecated
        # `@app.on_event("startup")` decorator do NOT compose the way one
        # might assume from the deprecation warning alone: verified directly
        # against this repo's installed fastapi==0.136.1 (not assumed —
        # 02-RESEARCH.md Assumption A1) that once `lifespan=` is passed to
        # `FastAPI(...)`, any `@app.on_event("startup")` handler registered
        # afterward is silently NEVER invoked (only a DeprecationWarning
        # fires; the handler itself does not run). `lifespan=` is therefore
        # used exclusively for the whole app here — there is no other
        # `on_event` usage left in this file needing separate migration.
        if session_manager is not None:
            async with session_manager.run():
                yield
        else:
            yield

    app = FastAPI(
        title="memex MCP Server",
        description=f"Serving context for {repo_root}",
        version="0.2.0",
        lifespan=lifespan,
    )
    # Exposed on app.state for direct testability of the lifespan wiring
    # (Task 1's acceptance test asserts the session manager's task group is
    # initialized once the TestClient context-manager protocol has entered
    # the lifespan) and so future code (e.g. graceful shutdown hooks) has a
    # single place to reach the manager without a closure.
    app.state.session_manager = session_manager

    # NET-20 (05-01-PLAN.md): browser session auth, orthogonal to the
    # bearer-token Principal auth above (Phase 02) — this gates the future
    # team dashboard's /login-driven browser flow, not /graph, /stats,
    # /report, or /mcp, which stay on Depends(require_principal).
    app.add_middleware(
        SessionMiddleware,
        secret_key=get_session_secret(),
        session_cookie="memex_session",
        max_age=60 * 60 * 8,  # 8h — 05-RESEARCH.md Assumption A3
        same_site="lax",
        https_only=False,  # flip to True once deployed behind TLS (Phase 06)
    )
    app.include_router(create_auth_router())

    # NET-18 (05-02-PLAN.md): /team/* read endpoints (activity, confidence,
    # conflicts, graph), all gated by Depends(require_role("viewer")) —
    # added after the auth router since they depend on its session
    # middleware being registered first.
    app.include_router(create_team_router(repo_root))

    @app.get("/health")
    async def health_check():
        # /health is unauthenticated — don't leak the absolute repo path (B5).
        try:
            client = await get_graph_client()
            await client.driver.execute_query("RETURN 1")
            return {"status": "ok"}
        except Exception as e:
            logger.warning(f"Health check failed (Neo4j connection issue): {e}")
            return JSONResponse(
                status_code=503,
                content={"status": "error", "detail": "Neo4j connection failed"}
            )

    @app.get("/graph")
    async def get_graph(project: str = None, principal: Principal = Depends(require_principal)):
        # NET-10: /graph previously had zero authentication. Any valid
        # principal suffices — read-only endpoint, no role check needed.
        client = await get_graph_client()
        canonical_repo = canonical_repo_path(repo_root)

        try:
            return await fetch_graph_payload(client, canonical_repo, project)
        except Exception as e:
            logger.error(f"Failed to fetch graph data: {e}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={"detail": "Failed to fetch graph data"}
            )

    @app.get("/events")
    async def sse_endpoint(request: Request):
        async def event_generator():
            queue = asyncio.Queue()
            _subscribers.append(queue)
            try:
                # Yield initial connect ping
                yield "event: ping\ndata: {\"status\": \"connected\"}\n\n"
                
                while True:
                    try:
                        # Wait for a message with a 15.0s timeout
                        msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                        event_type = msg.get("event", "message")
                        data_str = json.dumps(msg.get("data", {}))
                        yield f"event: {event_type}\ndata: {data_str}\n\n"
                    except asyncio.TimeoutError:
                        # Keep-alive
                        yield "event: ping\ndata: {\"status\": \"keep-alive\"}\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                if queue in _subscribers:
                    _subscribers.remove(queue)
                    
        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @app.post("/notify")
    async def post_notify():
        await broadcast_event("graph_updated", {})
        return {"status": "ok"}

    @app.get("/stats")
    async def get_stats_endpoint(
        repo: str = None,
        days: int = 30,
        project: str = None,
        principal: Principal = Depends(require_principal),
    ):
        # Authentication now shared with /graph via require_principal — no
        # third copy of the header-parsing/token-extraction logic in this
        # file (02-RESEARCH.md Pitfall #4).

        # Get Stats from unified stats service
        try:
            from memex.graph.stats import get_stats_data
            path = repo or repo_root
            stats = await get_stats_data(path, project=project)
            return stats
        except Exception as e:
            logger.error(f"Failed to generate stats in /stats endpoint: {e}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={"detail": f"Failed to generate stats: {str(e)}"}
            )

    @app.get("/report")
    async def get_report_endpoint(
        repo: str = None,
        principal: Principal = Depends(require_principal),
    ):
        # Bearer-token gated via the same require_principal dependency as
        # /graph and /stats (Phase 02 has landed since this endpoint was
        # originally researched -- verify_auth_token() is legacy now, kept
        # only for the deprecated SSE branch below). Role-gating (e.g.
        # restricting this manager-facing endpoint to principal.role ==
        # "admin") is not implemented here -- it would be a scope increase
        # beyond this plan and beyond /graph's and /stats's current
        # behavior, which also don't role-gate today.
        try:
            from memex.graph.governance_report import find_latest_report

            path = repo or repo_root
            latest = find_latest_report(path)
            if latest is None:
                return JSONResponse(
                    status_code=404,
                    content={"detail": "No report generated yet"},
                )
            return JSONResponse(content=json.loads(latest.read_text()))
        except Exception as e:
            logger.error(f"Failed to fetch report in /report endpoint: {e}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={"detail": "Failed to fetch report"},
            )

    # Custom ASGI app for MCP to handle raw send/receive
    async def mcp_asgi_app(scope, receive, send):
        if scope["type"] != "http":
            return

        logger.info(f"MCP ASGI request: {scope['method']} {scope['path']}")
        request = Request(scope, receive)

        if transport_mode == "sse":
            # SSE stays on the legacy boolean `validate_key`/`verify_auth_token`
            # auth model — it is deliberately NOT extended to the new
            # role-aware `resolve_principal`/`principal_ctx` design. SSE is
            # already DeprecationWarning-marked and out of scope for both the
            # Task 1 transport migration and this task's auth model (see
            # threat_model T-02-09, 02-RESEARCH.md Open Question #3).
            auth_header = request.headers.get("Authorization")
            token = None
            if auth_header and auth_header.startswith("Bearer "):
                # Strip only the leading scheme — a token containing 'Bearer '
                # as a substring must survive intact (B6).
                token = auth_header.removeprefix("Bearer ")

            if not await verify_auth_token(token):
                response = JSONResponse(
                    status_code=401,
                    content={"detail": "Missing or invalid Authorization header"}
                )
                await response(scope, receive, send)
                return

            if scope["path"].endswith("/sse") and scope["method"] == "GET":
                try:
                    async with sse.connect_sse(scope, receive, send) as (read_stream, write_stream):
                        await server.run(
                            read_stream,
                            write_stream,
                            server.create_initialization_options()
                        )
                except Exception as e:
                    logger.error(f"SSE Error: {e}", exc_info=True)
            elif scope["path"].endswith("/messages") and scope["method"] == "POST":
                try:
                    await sse.handle_post_message(scope, receive, send)
                except Exception as e:
                    logger.error(f"POST Message Error: {e}", exc_info=True)
            else:
                response = JSONResponse(status_code=404, content={"detail": "Not Found"})
                await response(scope, receive, send)
            return

        # Streamable HTTP transport (default). Resolve the caller's Principal
        # and stash it in the ambient principal_ctx BEFORE handing off to the
        # session manager. This is the first point in the call chain where
        # task creation happens after principal_ctx.set() — and it only
        # propagates correctly because of the Task 1 migration:
        # StreamableHTTPSessionManager(stateless=True) spawns the per-request
        # dispatch task via task_group.start(...) called directly from this
        # same coroutine, so contextvars.Context (copied at task-creation
        # time) carries principal_ctx's value into it (02-RESEARCH.md
        # Architecture Pattern 3 / Pitfall #3).
        # `mcp_asgi_app` is a raw ASGI callable under `app.mount()` —
        # Starlette's `Mount` bypasses FastAPI's DI entirely, so it cannot
        # use `Depends(require_principal)` (02-RESEARCH.md Pitfall #4). It
        # calls the same `resolve_principal_from_headers` helper manually
        # instead, so the token-extraction logic still lives in exactly one
        # place in this file.
        principal = await resolve_principal_from_headers(request.headers)
        if principal is None:
            response = JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid Authorization header"}
            )
            await response(scope, receive, send)
            return

        reset_token = principal_ctx.set(principal)
        try:
            # StreamableHTTPSessionManager creates a fresh transport + task
            # for this request (see Task 1 comment on session_manager's
            # construction above).
            await session_manager.handle_request(scope, receive, send)
        finally:
            principal_ctx.reset(reset_token)

    app.mount("/mcp", mcp_asgi_app)

    # Bare "/mcp" (no trailing slash) redirect shim — a deviation from the
    # plan discovered while verifying Task 2 (Rule 1 - bug: fix caused by
    # this task's own change; tests/test_http_transport.py caught it).
    # Starlette's Mount("/mcp", ...) compiles its path_regex as
    # "^/mcp/(?P<path>.*)$", which does NOT match the bare path "/mcp" —
    # only "/mcp/" or deeper. Previously (no root-level Mount registered),
    # an unmatched "/mcp" fell through to Starlette Router.app()'s built-in
    # redirect_slashes fallback, which retries the match with a trailing
    # slash appended and 307-redirects to "/mcp/", transparently reaching
    # mcp_asgi_app. That fallback only runs when NO route produces a FULL
    # match on the first pass. Once StaticFiles("/") below is registered,
    # its path_regex "^/(?P<path>.*)$" fully matches ANY path — including
    # the bare "/mcp" — on the very first pass, permanently pre-empting the
    # redirect_slashes fallback and misrouting real MCP client requests
    # into the static file server (405 for POST/PUT/etc., 404 for GET,
    # since StaticFiles only serves GET/HEAD and has no file named "mcp").
    # This explicit route restores the original client-observable behavior
    # deterministically, without depending on redirect_slashes fallback
    # ordering.
    @app.api_route(
        "/mcp",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
        include_in_schema=False,
    )
    async def _mcp_root_trailing_slash_redirect():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/mcp/", status_code=307)

    # NET-19 (05-03-PLAN.md): static dashboard assets (login/index/
    # confidence/conflicts HTML + forked dashboard.js/css). Registered LAST,
    # after /mcp, the auth router, and the team router — StaticFiles is a
    # catch-all Mount("/") that matches every path by prefix; registering it
    # any earlier in Starlette's route-matching order would shadow every API
    # route that comes after it (05-RESEARCH.md, T-05-11). Packaged inside
    # memex/mcp_server/dashboard/ (not a top-level dashboard/ dir) because
    # pyproject.toml's [tool.hatch.build.targets.wheel] only packages
    # ["memex"] — a top-level directory would not ship in the built wheel.
    app.mount(
        "/",
        StaticFiles(directory=str(Path(__file__).parent / "dashboard"), html=True),
        name="dashboard",
    )

    return app

async def run_http_server(server: Server, repo_root: str, host: str = "127.0.0.1", port: int = 8000):
    """
    Runs the FastAPI app using uvicorn.
    """
    app = create_app(server, repo_root)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server_uvicorn = uvicorn.Server(config)

    from pathlib import Path
    from memex.config import canonical_repo_path

    repo_canon = canonical_repo_path(repo_root)
    port_dir = Path(repo_canon) / ".memex"
    port_file = port_dir / "port"

    try:
        port_dir.mkdir(parents=True, exist_ok=True)
        port_file.write_text(str(port))
        logger.info("Saved active server port %d to %s", port, port_file)
    except Exception as e:
        logger.warning("Failed to save active server port to file: %s", e)

    try:
        logger.info("Starting memex MCP HTTP server on %s:%s", host, port)
        if os.environ.get("MEMEX_MCP_TRANSPORT") == "sse":
            logger.info("MCP SSE endpoint: http://%s:%s/mcp/sse", host, port)
        else:
            logger.info("MCP Streamable HTTP endpoint: http://%s:%s/mcp", host, port)
        await server_uvicorn.serve()
    finally:
        try:
            if port_file.exists():
                port_file.unlink()
                logger.info("Cleaned up active server port file: %s", port_file)
        except Exception as e:
            logger.warning("Failed to clean up active server port file: %s", e)
