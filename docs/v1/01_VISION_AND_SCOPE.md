# Vision, scope and requirements

Status: approved product direction; baseline v1 requirements. Date: 3 October 2026.

## Product promise

**Memex keeps an agent's engineering context current as the code changes.**

Delivered context is a working set maintained against explicit repository evidence. Memex tracks what a session received, detects changed support, revalidates relevant assertions and sends small corrections at action boundaries. The agent owns its reasoning and patch; Memex owns the bounded evidence and delivery contract.

The supported statement is "this evidence was checked in view V at generation G." It is not "the entire repository is understood" or "the model cannot make a mistake." Correct source hashes establish source identity, not semantic truth. Existing confidence decay is not an evidence-freshness check.

## Flagship user journey

Claude Code receives an API constraint and begins a change. Codex changes the supporting API in the same checkout. Claude's next affected edit is held for reconsideration, with the old assertion identified and current evidence supplied. An unrelated action proceeds without interruption. In separate worktrees, Codex's unmerged change does not become Claude's local truth; integration into Claude's view triggers revalidation.

This makes concurrent-agent behavior part of the product, rather than an extension after v1 ships.

## Requirements

| ID | Required behavior | Acceptance boundary |
| --- | --- | --- |
| R01 | Bind context to a repository and worktree view, including content and completed-index generations | No implicit "latest across all branches" lookup |
| R02 | Recover ordered, idempotent source changes and reconcile removed structure | Body-only edits, zero-call files, restart and event loss tests |
| R03 | Track explicit necessary/alternative evidence supports and derived-claim dependencies | Changed support triggers revalidation, not blind global retraction |
| R04 | Separate structural facts, inferred explanations and approved constraints | No automatic authority promotion; time/revision applicability checked |
| R05 | Maintain bounded task subscriptions and session-specific delivered revisions | One session's acknowledgement never suppresses another's correction |
| R06 | Supply material corrections before the affected action executes, with model reconsideration | Native-client trace must prove ordering |
| R07 | Isolate concurrent worktrees and correctly process integration into a view | Unmerged branch claims cannot overwrite another branch's current facts |
| R08 | Detect shared-checkout drift and offer explicitly negotiated guarded writes | Advisory mode never advertises atomic overwrite prevention |
| R09 | Bound selection, corrections, propagation and repeated interruption | Correction overflow requests resynchronization rather than truncation |
| R10 | Surface unavailable, incomplete or unsupported freshness and bounded retries | Missing service never becomes a successful/current context receipt |
| R11 | Preserve Hermes/MCP and provide reversible v0.9 migration | Legacy unverified claims stay distinguishable from verified v1 claims |
| R12 | Join evidence revisions, delivery, action attempts and objective outcomes | Exposure, acknowledgement and compliance are separate observations |
| R13 | Demonstrate value over fresh retrieval and hash-bound notes at comparable resources | Preregistered paired evaluation with negative results published |
| R14 | Make local setup, diagnostics and native host integrations reproducible | Independent-maintainer install and continued-use evidence |
| R15 | Preserve authorization, explicit promotion and minimal capture | Repo labels do not substitute for permissions; no transcript mirroring |

## Included in v1

Python is the initial structural and predicate slice. Unsupported languages can use content-hash evidence and explicit claims, with unsupported structural coverage reported. Two native integrations, Claude Code and Codex, are required for the concurrency gate; Hermes's proven pretask path remains compatible even if its host cannot supply the live action loop. MCP is a projection and explicit fallback, not the universal delivery mechanism.

The release must handle one shared repository with multiple worktrees, two sessions sharing one checkout, session continuation/compaction, indexing lag, branch changes, and unavailable services. It must support human-approved constraints without confusing their applicability with the implementation files they govern.

## Excluded

No datastore replacement, universal semantic oracle, autonomous coding planner, generic personal-memory service, transcript ingestion, automatic patch merging or background model call on every keystroke. No claim that a selected test subset replaces full regression testing. No distributed consensus service or hard locks over arbitrary external writers. These exclusions keep the work centered on evidence maintenance.

## Product success and stop condition

The decisive outcome is fewer incorrect actions caused by context that became stale after delivery. Retrieval answer scores and notification counts are supporting metrics only. If a fresh per-action query or a hash-bound note matches the complete runtime on this outcome at comparable total cost, the additional graph/runtime machinery is not justified as the defining v1 contribution.
