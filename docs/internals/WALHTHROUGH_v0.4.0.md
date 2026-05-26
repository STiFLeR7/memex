# memex v0.4.0 — Internal Walkthrough

This document outlines the changes implemented for **memex v0.4.0** (Theme: **Proof**), covering **Pillar 1: Context Cost Telemetry** and **Pillar 2: Confidence-Weighted Write Discipline**.

---

## 1. Pillar 1: Context Cost Telemetry

Telemetry provides concrete, provable metrics of token savings across agent sessions.

### Storage & Schema
- **Database Path**: A local user-global SQLite database is initialized at `~/.memex/telemetry.db`.
- **Concurrency Safety**: Configured in WAL (Write-Ahead Logging) mode to prevent lock contention between concurrent agent sessions.
- **Table Definition**:
  ```sql
  CREATE TABLE IF NOT EXISTS tool_calls (
      id               INTEGER PRIMARY KEY,
      tool_name        TEXT    NOT NULL,
      called_at        TEXT    NOT NULL,   -- ISO 8601 UTC timestamp
      repo_path        TEXT    NOT NULL,   -- absolute repository path
      agent            TEXT    NOT NULL DEFAULT 'unknown',
      tokens_returned  INTEGER NOT NULL,
      tokens_naive     INTEGER,            -- naive token estimate
      tokens_saved     INTEGER             -- tokens_naive - tokens_returned
  );
  ```

### Naive Token Estimation
- **Scoped Tools**: Stats the actual files involved using `os.stat()` without reading content (`Σ file_size // 4`). Includes [get_project_context](file:///D:/memex/memex/mcp_server/tools_read.py), [get_symbol_context](file:///D:/memex/memex/mcp_server/tools_read.py), and `get_open_problems`.
- **Unscoped Tools**: Applies custom fixed multipliers to `tokens_returned` (e.g., multiplier of 12 for `search_context`, 5 for `get_recent_decisions`) wrapping calls in `OSError` safety fallbacks.

### Agent Harness Detection
- Sniffs environment variables (`CLAUDE_CODE`, `GEMINI_CLI`, etc.) and reads `clientInfo.name` from the MCP `initialize` request context to record which agent client made the call.

### Server & CLI Interface
- **HTTP Endpoint**: Added authenticated `GET /stats` endpoint to [http.py](file:///D:/memex/memex/mcp_server/http.py).
- **CLI Subcommand**: Added `memex stats` subcommand in [cli.py](file:///D:/memex/memex/cli.py).
- **VS Code Extension**: Integrated connection status and display of today's saved token counts directly in the VS Code status bar.

---

## 2. Pillar 2: Confidence-Weighted Write Discipline

Ensures that the temporal knowledge graph maintains high data integrity by decaying stale unvalidated nodes while reinforcing corroborated nodes.

### Three-Regime Computed Confidence Model
Defined in [confidence.py](file:///D:/memex/memex/graph/confidence.py) (never stored as mutating property):
1. **Regime 1 (Validated)**: `validated = True`. Decays slowly at `LAMBDA_VALIDATED = 0.005`, but is hard-floored at `VALIDATED_FLOOR = 0.7`.
2. **Regime 2 (Unvalidated New, <= 30 days)**: Standard decay rate (`LAMBDA_UNVALIDATED = log(2)/30`).
3. **Regime 3 (Unvalidated Old, > 30 days)**: Accelerated decay rate (`LAMBDA_UNVALIDATED_OLD = log(2)/20`), hard-capped at `UNVALIDATED_OLD_CAP = 0.5`.

### Nightly Staleness Sync
- Refactored `_STALE_REFRESH_QUERY` in [decay.py](file:///D:/memex/memex/graph/decay.py) to mirror the exact three-regime decay calculations inside the Cypher query.

### Two-Pass Corroboration Pipeline
Implemented in [handlers.py](file:///D:/memex/memex/watcher/handlers.py):
- **Pass 1 (File Match)**: Scans for unvalidated decisions whose linked module matches at least one changed file in the commit event.
- **Pass 2 (Semantic Similarity)**: Computes the cosine similarity between the commit message and the decision text embeddings. If the similarity is `>= 0.6`, it marks the decision as `corroborated = True` and updates `last_reinforced_at = now`.

### Hardened Write UUID Retrieval
- Added fallback lookup logic inside [writer.py](file:///D:/memex/memex/graph/writer.py). If Graphiti returns `None` for the node UUID upon `add_episode`, it performs a name-matching query to retrieve the identifier. Raises `MemexWriteError` if both return `None`.

### Write Governance and Scoped Contradiction Checks
- **Supersedes Target Check**: Verifies that the specified target UUID exists in Neo4j before permitting a write. Returns `Error: supersedes target '<id>' not found` on failure.
- **Module-Scoped Contradiction**: Scopes the contradiction checks to decisions matching the same module path (or no module if unspecified) to prevent cross-module interference.

---

## 3. Verification & Testing

All unit, mock, and property-based tests are passing cleanly:
- **Property-based tests** (`tests/test_confidence.py` using `hypothesis`): 24 tests passed.
- **Unit and Mock tests** (`tests/test_write_discipline.py`): 6 tests passed.
- **Telemetry tests** (`tests/test_telemetry.py`): 6 tests passed.
- **Corroboration tests** (`tests/test_corroboration.py`): 3 tests passed.
- **Reranker and Archive tests**: Updated to align with the new decay boundaries.

```bash
uv run pytest -m "not integration"
```
**Status: 378 passed, 1 skipped, 9 deselected**
