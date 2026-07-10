# memex — Architecture v0.3.0

> Inspired by Vannevar Bush's 1945 concept of a machine that remembers everything.

**Document type:** Architecture reference — developer handoff
**Prepared by:** Hill Patel
**Status:** Authoritative for v0.3.0 implementation
**Read alongside:** PLAN-v0.3.0.md

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Component Map](#2-component-map)
3. [Knowledge Graph Schema](#3-knowledge-graph-schema)
4. [Data Flow](#4-data-flow)
5. [Watcher Pipeline](#5-watcher-pipeline)
6. [MCP Server](#6-mcp-server)
7. [Write Governance Model](#7-write-governance-model)
8. [Retrieval Quality Model](#8-retrieval-quality-model)
9. [Hallucination Mitigation](#9-hallucination-mitigation)
10. [Graph Scalability Strategy](#10-graph-scalability-strategy)
11. [Transport Layer](#11-transport-layer)
12. [Configuration Reference](#12-configuration-reference)
13. [File Structure](#13-file-structure)
14. [Dependency Map](#14-dependency-map)
15. [Design Decisions and Rationale](#15-design-decisions-and-rationale)
16. [Known Constraints](#16-known-constraints)

---

## 1. System Overview

memex has three independently running processes that communicate through a shared Neo4j database:

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Your Repositories                             │
│                                                                      │
│   repo-A/  ──► git hook (post-commit)  ──►┐                         │
│   repo-A/  ──► watchdog (file saves)   ──►│                         │
│   repo-B/  ──► git hook               ──►│  Global asyncio Queue   │
│   repo-B/  ──► watchdog               ──►│  (one per daemon)       │
│   repo-N/  ──► ...                    ──►┘                         │
└──────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    PROCESS 1: Watcher Daemon                         │
│                    (memex watch)                                     │
│                                                                      │
│  EventRouter (800ms debounce on file events)                        │
│      │                                                               │
│      ├── FileChangeEvent ──► tree-sitter Extractor                  │
│      │                           └──► Graph Writer                  │
│      │                                                               │
│      └── CommitEvent ──► Gemini Flash Synthesiser                   │
│                              └──► Graph Writer ──► validated:false  │
│                                                                      │
│  CommitPoller (500ms, reads .memex/pending_commit.json)             │
│  DecayScheduler (nightly 02:00 UTC)                                 │
│  ArchiveScheduler (weekly, prunes cold nodes to SQLite)             │
│  SummarisationScheduler (triggered at 20 decisions/module)          │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ read / write
                           ▼
                    ┌─────────────┐       ┌──────────────────┐
                    │   Neo4j     │       │  archive.db      │
                    │  (live      │◄─────►│  (cold SQLite    │
                    │   graph)    │ prune │   store)         │
                    └─────────────┘       └──────────────────┘
                           │ read
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    PROCESS 2: MCP Server                             │
│                    (memex serve)                                     │
│                                                                      │
│  Transport A: stdio  (local agents — Gemini CLI, Claude Code)       │
│  Transport B: HTTP/SSE  (remote/team agents, port 7463)             │
│                                                                      │
│  12 MCP Tools                                                        │
│  ├── Read (6):  get_project_context, get_symbol_context,            │
│  │              get_recent_decisions, get_open_problems,             │
│  │              search_context, get_stale_context                   │
│  ├── Read (2):  explain_change, predict_impact  [NEW v0.3.0]        │
│  └── Write (4): record_decision, record_problem,                    │
│                 resolve_problem, invalidate_edge                    │
│                                                                      │
│  Query Layer    (memex/mcp_server/queries.py)                       │
│  Formatter      (memex/mcp_server/formatter.py)                     │
└──────────────────────────────────────────────────────────────────────┘
                           │ MCP protocol
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
    Gemini CLI       Claude Code         Codex
    (stdio)          (stdio)             (stdio)
                                    Remote agents
                                    (HTTP/SSE)
```

**Key invariant:** The MCP server is read-heavy and never blocks. It queries Neo4j directly using Cypher for structured reads and delegates to Graphiti's hybrid retrieval only for `search_context()`. The watcher is write-heavy and runs in a separate process so a slow parse job never delays an agent query.

---

## 2. Component Map

```
memex/
│
├── cli.py                        Entry point for all CLI commands
├── config.py                     Single source for all config + env vars
│
├── watcher/
│   ├── registry.py               Single import surface for daemon.py
│   ├── daemon.py                 Orchestrator — starts all watcher components
│   ├── events.py                 FileChangeEvent, CommitEvent dataclasses
│   ├── fs_observer.py            watchdog → asyncio.Queue bridge
│   ├── git_hook.py               Hook installer + CommitEvent emitter
│   ├── commit_poller.py          Polls .memex/pending_commit.json (500ms)
│   ├── event_router.py           Debounce (800ms) + dispatch to handlers
│   └── handlers.py               handle_file_change, handle_commit
│
├── extractor/
│   ├── treesitter.py             file diff → SymbolDelta (no LLM)
│   ├── lockfile.py               requirements.txt / pyproject.toml → Dependency nodes
│   └── todo_scanner.py           TODO/FIXME comments → Problem nodes
│
├── synthesiser/
│   └── commit.py                 commit message → Decision[] via Gemini Flash
│                                 trivial filter, exponential backoff, validated:false
│
├── graph/
│   ├── client.py                 Graphiti singleton (Gemini LLM + embedder)
│   ├── schema.py                 Pydantic v2 models for all node types
│   ├── writer.py                 Graphiti episode ingestion + edge management
│   │                             write policy enforcement, contradiction detection
│   ├── decay.py                  Nightly confidence decay + accelerated decay
│   │                             for unvalidated decisions > 30 days
│   ├── archive.py                [NEW v0.3.0] Prune cold nodes → archive.db
│   └── summariser.py             [NEW v0.3.0] Decision summarisation background job
│
└── mcp_server/
    ├── server.py                 create_server() + run_server() (separated)
    ├── queries.py                All Cypher queries as named async functions
    ├── formatter.py              All markdown assembly + token budget enforcement
    ├── tools_read.py             6 read tools + 2 new tools (explain_change,
    │                             predict_impact)
    └── tools_write.py            4 write tools with governance enforcement
```

**Rule for incoming developer:** Every module imports from at most one level below it. `daemon.py` imports only from `registry.py`. `registry.py` imports from the actual modules. This prevents coupling chains that make testing and refactoring painful.

---

## 3. Knowledge Graph Schema

### Node Types

```
(:Repository)
    path:          str   — absolute filesystem path, primary key
    name:          str   — inferred from directory name
    added_at:      datetime
    last_active:   datetime

(:Cluster)                          [NEW v0.3.0]
    name:          str   — directory name or inferred domain label
    repo_path:     str   — foreign key → Repository.path
    description:   str | None
    module_count:  int   — maintained by watcher

(:Module)
    path:          str   — relative to repo root
    language:      str   — inferred by tree-sitter
    description:   str | None
    repo_path:     str
    cluster_name:  str   — foreign key → Cluster.name [NEW v0.3.0]
    write_policy:  str   — "locked" always for Module

(:Symbol)
    name:          str
    kind:          str   — "fn" | "class" | "const"
    signature:     str
    file:          str
    line:          int
    repo_path:     str
    write_policy:  str   — "locked" always for Symbol

(:Decision)
    text:          str   — max 2000 chars, sanitised
    rationale:     str | None
    scope:         str | None
    source:        str   — "watcher" | "agent"
    validated:     bool  — False for watcher-synthesised, True after memex review
    confidence:    float — starts at 0.6 (watcher) or per-harness config (agent)
    corroborated:  bool  — True when a subsequent commit confirmed it
    corroboration_commit: str | None
    repo_path:     str
    write_policy:  str   — "open"
    supersedes:    str | None  — node_id of the Decision this replaces [NEW v0.3.0]

(:DecisionSummary)                  [NEW v0.3.0]
    text:          str   — synthesised summary of N Decision nodes
    source_count:  int   — number of decisions summarised
    confidence:    float — max confidence of source decisions
    repo_path:     str
    write_policy:  str   — "locked" (only summariser writes this)

(:Problem)
    text:          str   — max 2000 chars
    severity:      str   — "critical" | "high" | "medium" | "low"
    status:        str   — "open" | "resolved"
    source:        str   — "watcher" | "agent"
    repo_path:     str
    write_policy:  str   — "open"

(:AgentSession)
    session_id:    str   — hash of (process start time + repo path)
    agent:         str   — "gemini-cli" | "claude-code" | "codex" | "unknown"
    started_at:    datetime
    repo_path:     str
    write_policy:  str   — "self"

(:Dependency)
    name:          str
    version:       str
    ecosystem:     str   — "pypi" | "npm" | "cargo" | etc.
    repo_path:     str
    write_policy:  str   — "locked"
```

### Edge Types

```
(Repository)-[:CONTAINS]->(Cluster)
(Cluster)-[:CONTAINS]->(Module)         [NEW v0.3.0, replaces direct Repo→Module]
(Module)-[:EXPORTS]->(Symbol)
(Symbol)-[:CALLS]->(Symbol)
(Symbol)-[:MOTIVATES]->(Decision)
(Decision)-[:SUPERSEDES]->(Decision)    [NEW v0.3.0]
(Decision)-[:SUMMARISED_INTO]->(DecisionSummary)  [NEW v0.3.0]
(Problem)-[:CAUSED_BY]->(Symbol)
(Problem)-[:RESOLVED_BY]->(AgentSession)
(Problem)-[:SURFACED_IN]->(AgentSession)
(Module)-[:DEPENDS_ON]->(Module)        cross-repo dependency edge
(Module)-[:PINNED_TO]->(Dependency)
```

### Temporal Fields on Every Edge

```
valid_from:       datetime   — when this relationship became true
valid_until:      datetime | None  — None means still true today
confidence:       float      — 1.0 at creation, modified by decay and validation rules
source_commit:    str | None — traceable to the git event that created it
last_touched:     datetime   — updated when the source file is modified
```

### Pydantic Validation

All node writes go through `memex/graph/schema.py` Pydantic v2 models before reaching Graphiti. Validators enforce:
- `kind` in `{"fn", "class", "const"}` for Symbol nodes
- `severity` in `{"critical", "high", "medium", "low"}` for Problem nodes
- `confidence` clamped to `[0.0, 1.0]`
- null bytes stripped from all string fields
- all text fields capped at 2000 characters (silent truncation)
- `write_policy` is immutable after node creation

---

## 4. Data Flow

### File save → Symbol in graph

```
Developer saves auth/service.py
        │
        ▼
watchdog FileSystemEventHandler
        │   emits FileChangeEvent { path, kind, timestamp }
        ▼
asyncio Queue
        │
        ▼
EventRouter
        │   debounce: 800ms window per file path
        │   if another save arrives within 800ms → cancel + reschedule
        ▼
handle_file_change(event)
        │
        ├── read current content:  open(event.path).read()
        ├── read previous content: git show HEAD:<posix_path>
        │   (uses asyncio.create_subprocess_exec — never blocks event loop)
        │
        ▼
treesitter.extract_symbol_delta(old, new, path)
        │   returns SymbolDelta { added[], removed[], modified[] }
        │   pure local computation — no network, no LLM
        ▼
graph.writer.write_symbol_delta(delta, source_commit=None)
        │
        ├── validate each Symbol through Pydantic SymbolNode model
        ├── upsert added/modified symbols via Graphiti.add_episode()
        └── set valid_until=now on removed symbols (never delete)
        │
        ▼
Neo4j — Symbol node written or updated
```

### git commit → Decision in graph

```
Developer runs: git commit -m "refactor auth: switched to EdDSA for key rotation"
        │
        ▼
post-commit hook (shell script, installed by memex init)
        │   calls: python -m memex.watcher.git_hook emit --repo <root>
        ▼
git_hook.emit_commit_event()
        │   reads: git log -1 (sha, message, author)
        │   reads: git diff HEAD~1 HEAD (diff summary)
        │   writes: .memex/pending_commit.json
        ▼
CommitPoller (polling every 500ms)
        │   reads .memex/pending_commit.json
        │   deletes it atomically (Path.unlink(missing_ok=True))
        │   puts CommitEvent onto asyncio Queue
        ▼
EventRouter (no debounce — CommitEvents pass through immediately)
        │
        ▼
handle_commit(event)
        │
        ├── trivial filter: if message matches wip|fix|fmt|lint|typo|bump|merge|style
        │   └── return [] immediately, no LLM call
        │
        ▼
synthesiser.commit.extract_decisions(message, diff, sha)
        │   Gemini Flash call with strict JSON schema
        │   returns Decision[] or []
        │   exponential backoff on RateLimitError (max 3 retries)
        │
        ▼
for each Decision:
        │
        ├── validate through Pydantic DecisionNode model
        ├── set validated=False, confidence=0.6, source="watcher"
        ├── write via Graphiti.add_episode()
        └── link to affected Module nodes
        │
        ▼
Neo4j — Decision node written with validated:false
```

### Agent session → write back to graph

```
Agent (Gemini CLI) calls record_decision(text, module)
        │
        ▼
tools_write.record_decision()
        │
        ├── validate text length (≥ 10 chars, ≤ 2000 chars)
        ├── sanitise null bytes and control characters
        │
        ├── contradiction check:
        │   query last 50 open decisions for this module
        │   run Graphiti similarity search (threshold: 0.85)
        │   if match found → return existing node ID + options
        │       (corroborate or supersede — agent decides)
        │
        ├── write policy check:
        │   module nodes are "locked" — agent cannot create them
        │   if module does not exist → Decision created without module link
        │
        ├── acquire asyncio.Lock for (module, repo_path)
        │
        ▼
graph.writer.write_decision()
        │   confidence = config.harnesses[agent].initial_decision_confidence
        │   default: 0.6
        │   source = "agent"
        │   validated = False (agent decisions also require memex review)
        │
        ▼
Neo4j — Decision node written at agent-configured confidence
```

---

## 5. Watcher Pipeline

### Startup sequence (daemon.py → run_daemon())

```
1. config.validate()
        └── raises ConfigError if GEMINI_API_KEY or NEO4J_URI missing

2. graph.client.init()
        └── raises MemexStartupError if Neo4j unreachable (fail fast, < 3s)

3. check .memex/paused
        └── if exists → sleep loop until file removed (respect pause on boot)

4. FSObserver.start()
        └── watchdog thread started, events flowing to asyncio Queue

5. CommitPoller.run() as background asyncio Task
        └── task reference stored — not fire-and-forget (GC safety)

6. EventRouter.run() as background asyncio Task
        └── registers handle_file_change and handle_commit

7. DecayScheduler.start()  (APScheduler, AsyncIOScheduler, nightly 02:00 UTC)

8. ArchiveScheduler.start()  (weekly, Sunday 03:00 UTC)  [NEW v0.3.0]

9. SummarisationScheduler.start()  (daily check, triggers at 20 decisions)  [NEW v0.3.0]

10. logger.info("memex watching %s | repos: %d | neo4j: %s",
                repo_root, len(registry), config.neo4j_uri_redacted)
```

### Graceful shutdown (KeyboardInterrupt or SIGTERM)

```
1. Cancel all background tasks
2. asyncio.gather(*tasks, return_exceptions=True)
3. FSObserver.stop()
4. DecayScheduler.stop()
5. ArchiveScheduler.stop()
6. SummarisationScheduler.stop()
7. logger.info("memex stopped")
8. sys.exit(0)
```

### Event router debounce

The debounce window is 800ms per file path. Implementation:

```python
pending: dict[str, asyncio.Task] = {}

async def route(event):
    if isinstance(event, CommitEvent):
        await handle_commit(event)   # immediate, no debounce
        return

    path = event.path
    if path in pending:
        pending[path].cancel()       # cancel previous scheduled call

    async def deferred():
        await asyncio.sleep(0.8)
        await handle_file_change(event)
        pending.pop(path, None)

    pending[path] = asyncio.create_task(deferred())
    # task reference stored in pending dict — prevents GC
```

---

## 6. MCP Server

### Construction vs transport (critical separation)

```python
# create_server() — safe to call in tests, never touches stdio
def create_server(repo_root: str) -> tuple[FastMCP, Config]:
    config = Config.load(repo_root)
    config.validate()
    check_neo4j(config)           # raises MemexStartupError if unreachable
    server = FastMCP("memex", version=__version__)
    _register_all_tools(server, config)
    return server, config

# run_server() — only called by CLI, opens stdio transport
async def run_server(repo_root: str, transport: str = "stdio") -> None:
    server, config = create_server(repo_root)
    if transport == "stdio":
        await server.run_async()
    elif transport == "http":
        await run_http_server(server, config)
    elif transport == "both":
        await asyncio.gather(
            server.run_async(),
            run_http_server(server, config)
        )
```

Tests only import and call `create_server()`. stdio is never opened during tests. This is why `pyproject.toml` has `addopts = "-p no:capture"` — on Windows, pytest's capture system conflicts with the MCP SDK's stdio handling during teardown even when not fully opened.

### Tool registration (all 12)

```python
def _register_all_tools(server: FastMCP, config: Config) -> None:
    # Read tools — query only, never write
    server.add_tool(get_project_context)
    server.add_tool(get_symbol_context)
    server.add_tool(get_recent_decisions)
    server.add_tool(get_open_problems)
    server.add_tool(search_context)
    server.add_tool(get_stale_context)

    # New read tools — v0.3.0
    server.add_tool(explain_change)
    server.add_tool(predict_impact)

    # Write tools — governance enforced inside each handler
    server.add_tool(record_decision)
    server.add_tool(record_problem)
    server.add_tool(resolve_problem)
    server.add_tool(invalidate_edge)
```

### Tool response contract

Every tool must:
- return `str` (never `None`, `dict`, `list`)
- catch all exceptions and return a graceful error string (never raise into MCP protocol)
- respect the token budget (enforced by `formatter.py`)
- log errors at `ERROR` level with `exc_info=True`

```python
# Pattern used by every tool handler
async def get_project_context(scope: str | None = None) -> str:
    """
    Returns a compressed briefing of the repository: active modules, recent
    decisions, open problems, and stale warnings. Call this once at session
    start. Use scope= to filter to a specific directory or service.
    Returns: Markdown string under 2000 tokens.
    """
    try:
        counts = await queries.get_node_counts()
        modules = await queries.get_active_modules(since_days=30, scope=scope)
        decisions = await queries.get_recent_decisions_raw(since_days=7, module=scope, limit=10)
        problems = await queries.get_open_problems_raw(module=scope)
        stale_count = await queries.count_stale_edges(threshold=0.3)
        return formatter.format_project_context(counts, modules, decisions, problems, stale_count)
    except Exception:
        logger.error("get_project_context failed", exc_info=True)
        return "context temporarily unavailable — neo4j may be unreachable"
```

---

## 7. Write Governance Model

### Write policy enforcement

```python
# memex/graph/writer.py

WRITE_POLICIES = {
    "Module":          "locked",
    "Symbol":          "locked",
    "Dependency":      "locked",
    "Decision":        "open",
    "Problem":         "open",
    "AgentSession":    "self",
    "DecisionSummary": "locked",
    "Cluster":         "locked",
}

def check_write_policy(node_type: str, caller: str) -> None:
    policy = WRITE_POLICIES.get(node_type, "open")
    if policy == "locked" and caller == "agent":
        raise MemexWritePolicyError(
            f"{node_type} nodes are locked — only the watcher can create or modify them"
        )
```

`caller` is `"agent"` when the call originates from an MCP write tool, `"watcher"` when it originates from the watcher pipeline. The MCP write tool handlers pass `caller="agent"` explicitly. The watcher handlers pass `caller="watcher"`.

### Contradiction detection

```python
# In tools_write.record_decision() — before any write

async def _check_contradiction(text: str, module: str | None) -> dict | None:
    if not module:
        return None
    candidates = await queries.get_open_decisions_raw(module=module, limit=50)
    for candidate in candidates:
        similarity = await graphiti_client.similarity(text, candidate["text"])
        if similarity >= 0.85:
            return candidate  # return the existing node, do not write
    return None
```

If contradiction detected, the tool returns:

```
similar decision already exists [id: abc123]:
"switched auth to EdDSA for key rotation simplicity"
confidence: 0.94 | validated: yes | source: watcher

to corroborate: record_decision(corroborates="abc123")
to supersede:   record_decision(supersedes="abc123", text="<new text>")
```

### Concurrent write safety

```python
# module-level in tools_write.py
_problem_write_locks: dict[str, asyncio.Lock] = {}
_decision_write_locks: dict[str, asyncio.Lock] = {}

def _get_lock(store: dict, key: str) -> asyncio.Lock:
    if key not in store:
        store[key] = asyncio.Lock()
    return store[key]

# usage in record_decision
lock_key = f"{config.repo_root}:{module or '__global__'}"
async with _get_lock(_decision_write_locks, lock_key):
    contradiction = await _check_contradiction(text, module)
    if contradiction:
        return _format_contradiction_response(contradiction)
    await graph.writer.write_decision(...)
```

Lock is per `(repo_root, module)` — fine-grained enough to allow concurrent writes to different modules, coarse enough to prevent duplicate nodes within the same module.

---

## 8. Retrieval Quality Model

### Composite retrieval score

Applied in `queries.py` to all results from `search_context()`:

```python
def composite_score(
    semantic_similarity: float,
    valid_from: datetime,
    confidence: float,
) -> float:
    age_days = (datetime.now(UTC) - valid_from).days
    recency_score = max(0.0, 1.0 - (age_days / 180))
    return (
        (semantic_similarity * 0.4) +
        (recency_score       * 0.3) +
        (confidence          * 0.3)
    )
```

Results are sorted by `composite_score` descending before formatting. The score breakdown is included in `search_context()` output so agents understand the ranking:

```
[Decision] switched auth to EdDSA for key rotation
  file: auth/service.py
  composite: 0.81  (semantic: 0.74 | recency: 0.92 | confidence: 0.88)
  stale: no
```

### Conflict detection

Run during `get_recent_decisions()` for each module requested:

```python
async def detect_conflicts(decisions: list[dict]) -> list[dict]:
    for i, d1 in enumerate(decisions):
        for d2 in decisions[i+1:]:
            if d1["module"] == d2["module"]:
                overlap = validity_overlap(d1, d2)
                if overlap and await graphiti_client.similarity(
                    d1["text"], d2["text"]
                ) < 0.4:  # low similarity + same module + overlapping validity = conflict
                    d1["conflict"] = True
                    d2["conflict"] = True
    return decisions
```

Conflicting decisions are returned with a `[CONFLICT]` prefix in the formatter output. The agent receiving the context sees both and can resolve using `invalidate_edge()`.

---

## 9. Hallucination Mitigation

### Confidence state machine for Decision nodes

```
                    watcher synthesises commit
                             │
                             ▼
                    ┌─────────────────┐
                    │  validated: F   │
                    │  confidence:0.6 │
                    │  source: watcher│
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
         memex review    corroborating   30 days pass,
         validates        commit          no validation,
              │           arrives         no corroboration
              ▼              │              │
    ┌─────────────────┐      │              ▼
    │  validated: T   │      │   ┌─────────────────┐
    │  confidence     │      │   │  validated: F   │
    │  floor: 0.7     │      │   │  confidence cap │
    │  decay: std     │      │   │  max: 0.5       │
    └─────────────────┘      │   │  decay: 2x std  │
                             ▼   └─────────────────┘
                   ┌─────────────────┐
                   │  corroborated:T │
                   │  confidence:1.0 │
                   │  decay: std     │
                   └─────────────────┘
```

### `memex review` terminal UI

Rendered with `rich`. Keyboard-driven. Non-blocking — does not interrupt watcher.

```
memex review — 7 decisions pending validation

[1/7] Decision from commit a3f92b1 — 2026-05-14

Commit message:
  "refactor auth: switched from RS256 to EdDSA for key rotation simplicity"

Synthesised decision:
  "Switched authentication token signing from RS256 to EdDSA because
   RSA key rotation was operationally complex at the current scale"

Module: memex/watcher/handlers.py
Confidence: 0.60 | Source: watcher | Corroborated: no

  [y] validate    [n] delete    [e] edit    [s] skip    [q] quit
```

Edit mode opens the decision text in `$EDITOR` (same pattern as `git commit --amend`).

---

## 10. Graph Scalability Strategy

### Cluster hierarchy (schema)

```
Before v0.3.0:
  Repository → Module (flat, N modules = N nodes at top level)

After v0.3.0:
  Repository → Cluster → Module (hierarchical)

Cluster assignment on memex init:
  top-level directories become Clusters automatically
  memex/watcher/ → Cluster "watcher"
  memex/graph/   → Cluster "graph"
  memex/mcp_server/ → Cluster "mcp_server"
```

`get_project_context()` default response returns Cluster summaries (symbol count, decision count, open problems per cluster). Module-level detail requires `scope="memex/watcher"` explicitly. This keeps the default context response under 1500 tokens regardless of repo size.

### Pruning strategy

Conditions for a node to be eligible for archival:

```python
def is_cold(node: dict) -> bool:
    return (
        node["confidence"] < 0.1
        and node["valid_until"] is not None
        and (datetime.now(UTC) - node["valid_until"]).days > 90
    )
```

Archival process (weekly, Sunday 03:00 UTC):

```
1. Query all cold nodes from Neo4j
2. Write each node + its edges to archive.db (SQLite)
3. Delete node and all its edges from Neo4j
4. Log: "archived N nodes, graph size: M remaining"
```

Restore:

```bash
memex archive --restore <node_id>
# reads from archive.db, re-inserts into Neo4j with original temporal fields
```

### Decision summarisation

Trigger condition: a Module accumulates > 20 Decision nodes with `valid_until=null`.

```
1. Query all N Decision nodes for the module
2. Send texts to Gemini Flash with prompt:
   "Synthesise these N architectural decisions into one coherent summary.
    Preserve rationale. Return JSON: {text, scope, key_themes[]}"
3. Create DecisionSummary node (confidence = max of source confidences)
4. Create SUMMARISED_INTO edges from all source decisions to the summary
5. Set source decisions to excluded=true (filtered from default retrieval)
6. Log: "summarised N decisions for module X → DecisionSummary <id>"
```

The summarisation job runs in the background — it does not block the watcher or MCP server. It uses the same Gemini Flash model as commit synthesis to keep costs predictable.

---

## 11. Transport Layer

### stdio transport (default)

Used by local agent harnesses. One MCP server process per developer machine. Communicates via stdin/stdout using the MCP protocol. Started by `memex serve --repo .` or `memex serve --transport stdio --repo .`.

### HTTP/SSE transport

Used for team-shared instances and remote agents. Runs on port 7463 (configurable).

```
Agent harness
    │  HTTP POST /tools/<tool_name>
    │  Authorization: Bearer <api_key>
    ▼
FastAPI app (memex/mcp_server/http_server.py)
    │  validates API key
    │  calls the same tool handler as stdio transport
    │  streams response via Server-Sent Events
    ▼
Same Neo4j graph — same tools — same response format
```

API key lifecycle:

```bash
# Generated on first HTTP serve, stored at .memex/server.key
# Printed once to stdout — save it
memex serve --transport http --repo .
> memex HTTP server started on port 7463
> API key: memex_sk_a3f92b1c...  (save this — shown once)

# Subsequent starts use stored key silently
```

Team member config (Gemini CLI):

```json
{
  "mcpServers": {
    "memex": {
      "url": "http://memex-host:7463/sse",
      "headers": {
        "Authorization": "Bearer memex_sk_a3f92b1c..."
      }
    }
  }
}
```

### Supported team topology (v0.7.0+)

For team deployments, "one shared `memex-server` process, many remote
authenticated MCP clients" is the only supported concurrent-write-safe
topology. Running N independent `memex` processes against one shared Neo4j
instance is explicitly **unsupported**: the per-process `asyncio.Lock` dicts
described in §7 have no cross-process visibility, so two developers' separate
processes can both pass the same dedup check before either writes, producing
duplicate `Decision`/`Problem` nodes. See `docs/DEPLOYMENT-TOPOLOGY.md` for the
full diagrams, the specific failure mechanism, and onboarding guidance.

---

## 12. Configuration Reference

All configuration lives in `config.yaml` at the repo root and is loaded by `memex/config.py`. Environment variables override config file values.

```yaml
# config.yaml

neo4j:
  uri: bolt://localhost:7687       # NEO4J_URI
  user: neo4j                      # NEO4J_USER
  password: "your@password"        # NEO4J_PASSWORD — quote if special chars

gemini:
  api_key: ""                      # GEMINI_API_KEY — prefer env var
  synthesis_model: gemini-2.0-flash
  embedding_model: models/text-embedding-004

watcher:
  debounce_ms: 800
  poll_interval_ms: 500
  ignored_dirs:
    - .git
    - __pycache__
    - .venv
    - node_modules
    - dist
    - build

graph:
  decay_rate_per_day: 0.01
  decay_schedule_utc: "02:00"
  stale_threshold: 0.3
  archive_threshold_confidence: 0.1
  archive_threshold_days: 90
  summarisation_trigger: 20        # decisions per module before summarisation

retrieval:
  score_weights:
    semantic: 0.4
    recency: 0.3
    confidence: 0.3
  recency_halflife_days: 180
  conflict_similarity_threshold: 0.4
  contradiction_similarity_threshold: 0.85

validation:
  unvalidated_confidence_cap: 0.8
  unvalidated_old_confidence_cap: 0.5
  unvalidated_old_threshold_days: 30
  validated_confidence_floor: 0.7
  corroboration_window_days: 14

mcp:
  http_port: 7463
  http_key_path: .memex/server.key

harnesses:
  gemini-cli:
    initial_decision_confidence: 0.6
    corroboration_window_days: 14
  claude-code:
    initial_decision_confidence: 0.7
    corroboration_window_days: 10
  default:
    initial_decision_confidence: 0.6
    corroboration_window_days: 14
```

---

## 13. File Structure

```
memex/
├── memex/
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── watcher/
│   │   ├── __init__.py
│   │   ├── registry.py
│   │   ├── daemon.py
│   │   ├── events.py
│   │   ├── fs_observer.py
│   │   ├── git_hook.py
│   │   ├── commit_poller.py
│   │   ├── event_router.py
│   │   └── handlers.py
│   ├── extractor/
│   │   ├── __init__.py
│   │   ├── treesitter.py
│   │   ├── lockfile.py
│   │   └── todo_scanner.py
│   ├── synthesiser/
│   │   ├── __init__.py
│   │   └── commit.py
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── client.py
│   │   ├── schema.py
│   │   ├── writer.py
│   │   ├── decay.py
│   │   ├── archive.py          [NEW v0.3.0]
│   │   └── summariser.py       [NEW v0.3.0]
│   └── mcp_server/
│       ├── __init__.py
│       ├── server.py
│       ├── http_server.py      [NEW v0.3.0]
│       ├── queries.py
│       ├── formatter.py
│       ├── tools_read.py
│       └── tools_write.py
├── tests/
│   ├── test_cli.py
│   ├── test_config.py
│   ├── test_extractor.py
│   ├── test_fs_observer.py
│   ├── test_commit_poller.py
│   ├── test_event_router.py
│   ├── test_handlers.py
│   ├── test_graph_client.py
│   ├── test_schema.py
│   ├── test_graph_writer.py
│   ├── test_decay.py
│   ├── test_archive.py         [NEW v0.3.0]
│   ├── test_summariser.py      [NEW v0.3.0]
│   ├── test_mcp_server.py
│   ├── test_mcp_queries.py
│   ├── test_mcp_tools.py
│   ├── test_tools_write.py
│   ├── test_formatter.py
│   ├── test_retrieval_score.py [NEW v0.3.0]
│   ├── test_bidirectional.py
│   └── fixtures/
├── npm/
│   ├── package.json
│   └── bin/
│       └── memex-mcp.js
├── docker/
│   └── docker-compose.yml
├── docs/
│   ├── memex-system-design.md
│   ├── PLAN.md
│   ├── PLAN-v0.3.0.md
│   └── ARCHITECTURE-v0.3.0.md  ← this file
├── .github/
│   └── workflows/
│       ├── ci.yml
│       └── publish.yml
├── AUDIT.md
├── GEMINI.md
├── LICENSE
├── README.md
├── config.yaml.example
└── pyproject.toml
```

---

## 14. Dependency Map

```
memex/cli.py
    └── memex/config.py
    └── memex/watcher/daemon.py (via asyncio.run)
    └── memex/mcp_server/server.py (via asyncio.run)
    └── memex/graph/archive.py (memex archive command)

memex/watcher/daemon.py
    └── memex/watcher/registry.py  ← ONLY internal import

memex/watcher/registry.py
    └── memex/watcher/fs_observer.py
    └── memex/watcher/commit_poller.py
    └── memex/watcher/event_router.py
    └── memex/watcher/handlers.py
    └── memex/graph/decay.py
    └── memex/graph/archive.py
    └── memex/graph/summariser.py

memex/watcher/handlers.py
    └── memex/extractor/treesitter.py
    └── memex/synthesiser/commit.py
    └── memex/graph/writer.py

memex/graph/writer.py
    └── memex/graph/client.py
    └── memex/graph/schema.py

memex/mcp_server/server.py
    └── memex/mcp_server/tools_read.py
    └── memex/mcp_server/tools_write.py
    └── memex/config.py
    └── memex/graph/client.py

memex/mcp_server/tools_read.py
    └── memex/mcp_server/queries.py
    └── memex/mcp_server/formatter.py

memex/mcp_server/tools_write.py
    └── memex/mcp_server/queries.py
    └── memex/mcp_server/formatter.py
    └── memex/graph/writer.py
    └── memex/config.py

External dependencies (pinned in pyproject.toml):
    graphiti-core[google-genai]  — knowledge graph engine + Gemini integration
    neo4j                        — database driver
    google-genai                 — Gemini Flash + embeddings
    tree-sitter-language-pack    — 305 language grammars, local, no network
    watchdog                     — filesystem events
    mcp                          — MCP server SDK
    fastapi + uvicorn            — HTTP/SSE transport
    apscheduler                  — nightly/weekly scheduled jobs
    pydantic>=2.0                — node schema validation
    rich                         — memex review terminal UI  [NEW v0.3.0]
    gitpython                    — test fixtures (git repo creation)
    python-dotenv                — .env loading
    pytest + pytest-asyncio + pytest-cov  — test suite
```

---

## 15. Design Decisions and Rationale

**Why Graphiti over a raw Neo4j ORM?**
Graphiti provides bi-temporal edge management out of the box — `valid_from`, `valid_until`, conflict resolution, and hybrid retrieval (semantic + keyword + graph traversal) in one library. Building this from scratch on raw Neo4j would take months. The tradeoff is that Graphiti moves fast and has had breaking changes between minor versions — all deps are pinned for this reason.

**Why tree-sitter for code parsing and not an LLM?**
tree-sitter runs in milliseconds, requires no network, produces deterministic output, and handles 305 languages with a single pip install. An LLM-based code parser would be 100x slower, cost money on every file save, and produce non-deterministic results. tree-sitter handles all structural extraction. Gemini Flash handles only semantic interpretation of commit messages.

**Why a sidecar file for git hooks instead of IPC?**
Git hooks run in a subprocess context that may not have access to the daemon's asyncio event loop. Writing to `.memex/pending_commit.json` and polling from the daemon is simple, reliable, works on Windows without Unix sockets, and is idempotent. The 500ms poll interval means commit events appear in the graph within half a second of the hook firing.

**Why stdio-first for MCP transport?**
stdio transport has zero configuration overhead — no port, no auth, no network stack. It is the correct default for a developer tool used locally. HTTP/SSE transport was added in v0.2.0 for team use but is never the default because it introduces security surface (API key management) that most users do not need.

**Why asyncio.Lock per (module, repo_path) and not a global lock?**
A global write lock would serialize all agent writes across all modules — unacceptable latency for a team with 10 agents writing simultaneously. Fine-grained locks per module allow concurrent writes to different modules while preventing duplicate nodes within the same module. The lock store (`dict[str, asyncio.Lock]`) is module-level state in `tools_write.py` — it lives for the lifetime of the MCP server process, which is correct.

**Why Neo4j Community Edition and not FalkorDB or Kuzu?**
Neo4j Community is free, has a browser UI at `localhost:7474` that makes debugging the graph visual during development, and is the most documented option. FalkorDB is lighter but has less tooling. Kuzu is faster for analytical queries but less mature for production graph workloads. The schema is not Neo4j-specific — Graphiti supports all three backends — so switching is possible in a future version if performance requires it.

**Why separate `create_server()` from `run_server()`?**
The MCP SDK's stdio transport touches `sys.stdout` and `sys.stdin` when started. On Windows, pytest's capture system intercepts these file handles, and when the test teardown closes them, the MCP SDK raises `ValueError: I/O operation on closed file`. Separating construction from transport means tests can exercise all server logic (tool registration, config validation, Neo4j checks) without ever opening a stdio stream.

---

## 16. Known Constraints

**Neo4j Community Edition has no clustering.** If the team's shared memex instance goes down, all agents lose context until it restarts. Mitigation: `memex doctor` checks daemon health; agents fall back to `"context temporarily unavailable"` error strings rather than crashing.

**Graphiti version lock risk.** Graphiti releases frequently. The pinned version was validated against the v0.1.1 audit. A minor version bump may introduce breaking schema changes. Before upgrading Graphiti, run the full test suite and check the Graphiti changelog for Neo4j schema migrations.

**Windows file locking on `pending_commit.json`.** On Windows, if a git hook fires while the CommitPoller is reading the sidecar file, the read may fail with a file lock error. `Path.unlink(missing_ok=True)` and `json.JSONDecodeError` handling mitigate this but do not eliminate it entirely. If a commit event is lost due to this race, the watcher continues — it does not crash and does not retry.

**Gemini Flash rate limits.** The trivial commit filter and exponential backoff with 3 retries handle burst scenarios, but a developer making 100 commits in rapid succession (e.g. a rebase) may exhaust the rate limit and lose some Decision synthesis. Lost synthesis is not a data loss — the commits are still in git. `memex review` can be used to manually record decisions from commits that were not synthesised.

**`memex review` is not a blocking gate.** Unvalidated decisions are served to agents at reduced confidence. A team that never runs `memex review` still gets a functioning system — they just get lower-confidence decisions in context. This is intentional. A blocking gate would make the watcher unusable in CI or automated environments.

---

*memex — inspired by Vannevar Bush's 1945 concept of a machine that remembers everything.*
