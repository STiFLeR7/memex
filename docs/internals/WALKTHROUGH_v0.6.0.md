# memex v0.6.0 — Internal Walkthrough

**Theme: Signal** — separating what the graph *knows* from what it is *guessing*, at the data layer.

This release closes the remaining gaps in the Signal write-discipline model. Most of
Signal had already landed incrementally (internally tagged "Phase 8/9"): the
three-regime confidence model (`confidence.py`), corroboration detection
(`watcher/handlers.py`), `record_decision` `corroborates`/`supersedes` + 0.85
duplicate detection (`tools_write.py`), the `memex review` CLI (`cli_review.py`),
`memex stats` validation health (`graph/stats.py`), `get_recent_decisions(corroborated_only=)`,
and the unvalidated-count banner in briefings. v0.6.0 was scoped by a **verify-first
audit** that ran the offline suite (mirroring CI) plus the corroboration integration
suite against a live Neo4j, then fixed only what was genuinely broken or missing.

---

## Pillar 0: Verify-first audit (what the audit found)

- Offline suite (CI mirror): **370 passed, 1 failed** before this work.
- Corroboration integration suite, live Neo4j: **3/3 passed** — the core Signal
  mechanism verified end-to-end.
- The one offline failure was a **temporal-drift test bomb**, not a code regression,
  and it had already turned master CI red.

---

## Pillar 1: Temporal-drift test fix (`tests/test_context_briefing.py`)

`test_briefing_includes_all_sections_when_budget_allows` hardcoded fixture dates
(2026-06-08/09) but scored decisions against the real wall-clock via
`current_confidence()`. `get_context_briefing` drops decisions scoring `<= 0.5`, so
once ~21 days of real time elapsed, the unvalidated fixture decision decayed below the
cutoff and the assertion failed.

**Fix:** an autouse fixture freezes `memex.graph.confidence._utc_now` to 2026-06-10, so
the intended scenario (validated 1-day-old, unvalidated 2-day-old, both above the
cutoff) is deterministic. Mirrors the `frozen_now` pattern from `test_confidence.py`
(commit 3292927). Production logic was already correct.

---

## Pillar 2: Per-harness initial confidence wiring (Signal Pillar A)

The `harnesses.*.initial_decision_confidence` config (defined in `config.py` and
`config.yaml.example`) was **dead — read by nothing**. Worse, the agent
`record_decision` path never set `base_confidence` at all, so `current_confidence()`
fell back to `coalesce(..., 1.0)` — silently treating **every** agent-written Decision
as a fully-trusted fact. That is the exact over-trust Signal exists to prevent; only
the watcher synthesis path set an explicit 0.6.

- `config.py`: `Config.harness_config()` / `Config.initial_confidence_for()` — a single
  source of truth that resolves initial confidence by harness, falls back to the
  `default` entry, and never yields an implicit 1.0.
- `mcp_server/tools_write.py`: `record_decision` now explicitly SETs `base_confidence`
  (config-driven), `validated=False`, `source='agent'`, and `last_reinforced_at` on the
  new node.
- `synthesizer/commit.py`: routes `base_confidence` through the resolver instead of
  hardcoding 0.6 (default remains 0.6 — behaviour preserved, now tunable per repo).

**Deferred:** harness *identity* is not yet threaded from the MCP `initialize`
handshake, so both paths resolve the `default` harness. Per-client differentiation
(claude-code 0.7 vs codex 0.6) lands when `clientInfo` capture is added — the same
multi-agent attribution item deferred since the v0.3.0 audit.

---

## Pillar 3: Confidence + validation health in OpenTelemetry (Signal Pillar D / D3)

The only clearly-missing Signal feature. Confidence and validation health were invisible
in the observability pipeline that already tracks token savings.

- `graph/otel.py`: `set_decision_confidence()` annotates the active span with
  `memex.decision.confidence`; `record_validated_ratio()` drives a
  `memex.decision.validated_ratio` gauge. Both no-op cleanly without the OTel SDK.
- `mcp_server/tools_read.py`: `get_recent_decisions` emits the mean computed confidence
  of returned decisions; `get_context_briefing` emits both confidence and the
  validated/corroborated ratio. Best-effort — observability never breaks a read tool.

Confidence and savings are now two axes of the same span/metrics view.

---

## Pillar 4: Release plumbing

- Versions bumped to `0.6.0`: `pyproject.toml`, `npm/package.json`, and `server.json`
  (MCP Registry manifest — server + both package entries, previously stale at 0.4.0).
- `AUDIT.md` gains a v0.6.0 section.
- The VS Code extension (`memex-vscode`) is **unchanged** at 0.4.0 — the
  `memex.openReview` Webview command was deferred. `memex review` in the terminal
  already satisfies Pillar C's acceptance criterion; the VS Code surface is additive.

---

## Test posture

- 403 test functions across 54 files (+6 this release: 2 write-discipline, 4 OTel).
- Offline suite (CI mirror): **377 passed**, 1 skipped, 3 deselected.
- Corroboration integration suite verified green against live Neo4j 5.26 Community.

---

## What v0.6.0 deliberately does not include

- VS Code `memex.openReview` Webview command (deferred — CLI review ships).
- Per-client harness attribution via MCP `clientInfo` (needs `initialize` plumbing).
- The plan's **pre-condition feedback window** (triage post-article issues, possibly a
  v0.5.2 patch) was *not* performed as part of this engineering pass and remains a gate
  before tagging/publishing the release.
</content>
</invoke>
