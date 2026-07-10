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
- `require_role()` is an intentional permissive stub today (T-05-05,
  accepted/documented risk) — Phase 02's RBAC has since landed in this
  codebase, but as a SEPARATE bearer-token mechanism
  (`Principal`/`resolve_principal()`). It was never intended to wire into
  this session layer's `require_role()`; that remains a future integration
  decision, not part of this plan's scope.
"""

import logging
import os
import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from memex.watcher.registry import validate_key

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
    """Extension-point factory for a future role-gated dependency.

    TODO(Phase 02 / NET-09): today this is an intentionally permissive
    stub — any authenticated session (any `require_session` principal)
    satisfies `require_role(...)` regardless of `min_role`, with no actual
    role resolution performed. This lets call sites elsewhere adopt
    `Depends(require_role("viewer"))` now (instead of hardcoding
    `Depends(require_session)`) so that when real role checks are wired in
    later, only this one function needs to change — no call-site changes.
    `test_require_role_is_permissive_stub` in tests/test_team_dashboard.py
    is the explicit tripwire test that must be intentionally
    updated/broken when real roles land.
    """

    async def _dependency(principal: str = Depends(require_session)) -> str:
        return principal

    return _dependency


def create_auth_router() -> APIRouter:
    """Builds the `/login` + `/logout` router, reusing the existing
    `mx_...` bearer-key registry (`validate_key`) as the identity source —
    no new credential type is introduced.
    """
    router = APIRouter()

    @router.post("/login")
    async def login(request: Request, key: str = Form(...)):
        if not validate_key(key):
            raise HTTPException(status_code=401, detail="Invalid key")
        # Never store the full key in session state — signed != encrypted
        # (05-RESEARCH.md Pitfall 4, T-05-04).
        request.session["principal"] = key[:11]
        return RedirectResponse("/index.html", status_code=303)

    @router.post("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login.html", status_code=303)

    return router
