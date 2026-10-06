# Dependency-ordered v1 roadmap

Status: P1/P2 complete and verified 4 October 2026; P3 complete, verified and twice hardened 5 October 2026 on one host; P4 complete and verified 5 October 2026 on the corrected code, with both hosts live; P5 executed 6 October 2026 with a negative efficacy result and failed latency gates, so v1 is not released ([24](24_PHASE5_RELEASE_READINESS.md)). No calendar or staffing estimate is asserted. Phase exits require evidence. Detailed first-wave tasks are in [09_FIRST_WAVE_IMPLEMENTATION_PLAN.md](09_FIRST_WAVE_IMPLEMENTATION_PLAN.md).

## Dependency sequence

`P0 planning â†’ P1 trustworthy views â†’ P2 evidence validity â†’ P3 native action loop â†’ P4 concurrent clients â†’ P5 efficacy and release`

Fixture design and host capability spikes can occur alongside P1. Do not build dependent delivery behavior against unstable identity/support contracts. Produce each later wave's detailed test-first implementation plan at its entry gate, with source-verified transaction/host APIs; the acceptance requirements below are already fixed.

## P1: trustworthy repository views

| Work | Ownership / deliverable | Dependencies | Required evidence |
| --- | --- | --- | --- |
| W01 | `context/revision.py`: content identity and completed-index invariants | P0 | View identity stable under index progress; worktrees distinct; unborn HEAD |
| W02 | Extractor/handler: detect body edits and refresh structure independent of signatures | P0 | Existing and new local regression tests |
| W03 | Graph writer/indexing: reconcile symbols/calls/imports atomically within file/view, including empty contributions | W01,W02,S03 | Removed last edge; invalid parse retains unknown coverage; graph transaction evidence |
| W04 | Runtime journal/watcher: durable idempotent event processing, completion records, catch-up | W01 | Crash after graph commit/before acknowledgement; rapid commits; missed file event |
| W05 | Resolver/hook registration: linked worktrees, scoped writes, existing-hook composition | W01,W04 | `.git` file, detached/unborn HEAD, case/path aliases, `core.hooksPath` |

P1 exit: no action can receive a v1 fresh certificate from partial structural work; a body edit/removal/restart/checkout change resolves to coherent scoped state. W01/W02 are the first executable wave. W03â€“W05 are planned with precise transaction APIs after spike S03. An unrelated Neo4j schema or extractor rewrite is outside scope. P1 uses additive view-scoped structural records on the existing Neo4j substrate.

**P1 exit evidence:** [14_PHASE1_VERIFICATION.md](14_PHASE1_VERIFICATION.md): W01-W05 implemented, 80 phase checks passed, 655 compatibility checks passed, native graph transactions and real daemon/Git/filesystem tests, final independent review fixes verified. Enable the internal structural runtime with `MEMEX_LIVE_CONTEXT=1`. Task/action certificates and native host delivery are P2/P3, not delivered by P1.

## P2: evidence validity and task working sets

| Work | Ownership / deliverable | Dependencies | Required evidence |
| --- | --- | --- | --- |
| W06 | Runtime supports/verification: immutable revisions, per-view status, necessary/alternative support | P1 | F05,F06,F07,F16; changed hash is dirty, not automatic false |
| W07 | Runtime tasks: subscriptions, snapshot/change replay, expiry, session baseline | W06 | No lost event between subscribe/snapshot; two independent cursors; bounded retention |
| W08 | Runtime delta/action compiler: scoped corrections, budgets, idempotency and unknown coverage | W06,W07 | F01,F04,F13,F14,F15; correction overflow resynchronizes |

P2 exit (satisfied; see [16_PHASE2_VERIFICATION.md](16_PHASE2_VERIFICATION.md)): deterministic fixture can change evidence after initial delivery and obtain the right scoped correction without LLM inference or host claims. Legacy unverified knowledge remains usable only with explicit status. No fully autonomous claim promotion.

## P3: one native action loop

| Work | Ownership / deliverable | Dependencies | Required evidence |
| --- | --- | --- | --- |
| W09 | Claude Code integration and diagnostics | W08,S01 | Mutation â†’ check â†’ pending write prevented â†’ model reconsideration â†’ revised write |
| W10 | Delivery/outcome trace projection | W09 | Per-session accepted/failed receipts; objective result distinct from exposure |
| W11 | Hermes/MCP compatibility projection | W08 | Existing provider/context tests; negotiated capabilities; no transcript capture |

P3 exit (satisfied; see [18_PHASE3_VERIFICATION.md](18_PHASE3_VERIFICATION.md)): a real Claude Code 2.1.289 run received a packet, evidence changed in a file the agent was not editing, the affected mutation was prevented before it executed, the correction reached that session, and the agent reconsidered and executed a revised action that passed an objective check. The supported route is a synchronous `PreToolUse` deny; S01's measured findings and the resulting abstain policy are recorded in 18. The objective contract is evaluated against both the denied proposal and the shipped implementation, so the exit requires a semantically different patch rather than a retry. Delivery is acknowledged only on a host record that shows the packet being inserted; a hook's own stdout log is not one, at any exit code. The gate is a check, not a lock, and a hook timeout fails open.

## P4: concurrent agents and second host

| Work | Ownership / deliverable | Dependencies | Required evidence |
| --- | --- | --- | --- |
| W12 | Codex integration | W08,W10,S02 | Same native ordering evidence and version-pinned capabilities |
| W13 | Per-worktree overlays and integration propagation | P1,P2,W12 | F08,F09; no cross-view current-fact leakage |
| W14 | Shared-checkout coordination and optional supported guarded writes | W09,W12 | F10,F11; leases/fencing/expiry; explicit bypass limits |
| W15 | Session resume/compaction and repeated conflict handling | W07,W14 | F12; two-host ID collision; bounded retries; no project-wide exposure suppression |

P4 exit (satisfied on the corrected code, after the recovery corrections; see [20_PHASE4_VERIFICATION.md](20_PHASE4_VERIFICATION.md)): Claude Code and Codex ran live together in separate worktrees and in one checkout, in both orderings; each received context for its own view with independent delivery state, and each affected mutation was prevented and semantically revised before executing. Two live clients racing guarded writes lost no update and landed no stale write. Codex's supported mode is an app-server thread; hooks do not run under `codex exec`.

Original P4 exit statement: complete concurrency acceptance matrix with Claude-first and Codex-first orderings. Worktree isolation and shared-checkout context coordination are mandatory. Supported cooperating write protection is scoped and opt-in; universal arbitrary-writer locking is excluded. If native hosts cannot support guarded writes, expose that limitation and record which wrapper supplies the supported mode.

## P5: proof, migration and OSS release

| Work | Ownership / deliverable | Dependencies | Required evidence |
| --- | --- | --- | --- |
| W16 | Public fixture harness, baselines, ablations and preregistration | P2â€“P4,S04 | F01â€“F16, resource accounting, disjoint test set |
| W17 | Comparable native trials and independent-maintainer pilot | W16 | Confidence analysis, hidden tests, install/use/rework outcomes |
| W18 | Migration, rollback, diagnostics and setup recipes | P1â€“P4 | Resumable legacy backfill, downgrade behavior, existing-service recipe |
| W19 | Release report and capability documentation | W17,W18 | All mandatory gates; failures and not-run external tests disclosed |

P5 exit: preregistered mechanism/efficacy/compatibility/isolation gates satisfied and limitations match documentation. No Docker is needed for the first engineering wave; external container benchmarks remain explicitly deferred under the current constraint. No v1 claim based solely on the old eight-case ceiling benchmark.

## Ownership and review discipline

One work package has one accountable owner and an independent review boundary, whether implemented by Claude, Codex or a human. Concurrent contributors should agree on shared contract changes before implementation. W02 and journal design can progress independently after W01 is stable; do not have two agents edit the same graph transaction or adapter module in a shared checkout without a coordinated ownership boundary.

Use small reversible commits for completed deliverables. Preserve others' work. Commit references, commands/results, client versions and remaining limits are recorded at phase exits; do not invent future commit hashes or mark unrun checks passing.

## Requirement traceability

| Requirement | Work packages | Acceptance fixtures / evidence |
| --- | --- | --- |
| R01 | W01,W03,W05,W13 | View identity, F08,F09 |
| R02 | W02,W03,W04 | F01,F02,F07,F13 |
| R03 | W06,W08 | F05,F06,F16 |
| R04 | W06,W18 | F03,F06,F16, legacy authority migration |
| R05 | W07,W10,W15 | F10,F12, subscribe/snapshot race |
| R06 | W09,W12 | Native action-order traces |
| R07 | W05,W13 | F08,F09, isolated graph writes |
| R08 | W14,W15 | F10,F11, bypass/lease tests |
| R09 | W08,W15,W16 | F04,F15, retry/propagation bounds |
| R10 | W04,W08,W09,W12 | F07,F13,F14 |
| R11 | W11,W18 | Provider/MCP compatibility, migration recovery |
| R12 | W10,W16 | Receipt/outcome joins and failed-delivery evidence |
| R13 | W16,W17 | Paired trials, C/D baseline comparisons |
| R14 | W18,W19 | Independent install/use evidence |
| R15 | W06,W07,W11,W18 | Principal isolation, explicit promotion, bounded capture |
