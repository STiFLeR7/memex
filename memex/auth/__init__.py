"""Session/browser auth-provider abstraction (v0.8.0 Pillar B).

Scoped deliberately to the dashboard's session/cookie login flow only.
Bearer-token auth (memex/mcp_server/http.py's Depends(require_principal),
gating /mcp, /graph, /stats, /report) is a separate, permanent mechanism
outside this abstraction — an MCP tool caller can't perform a redirect-based
OIDC login, so OIDC only ever makes sense here, on the browser path.
"""
