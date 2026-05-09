# memex

> "A memex is a device in which an individual stores all his books, records, and communications, and which is mechanized so that it may be consulted with exceeding speed and flexibility." — Vannevar Bush, 1945

**memex** is a developer context continuity system. It builds and serves a temporal knowledge graph of your codebase to AI coding agents, ensuring they understand not just *what* the code is now, but *why* it changed over time.

## 🚀 Current Status: Phase 1 Complete
Phase 1 (Graph Writes) is fully implemented. The system can now:
- **Extract Symbols**: Automatically detect and parse functions, classes, and structs using `tree-sitter`.
- **Synthesize Decisions**: Use Gemini 2.0 Flash to identify architectural decisions from commit messages.
- **Temporal Persistence**: Store everything as a time-aware knowledge graph using `Graphiti` and `Neo4j`.

## 🛠 Tech Stack
- **Language**: Python 3.11+ (managed by `uv`)
- **Graph Database**: Neo4j 5.26 (Dockerized)
- **Knowledge Graph**: [Graphiti](https://github.com/getzep/graphiti)
- **AI Models**: Gemini 2.0 Flash (Synthesis), Text-Embedding-004 (Semantics)
- **Parsing**: Tree-sitter

## 📦 Getting Started

### Prerequisites
- [uv](https://github.com/astral-sh/uv)
- Docker & Docker Compose
- Gemini API Key

### Installation
1. Clone the repo.
2. Setup environment:
   ```bash
   cp config.yaml.example .env
   # Edit .env with your GEMINI_API_KEY
   ```
3. Install dependencies:
   ```bash
   uv sync
   ```
4. Start Neo4j:
   ```bash
   docker-compose -f docker/docker-compose.yml up -d
   ```

### Running Tests
```bash
uv run pytest tests/test_extractor.py
uv run pytest tests/test_pipeline_e2e.py
```

## 🗺 Roadmap
- [x] **Phase 1**: Graph Writes (Skeleton & Pipeline)
- [ ] **Phase 2**: Watcher Daemon & CLI (Automation)
- [ ] **Phase 3**: MCP Read Tools (Context serving)
- [ ] **Phase 4**: MCP Write Tools & Polish (Graph-aware editing)
