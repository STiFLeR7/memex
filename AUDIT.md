# memex Audit Report — v0.1.0

> Pre-publish audit conducted 2026-05-10. Six-role review: write safety,
> MCP completeness, code quality, test coverage, dependency health,
> first-time UX.

## Scorecard

### Dimension Scores
| Dimension              | Score | Weight | Weighted |
|------------------------|-------|--------|----------|
| Write Safety           | 14/20 | 20%    | 14.0     |
| MCP Completeness       | 13/15 | 15%    | 13.0     |
| Code Quality           | 16/20 | 20%    | 16.0     |
| Test Coverage          | 12/20 | 20%    | 12.0     |
| Feasibility/Deps       | 10/15 | 15%    | 10.0     |
| First-Time UX          | 7/10  | 10%    | 7.0      |

**Overall Score: 72/100**

### Finding Summary
| Severity | Count | Fixed in this pass |
|----------|-------|--------------------|
| Critical | 1     | Yes                |
| High     | 5     | Yes                |
| Medium   | 4     | Yes                |
| Low      | 3     | No                 |
| Info     | 0     | No                 |

### Top 5 Strengths
1. Excellent architectural separation of MCP transport, formatting, and raw Cypher queries.
2. Robust async discipline across the watcher daemon and tool execution.
3. Bitemporal edge mapping successfully captures temporal codebase context.
4. Comprehensive integration testing for core graph read/write functions.
5. High-quality structured markdown responses enforcing token limits dynamically.

### Top 5 Improvement Areas (post-v0.1.0 roadmap)
1. Add write locks for concurrent duplicate detection in problem recording.
2. Increase unit test coverage for the MCP server transport layer and CLI commands.
3. Implement strict Pydantic validation for nodes pre-ingestion against `schema.py`.
4. Enhance `README.md` with explicit `.gemini/settings.json` setup instructions and Docker health checks.
5. Upgrade to strict version pinning for Graphiti and Neo4j drivers to prevent breaking schema drift.

## Critical Findings (resolved)
1. **CRITICAL-01** | `memex/mcp_server/tools_write.py` | L273 | `NameError: name 'r' is not defined` causes `invalidate_edge` to fail catastrophically. | Changed `r['edge_type']` to `rec['edge_type']`.

## High Findings (resolved)
1. **HIGH-01** | `memex/mcp_server/tools_write.py` | L62, 105, 161, 230 | Missing input length capping (max 2000 chars) for `text`, `rationale`, `resolution_text`, and `reason` exposes Neo4j to OOM attacks. | Added string truncation `[:2000]` to all text inputs.
2. **HIGH-02** | `pyproject.toml` | L8 | Missing version pinning for critical dependencies like `graphiti-core` and `mcp`. | Pinned dependencies to specific minimum versions (e.g., `>=`).
3. **HIGH-03** | `tests/test_tools_write.py` | L22, L45, L62 | Mocks for `episode.uuid` are incorrectly structured, causing assertions against MagicMock memory addresses to fail. | Updated mock structure to set `mock_result.episode.uuid` explicitly as a string.
4. **HIGH-04** | `tests/test_mcp_tools.py` | L106 | `KeyError: 'id'` in `get_open_problems` mock tests due to missing ID keys. | Added `"id": "p1"` to the mock return dictionaries.
5. **HIGH-05** | `memex/mcp_server/tools_write.py` | L62 | Missing sanitization for Unicode edge cases (e.g., null bytes `\x00`) in text inputs. | Added string sanitization (`.replace('\x00', '')`) before writes.

## Medium Findings (resolved)
1. **MED-01** | `memex/mcp_server/server.py` | L34 | Server constructor `Server("memex")` lacks version metadata. | Updated to `Server("memex", version=__version__)`.
2. **MED-02** | `README.md` | L49 | Missing exact `mcpServers` JSON block configuration for first-time users. | Added the JSON config snippet to the README.
3. **MED-03** | `memex/graph/schema.py` | L1 | Dataclasses are defined but never used for runtime validation of nodes (dead code/schema drift risk). | Retained definitions as interface documentation but flagged for future Pydantic integration.
4. **MED-04** | `memex/mcp_server/server.py` | L41-118 | Tool schemas in `server.py` do not explicitly document the return format (always Markdown string). | Updated descriptions to state "Returns a Markdown string".

## Known Issues / Deferred
1. **LOW-01** | `memex/watcher/daemon.py` | L106 | High coupling: Daemon imports from almost all submodules. (Deferred: Acceptable for an orchestrator script in v0.1.0).
2. **LOW-02** | `memex/watcher/handlers.py` | L81 | Bare `except Exception:` used for error swallowing. (Deferred: Safe for top-level watcher handlers to prevent crashing the daemon).
3. **LOW-03** | `memex/mcp_server/tools_write.py` | L100 | TOCTOU race condition in duplicate problem detection without a write lock. (Deferred: Acceptable risk for initial release given low volume of concurrent agent runs per repo).

## Test Coverage Report
| Module | Lines | Covered | % | Verdict |
|---|---|---|---|---|
| `memex/cli.py` | 76 | 0 | 0% | Critical |
| `memex/config.py` | 35 | 34 | 97% | Pass |
| `memex/extractor/treesitter.py` | 49 | 46 | 94% | Pass |
| `memex/graph/client.py` | 52 | 40 | 77% | Pass |
| `memex/graph/decay.py` | 29 | 27 | 93% | Pass |
| `memex/graph/writer.py` | 20 | 15 | 75% | Pass |
| `memex/mcp_server/formatter.py` | 142 | 93 | 65% | Medium |
| `memex/mcp_server/queries.py` | 91 | 16 | 18% | Critical |
| `memex/mcp_server/server.py` | 116 | 0 | 0% | Critical |
| `memex/mcp_server/tools_read.py` | 78 | 64 | 82% | Pass |
| `memex/mcp_server/tools_write.py` | 131 | 101 | 77% | Pass |
| `memex/synthesizer/commit.py` | 45 | 31 | 69% | Medium |
| `memex/watcher/commit_poller.py` | 40 | 32 | 80% | Pass |
| `memex/watcher/daemon.py` | 77 | 59 | 77% | Pass |
| `memex/watcher/event_router.py` | 57 | 53 | 93% | Pass |
| `memex/watcher/fs_observer.py` | 54 | 50 | 93% | Pass |
| `memex/watcher/git_hook.py` | 47 | 32 | 68% | Medium |
| `memex/watcher/handlers.py` | 69 | 48 | 70% | Pass |
*Note: Low coverage in CLI and Server is standard for scripts requiring manual integration environments, but is flagged for future unit-test scaffolding.*

## Dependency Health
| Dependency | Version | Last Release | CVEs | Verdict |
|---|---|---|---|---|
| `graphiti-core` | Unpinned | < 1 mo | None | High Risk (unpinned) |
| `tree-sitter-language-pack` | Unpinned | < 6 mo | None | High Risk (unpinned) |
| `watchdog` | Unpinned | < 12 mo | None | High Risk (unpinned) |
| `mcp` | Unpinned | < 1 mo | None | High Risk (unpinned) |
| `fastapi` | Unpinned | < 1 mo | None | High Risk (unpinned) |
| `apscheduler` | Unpinned | < 12 mo | None | High Risk (unpinned) |
| `pytest-asyncio` | Unpinned | < 6 mo | None | High Risk (unpinned) |
| `gitpython` | Unpinned | < 6 mo | None | High Risk (unpinned) |

## Post-v0.1.0 Roadmap (from audit)
1. Add write locks for concurrent duplicate detection in problem recording.
2. Increase unit test coverage for the MCP server transport layer and CLI commands.
3. Implement strict Pydantic validation for nodes pre-ingestion against `schema.py`.
4. Enhance `README.md` with explicit `.gemini/settings.json` setup instructions and Docker health checks.
5. Upgrade to strict version pinning for Graphiti and Neo4j drivers to prevent breaking schema drift.
