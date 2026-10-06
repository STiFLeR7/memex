# Changelog

All notable changes to memex are documented here.

## [1.0.0-rc.1] — 2026-10-06

Release candidate for memex v1: live engineering context for coding agents,
kept current as the code changes. Published as `1.0.0rc1` on PyPI and as
`1.0.0-rc.1` under the `next` tag on npm.

### Added

- **Claude Code and Codex adapters.** `memex v1 install claude` and
  `memex v1 install codex` add hooks to the checkout's own project
  configuration and leave global client settings alone. Each session starts
  with a bounded working set, and delivery is confirmed from the client's own
  session record.
- **Action checks and corrections.** Before an edit, memex checks whether the
  context it rests on still holds. If not, the edit is held, the agent gets a
  scoped correction, and it revises (Claude Code `Edit`/`Write`/`MultiEdit`/
  `NotebookEdit`; Codex `apply_patch` in app-server sessions).
- **Change notice on resume.** When a session resumes after the code changed,
  memex names the files whose claims changed so the agent re-reads them.
- **Hook service.** A per-user local service keeps hooks warm, so a check is
  answered in a fraction of a second instead of seconds. It listens on
  127.0.0.1 only, with a token check, stops after 15 idle minutes, and falls
  back to a one-shot run if it is unavailable. `MEMEX_HOOKD=0` turns it off.
- **Worktree-scoped views.** Durable structural views per linked worktree, so
  parallel agents in separate worktrees each see their own code.
- **Modes and rollback.** `memex v1 mode off|shadow|live`. Shadow records what
  would have been corrected without changing anything. `memex v1 rollback`
  stops memex at once.
- **Diagnostics and migration.** `memex v1 doctor` reports what is set up and
  what to do next. `memex v1 migrate` imports v0.9 knowledge additively, marked
  `legacy_unverified`.
- **Opt-in guarded writes** for cooperating writers, with fencing enforced at
  commit.
- **Field pilot kit.** `memex v1 pilot` runs a self-contained, privacy-preserving
  pilot that produces count-only reports.

### Changed

- Source capture reads HEAD from ref files, lists sources once per check, and
  caches parses by content hash.
- Fixed: delivery is confirmed exactly once under concurrent hooks, interrupted
  guarded writes are resolved before the next guarded operation, and
  structural context refreshes after body-only edits.
- The publish workflow sends pre-releases to npm's `next` dist-tag.

### Documentation

- Onboarding, host recipes and rollback: `docs/v1/25_ONBOARDING.md`.
- Design, protocols and evaluation records: `docs/v1/`.

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
