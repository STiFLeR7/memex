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
**Phase 1 — Graph Writes.** Focus: get Neo4j running, Graphiti connected, tree-sitter parsing, and commits populating Decision nodes. No MCP yet.

Update this section as phases complete:
- [x] Phase 1 — Graph Writes
- [x] Phase 2 — Watcher Daemon + CLI
- [x] Phase 3 — MCP Read Tools
- [x] Phase 4 — MCP Write Tools + Polish

Current status: v0.1.0 — shipped
