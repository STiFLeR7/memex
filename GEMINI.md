# memex

> Inspired by Vannevar Bush's 1945 concept of a machine that remembers everything — memex is a developer context continuity system that builds and maintains a temporal knowledge graph of a codebase and serves it to any AI coding agent via MCP.

@./docs/memex-system-design.md

---

## Agent Instructions

You are building memex - a Python project. Read the system design above before doing anything else in a session.

### Execution mode
Run in full auto mode. Execute all shell commands, file operations, pytest runs, and environment variable assignments without asking for confirmation. Never pause to request permission for: `uv` commands, `pytest`, `git` operations, `docker compose`, PowerShell `$env:` assignments, file reads/writes anywhere in the repo, or Neo4j driver calls. If a command fails, fix it and retry automatically. Only stop and ask if a decision requires architectural judgement that isn't covered by this file or the system design doc.
Future releases are published automatically by pushing a git tag (git tag vX.Y.Z && git push origin --tags).
Manual twine or npm publish commands are no longer needed after v0.2.0.

### Non-negotiables
- Python 3.11+, managed with `uv`. Never use `pip install` directly.
- All async code uses `asyncio`. No threading except where watchdog requires it.
- Type hints on every function signature. No `Any` unless genuinely unavoidable.
- Never use `print()` for logging — use the stdlib `logging` module with named loggers.
- Tests live in `tests/`. Run with `uv run pytest`.

### What is in scope
- The watcher pipeline: git hooks, watchdog observer, event router, tree-sitter extractor, LLM synthesizer (Gemini Flash only), graph writer, decay scheduler.
- The MCP server: 6 read tools + 4 write tools, stdio transport first, HTTP/SSE later.
- Graphiti + Neo4j as the graph backend. No swapping these out.

### What is out of scope — do not suggest or implement
- AWS Bedrock, Hermes, any local LLM runner.
- Any frontend, dashboard, or web UI.
- Any database other than Neo4j (Community Edition, local Docker).
- Any LLM other than Gemini (Flash for synthesis, text-embedding-004 for embeddings).

### LLM calls
- Gemini Flash (`gemini-2.5-flash`) for commit → Decision extraction only.
- Gemini embeddings (`models/text-embedding-004`) via Graphiti's google-genai provider.
- tree-sitter handles all code parsing — no LLM involved in extraction.

### Repo layout (target)
```
memex/
├── memex/
│   ├── cli.py
│   ├── config.py
│   ├── watcher/
│   ├── extractor/
│   ├── synthesizer/
│   ├── graph/
│   └── mcp_server/
├── tests/
├── docker/
│   └── docker-compose.yml
├── docs/
├── config.yaml.example
├── pyproject.toml
└── GEMINI.md   ← this file
```

### Current phase
**v0.5.0 — Production-Ready (Telescope) complete.** See `docs/implementation_plan/PLAN_v0.5.0.md` for details.

Update this section as phases complete:
- [x] Phase 1 — Graph Writes
- [x] Phase 2 — Watcher Daemon + CLI
- [x] Phase 3 — MCP Read Tools
- [x] Phase 4 — MCP Write Tools + Polish
- [x] v0.2.0 — Multi-Repo + HTTP/SSE
- [x] Phase 5.9 — v0.3.0 scaffolding (schema fields, CLI subparsers, query placeholders)
- [~] Phase 6 — Cluster + lockfile + archive (Wave 1; lockfile + archive done, cluster engine deferred to dev2)
- [x] Phase 7 — Composite scoring + expired_at filter (Wave 2)
- [x] Phase 8 — HITL + TempValid decay + memex review (Wave 1)
- [x] Phase 9 — Write governance ACL + intent confirmation + explain_change/predict_impact (Wave 2)
- [x] Move 1 — Anthropic memory-tool backend adapter (Wave 2)
- [x] Phase 10 — memex graph viz + audit + release (Wave 3)
- [x] Phase 11 — Context Cost Telemetry (Wave 1 of v0.4.0)
- [x] Phase 12 — Confidence-Weighted Write Discipline (Wave 1 of v0.4.0)
- [x] Wave 1 — Streamable HTTP Transport (v0.5.0)
- [x] Wave 2 — OpenTelemetry Integration (v0.5.0)
- [x] Wave 3 — Context Quality (C-lite) (v0.5.0)

Current status: v0.5.0 — Streamable HTTP, OTel, Leiden cluster summaries, and get_context_briefing complete and verified with unit/integration tests

### v0.3.0 additions to non-negotiables
- Two new optional Gemini calls: cluster topic summarisation (Gemini Flash) and `explain_change` (Gemini Pro for synthesis quality).
- `anthropic` SDK is allowed but only inside `memex/memory_tool/` — it must not leak into the watcher, MCP server, or graph layers.
- `graspologic` and `hdbscan` are allowed dependencies despite their Windows build-tools requirement; document the requirement in the README for Windows contributors.
- Confidence is computed at query time via `memex/graph/confidence.py` — never stored as a mutating value. The nightly job does NOT decay confidence; it only refreshes the `stale` cache and tombstones cold nodes.
- All queries against Graphiti-managed edges must filter `WHERE e.expired_at IS NULL` — this is the latent v0.2.0 bug fix.
