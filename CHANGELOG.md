# Changelog

All notable changes to memex are documented here.

## [0.9.0] — 2026-08-26

### Added

- Trusted engineering-context vertical slice for agentic software engineering.
- Bounded, structured, provenance-aware `ContextPacket` contract.
- Shared context selection for Hermes prefetch and the `get_engineering_context`
  MCP fallback.
- Read-only Hermes `MemoryProvider` integration with timeout and fail-open
  behavior.
- Objective Goal 10 evaluation across eight isolated engineering-task cases.

### Changed

- Hermes owns personal memory, raw session state, and execution state; memex
  owns repository engineering knowledge and its provenance, temporal validity,
  confidence, supersession, and task context.
- Release metadata is aligned across PyPI, npm, MCP Registry, Docker team
  deployment, and the lockfile.

### Verified

- Goal 10: 8/8 valid paired runs, 0 treatment failures, 0 treatment
  regressions, provider-scoped `GO` using OpenRouter `stealth/ox-alpha`.
- The result is a validated vertical slice and non-regression result, not a
  universal claim that memex improves every model or engineering task.

### Security and privacy

- The first Hermes integration does not ingest raw `state.db`, transcripts,
  prompts, tool results, or secrets.
- Prefetch traces are opt-in metadata only and exclude context content.

## [0.8.0] — historical

The v0.8 trust and governance work is retained in repository history and the
architecture documents. See Git tags and `docs/architecture/v0.9/` for the
verified v0.9 context integration record.
