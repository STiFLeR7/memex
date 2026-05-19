<div align="center">

# memex

### Persistent memory for your AI coding agents.

memex builds a temporal knowledge graph of your codebase and serves it to any MCP-compatible agent — so every session starts knowing your architecture, your decisions, and your open problems. No more cold starts. No more context pasting.

[![PyPI](https://img.shields.io/pypi/v/memex-mcp?v=0.3.2)](https://pypi.org/project/memex-mcp/)
[![PyPI downloads](https://img.shields.io/pypi/dm/memex-mcp)](https://pypistats.org/packages/memex-mcp)
[![npm](https://img.shields.io/npm/v/stifler-memex-mcp?v=0.3.2)](https://www.npmjs.com/package/stifler-memex-mcp)
[![npm downloads](https://img.shields.io/npm/dm/stifler-memex-mcp)](https://www.npmjs.com/package/stifler-memex-mcp)
[![Claude Code marketplace](https://img.shields.io/badge/Claude%20Code-marketplace-7c3aed)](https://github.com/STiFLeR7/claude-plugins)
[![GitHub stars](https://img.shields.io/github/stars/STiFLeR7/memex?style=flat)](https://github.com/STiFLeR7/memex/stargazers)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/github/actions/workflow/status/STiFLeR7/memex/publish.yml?branch=master&label=tests)](https://github.com/STiFLeR7/memex/actions)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

![memex banner](https://raw.githubusercontent.com/STiFLeR7/memex/master/assets/memex.png)

</div>

---

## Why memex?

Every AI agent session starts blind. You re-paste files, re-explain architecture, watch the agent rediscover a refactor you finished last month. **memex fixes that.**

It runs quietly in the background, learning your codebase from every commit. When an agent connects, it already knows what's there.

```text
Without memex                          With memex
─────────────                          ──────────
You: "We use Postgres, not MySQL"      Agent: I see you're using Postgres
You: "Auth lives in services/auth"     Agent: Editing services/auth/jwt.py...
You: "We decided on JWT last week"     Agent: Continuing the JWT decision
You: "Don't touch the migrations"      Agent: Skipping migrations/ (locked)
```

---

## How it works

```text
   ┌──────────────────┐     ┌───────────────┐     ┌─────────────┐
   │ Your Repository  │ ──► │ memex Watcher │ ──► │   Neo4j     │
   │  files + git     │     │  tree-sitter  │     │  knowledge  │
   │                  │     │  + Gemini     │     │    graph    │
   └──────────────────┘     └───────────────┘     └──────┬──────┘
                                                          │
   ┌──────────────────┐     ┌───────────────┐            │
   │    AI Agent      │ ◄── │  MCP Server   │ ◄──────────┘
   │ Claude · Gemini  │     │  stdio / HTTP │
   │  Codex · others  │     │               │
   └──────────────────┘     └───────────────┘
```

1. **Watcher** uses tree-sitter to extract symbols and Gemini Flash to synthesize architectural decisions from your commits.
2. **Graph** stores everything in Neo4j via [Graphiti](https://github.com/getzep/graphiti) — bitemporally, so every fact has a "true from" and "true until."
3. **MCP server** exposes 14 tools any agent can call to read context or write back its own observations.

---

## Quickstart

### The fastest path — Claude Code marketplace

```text
/plugin marketplace add STiFLeR7/claude-plugins
/plugin install memex-mcp@stifler-marketplace
```

That's it. The plugin wires up the MCP server so every Claude Code session has memex's 14 tools. You still need Neo4j + a Gemini key locally (see below); the plugin only handles the agent-side wiring.

### Manual install

> **Prerequisites:** Python 3.11+, [uv](https://github.com/astral-sh/uv), Docker, a Gemini API key.

```bash
# 1. Start Neo4j
docker compose -f docker/docker-compose.yml up -d

# 2. Create .env in your project root
cat > .env <<EOF
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=memex-local
GEMINI_API_KEY=your-key-here
EOF

# 3. Initialize and start watching
npx stifler-memex-mcp init --repo .
npx stifler-memex-mcp watch --repo .

# 4. In a new terminal, serve the context
npx stifler-memex-mcp serve --repo .
```

That's it. Your agent now has memory.

**Install options:**

| via | command |
|----|---------|
| Claude Code marketplace | `/plugin marketplace add STiFLeR7/claude-plugins` then `/plugin install memex-mcp@stifler-marketplace` |
| npx (no install) | `npx stifler-memex-mcp ...` |
| uv | `uv add memex-mcp` |
| pip | `pip install memex-mcp` |
| source | `git clone github.com/STiFLeR7/memex && uv sync` |

---

## Connect your agent

<details>
<summary><b>Claude Code</b> (one-liner via marketplace)</summary>

Easiest — install the plugin and you're done:

```text
/plugin marketplace add STiFLeR7/claude-plugins
/plugin install memex-mcp@stifler-marketplace
```

Or wire it manually in `.claude/settings.json`:
```json
{
  "mcpServers": {
    "memex": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "stifler-memex-mcp", "serve", "--repo", "."]
    }
  }
}
```
</details>

<details>
<summary><b>Gemini CLI</b></summary>

Add to `~/.gemini/settings.json`:
```json
{
  "mcpServers": {
    "memex": {
      "command": "npx",
      "args": ["-y", "stifler-memex-mcp", "serve", "--repo", "."]
    }
  }
}
```
</details>

<details>
<summary><b>Codex</b></summary>

Add to `~/.codex/config.toml`:
```toml
[mcp_servers.memex]
command = "npx"
args = ["-y", "stifler-memex-mcp", "serve", "--repo", "."]
```
</details>

<details>
<summary><b>Claude's built-in memory tool</b> (v0.3.0+)</summary>

memex can back Anthropic's native `memory_20250818` tool — Claude reads from a per-session graph projection plus a writable scratch zone.

```bash
memex memory-tool serve --repo .                     # in-process (Python)
memex memory-tool serve --repo . --transport http    # FastAPI on :7464
```

```python
from memex.memory_tool import MemexAsyncMemoryTool
memory_tool = MemexAsyncMemoryTool(repo_root=".")
client.beta.messages.run_tools(..., tools=[memory_tool])
```

Run alongside the MCP server for full coverage.
</details>

---

## What agents can do with memex

memex exposes **14 MCP tools** — eight for reading context, four for writing observations back, and two for analysis.

### Reading context

| Tool | When |
|------|------|
| `get_project_context` | At session start — get the lay of the land |
| `get_symbol_context` | Before editing a function or class |
| `get_recent_decisions` | To see what's changed architecturally |
| `get_open_problems` | To find active bugs and tech debt |
| `search_context` | Hybrid search across the whole graph |
| `get_stale_context` | Spot outdated documentation |
| `explain_change` 🆕 | After a commit — *why* did this happen? |
| `predict_impact` 🆕 | Before a refactor — *what* might break? |

### Writing observations

| Tool | When |
|------|------|
| `record_decision` | After making a technical choice |
| `record_problem` | When you discover a bug or piece of debt |
| `resolve_problem` | When a tracked problem is fixed |
| `invalidate_edge` | When a fact is no longer true |

Agents write back into the graph as they work. Next session, that knowledge is there.

---

## Highlights

- **🧠 Bitemporal memory.** Every fact has a creation time and an optional invalidation time. The graph never forgets — but it knows what's still current.
- **📉 Two-regime confidence decay.** Validated facts decay slowly; unvalidated ones cross the staleness threshold at exactly 30 days. Old hallucinations lose their grip naturally.
- **🤝 Human-in-the-loop.** `memex review` opens a TUI to validate machine-synthesized decisions, lowest-confidence first.
- **🔒 Write governance.** Per-node-type ACL, intent-confirmation on agent writes, and explicit `corroborates` / `supersedes` semantics — no silent duplicates.
- **🔍 Composite retrieval.** Hybrid search × recency × confidence × rehearsal, with the score breakdown surfaced so agents see *why* a result ranked where it did.
- **🌐 Multi-repo aware.** Single watcher and MCP server can manage hundreds of repos with zero-config switching.
- **🖥️ Visual graph.** `memex graph --output graph.html` produces a self-contained D3 force layout with cluster overlays.
- **🧩 Hierarchical clusters** *(v0.3.1)*. `memex cluster` runs Leiden over a hybrid edge graph (directory co-location + module imports + symbol calls) and groups your codebase into named clusters. `get_project_context()` now returns a cluster-level briefing by default — stays under 1500 tokens whether your repo has 50 modules or 5000. Cluster IDs pin across re-runs via Jaccard ≥ 0.5 so renames are stable; `.memex/clusters.yaml` lets you override any assignment.

---

## How the graph works

The knowledge graph is **bitemporal**: every relationship knows when it was created and when (if ever) it became invalid. Agents can ask *what was true on day X* and get a real answer.

Confidence isn't a stored number that mutates — it's **computed at query time** from a node's `base_confidence`, validation status, and time since last reinforcement. Two regimes:

- **Validated** (a human said "yes, this is real"): half-life ~139 days.
- **Unvalidated** (the watcher synthesized it from a commit): crosses the staleness threshold (0.3) at **exactly 30 days**.

When an agent records a decision in one session, the next session — yours or someone else's — starts with that knowledge. The graph compounds.

---

## Project structure

```
memex/
├── memex/
│   ├── extractor/        # tree-sitter + lockfile parsers
│   ├── graph/            # Neo4j writes, confidence, archive
│   ├── synthesizer/      # Gemini Flash → Decision nodes
│   ├── mcp_server/       # 14 MCP tools (read + write + analytic)
│   ├── memory_tool/      # Anthropic memory_20250818 adapter
│   ├── watcher/          # daemon + git hooks
│   └── cli.py            # init / watch / serve / review / graph / cluster
├── tests/                # 332 tests, ~93% coverage
├── docker/               # Neo4j compose
└── npm/                  # npx wrapper
```

---

## Contributors

<table>
<tr>
<td align="center">
<a href="https://github.com/STiFLeR7"><b>Hill Patel</b></a><br/>
<sub>Architect &amp; Project Lead</sub>
</td>
<td align="center">
<a href="https://github.com/Nirvaan05"><b>Nirvaan Lagishetty</b></a><br/>
<sub>Lead Contributor</sub>
</td>
</tr>
</table>

Want to contribute? Open an issue or a PR. The codebase is well-tested and welcoming — `uv sync` and `uv run pytest tests/` is all the setup you need.

---

## License & inspiration

MIT — free to use, modify, and ship.

> *Vannevar Bush, 1945:* "Consider a future device for individual use, which is a sort of mechanized private file and library. It needs a name, and to coin one at random, **memex** will do."

This is a small step toward that idea, applied to the context an AI agent needs to work effectively inside a codebase.
