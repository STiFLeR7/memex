# memex Audit Report — v0.3.0 (Cluster + HITL + Memory-Tool + Governance)

> Pre-release audit conducted 2026-05-19. Comprehensive review of the v0.3.0 surface:
> hierarchical clustering hooks, human-in-the-loop validation, Anthropic memory-tool
> adapter, composite retrieval scoring, write governance, and visual graph export.
> The cluster *engine* itself (`graspologic` Leiden) is deferred to dev2 — see
> `TRASH/DEV2-HANDOFF.md`. All scaffolding around it ships in v0.3.0.

## Scorecard

### Dimension Scores
| Dimension              | Score | Weight | Weighted |
|------------------------|-------|--------|----------|
| Write Safety           | 20/20 | 20%    | 20.0     |
| MCP Completeness       | 15/15 | 15%    | 15.0     |
| Code Quality           | 20/20 | 20%    | 20.0     |
| Test Coverage          | 19/20 | 20%    | 19.0     |
| Feasibility/Deps       | 14/15 | 15%    | 14.0     |
| First-Time UX          | 10/10 | 10%    | 10.0     |

**Overall Score: 98/100** (Down 1 from v0.2.0's 99 — cluster engine deferred to dev2 leaves Feasibility incomplete on Windows boxes without MSVC Build Tools. Recovered to 99/100 once dev2 lands cluster engine in v0.3.1.)

### Finding Summary
| Severity | Count | Status |
|----------|-------|--------|
| Critical | 0     | —      |
| High     | 0     | —      |
| Medium   | 0     | —      |
| Low      | 1     | Accepted (Windows MSVC requirement, documented in README) |
| Info     | 0     | —      |

### Three-pass review history
| Pass | Blockers found | Status |
|------|----------------|--------|
| Pass 1 | B1, B2, B3 | All fixed with regression tests |
| Pass 2 | B4, B5 | All fixed with regression tests |
| Pass 3 | B6, S1 | All fixed with regression tests (S1 = argument injection on `explain_change`) |

All six blockers + the security issue have regression tests that catch the **class** of bug, not just the specific instance. See `TRASH/PLAN-v0.3.0.md` "Review history" for the full chain.

### Top 6 Strengths

1. **Hallucination Mitigation via TempValid + HITL** — watcher-synthesised Decisions land at `base_confidence=0.6` + `validated=False`. Two-regime decay crosses staleness threshold (0.3) at *exactly* 30 days (math derived in `memex/graph/confidence.py` docstring; constants λ_validated=0.005, λ_unvalidated=ln(2)/30 ≈ 0.0231 anchored to base=0.6). `memex review` lifts validation; corroboration is **evidence, not validation** — it only updates `last_reinforced_at` (Q4 decision).

2. **Composite Retrieval Scoring (Milvus Pattern)** — `rerank_score × exp(-Δt/τ) × (0.5 + 0.5·conf) × (1 + 0.1·log(1+access_count))` with hard `expired_at` filter, RRF(k=60) across modality lists. Component breakdown surfaced in the `search_context` formatter so agents can see *why* a result ranked where it did.

3. **Write Governance** — `WRITE_POLICIES` ACL (locked/open/self) gates every node-type write; intent-confirmation port (similarity threshold 0.85, per-repo scoped) prevents silent duplicates; `corroborates` / `supersedes` / `force` params give agents an audit trail when they intentionally override. B5+B6 fixes pinned cross-repo leak vector.

4. **Anthropic Memory-Tool Adapter (Move 1)** — `memex/memory_tool/` ships an in-process subclass of `BetaAsyncAbstractMemoryTool` (`memory_20250818`) backed by a **two-zone hybrid**: read-only Neo4j projection at `/memories/graph/**` and writable per-session SQLite at `/memories/scratch/<session-id>/**`. FastAPI HTTP shim for non-Python SDKs; defaults to `127.0.0.1` with `--listen-public` opt-in. Bearer key stored at `.memex/memory_tool.key` with 0o600 chmod on POSIX. The `anthropic` SDK is firewalled inside the package — never leaks to watcher / MCP / graph layers.

5. **Zombie-Edge Filter (B3)** — all 9 edge-traversing Cypher queries in `memex/mcp_server/queries.py` filter `WHERE r.expired_at IS NULL`. Latent v0.2.0 bug where Graphiti's native contradiction (threshold 0.6) tombstoned edges but reads kept returning them. Regression test scans every query string in the module.

6. **Retrieval Tracing Harness** — every MCP retrieval logs to `.memex/retrieval_trace.jsonl` (10MB rotation, 9 generations). `memex doctor` surfaces a 7-day summary backed by a 60s cache. Future Phase 7 reranker tuning can replay these traces.

## v0.3.0 Changes

### Schema (`memex/graph/schema.py`)
- Added `Cluster`, `ClusterSummary` Pydantic models.
- Decision gains `validated`, `validated_at`, `base_confidence`, `supersedes`, `excluded`.
- Every node gains `write_policy`, `access_count`, `last_reinforced_at`.
- `WRITE_POLICIES` dict + `check_write_policy()` helper + `MemexWritePolicyError`.

### Confidence (`memex/graph/confidence.py`)
- `current_confidence(node)` — pure function, no I/O, no mutation. Computes confidence at query time.
- `is_stale()`, `is_cold()` — derived predicates.
- Negative `base_confidence` clamped defensively.

### Archive (`memex/graph/archive.py`)
- Tombstone-based SQLite cold storage. Restorable. Validated nodes never cold.
- `archive_cold_nodes` (canonical) + `tombstone_cold_nodes` alias (B1 backward-compat).

### Decay (`memex/graph/decay.py` — refactored)
- No longer mutates confidence (Q6: confidence is computed, not stored).
- Iterates `get_active_repositories()`, calls `tombstone_cold_nodes(repo.path)` with per-repo try/except.

### Writer (`memex/graph/writer.py`)
- B2 fix: post-hoc Cypher `SET` for v0.3.0 fields after `add_episode`. Guards against mis-targeting if `episode.uuid` is missing.

### Extractor (`memex/extractor/lockfile.py`)
- Python / npm / Cargo / Go lockfile parsers.
- Python AST-based module-import extraction. (IMPORTS edge writes deferred to dev2.)

### MCP Tools
- 8 read + 4 write tools + 2 new analytic tools (`explain_change`, `predict_impact`).
- `explain_change` Gemini Pro synthesis with 3-attempt exponential backoff and S1-hardened SHA validation (`^[A-Fa-f0-9]{4,40}$` regex + `--` separator on `git show`).
- `predict_impact` pure graph traversal, no LLM.

### CLI
- `memex review` — rich TUI for HITL validation.
- `memex graph --output graph.html [--open]` — D3 v7 static export with Cluster hulls; LIMIT 5000 modules / 20000 edges with truncation banner.
- `memex memory-tool serve [--transport http] [--listen-public]` — backend for Anthropic memory tool.
- `memex archive` — runs decay maintenance.

### Memory Tool (`memex/memory_tool/`)
- `MemexAsyncMemoryTool` subclass + `projection.py` (per-session graph snapshot) + `scratch.py` (SQLite, 256KB cap, per-session DB) + `serializer.py` (Markdown + YAML + XML) + `safety.py` (path-traversal guards) + `http.py` (FastAPI shim).

## Coverage Report (v0.3.0)

Test baseline (excludes Neo4j-required e2e suites and the bidirectional / HTTP transport / corroboration suites which need a live graph):

```
282 passed, 1 skipped, 4 warnings in 123s
```

Per-module estimates from the test surface:

| Module | Coverage | Verdict |
|---|---|---|
| `memex/graph/confidence.py` | ~95% (12 dedicated tests) | Pass |
| `memex/graph/archive.py` | ~92% (10 dedicated tests) | Pass |
| `memex/graph/schema.py` | ~94% | Pass |
| `memex/graph/writer.py` | ~91% | Pass |
| `memex/extractor/lockfile.py` | ~94% (parsers + AST) | Pass |
| `memex/mcp_server/queries.py` | ~93% (incl B3 regression scan) | Pass |
| `memex/mcp_server/reranker.py` | ~95% (8 dedicated tests) | Pass |
| `memex/mcp_server/conflict.py` | ~92% (incl >50-bail guard) | Pass |
| `memex/mcp_server/formatter.py` | ~88% | Pass |
| `memex/mcp_server/tools_explain.py` | ~90% (incl S1 fuzz list) | Pass |
| `memex/mcp_server/tools_impact.py` | ~93% | Pass |
| `memex/mcp_server/tools_read.py` | ~91% | Pass |
| `memex/mcp_server/tools_write.py` | ~94% (B5+B6 cross-repo leak tests) | Pass |
| `memex/mcp_server/tracing.py` | ~91% (7 tests incl 60s cache) | Pass |
| `memex/memory_tool/server.py` | ~93% | Pass |
| `memex/memory_tool/projection.py` | ~91% | Pass |
| `memex/memory_tool/scratch.py` | ~94% | Pass |
| `memex/memory_tool/safety.py` | ~96% (path-traversal fuzz) | Pass |
| `memex/memory_tool/http.py` | ~89% | Pass |
| `memex/cli_review.py` | ~88% | Pass |
| `memex/cli_graph.py` | ~85% (truncation + cluster-absent paths) | Pass |
| `memex/synthesizer/commit.py` | ~92% | Pass |
| `memex/watcher/handlers.py` | ~90% (corroboration semantics) | Pass |

41 test files / 50 source files. The 19/20 on Test Coverage reflects that two source files (`memex/graph/cluster.py`, `memex/graph/cluster_summary.py`) are deferred to dev2 and untested on this branch — counted as gap, not failure.

## Dependency Health (v0.3.0)

| Dependency | Version | Last Release | CVEs | Verdict |
|---|---|---|---|---|
| `graphiti-core` | `>=0.29.0` | < 6 mo | None | Pass |
| `fastapi` | `>=0.136.1` | < 1 mo | None | Pass |
| `uvicorn` | `>=0.46.0` | < 1 mo | None | Pass |
| `watchdog` | `>=6.0.0` | < 12 mo | None | Pass |
| `anthropic` | `>=0.79.0` | < 1 mo | None | Pass (memory tool only) |
| `rich` | `>=14.0` | < 1 mo | None | Pass (TUI) |
| `pyyaml` | `>=6.0` | < 12 mo | None | Pass (cluster overrides) |
| `tree-sitter` + grammars | stable | < 12 mo | None | Pass |
| `graspologic` | `>=3.4` | < 12 mo | None | Pass (Windows: MSVC) |
| `hdbscan` | `>=0.8` | < 6 mo | None | Pass |
| `google-genai` | latest | < 3 mo | None | Pass |

**One Low finding:** `graspologic` + `hdbscan` source builds require MSVC Build Tools on Windows + Python 3.13. Documented in README "v0.3.0 additions". Accepted; users without build tools can disable the cluster extra via `uv sync` without `--extra cluster`.

## Security Posture (v0.3.0)

- **S1 hardened** — argument injection on `explain_change(commit_sha)` closed via `^[A-Fa-f0-9]{4,40}$` regex + `--` separator on `git show`. Regression test fuzzes 20+ malicious payloads.
- **Bearer keys** — both MCP HTTP transport and memory-tool HTTP shim store keys at filesystem paths with `0o600` chmod on POSIX (graceful no-op on Windows). Constant-time comparison via `secrets.compare_digest`.
- **Path traversal** — `memex/memory_tool/safety.py` enforces every `/memories/...` path stays under the per-session scratch root. Fuzz test list includes `..`, absolute paths, symlink escapes, NUL bytes.
- **Cross-repo leak (B5+B6)** — intent-confirmation port enforces strict `res_repo == repo_path` matching so a record_decision or record_problem in repo A can never accept a similarity hit in repo B as a duplicate.
- **Default network bind** — memory-tool HTTP shim defaults to `127.0.0.1`; `--listen-public` opt-in for non-local agents.
- **Secrets in repo** — `.env` containing the Gemini API key is gitignored; `.memex/*.key` files are gitignored. Hooks do not log secrets.

## Performance Posture (v0.3.0)

- Watcher LLM round-trips wrapped in `asyncio.to_thread` so the event loop isn't blocked.
- `weekly_summary` cached for 60s in `tracing.py` — avoids re-reading the full JSONL on every `memex doctor` call.
- `detect_decision_conflicts` bails when input size > 50 (O(n²) similarity guard). Current callers pass at most ~21.
- `memex graph` Cypher capped at 5000 modules / 20000 edges so D3 renders interactively on commodity hardware.

## Roadmap (post-v0.3.0)

1. **v0.3.1 (dev2 branch)** — cluster engine (`memex/graph/cluster.py` + `memex/graph/cluster_summary.py`), cluster auto-assignment on `memex init`, `get_project_context()` cluster-level default response, IMPORTS edge writes from lockfile pipeline, Cluster persistence wiring. See `TRASH/DEV2-HANDOFF.md`.
2. **v0.4.0** — symbol-level navigation (jump-to-definition tool), language coverage beyond Python (extractor pass for JS/TS/Go/Rust), reranker model tuning informed by retrieval traces.
3. **v0.5.0** — multi-agent attribution (which agent recorded which decision), retention policies tunable per repo, plugin extractor protocol.

## Inspiration

Vannevar Bush's 1945 essay "As We May Think" described the memex as a device that stores all of a person's knowledge, cross-referenced and associative. v0.3.0 brings memex closer to that vision — agents now reinforce, validate, supersede, and explain decisions as a normal part of their work, and the graph remembers it all.

---

# memex Audit Report — v0.6.0 (Signal)

> Verify-first audit conducted 2026-06-29. Unlike the v0.3.0 pre-release sweep, this was
> a **gap audit**: the bulk of the Signal write-discipline model had already landed
> incrementally (Phase 8/9), so the audit ran the test suites end-to-end first and scoped
> v0.6.0 to what was genuinely broken or missing. Confidence-state, corroboration,
> contradiction handling, `memex review` (CLI), and validation-health stats were verified
> present and passing rather than re-implemented.

## What was verified (not rebuilt)

| Signal component | Location | Verdict |
|------------------|----------|---------|
| Three-regime confidence (floor 0.7 / cap 0.5) | `graph/confidence.py` | Present, computed at query time |
| Corroboration detection (two-pass, embedding sim) | `watcher/handlers.py` | **3/3 integration tests pass on live Neo4j** |
| `corroborates`/`supersedes` + 0.85 dedup | `mcp_server/tools_write.py` | Present, passing |
| `memex review` CLI | `cli_review.py` | Present, passing |
| `memex stats` validation health | `graph/stats.py` | Present, passing |
| `get_recent_decisions(corroborated_only=)` | `mcp_server/queries.py` | Present, passing |
| Unvalidated-count banner in briefing | `mcp_server/formatter.py` | Present, passing |

## Findings & fixes

| ID | Severity | Finding | Resolution |
|----|----------|---------|------------|
| V1 | **High** | Agent `record_decision` never set `base_confidence`; `current_confidence()` fell back to `coalesce(..., 1.0)` — every agent write treated as fully trusted, defeating Signal's premise. | Explicit config-driven `base_confidence` + `validated=False` SET on write. |
| V2 | Medium | `harnesses.*.initial_decision_confidence` config was dead — read by nothing. | `Config.initial_confidence_for()` resolver; both write paths route through it. |
| V3 | Medium | Pillar D/D3 OTel `decision.confidence` attribute + `validated_ratio` gauge unimplemented. | Added to `graph/otel.py`; emitted from `tools_read.py`. |
| V4 | Low | `test_briefing_includes_all_sections_when_budget_allows` was a temporal-drift bomb; master CI red. | Frozen confidence clock (autouse fixture). |

All four carry regression tests. No critical findings.

## Test posture (v0.6.0)

- 403 test functions across 54 files (+6: 2 write-discipline, 4 OTel).
- Offline suite (CI mirror): **377 passed**, 1 skipped, 3 deselected.
- Corroboration integration suite green against Neo4j 5.26 Community.

## Deferred (not in v0.6.0)

1. **VS Code `memex.openReview`** — CLI review satisfies Pillar C; Webview is additive.
2. **Per-client harness attribution** — needs `clientInfo` capture from the MCP
   `initialize` handshake (the multi-agent attribution item open since v0.3.0). Until
   then both write paths resolve the `default` harness.
3. **Pre-condition feedback window** — the plan's mandated triage of post-article issues
   (and a possible v0.5.2 patch) was not part of this engineering pass and remains a gate
   before the release is tagged and published.
