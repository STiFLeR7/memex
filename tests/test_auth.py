"""AuthProvider abstraction tests (v0.8.0 Pillar B).

Scoped to session/browser auth only — bearer (Depends(require_principal) in
memex/mcp_server/http.py, gating /mcp, /graph, /stats, /report) is
untouched by this abstraction and has no AuthProvider of its own, because
OIDC's redirect-based login flow only ever applies to a browser client.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from mcp.server import Server
from pydantic import ValidationError

from memex.auth.providers import OIDCAuthProvider, SessionAuthProvider
from memex.config import Config
from memex.mcp_server.http import create_app


@pytest.mark.asyncio
async def test_session_provider_resolves_active_session():
    """SessionAuthProvider.resolve_principal wraps the existing
    resolve_principal() lookup used by v0.7.0's /login — same registry, same
    Principal object, unchanged behavior."""
    provider = SessionAuthProvider()
    fake_principal = object()
    with patch(
        "memex.auth.providers.resolve_principal", new=AsyncMock(return_value=fake_principal)
    ):
        result = await provider.resolve_principal_for_key("mx_fake_key")
    assert result is fake_principal


@pytest.mark.asyncio
async def test_oidc_provider_raises_not_implemented_on_login_redirect():
    provider = OIDCAuthProvider()
    with pytest.raises(NotImplementedError):
        await provider.login_redirect(request=None)


def test_auth_provider_is_a_protocol_with_required_methods():
    """Structural check — both concrete providers satisfy the Protocol's
    shape (resolve_principal_for_key, login_redirect, logout)."""
    for cls in (SessionAuthProvider, OIDCAuthProvider):
        assert hasattr(cls, "resolve_principal_for_key")
        assert hasattr(cls, "login_redirect")
        assert hasattr(cls, "logout")


def test_default_config_has_session_dashboard_provider():
    """No auth: block in config.yaml == v0.7.0's only behavior (session),
    per NET-11-style backward-compat: absence of new config must not change
    existing deployments."""
    config = Config(
        neo4j_uri="bolt://x", neo4j_user="x", neo4j_password="x", gemini_api_key="x"
    )
    assert config.auth.dashboard_provider == "session"


def test_config_accepts_oidc_dashboard_provider():
    config = Config(
        neo4j_uri="bolt://x",
        neo4j_user="x",
        neo4j_password="x",
        gemini_api_key="x",
        auth={"dashboard_provider": "oidc"},
    )
    assert config.auth.dashboard_provider == "oidc"


def test_config_rejects_unknown_dashboard_provider():
    with pytest.raises(ValidationError):
        Config(
            neo4j_uri="bolt://x",
            neo4j_user="x",
            neo4j_password="x",
            gemini_api_key="x",
            auth={"dashboard_provider": "carrier-pigeon"},
        )


def test_login_with_oidc_provider_returns_501():
    """auth.dashboard_provider: oidc must not silently fall through to the
    session flow — it must fail loudly and clearly."""
    mock_server = MagicMock(spec=Server)
    with patch("memex.config.get_config") as mock_get_config:
        mock_get_config.return_value.auth.dashboard_provider = "oidc"
        app = create_app(mock_server, "/fake/repo")
        with TestClient(app) as client:
            response = client.post("/login", data={"key": "mx_anything"})
    assert response.status_code == 501


def test_login_with_session_provider_unchanged_from_v070():
    """auth.dashboard_provider: session (the default) behaves identically —
    an invalid key still 401s."""
    mock_server = MagicMock(spec=Server)
    app = create_app(mock_server, "/fake/repo")
    with TestClient(app) as client:
        response = client.post("/login", data={"key": "not-a-real-key"})
    assert response.status_code == 401


def test_bearer_routes_unaffected_by_oidc_dashboard_provider():
    """/graph (Depends(require_principal), memex/mcp_server/http.py) must
    never look at auth.dashboard_provider — it's a wholly separate bearer-
    token auth mechanism from the dashboard's session-cookie login. Setting
    dashboard_provider to oidc must not change /graph's behavior at all: a
    request with no Authorization header still 401s via require_principal,
    never 501."""
    mock_server = MagicMock(spec=Server)
    with patch("memex.config.get_config") as mock_get_config:
        mock_get_config.return_value.auth.dashboard_provider = "oidc"
        app = create_app(mock_server, "/fake/repo")
        with TestClient(app) as client:
            response = client.get("/graph")
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing or invalid Authorization header"
