import os
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
from mcp.server import Server
from memex.mcp_server.http import create_app

@pytest.fixture
def mock_server():
    return MagicMock(spec=Server)

@pytest.fixture
def client(mock_server):
    app = create_app(mock_server, "/fake/repo")
    return TestClient(app)

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

@patch("memex.mcp_server.http.validate_key")
def test_mcp_auth_strips_only_bearer_prefix(mock_validate, client):
    """Audit B6 — only the leading 'Bearer ' scheme is stripped; a token that
    contains the substring must survive intact."""
    mock_validate.return_value = False
    client.post("/mcp", headers={"Authorization": "Bearer Bearer x"})
    mock_validate.assert_called_once_with("Bearer x")

@patch("memex.mcp_server.http.validate_key")
def test_mcp_auth_missing(mock_validate, client):
    response = client.post("/mcp")
    assert response.status_code == 401
    assert response.json() == {"detail": "Missing or invalid Authorization header"}

@patch("memex.mcp_server.http.validate_key")
def test_mcp_auth_invalid(mock_validate, client):
    mock_validate.return_value = False
    response = client.post("/mcp", headers={"Authorization": "Bearer invalid"})
    assert response.status_code == 401
    mock_validate.assert_called_once_with("invalid")

@patch("memex.mcp_server.http.validate_key")
@patch("mcp.server.streamable_http.StreamableHTTPServerTransport")
def test_mcp_streamable_http_success(mock_transport_class, mock_validate, mock_server):
    mock_validate.return_value = True
    mock_transport = MagicMock()
    mock_transport_class.return_value = mock_transport

    async def mock_handle_request(scope, receive, send):
        from fastapi.responses import Response
        res = Response(status_code=200, content=b"streamable http success")
        await res(scope, receive, send)

    mock_transport.handle_request = MagicMock(side_effect=mock_handle_request)

    app = create_app(mock_server, "/fake/repo")
    client = TestClient(app)

    response = client.post("/mcp", headers={"Authorization": "Bearer valid"}, json={"test": "data"})
    assert response.status_code == 200
    assert response.text == "streamable http success"
    mock_validate.assert_called_with("valid")


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
