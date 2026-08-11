"""AuthProvider Protocol + concrete implementations (v0.8.0 Pillar B).

`AuthProvider` governs how memex/mcp_server/auth_session.py's `/login` and
`/logout` routes resolve a principal and handle the login/logout HTTP flow.
Two implementations ship in v0.8.0:

- SessionAuthProvider — wraps v0.7.0's existing resolve_principal()/
  validate_key() logic unchanged. This is the default and, until v0.9.0, the
  only fully-working option.
- OIDCAuthProvider — a skeleton. Every method raises NotImplementedError.
  Ships now so the interface contract is visible to a future contributor and
  auth.dashboard_provider: oidc is a documented, selectable (if
  not-yet-functional) config value rather than an undocumented dead end.
"""

from __future__ import annotations

from typing import Optional, Protocol

from fastapi import Request
from fastapi.responses import Response

from memex.graph.schema import Principal
from memex.watcher.registry import resolve_principal


class AuthProvider(Protocol):
    """Resolves a dashboard session to a Principal, and handles the
    provider-specific login/logout HTTP flow."""

    async def resolve_principal_for_key(self, key: str) -> Optional[Principal]:
        ...

    async def login_redirect(self, request: Request) -> Response:
        """Handle a login attempt. Returns the redirect response on success,
        raises HTTPException on failure (mirrors v0.7.0's /login contract)."""
        ...

    async def logout(self, request: Request) -> Response:
        ...


class SessionAuthProvider:
    """Wraps v0.7.0's existing session-cookie logic
    (memex/mcp_server/auth_session.py's pre-v0.8.0 /login and /logout
    bodies) unchanged. This is `auth.dashboard_provider: session`, the
    default."""

    async def resolve_principal_for_key(self, key: str) -> Optional[Principal]:
        return await resolve_principal(key)

    async def login_redirect(self, request: Request) -> Response:
        raise NotImplementedError(
            "SessionAuthProvider.login_redirect is not called directly — "
            "memex/mcp_server/auth_session.py's /login route handles the "
            "session-cookie flow inline, since it needs the Form(...) body "
            "and request.session mutation that don't fit this Protocol's "
            "generic signature cleanly. See Task 4."
        )

    async def logout(self, request: Request) -> Response:
        raise NotImplementedError(
            "See login_redirect's docstring — /logout stays inline in "
            "auth_session.py for the same reason."
        )


class OIDCAuthProvider:
    """Skeleton for v0.9.0. Every method raises NotImplementedError with a
    clear message naming the target version, so `auth.dashboard_provider:
    oidc` is a documented, selectable-but-inert config value rather than an
    undocumented dead end."""

    async def resolve_principal_for_key(self, key: str) -> Optional[Principal]:
        raise NotImplementedError("OIDC provider not yet implemented — coming in v0.9.0")

    async def login_redirect(self, request: Request) -> Response:
        raise NotImplementedError("OIDC provider not yet implemented — coming in v0.9.0")

    async def logout(self, request: Request) -> Response:
        raise NotImplementedError("OIDC provider not yet implemented — coming in v0.9.0")
