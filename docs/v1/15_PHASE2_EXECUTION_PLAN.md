# Phase 2 execution plan

Status: executing W06â€“W08 from verified Phase 1 commit `5640128`.
Spec: [04_CONTEXT_CONTRACTS.md](04_CONTEXT_CONTRACTS.md), [03_ARCHITECTURE.md](03_ARCHITECTURE.md), [07_EVALUATION.md](07_EVALUATION.md).

## Constraints

No Docker, paid inference, native-host compliance claim, new public MCP tools, legacy automatic promotion, or unrelated refactors. Neo4j owns immutable evidence, claims and verification history. SQLite owns bounded tasks, dependency IDs, cursors, pending deliveries and idempotency metadata. Authentication and worktree/source authorization precede projection. Tests use temporary Git repos and the isolated native Neo4j server from Phase 1.

## Task 1: W06 evidence and scoped verification

Produce strict versioned records in `memex/context/live.py`, immutable graph storage in `runtime/supports.py`, and bounded deterministic evaluation in `runtime/verification.py`. Support expressions are OR-of-AND with exact evidence/revision dependencies. Hash-only edits become needs_revalidation; structural predicates may be rechecked; approval/test references retain explicit scope. Future records, cycles, parse errors, missing permissions and traversal overflow cannot certify. Supersession is explicit and scoped; inferred records cannot become approved automatically.

Write and run failing contract/support tests before code, then verify unit tests and real graph immutability/per-view checks. Expected: missing capabilities fail first; all named behaviors pass after implementation. Commit W06.

## Task 2: W07 durable task streams

Consume revision IDs and verification fingerprints from W06. Produce `runtime/tasks.py`: authenticated task lookup, bounded normalized intent and retention, independent cursors, durable pending snapshots/deltas, replay, expiry and explicit continuity tokens. Snapshot capture plus source manifest baseline closes subscribe/snapshot gaps by recapturing at every check; no ephemeral notification is required for correctness. Store dependency IDs so deterministic scan catches missed events and restarts.

Write and run failing task tests before code. Verify independent Claude/Codex cursors, failed/successful receipts, retry replay, mismatched base, expiry and concurrent SQLite managers. Expected: no lost correction or cross-session receipt. Commit W07.

## Task 3: W08 action corrections and end-to-end flow

Consume P1 indexer, W06 verification and W07 delivery state. Produce `runtime/actions.py`: authorized open_task/check_action/ack_delivery/close_task; scoped disk recapture and indexing; exact expected-hash checks; bounded corrections and full resync; durable action-attempt idempotency; max two reconsiderations; explicit unavailable coverage. Unchanged unrelated files stay quiet; known corrections take precedence over unknown coverage. Full snapshots explicitly replace earlier packets.

Write and run failing action/E2E tests before code. Test F01/F04/F05/F06/F07/F08/F10/F13/F14/F15/F16, graph outage, subscribe/check race, restart and malformed schemas. Use real Git/SQLite/Graphiti/Neo4j for evidence-to-correction flow. Expected: changed support yields scoped uncertainty/retraction/replacement tied to delivered revision; no LLM needed. Commit W08.

## Task 4: phase completion

Run the complete Phase 2 suite without skips against native Neo4j, Phase 1 integration suite, compatibility suite, lint, package build and documentation checks. Obtain one fresh whole-branch reviewer under executing-plans/requesting-code-review skills. Fix important findings with REDâ†’GREEN tests and rerun suites. Record evidence in `16_PHASE2_VERIFICATION.md`, update planning status, commit and push `codex/v1-phase2`. Stop only the owned temporary server.

## Review focus

Check immutable concurrent writes, approvals and scoped supersession, exact source/hash/test applicability, unauthorized projection, persisted delivery races and cursor ownership, empty/partial coverage, deterministic correction completeness, changed disk while graph refresh runs, replay payload integrity, retention/expiry, timeout/cancellation, propagation bound and external writer race boundaries.
