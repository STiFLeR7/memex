"""AuthProvider abstraction tests (v0.8.0 Pillar B).

Scoped to session/browser auth only — bearer (Depends(require_principal) in
memex/mcp_server/http.py, gating /mcp, /graph, /stats, /report) is
untouched by this abstraction and has no AuthProvider of its own, because
OIDC's redirect-based login flow only ever applies to a browser client.
"""

from unittest.mock import AsyncMock, patch

import pytest

from memex.auth.providers import AuthProvider, OIDCAuthProvider, SessionAuthProvider


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
