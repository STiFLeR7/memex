"""Browser session authentication (NET-20, 05-01-PLAN.md).

Adds a signed-cookie session layer on top of the existing `mx_...`
bearer-key registry (`memex.watcher.registry`), for browser clients (the
Phase 05 team dashboard) that can't reasonably paste a bearer token into an
`Authorization` header on every request. This is orthogonal to the
Phase 02 `Principal`/`resolve_principal()`/`Depends(require_principal)`
bearer-token auth used by `/graph`, `/stats`, `/report`, and `/mcp` in
`memex/mcp_server/http.py` — that mechanism is untouched by this module.

Security notes (05-RESEARCH.md Pattern 1/2, Pitfall 4; threat_model T-05-*):
- The session cookie is signed (via `itsdangerous`, through Starlette's
  `SessionMiddleware`) but NOT encrypted — its payload is base64-readable by
  anyone holding the cookie, just not forgeable without the secret key.
  Therefore the raw `mx_...` bearer key is never placed in session state,
  only a truncated, non-sensitive prefix (`key[:11]`).
- `require_role()` resolves the real role via Phase 02's `resolve_principal()`
  at login time and caches it in the session — a key with no registered
  Principal (or an unrecognized role) fails safe to "viewer".
"""

import logging
import os
import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from memex.watcher.registry import resolve_principal, validate_key

_ROLE_RANK = {"viewer": 0, "contributor": 1, "admin": 2}

logger = logging.getLogger(__name__)


def get_session_secret() -> str:
    """Resolves the `SessionMiddleware` signing secret.

    Reads `MEMEX_SESSION_SECRET` from the environment (the same
    env-var-only pattern already used for `NEO4J_PASSWORD`/`GEMINI_API_KEY`
    — never hardcode a static secret). If unset, falls back to a
    per-process random value (`secrets.token_hex(32)`) so the server still
    starts in a zero-config local dev setup — this is the one acceptable
    zero-config default: an ephemeral, per-process random value, not a
    shared constant. Sessions will not survive a server restart without the
    env var set.
    """
    secret = os.environ.get("MEMEX_SESSION_SECRET")
    if secret:
        return secret
    logger.warning(
        "MEMEX_SESSION_SECRET not set — falling back to an ephemeral, "
        "per-process random session secret. Existing browser sessions will "
        "be invalidated on every server restart. Set MEMEX_SESSION_SECRET "
        "in your .env for persistent sessions."
    )
    return secrets.token_hex(32)


def require_session(request: Request) -> str:
    """FastAPI dependency gating a route on an authenticated browser
    session. Returns the session's `principal` (a truncated key prefix, see
    `create_auth_router`'s `/login` handler) or raises 401 if absent.
    """
    principal = request.session.get("principal")
    if not principal:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return principal


def require_role(min_role: str = "viewer"):
    """FastAPI dependency factory gating a route on session + Phase 02 role.

    Role is resolved once at `/login` (via `resolve_principal()`) and cached
    in the session, so this stays a cheap in-memory rank comparison — no
    registry lookup per request. Unknown ranks fail safe to "viewer".
    """

    async def _dependency(request: Request, principal: str = Depends(require_session)) -> str:
        role = request.session.get("role", "viewer")
        if _ROLE_RANK.get(role, 0) < _ROLE_RANK.get(min_role, 0):
            raise HTTPException(status_code=403, detail=f"Requires role '{min_role}' or higher")
        return principal

    return _dependency


def create_auth_router() -> APIRouter:
    """Builds the `/login` + `/logout` router. Branches on
    `Config.auth.dashboard_provider` (v0.8.0 Pillar B) — `"session"` (the
    default) reuses the exact v0.7.0 `mx_...` bearer-key-as-credential flow
    unchanged; `"oidc"` returns 501 with a clear message rather than
    silently falling back to session auth or crashing.
    """
    from memex.config import get_config

    router = APIRouter()

    @router.post("/login")
    async def login(request: Request, key: str = Form(...)):
        provider = get_config().auth.dashboard_provider
        if provider == "oidc":
            raise HTTPException(
                status_code=501,
                detail="OIDC provider not yet implemented — coming in v0.9.0",
            )
        if not validate_key(key):
            raise HTTPException(status_code=401, detail="Invalid key")
        # Never store the full key in session state — signed != encrypted
        # (05-RESEARCH.md Pitfall 4, T-05-04).
        request.session["principal"] = key[:11]
        principal_obj = await resolve_principal(key)
        request.session["role"] = principal_obj.role if principal_obj else "viewer"
        return RedirectResponse("/index.html", status_code=303)

    @router.post("/logout")
    async def logout(request: Request):
        provider = get_config().auth.dashboard_provider
        if provider == "oidc":
            raise HTTPException(
                status_code=501,
                detail="OIDC provider not yet implemented — coming in v0.9.0",
            )
        request.session.clear()
        return RedirectResponse("/login.html", status_code=303)

    return router
