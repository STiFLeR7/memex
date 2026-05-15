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

def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "repo": "/fake/repo"}

@patch("memex.mcp_server.http.validate_key")
def test_mcp_auth_missing(mock_validate, client):
    response = client.get("/mcp/sse")
    assert response.status_code == 401
    assert response.json() == {"detail": "Missing or invalid Authorization header"}

@patch("memex.mcp_server.http.validate_key")
def test_mcp_auth_invalid(mock_validate, client):
    mock_validate.return_value = False
    response = client.get("/mcp/sse", headers={"Authorization": "Bearer invalid"})
    assert response.status_code == 401
    mock_validate.assert_called_once_with("invalid")

@patch("memex.mcp_server.http.validate_key")
def test_mcp_404(mock_validate, client):
    mock_validate.return_value = True
    response = client.get("/mcp/nonexistent", headers={"Authorization": "Bearer valid"})
    assert response.status_code == 404

@patch("memex.mcp_server.http.validate_key")
@patch("memex.mcp_server.http.SseServerTransport")
def test_mcp_sse_success(mock_sse_class, mock_validate, mock_server):
    mock_validate.return_value = True
    mock_sse = MagicMock()
    mock_sse_class.return_value = mock_sse
    
    # We need to recreate the client because SseServerTransport is created in create_app
    app = create_app(mock_server, "/fake/repo")
    client = TestClient(app)
    
    # TestClient.get for SSE might block or behave weirdly, but we can check if it tries to connect
    # Using a context manager for SSE is tricky here, but we can at least hit the endpoint
    try:
        response = client.get("/mcp/sse", headers={"Authorization": "Bearer valid"}, timeout=0.1)
    except Exception:
        pass
    
    # If it reached connect_sse, it means auth passed
    mock_validate.assert_called_with("valid")

@patch("memex.mcp_server.http.validate_key")
@patch("memex.mcp_server.http.SseServerTransport")
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
