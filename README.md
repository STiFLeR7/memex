<div align="center">

# memex

**Developer context continuity system building a temporal knowledge graph of your codebase.**

[![Phase 3 Complete](https://img.shields.io/badge/Phase-3_Complete-green?style=for-the-badge)](https://github.com/STiFLeR7/memex)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue?style=for-the-badge&logo=python)](https://www.python.org/)
[![MCP Server](https://img.shields.io/badge/MCP-Server-orange?style=for-the-badge)](https://modelcontextprotocol.io/)

</div>

---

## 🧠 What is memex?

Every time you open a new agent session, the agent starts blind. You re-explain architecture. You watch it rediscover refactors. **memex** fixes this. 

It runs a background watcher that continuously observes your git commits and file changes, extracts structured knowledge, and builds a temporal knowledge graph. This graph is then served to any AI agent (Gemini CLI, Claude Code, Codex) via the **Model Context Protocol (MCP)**.

The result: every session starts with a live, confident, temporally-aware briefing of your codebase.

---

## 🚀 Key Features

| Capability | Description |
|------------|-------------|
| 🔍 **Deep Discovery** | 6 Read tools to query project briefings, symbol deep-dives, and technical debt. |
| 🛡️ **Resilient Watcher** | Background daemon that reacts to file saves and git commits in real-time. |
| 🕰️ **Temporal Memory** | Bitemporal knowledge graph (Graphiti + Neo4j) that tracks *how* and *why* code changed. |
| 🤖 **AI-Native** | Powered by Gemini 2.0 Flash for architectural synthesis and Text-Embedding-004 for semantics. |
| 🔌 **Agent Agnostic** | Standard MCP interface works with Gemini CLI, Claude Code, and more. |

---

## 🛠️ Architecture

```
User --> Agent (Gemini/Claude) --> MCP (memex serve) --> Neo4j (Graphiti)
                                                             ^
                                                             |
Watcher Daemon (memex watch) <-- Filesystem + Git <----------+
```

---

## 📦 Quick Start

### 1. Prerequisites
- **uv** (Python package manager)
- **Docker** (for Neo4j)
- **Gemini API Key**

### 2. Infrastructure
```bash
# Start Neo4j in Docker
docker-compose -f docker/docker-compose.yml up -d
```

### 3. Setup
```bash
# Clone and Install
git clone https://github.com/STiFLeR7/memex.git
cd memex
uv sync

# Configure environment
cp config.yaml.example .env
# Edit .env with your credentials
```

### 4. Usage
```bash
# Initialize a repository
memex init --repo .

# Start the background watcher
memex watch --repo .

# Serve context to agents via MCP
memex serve --repo .
```

---

## 🛠️ MCP Tool Surface

Agents connected to memex gain these superpowers:

- `get_project_context(scope?)`: Get up to speed on active modules, recent decisions, and open problems.
- `get_symbol_context(symbol_name, file?)`: Deep dive into specific functions/classes and their relationships.
- `get_recent_decisions(days?, module?)`: Prevent undoing architectural choices from last week.
- `get_open_problems(module?)`: See outstanding tech debt and parser-extracted TODOs.
- `search_context(query)`: Hybrid (semantic + keyword) search across the entire graph.
- `get_stale_context()`: Identify areas of the graph that need re-validation.

---

## 🗺️ Roadmap
- [x] **Phase 1**: Graph Writes (Core Pipeline)
- [x] **Phase 2**: Watcher Daemon + CLI (Automation)
- [x] **Phase 3**: MCP Read Tools (Servability)
- [ ] **Phase 4**: MCP Write Tools + Polish (Interactive Editing)

---

## 📄 License
MIT - [STiFLeR7](https://github.com/STiFLeR7)
