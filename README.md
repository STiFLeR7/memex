<div align="center">

# memex

**A developer context continuity system that builds and maintains a temporal knowledge graph of your codebase.**

[![Phase 4 Complete](https://img.shields.io/badge/Phase-4_Complete-green?style=for-the-badge)](https://github.com/STiFLeR7/memex)
[![Version](https://img.shields.io/badge/version-v0.1.0-blue?style=for-the-badge)](https://github.com/STiFLeR7/memex)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)

![memex banner](assets/memex.png)

> *Inspired by Vannevar Bush's 1945 concept of a machine that remembers everything — memex is a developer context continuity system. It watches your repo, builds a temporal knowledge graph of your codebase, and serves it to any AI coding agent via MCP — so every agent session starts knowing your architecture, your recent decisions, and your open problems. Automatically. Without any manual context pasting.*

</div>

---

## The problem

Every time you open a new agent session — Gemini CLI, Claude Code, Codex — the agent starts blind. You find yourself re-explaining architecture decisions, pasting the same core files, and watching the agent rediscover refactors you finished last month. This cycle wastes tokens, adds friction, and prevents AI agents from becoming deep collaborators. memex eliminates this "cold start" problem by providing agents with a persistent, evolving memory of your project's history and rationale.

## How it works

```text
┌──────────────────┐      ┌──────────────┐      ┌─────────────┐
│ Your Repository  │ ───► │ memex Watcher│ ───► │    Neo4j    │
│ (Files + Git)    │      │ (Tree-sitter)│      │ (Knowledge  │
└──────────────────┘      └──────────────┘      │    Graph)   │
                                                └──────┬──────┘
                                                       │
                                                       ▼
┌──────────────────┐      ┌──────────────┐      ┌─────────────┐
│    AI Agent      │ ◄─── │  MCP Server  │ ◄────┤  Graphiti   │
│ (Gemini/Claude)  │      │  (stdio)     │      │   Engine    │
└──────────────────┘      └──────────────┘      └─────────────┘
```

memex runs a background watcher that uses tree-sitter to extract structured symbols and Gemini Flash to synthesize technical decisions from git commits. This data is woven into a temporal knowledge graph powered by Graphiti and Neo4j, which maintains a high-fidelity record of how your code evolves. Agents connect to this graph via a standard Model Context Protocol (MCP) server, allowing them to query context on demand rather than requiring manual file-pasting.

## Quickstart

**Prerequisites**: Python 3.11+, [uv](https://github.com/astral-sh/uv), Docker, and a Gemini API Key.

```bash
# 1. Clone and install
git clone https://github.com/STiFLeR7/memex.git
cd memex
uv sync

# 2. Start Neo4j infrastructure
docker-compose -f docker/docker-compose.yml up -d

# 3. Configure environment
cp config.yaml.example .env
# Edit .env with your GEMINI_API_KEY and NEO4J_PASSWORD

# 4. Initialize and watch your repo
memex init --repo .
memex watch --repo .

# 5. Serve the MCP context (in a new terminal)
memex serve --repo .
```

## Connecting your agent

### Gemini CLI
Add to `~/.gemini/settings.json`:
```json
{
  "mcpServers": {
    "memex": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/memex", "memex", "serve", "--repo", "."]
    }
  }
}
```

### Claude Code
Add to `.claude/settings.json`:
```json
{
  "mcpServers": {
    "memex": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--project", "/path/to/memex", "memex", "serve", "--repo", "."]
    }
  }
}
```

### Codex
Add to `~/.codex/config.toml`:
```toml
[mcp_servers.memex]
command = "uv"
args = ["run", "--project", "/path/to/memex", "memex", "serve", "--repo", "."]
```

## MCP tools

### Read Tools (Context Retrieval)
| Tool | When to call it | Returns |
|------|-----------------|---------|
| `get_project_context` | At session start to get a project overview. | Markdown briefing of modules, decisions, and debt. |
| `get_symbol_context` | Before editing a specific function or class. | Signatures, callers, callees, and linked history. |
| `get_recent_decisions` | To understand recent architectural shifts. | Chronological list of tech decisions and rationales. |
| `get_open_problems` | To find technical debt or active bugs. | List of problems sorted by severity (Critical → Low). |
| `search_context` | For broad discovery across all node types. | Hybrid search results (semantic + keyword + graph). |
| `get_stale_context` | To identify potentially outdated documentation. | Report of edges with low confidence scores. |

### Write Tools (Graph Compounding)
| Tool | When to call it | Returns |
|------|-----------------|---------|
| `record_decision` | After making a technical or architectural choice. | Confirmation with the new Decision ID. |
| `record_problem` | When discovering a bug or technical debt item. | Confirmation with the new Problem ID. |
| `resolve_problem` | When a tracked problem has been fixed. | Confirmation of closure and session link. |
| `invalidate_edge` | When identifying a stale or incorrect fact. | Confirmation of edge invalidation. |

## How the graph works

The memex knowledge graph is built on a bitemporal model, meaning every relationship has both a creation time and an optional invalidation time. This allows the system to store a complete history of the codebase, enabling agents to query what was true at any point in time. To ensure the context remains relevant, a nightly decay scheduler reduces the confidence of information that hasn't been recently verified or interacted with. This "forgetting" mechanism prevents old documentation from cluttering agent context while still preserving it in the historical graph. Because the system is bidirectional, agent observations compound over time; if an agent records a decision in one session, every subsequent agent session automatically starts with that knowledge.

## Project status
Phase 4 complete. v0.1.0. Actively developed context continuity system for the era of AI engineering.

## Inspiration
Vannevar Bush's 1945 essay "As We May Think" described the memex as a device that stores all of a person's knowledge, cross-referenced and associative. This project is a small step toward that idea, applied to the context an AI agent needs to work effectively inside a codebase.

## License
MIT - [STiFLeR7](https://github.com/STiFLeR7)
