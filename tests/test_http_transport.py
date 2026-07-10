import os
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch
from mcp.server import Server
from memex.graph.schema import Principal
from memex.mcp_server.http import create_app
from memex.mcp_server.principal_ctx import principal_ctx

@pytest.fixture
def mock_server():
    return MagicMock(spec=Server)

@pytest.fixture
def client(mock_server):
    app = create_app(mock_server, "/fake/repo")
    # Enter/exit as a context manager so TestClient drives the app's
    # `lifespan=` (Task 1 migration) — without this, session_manager.run()
    # never executes and its task group stays uninitialized.
    with TestClient(app) as c:
        yield c

@patch("memex.mcp_server.http.get_graph_client")
def test_health_check_does_not_leak_repo(mock_get_client, client):
    """Audit B5 — /health is unauthenticated; it must not echo the absolute
    repo path."""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    from unittest.mock import AsyncMock
    mock_client.driver.execute_query = AsyncMock()

    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "repo" not in response.json()


# --- Streamable HTTP Tests (Default) ---

@patch("memex.mcp_server.http.resolve_principal")
def test_mcp_auth_strips_only_bearer_prefix(mock_resolve, client):
    """Audit B6 — only the leading 'Bearer ' scheme is stripped; a token that
    contains the substring must survive intact. Task 2: the streamable-http
    branch now resolves a Principal instead of a boolean validate_key()."""
    mock_resolve.return_value = None
    client.post("/mcp", headers={"Authorization": "Bearer Bearer x"})
    mock_resolve.assert_called_once_with("Bearer x")

@patch("memex.mcp_server.http.resolve_principal")
def test_mcp_auth_missing(mock_resolve, client):
    mock_resolve.return_value = None
    response = client.post("/mcp")
    assert response.status_code == 401
    assert response.json() == {"detail": "Missing or invalid Authorization header"}

@patch("memex.mcp_server.http.resolve_principal")
def test_mcp_auth_invalid(mock_resolve, client):
    mock_resolve.return_value = None
    response = client.post("/mcp", headers={"Authorization": "Bearer invalid"})
    assert response.status_code == 401
    mock_resolve.assert_called_once_with("invalid")

@patch("memex.mcp_server.http.resolve_principal")
@patch("mcp.server.streamable_http_manager.StreamableHTTPSessionManager.handle_request")
def test_mcp_streamable_http_success(mock_handle_request, mock_resolve, mock_server):
    """Task 1: /mcp is now backed by StreamableHTTPSessionManager — the
    transport is created fresh per-request inside the manager, so we mock
    StreamableHTTPSessionManager.handle_request directly rather than the old
    module-level singleton StreamableHTTPServerTransport."""
    mock_resolve.return_value = Principal(principal_id="valid-user", role="admin")

    async def mock_handle_request_impl(scope, receive, send):
        from fastapi.responses import Response
        res = Response(status_code=200, content=b"streamable http success")
        await res(scope, receive, send)

    mock_handle_request.side_effect = mock_handle_request_impl

    app = create_app(mock_server, "/fake/repo")
    with TestClient(app) as client:
        response = client.post("/mcp", headers={"Authorization": "Bearer valid"}, json={"test": "data"})
    assert response.status_code == 200
    assert response.text == "streamable http success"
    mock_resolve.assert_called_with("valid")


@patch("memex.mcp_server.http.resolve_principal")
def test_mcp_lifespan_starts_session_manager_task_group(mock_resolve, mock_server):
    """02-RESEARCH.md Assumption A1, verified directly: FastAPI's `lifespan=`
    must enter `session_manager.run()`'s async context manager exactly once
    per app lifetime, driven by TestClient's context-manager protocol. If the
    lifespan wiring were broken (e.g. `on_event`/`lifespan=` silently not
    composing), `session_manager._task_group` would stay `None` and any real
    call to `handle_request` would raise
    `RuntimeError: Task group is not initialized.`"""
    mock_resolve.return_value = Principal(principal_id="local", role="admin")
    app = create_app(mock_server, "/fake/repo")

    # Before entering the lifespan, the task group must not exist yet.
    assert app.state.session_manager._task_group is None

    with TestClient(app):
        assert app.state.session_manager._task_group is not None


# --- Task 2: Ambient principal_ctx across the per-request task boundary ---

@patch("memex.mcp_server.http.resolve_principal")
def test_missing_token_never_reaches_principal_ctx_set(mock_resolve, mock_server):
    """When resolve_principal returns None, the existing 401 path fires and
    principal_ctx.set is never reached for that request — verified by
    substituting the module-level `principal_ctx` name http.py resolves at
    call time with a MagicMock (ContextVar.set is a read-only C slot and
    cannot be patched via patch.object directly)."""
    mock_resolve.return_value = None
    app = create_app(mock_server, "/fake/repo")

    fake_ctx = MagicMock()
    with patch("memex.mcp_server.http.principal_ctx", fake_ctx):
        with TestClient(app) as client:
            response = client.post("/mcp", headers={"Authorization": "Bearer bad"})
        assert response.status_code == 401
        fake_ctx.set.assert_not_called()


def test_concurrent_requests_resolve_distinct_principals_in_tool_dispatch():
    """The single most important test in this phase (02-RESEARCH.md
    Pitfall #3 acceptance test). Registers a stub tool handler on a REAL
    `mcp.server.Server` instance that reads `principal_ctx.get(None)` and
    echoes `principal.principal_id` back as the tool result. Drives two
    concurrent requests through the ASGI app directly (httpx.AsyncClient,
    since TestClient is sync and we need true concurrency), each with a
    distinct bearer token mapping to a distinct principal via a
    monkeypatched `resolve_principal`.

    This test is written so that reverting Task 1's transport migration
    (reintroducing the single-persistent-transport + one
    asyncio.create_task(run_transport()) pattern) makes it fail: under that
    old wiring, `handle_call_tool` runs inside one persistent background
    task created once at app startup, long before either request's
    Authorization header exists — `principal_ctx.get(None)` would return
    `None` (or whichever value was ambient at that one task's creation
    time) for every request, never the per-request token's own principal.
    """
    import asyncio
    import anyio
    import httpx
    from mcp.server import Server
    from mcp.server.models import InitializationOptions
    from mcp.types import TextContent, Tool
    from memex.mcp_server.http import create_app

    real_server = Server("memex-test")

    @real_server.call_tool()
    async def handle_call_tool(name: str, arguments: dict):
        principal = principal_ctx.get(None)
        pid = principal.principal_id if principal is not None else "NONE"
        return [TextContent(type="text", text=pid)]

    @real_server.list_tools()
    async def handle_list_tools():
        return [
            Tool(
                name="echo_principal",
                description="Echoes the ambient principal_ctx principal_id",
                inputSchema={"type": "object", "properties": {}},
            )
        ]

    principals = {
        "token-alice": Principal(principal_id="alice", role="admin"),
        "token-bob": Principal(principal_id="bob", role="contributor"),
    }

    async def fake_resolve_principal(token):
        return principals.get(token)

    app = create_app(real_server, "/fake/repo")

    async def call_tool_via_mcp(async_client, token: str, request_id: int):
        """Performs a full MCP streamable-http round trip: initialize, then
        call the echo_principal tool, returning the resolved principal_id
        text the server echoed back for THIS request's token."""
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

        init_body = {
            "jsonrpc": "2.0",
            "id": request_id * 10,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
            },
        }
        init_resp = await async_client.post("/mcp", json=init_body, headers=headers)
        assert init_resp.status_code == 200
        session_id = init_resp.headers.get("mcp-session-id")

        call_headers = dict(headers)
        if session_id:
            call_headers["mcp-session-id"] = session_id

        call_body = {
            "jsonrpc": "2.0",
            "id": request_id * 10 + 1,
            "method": "tools/call",
            "params": {"name": "echo_principal", "arguments": {}},
        }
        call_resp = await async_client.post("/mcp", json=call_body, headers=call_headers)
        assert call_resp.status_code == 200
        return _extract_tool_text(call_resp)

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=True) as async_client:
            async with anyio.create_task_group() as tg:
                results = {}

                async def worker(token, request_id):
                    with patch("memex.mcp_server.http.resolve_principal", side_effect=fake_resolve_principal):
                        results[token] = await call_tool_via_mcp(async_client, token, request_id)

                # Both requests use their own independent httpx call, driven
                # concurrently — this is what exercises the per-request task
                # boundary the migration is responsible for getting right.
                tg.start_soon(worker, "token-alice", 1)
                tg.start_soon(worker, "token-bob", 2)
            return results

    # Manually drive the lifespan (TestClient's sync context manager can't be
    # used with an async httpx.AsyncClient in the same event loop).
    async def run_with_lifespan():
        session_manager = app.state.session_manager
        async with session_manager.run():
            return await run()

    results = asyncio.run(run_with_lifespan())

    assert results["token-alice"] == "alice"
    assert results["token-bob"] == "bob"
    assert results["token-alice"] != results["token-bob"]


def _extract_tool_text(response: "httpx.Response") -> str:
    """Streamable HTTP responses may be returned as `application/json` or as
    a single-event `text/event-stream` body depending on SDK negotiation —
    handle both."""
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        body = response.text
        for line in body.splitlines():
            if line.startswith("data:"):
                import json as _json
                payload = _json.loads(line[len("data:"):].strip())
                return payload["result"]["content"][0]["text"]
        raise AssertionError(f"No data: line found in SSE body: {body!r}")
    payload = response.json()
    return payload["result"]["content"][0]["text"]

    # After the lifespan exits, the task group is torn down again.
    assert app.state.session_manager._task_group is None


# --- SSE Fallback Tests (MEMEX_MCP_TRANSPORT=sse) ---

@patch("memex.mcp_server.http.validate_key")
@patch("memex.mcp_server.http.SseServerTransport")
@patch.dict(os.environ, {"MEMEX_MCP_TRANSPORT": "sse"})
def test_mcp_sse_success(mock_sse_class, mock_validate, mock_server):
    mock_validate.return_value = True
    mock_sse = MagicMock()
    mock_sse_class.return_value = mock_sse
    
    app = create_app(mock_server, "/fake/repo")
    client = TestClient(app)
    
    try:
        response = client.get("/mcp/sse", headers={"Authorization": "Bearer valid"}, timeout=0.1)
    except Exception:
        pass
    
    mock_validate.assert_called_with("valid")

@patch("memex.mcp_server.http.validate_key")
@patch("memex.mcp_server.http.SseServerTransport")
@patch.dict(os.environ, {"MEMEX_MCP_TRANSPORT": "sse"})
def test_mcp_messages_post(mock_sse_class, mock_validate, mock_server):
    mock_validate.return_value = True
    mock_sse = MagicMock()
    mock_sse_class.return_value = mock_sse
    
    async def mock_handle_post(scope, receive, send):
        from fastapi.responses import Response
        res = Response(status_code=204)
        await res(scope, receive, send)

    mock_sse.handle_post_message = MagicMock(side_effect=mock_handle_post)
    
    app = create_app(mock_server, "/fake/repo")
    client = TestClient(app)
    
    response = client.post("/mcp/messages", headers={"Authorization": "Bearer valid"}, json={"test": "data"})
    assert response.status_code == 204
    mock_validate.assert_called_with("valid")


# --- Graph, Notify, and Utils Tests ---

@patch("memex.mcp_server.http.get_graph_client")
def test_get_graph(mock_get_client, client):
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    
    from unittest.mock import AsyncMock
    mock_execute = AsyncMock()
    mock_client.driver.execute_query = mock_execute
    
    mock_node_record = MagicMock()
    mock_node_record.data.return_value = {
        "id": "node-1",
        "name": "foo.py",
        "raw_type": "Module",
        "summary": "a py module",
        "created_at": "2026-05-23T12:00:00",
        "status": "",
        "scope": "",
        "source_commit": ""
    }
    
    mock_edge_record = MagicMock()
    mock_edge_record.data.return_value = {
        "source": "node-1",
        "target": "node-2",
        "type": "MOTIVATES",
        "created_at": "2026-05-23T12:05:00"
    }
    
    mock_nodes_res = MagicMock()
    mock_nodes_res.records = [mock_node_record]
    
    mock_edges_res = MagicMock()
    mock_edges_res.records = [mock_edge_record]
    
    mock_execute.side_effect = [mock_nodes_res, mock_edges_res]
    
    response = client.get("/graph")
    assert response.status_code == 200
    data = response.json()
    assert "nodes" in data
    assert "edges" in data
    assert len(data["nodes"]) == 1
    assert data["nodes"][0]["name"] == "foo.py"
    assert data["nodes"][0]["type"] == "Module"
    assert len(data["edges"]) == 1
    assert data["edges"][0]["type"] == "MOTIVATES"

    # `GET /graph` (no query param) must pass project=None so the Cypher
    # WHERE falls through to the repo_root-scoped branch unchanged.
    nodes_call_params = mock_execute.call_args_list[0].kwargs["params"]
    edges_call_params = mock_execute.call_args_list[1].kwargs["params"]
    assert nodes_call_params["project"] is None
    assert edges_call_params["project"] is None


@patch("memex.mcp_server.http.get_graph_client")
def test_get_graph_with_project_query_param(mock_get_client, client):
    """NET-03: `GET /graph?project=<id>` scopes the Cypher by project_id
    instead of repo_path."""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    from unittest.mock import AsyncMock
    mock_execute = AsyncMock()
    mock_client.driver.execute_query = mock_execute

    mock_node_record = MagicMock()
    mock_node_record.data.return_value = {
        "id": "node-1",
        "name": "foo.py",
        "raw_type": "Module",
        "summary": "a py module",
        "created_at": "2026-05-23T12:00:00",
        "status": "",
        "scope": "",
        "source_commit": ""
    }
    mock_nodes_res = MagicMock()
    mock_nodes_res.records = [mock_node_record]

    mock_edges_res = MagicMock()
    mock_edges_res.records = []

    mock_execute.side_effect = [mock_nodes_res, mock_edges_res]

    response = client.get("/graph?project=github.com/acme/widgets")
    assert response.status_code == 200
    data = response.json()
    assert len(data["nodes"]) == 1

    nodes_call_args = mock_execute.call_args_list[0]
    edges_call_args = mock_execute.call_args_list[1]
    assert nodes_call_args.kwargs["params"]["project"] == "github.com/acme/widgets"
    assert edges_call_args.kwargs["params"]["project"] == "github.com/acme/widgets"
    assert "n.project_id = $project" in nodes_call_args[0][0]

@patch("memex.mcp_server.http.validate_key")
def test_stats_endpoint_accepts_project_query_param(mock_validate, client):
    """NET-03: `GET /stats?project=<id>` threads `project` into
    get_stats_data(); `GET /stats?repo=<x>` (no project) passes project=None
    so existing repo-only callers are unaffected."""
    mock_validate.return_value = True

    from unittest.mock import AsyncMock
    with patch("memex.graph.stats.get_stats_data", new_callable=AsyncMock) as mock_get_stats:
        mock_get_stats.return_value = {"today": {}, "lifetime": {}, "top_tools": [], "agents": [], "validation_health": {}}

        response = client.get(
            "/stats?project=github.com/acme/widgets",
            headers={"Authorization": "Bearer good-token"},
        )
        assert response.status_code == 200
        mock_get_stats.assert_called_once()
        args, kwargs = mock_get_stats.call_args
        assert kwargs.get("project") == "github.com/acme/widgets"

        mock_get_stats.reset_mock()

        response = client.get(
            "/stats?repo=/fake/repo",
            headers={"Authorization": "Bearer good-token"},
        )
        assert response.status_code == 200
        args, kwargs = mock_get_stats.call_args
        assert kwargs.get("project") is None


def test_notify_and_events(client):
    response = client.post("/notify")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    
    routes = [r.path for r in client.app.routes]
    assert "/events" in routes


@patch("urllib.request.urlopen")
@patch("memex.config.get_config")
def test_notify_local_server_resolves_port(mock_get_config, mock_urlopen):
    import tempfile
    import shutil
    import time
    from pathlib import Path
    import memex.watcher.handlers
    
    # Clean up any pre-existing timer
    with memex.watcher.handlers._notify_lock:
        if memex.watcher.handlers._notify_timer is not None:
            memex.watcher.handlers._notify_timer.cancel()
            memex.watcher.handlers._notify_timer = None
            
    temp_dir = tempfile.mkdtemp()
    try:
        mock_cfg = MagicMock()
        mock_cfg.repo_root = temp_dir
        mock_get_config.return_value = mock_cfg
        
        # Write mock port
        port_dir = Path(temp_dir) / ".memex"
        port_dir.mkdir(parents=True, exist_ok=True)
        port_file = port_dir / "port"
        port_file.write_text("8899")
        
        memex.watcher.handlers.notify_local_server()
        
        # Wait a brief moment for the thread to fire
        for _ in range(20):
            if mock_urlopen.called:
                break
            time.sleep(0.05)
            
        assert mock_urlopen.called
        # Check that the request URL used the correct resolved port (8899)
        called_req = mock_urlopen.call_args[0][0]
        assert called_req.full_url == "http://127.0.0.1:8899/notify"
    finally:
        with memex.watcher.handlers._notify_lock:
            if memex.watcher.handlers._notify_timer is not None:
                memex.watcher.handlers._notify_timer.cancel()
                memex.watcher.handlers._notify_timer = None
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass


@patch("urllib.request.urlopen")
@patch("memex.config.get_config")
def test_notify_local_server_debounces_bursts(mock_get_config, mock_urlopen):
    import tempfile
    import shutil
    import time
    from pathlib import Path
    import memex.watcher.handlers
    
    # Clean up any pre-existing timer
    with memex.watcher.handlers._notify_lock:
        if memex.watcher.handlers._notify_timer is not None:
            memex.watcher.handlers._notify_timer.cancel()
            memex.watcher.handlers._notify_timer = None

    temp_dir = tempfile.mkdtemp()
    try:
        mock_cfg = MagicMock()
        mock_cfg.repo_root = temp_dir
        mock_get_config.return_value = mock_cfg
        
        # Write mock port
        port_dir = Path(temp_dir) / ".memex"
        port_dir.mkdir(parents=True, exist_ok=True)
        port_file = port_dir / "port"
        port_file.write_text("7463")
        
        # Call multiple times in rapid succession
        memex.watcher.handlers.notify_local_server()
        memex.watcher.handlers.notify_local_server()
        memex.watcher.handlers.notify_local_server()
        
        # Verify it has not been called immediately
        time.sleep(0.1)
        assert not mock_urlopen.called, "Should debounce and not fire urlopen immediately"
        
        # Wait for the debounce timer to fire (0.5s from last call, so 0.6s total sleep is plenty)
        for _ in range(25):
            if mock_urlopen.called:
                break
            time.sleep(0.05)
            
        assert mock_urlopen.called
        assert mock_urlopen.call_count == 1, f"Expected exactly 1 call, got {mock_urlopen.call_count}"
        
    finally:
        with memex.watcher.handlers._notify_lock:
            if memex.watcher.handlers._notify_timer is not None:
                memex.watcher.handlers._notify_timer.cancel()
                memex.watcher.handlers._notify_timer = None
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass
