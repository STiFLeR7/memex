# Deployment Topology (v0.7.0+)

**Status:** Locked decision — Phase 03 (NET-12, NET-14)
**Scope:** Team deployments of memex's MCP write path (`record_decision` / `record_problem`)

This document formalizes the **one and only supported** team-deployment topology for
memex's concurrent-write-safety model, and explicitly documents the alternative
topology that is **unsupported** — a deliberate, scoped non-goal, not an oversight.

See `docs/ARCHITECTURE-v0.3.0.md` [§7 Write Governance Model](ARCHITECTURE-v0.3.0.md)
and [§11 Transport Layer](ARCHITECTURE-v0.3.0.md) for the underlying mechanisms this
document formalizes into a topology contract.

---

## 1. Supported topology (Topology A)

**One shared `memex serve --transport http` process, many remote authenticated MCP
clients over Streamable HTTP** is the ONLY supported topology for v0.7.0 team
deployments.

```
Topology A (SUPPORTED for v0.7.0)
==================================

  Developer A's IDE/agent          Developer B's IDE/agent          Developer N's IDE/agent
  (Claude Code / Cursor / Codex)   (Claude Code / Cursor / Codex)   (...)
         │  MCP over HTTP                  │  MCP over HTTP                  │
         │  Authorization: Bearer <key>    │  Authorization: Bearer <key>    │
         ▼                                 ▼                                 ▼
  ┌─────────────────────────────────────────────────────────────────────────────┐
  │             ONE shared `memex serve --transport http` process               │
  │             (one asyncio event loop, one OS process)                        │
  │                                                                             │
  │  mcp_asgi_app  ──►  validate_key(token)  ──►  tool dispatch                 │
  │                                                     │                       │
  │                                                     ▼                       │
  │  tools_write.py:  _get_decision_lock(module, repo) / _get_problem_lock(...) │
  │  (asyncio.Lock dict — coordinates ALL coroutines in THIS process)           │
  │                                                     │                       │
  │                                                     ▼                       │
  │  GraphClient singleton (memex/graph/client.py) ──► one Graphiti instance    │
  │  ──► one Neo4j AsyncDriver (connection-pooled, safe to share across         │
  │      coroutines; each execute_query()/add_episode() call draws its own     │
  │      session from the pool)                                                │
  └─────────────────────────────────────────┬───────────────────────────────────┘
                                              │ Bolt protocol
                                              ▼
                                     ┌─────────────────┐
                                     │   Neo4j (one     │
                                     │   shared graph)  │
                                     └─────────────────┘
```

### Why this is safe

- The `_decision_write_locks` / `_problem_write_locks` `asyncio.Lock` dicts
  (`memex/mcp_server/tools_write.py`, `_get_decision_lock`/`_get_problem_lock`,
  ~lines 22-42) coordinate every coroutine handling every remote client's request,
  because they all run in the SAME process's SAME event loop. A lock keyed on
  `f"{repo_path}:{module or '__global__'}"` serializes the check-then-act
  intent-confirmation / dedup sequence for concurrent writes to the same module.
- The `GraphClient` singleton (`memex/graph/client.py`, ~lines 39-76) holds one
  fixed credential set correctly, since there is only one process — no risk of
  divergent or stale credentials across concurrent callers.
- The underlying Neo4j `AsyncDriver` is documented safe to share across
  coroutines: each `execute_query()`/`add_episode()` call draws its own session
  from the connection pool rather than holding a shared session open across
  `await` boundaries.
- `tests/test_write_topology.py` (Phase 03, Plan 01) is the automated proof:
  three CI-runnable unit tests demonstrate the lock correctly serializes
  same-module concurrent writes and does NOT serialize different-module writes
  (preserving the fine-grained parallelism the design intends), and two
  `@pytest.mark.integration` tests (local-only, live Neo4j) demonstrate that 10
  concurrent `record_decision`/`record_problem` calls for the same module
  produce exactly one written node, not ten.

---

## 2. Unsupported topology (Topology B)

**N per-developer `memex` processes, one shared Neo4j instance** is explicitly
**UNSUPPORTED** for v0.7.0 — a documented non-goal, not an oversight.

```
Topology B (UNSUPPORTED for v0.7.0 — do not deploy this way)
==============================================================

  Developer A's memex process        Developer B's memex process
  (own asyncio.Lock dict,            (own asyncio.Lock dict,
   own GraphClient singleton,        own GraphClient singleton,
   own event loop)                   own event loop)
         │                                    │
         │   Both processes' lock dicts       │
         │   are independent Python dicts —   │
         │   NEITHER can see the other's      │
         │   in-flight critical section.      │
         ▼                                    ▼
  ┌─────────────────────────────────────────────────┐
  │              Same shared Neo4j instance          │
  │  Two concurrent record_decision() calls (one     │
  │  from each process) for the SAME module can BOTH │
  │  pass the intent-confirmation dedup check before │
  │  either commits its write ─► duplicate Decision  │
  │  nodes. No mechanism in either process detects   │
  │  the other's in-flight write.                    │
  └─────────────────────────────────────────────────┘
```

### The specific failure mechanism

Each `memex` process has its own independent, in-memory Python
`_decision_write_locks` / `_problem_write_locks` dict with **zero cross-process
visibility** — no file lock, no Neo4j-side lock, no distributed lock manager.
Two developers' separate processes can both pass
`_check_decision_intent_confirmation`'s dedup check (`tools_write.py`,
~lines 134-205) before either commits its write, producing duplicate
`Decision`/`Problem` nodes — exactly the failure this locking mechanism exists
to prevent under Topology A.

**No test in this phase (or anywhere in this repo) validates Topology B's
actual cross-process behavior.** A same-process, two-coroutine test would
trivially "pass" even for a Topology-B-shaped scenario, because it would
still share one lock dict under the hood — it would validate nothing about
cross-process races. The only way to genuinely test Topology B is spawning
two separate OS processes against a shared Neo4j instance, which this phase
intentionally does not do, because Topology B is out of scope for v0.7.0.

**Topology B is documented-unsupported based on code analysis of the
in-process lock mechanism — it has not been tested and found safe, nor
tested and found unsafe. It has simply not been built for, and this
document exists so nobody has to find that out the hard way in production.**

---

## 3. Onboarding cost note

Topology A means every developer's IDE/agent must point its MCP client
configuration at a **remote** HTTP URL (`http://<shared-host>:<port>`) with a
Bearer key — materially different from today's zero-config
`memex serve --transport stdio` local default, which spawns a private
per-developer subprocess with no shared state.

**Streamable HTTP** (not the deprecated SSE transport — see the deprecation
warning in `memex/mcp_server/http.py`) is the supported remote transport going
forward for Topology A deployments.

---

## 4. Future work note

`apoc.lock.nodes` / `apoc.lock.relationships` (Neo4j APOC — already available
via `NEO4J_PLUGINS=["apoc"]` in `docker/docker-compose.yml`) is flagged as the
only credible future primitive for real distributed, cross-process locking
**if Topology B is ever revisited post-v0.7.0**. APOC's lock procedures live
inside Neo4j itself, so they would be visible to every connecting process
regardless of language or host, unlike a custom external lock which would
need its own separate coordination service.

**Caveat:** Neo4j's own APOC documentation states these procedures are "not
considered safe to run from multiple threads" and "not supported by the
parallel runtime." This is a real limitation to weigh, not a turnkey fix.
**This is not built in this phase — it is deferred per the locked decision.**

---

## 5. Cross-references

- `docs/ARCHITECTURE-v0.3.0.md` §7 (Write Governance Model) — the per-module
  `asyncio.Lock` and intent-confirmation dedup mechanism this document
  formalizes into a topology contract.
- `docs/ARCHITECTURE-v0.3.0.md` §11 (Transport Layer) — stdio vs. HTTP/SSE
  transport details; see the "Supported team topology (v0.7.0+)" subsection
  there for the short pointer back to this document.
- `tests/test_write_topology.py` — automated proof of Topology A's
  concurrent-write safety (Phase 03, Plan 01).
