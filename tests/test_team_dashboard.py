"""Auth-gating and cookie-safety tests for the Phase 05 session auth layer
(NET-20, 05-01-PLAN.md).

Test 1/2 exercise `/login` on the real app built by `create_app()` (the
same app `/graph`/`/stats`/`/report`/`/mcp` live on) — `SessionMiddleware`
and the auth router are wired into that app's `create_app()` per Task 1.

Test 3/4 build a small standalone FastAPI app (per the plan's behavior
spec) to exercise `require_session`/`require_role` as reusable dependencies
independent of the rest of memex's HTTP surface.
"""

from unittest.mock import MagicMock, patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from mcp.server import Server
from starlette.middleware.sessions import SessionMiddleware

from memex.mcp_server.auth_session import create_auth_router, require_role, require_session
from memex.mcp_server.http import create_app


@patch("memex.mcp_server.auth_session.validate_key")
def test_login_sets_safe_session_cookie(mock_validate_key):
    """POST /login with a valid key returns a 303 redirect to /index.html
    and a Set-Cookie header that is HttpOnly + SameSite=Lax but never
    contains the raw bearer key (05-RESEARCH.md Pitfall 4, T-05-04)."""
    mock_validate_key.return_value = True
    mock_server = MagicMock(spec=Server)
    app = create_app(mock_server, "/fake/repo")

    with TestClient(app) as client:
        response = client.post(
            "/login",
            data={"key": "mx_faketoken123"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers.get("location") == "/index.html"

    set_cookie = response.headers.get("set-cookie")
    assert set_cookie is not None
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()
    assert "mx_faketoken123" not in set_cookie


@patch("memex.mcp_server.auth_session.validate_key")
def test_login_rejects_invalid_key(mock_validate_key):
    """POST /login with an invalid key returns 401 and sets no cookie."""
    mock_validate_key.return_value = False
    mock_server = MagicMock(spec=Server)
    app = create_app(mock_server, "/fake/repo")

    with TestClient(app) as client:
        response = client.post(
            "/login",
            data={"key": "mx_whatever"},
            follow_redirects=False,
        )

    assert response.status_code == 401
    assert response.headers.get("set-cookie") is None


def _build_standalone_app() -> FastAPI:
    """A minimal standalone app exercising require_session/require_role as
    reusable dependencies, independent of memex's main HTTP surface."""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")
    app.include_router(create_auth_router())

    @app.get("/_protected")
    async def protected(principal: str = Depends(require_session)):
        return {"principal": principal}

    @app.get("/_protected_role")
    async def protected_role(principal: str = Depends(require_role("admin"))):
        return {"principal": principal}

    return app


@patch("memex.mcp_server.auth_session.validate_key")
def test_require_session_gates_protected_route(mock_validate_key):
    """/_protected returns 401 with no session cookie; after logging in,
    replaying the session cookie returns 200 with the truncated principal.
    """
    app = _build_standalone_app()

    with TestClient(app) as client:
        response = client.get("/_protected")
        assert response.status_code == 401

        mock_validate_key.return_value = True
        posted_key = "mx_someuser12345"
        login_response = client.post(
            "/login",
            data={"key": posted_key},
            follow_redirects=False,
        )
        assert login_response.status_code == 303

        response = client.get("/_protected")
        assert response.status_code == 200
        assert response.json() == {"principal": posted_key[:11]}


@patch("memex.mcp_server.auth_session.validate_key")
def test_require_role_is_permissive_stub(mock_validate_key):
    """require_role() is an intentional permissive stub (T-05-05) until
    Phase 02 wires real role checks in — any authenticated session (no
    role/principal metadata beyond the truncated key prefix) passes.

    This is the explicit tripwire test Phase 02 must intentionally
    update/break when real roles land.
    """
    app = _build_standalone_app()

    with TestClient(app) as client:
        mock_validate_key.return_value = True
        login_response = client.post(
            "/login",
            data={"key": "mx_someuser12345"},
            follow_redirects=False,
        )
        assert login_response.status_code == 303

        response = client.get("/_protected_role")
        assert response.status_code == 200
