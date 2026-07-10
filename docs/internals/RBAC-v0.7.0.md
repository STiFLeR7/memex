# memex RBAC / Team Auth Layer — v0.7.0

**Theme: Identity** — turning a boolean bearer key ("valid" / "invalid") into a real
principal who has a role, so a shared deployment can tell operators apart without
replacing the mechanism that already works.

This closes Phase 02 (NET-08 through NET-11): the `mx_...` bearer-key registry now
carries identity and role, the `/mcp` transport was migrated to a session manager that
makes per-request identity propagation actually work, `/graph` is no longer wide open,
and `check_write_policy` enforces role at all four real call sites. Shipped across four
plans (02-01 through 02-04), all landed on `master`.

---

## The principal/role model

A **principal** is a human developer, or a CI/service account acting as one. This is a
new concept for memex and it is deliberately narrow: it answers "who authorized this
write," not "what tool made it." That second question — `harness` (e.g. `claude-code`,
`gemini-cli`) — is Phase 01's identity model (`Decision.harness`, `detect_agent()`) and
is orthogonal. A single principal can write through several different harnesses in the
same session; the two fields are never conflated.

Every principal carries one of three roles:

| Role | Meaning |
|------|---------|
| `viewer` | Read-only. Least privilege — the fail-safe default when a role string doesn't parse. |
| `contributor` | Can write `open`-tier nodes (the pre-Phase-02 implicit default for any authenticated caller). |
| `admin` | Can write `locked`-tier nodes and override `self`-tier ownership. |

Roles layer on top of the write-policy tiers that already existed (`memex/graph/schema.py`,
`WRITE_POLICIES`) rather than replacing them:

| Tier | Who may write | Example node types |
|------|----------------|---------------------|
| `locked` | `role in ("admin", "system")` | `Module`, `Symbol`, `Cluster`, `Repository`, `Principal` |
| `open` | anyone with a resolved identity, regardless of role | `Decision`, `Problem` |
| `self` | the owning `principal_id`, or any `admin` | `AgentSession` |

`"system"` is not a role a human ever holds — it's reserved for structurally-trusted,
non-agent actors (today: the cluster engine's `write_cluster_assignments`, which passes
`role="system"` explicitly so `memex cluster` keeps writing `locked` `Cluster` nodes
after the role-aware signature change).

`check_write_policy(node_type, principal_id, role="contributor", owner=None)` is the one
enforcement function. It is called from exactly four places: `tools_write.py`'s
`record_decision`, `record_problem`, `resolve_problem`, and `cluster_runner.py`'s
`write_cluster_assignments`. It is **not** called from the watcher's direct-write path
(`memex/graph/writer.py`) — the watcher is a structurally trusted local process, not a
principal, and stays that way by design, exactly as it did before this phase.

---

## Bearer keys post-Phase-02

Operators provision keys with:

```
memex keys add <name> --role viewer|contributor|admin --principal-id <id>
```

`--role` defaults to `admin` (matching the pre-Phase-02 implicit full-access behavior
for anyone who runs `keys add` without flags) and `--principal-id` defaults to `<name>`.

Newly created keys store a SHA-256 hash (`key_hash`) of the `mx_...` token, plus a
non-secret `key_prefix` for `keys list` display — never the plaintext secret. Token
generation is unchanged (`secrets.token_hex(16)`, 128 bits of entropy), and comparison
stays constant-time (`hmac.compare_digest`, full registry scan, no short-circuit) so
validation timing never leaks which record matched.

Pre-Phase-02 keys — plaintext `key`, no `role`, no `key_hash` — keep authenticating
unchanged. `resolve_principal()` explicitly branches on field *presence*, not just
absence-with-default: a record missing `role` entirely resolves to `role="admin"` (the
migration sentinel), which is a different case from a record that has `role` explicitly
set and empty. Conflating the two would have silently downgraded every pre-upgrade
admin-equivalent key to `contributor` on upgrade — the exact regression NET-11 exists to
prevent, and the reason `tests/test_backward_compat.py` exists as a dedicated,
non-optional phase-gate suite rather than an assumed side effect of the other plans'
unit tests.

When Neo4j is reachable, `memex keys add` also best-effort MERGEs a `:Entity{type:
'Principal'}` node (direct Cypher, never `client.add_episode()` — this is structured
identity metadata, not episodic narrative). This is supplementary team-visibility
metadata for later phases; key creation succeeds even when Neo4j is down, because the
registry-file key is the actual authentication mechanism.

---

## Why /mcp migrated to StreamableHTTPSessionManager(stateless=True)

Before this phase, `/mcp` was backed by a single `StreamableHTTPServerTransport` created
once at app startup, with one persistent background task (`asyncio.create_task
(run_transport())`) handling every request for the app's entire lifetime. A `ContextVar`
set inside a per-HTTP-request coroutine — which is exactly how `principal_ctx` needs to
work — is invisible to that persistent task: `contextvars.Context` is copied at
**task-creation time**, not read time, and the persistent task was created long before
any request (or its `Authorization` header) existed. The failure mode is silent, not a
crash: `principal_ctx.get(None)` would simply always return `None`, and every
HTTP-authenticated call would quietly collapse to the stdio-equivalent
`principal_id="local", role="admin"` fallback — RBAC would look correct in code review
while granting admin to every remote caller in practice.

`StreamableHTTPSessionManager(stateless=True)` fixes this by spawning a *fresh*
transport and a *fresh* dispatch task **per HTTP request**, via `task_group.start(...)`
called directly from within the same coroutine that already called
`principal_ctx.set(principal)`. Task creation captures the ambient context correctly in
that shape. `stateless=True` preserves the pre-migration behavior of not tracking
`mcp_session_id` — this was a like-for-like transport swap, not a protocol change for
existing clients.

Do not "simplify" this back to a single persistent transport. If a future refactor
merges the per-request transport creation back into one long-lived task, `principal_ctx`
silently stops propagating again, and the regression will not show up as a test failure
unless the concurrent-request isolation test (`tests/test_http_transport.py::
test_concurrent_requests_resolve_distinct_principals_in_tool_dispatch`) is run and kept
green. That test is written so it fails against the old wiring — treat it as load-bearing,
not incidental coverage.

---

## Accepted limitation: no native RBAC in Neo4j Community Edition

Neo4j Community Edition — the only edition memex ships against — has no built-in
role-based access control. Every enforcement point documented above
(`check_write_policy`, `resolve_principal`, `require_principal`) is **application-layer
only**. Anyone with direct Bolt or Neo4j Browser access to the database bypasses all of
it: `cypher-shell` or Neo4j Browser talking straight to the driver sees and can mutate
every node, including ones marked `locked`.

This is accepted, not solved, for v0.7.0's self-hosted/local-first deployment model. The
only in-scope mitigation is that Phase 06's shared-deployment design does not expose
Neo4j's Bolt (7687) or Browser (7474) ports outside the deployment's internal Docker
network — network isolation, not application-layer defense-in-depth. If that network
boundary is ever relaxed (e.g. exposing Bolt for an external admin tool), everything in
this document stops being a security boundary and becomes documentation of intent only.

---

## Non-Goals

The following are explicitly out of scope for v0.7.0's RBAC layer. These are not gaps
that slipped through — they are locked decisions from ROADMAP.md's Phase 02 success
criteria and 02-RESEARCH.md's Deferred Ideas, recorded here so a future phase doesn't
silently assume RBAC covers more than it does:

- **No SSO/OIDC.** Authentication is, and remains, static bearer tokens issued via
  `memex keys add`. No external identity provider integration.
- **No cross-team federation or multi-project role scoping.** One deployment is one
  project with one flat principal/role namespace this milestone — a principal's role is
  not scoped per-project or per-team.
- **No fine-grained per-node ACLs beyond node-type tier + self-ownership.** Enforcement
  is at the `locked`/`open`/`self` tier level and, for `self`, at the owning-principal
  level — not per-node, per-field, or per-relationship.
- **No direct-Neo4j-bypass protection beyond network isolation.** See "Accepted
  limitation" above — this is the one limitation that is discussed, not silently
  omitted.
- **No key rotation or short-lived tokens.** `mx_...` keys remain long-lived static
  bearer tokens, exactly as before this phase. There is no expiry, no refresh flow, and
  no push-invalidation of a key that's already been resolved into an in-flight request.
- **No per-principal filtering of `/stats`.** `/stats` requires *a* valid principal
  (shared `require_principal` dependency with `/graph`), but its output is not filtered
  or scoped by who is asking — every authenticated caller sees the same stats payload.
- **No role-awareness for the deprecated SSE transport.** `MEMEX_MCP_TRANSPORT=sse`
  stays on the legacy boolean `validate_key()` auth model it always used. SSE is already
  `DeprecationWarning`-marked; extending the new `resolve_principal`/`principal_ctx`
  design to a transport slated for removal was rejected as unnecessary scope expansion
  (02-RESEARCH.md Open Question #3).
