# Dependency-ordered v1 roadmap

Status: planning baseline; no calendar or staffing estimate is asserted. Phase exits require evidence. Detailed first-wave tasks are in [09_FIRST_WAVE_IMPLEMENTATION_PLAN.md](D:/memex/docs/v1/09_FIRST_WAVE_IMPLEMENTATION_PLAN.md).

## Dependency sequence

`P0 planning → P1 trustworthy views → P2 evidence validity → P3 native action loop → P4 concurrent clients → P5 efficacy and release`

Fixture design and host capability spikes can occur alongside P1. Do not build dependent delivery behavior against unstable identity/support contracts. Produce each later wave's detailed test-first implementation plan at its entry gate, with source-verified transaction/host APIs; the acceptance requirements below are already fixed.

## P1: trustworthy repository views

| Work | Ownership / deliverable | Dependencies | Required evidence |
| --- | --- | --- | --- |
| W01 | `context/revision.py`: content identity and completed-index invariants | P0 | View identity stable under index progress; worktrees distinct; unborn HEAD |
| W02 | Extractor/handler: detect body edits and refresh structure independent of signatures | P0 | Existing and new local regression tests |
| W03 | Graph writer/indexing: reconcile symbols/calls/imports atomically within file/view, including empty contributions | W01,W02,S03 | Removed last edge; invalid parse retains unknown coverage; graph transaction evidence |
| W04 | Runtime journal/watcher: durable idempotent event processing, completion records, catch-up | W01 | Crash after graph commit/before acknowledgement; rapid commits; missed file event |
| W05 | Resolver/hook registration: linked worktrees, scoped writes, existing-hook composition | W01,W04 | `.git` file, detached/unborn HEAD, case/path aliases, `core.hooksPath` |

P1 exit: no action can receive a v1 fresh certificate from partial structural work; a body edit/removal/restart/checkout change resolves to coherent scoped state. W01/W02 are the first executable wave. W03–W05 are planned with precise transaction APIs after spike S03. An unrelated Neo4j schema or extractor rewrite is outside scope.

## P2: evidence validity and task working sets

| Work | Ownership / deliverable | Dependencies | Required evidence |
| --- | --- | --- | --- |
| W06 | Runtime supports/verification: immutable revisions, per-view status, necessary/alternative support | P1 | F05,F06,F07,F16; changed hash is dirty, not automatic false |
| W07 | Runtime tasks: subscriptions, snapshot/change replay, expiry, session baseline | W06 | No lost event between subscribe/snapshot; two independent cursors; bounded retention |
| W08 | Runtime delta/action compiler: scoped corrections, budgets, idempotency and unknown coverage | W06,W07 | F01,F04,F13,F14,F15; correction overflow resynchronizes |

P2 exit: deterministic fixture can change evidence after initial delivery and obtain the right scoped correction without LLM inference or host claims. Legacy unverified knowledge remains usable only with explicit status. No fully autonomous claim promotion.

## P3: one native action loop

| Work | Ownership / deliverable | Dependencies | Required evidence |
| --- | --- | --- | --- |
| W09 | Claude Code integration and diagnostics | W08,S01 | Mutation → check → pending write prevented → model reconsideration → revised write |
| W10 | Delivery/outcome trace projection | W09 | Per-session accepted/failed receipts; objective result distinct from exposure |
| W11 | Hermes/MCP compatibility projection | W08 | Existing provider/context tests; negotiated capabilities; no transcript capture |

P3 exit: first real native-client end-to-end demonstration; the runtime sends a correction before the materially affected mutation executes. If installed host capabilities cannot achieve this, S01 selects a supported wrapper/SDK route or the phase fails. Advisory delivery is not an acceptable substitute.

## P4: concurrent agents and second host

| Work | Ownership / deliverable | Dependencies | Required evidence |
| --- | --- | --- | --- |
| W12 | Codex integration | W08,W10,S02 | Same native ordering evidence and version-pinned capabilities |
| W13 | Per-worktree overlays and integration propagation | P1,P2,W12 | F08,F09; no cross-view current-fact leakage |
| W14 | Shared-checkout coordination and optional supported guarded writes | W09,W12 | F10,F11; leases/fencing/expiry; explicit bypass limits |
| W15 | Session resume/compaction and repeated conflict handling | W07,W14 | F12; two-host ID collision; bounded retries; no project-wide exposure suppression |

P4 exit: complete concurrency acceptance matrix with Claude-first and Codex-first orderings. Worktree isolation and shared-checkout context coordination are mandatory. Supported cooperating write protection is scoped and opt-in; universal arbitrary-writer locking is excluded. If native hosts cannot support guarded writes, expose that limitation and record which wrapper supplies the supported mode.

## P5: proof, migration and OSS release

| Work | Ownership / deliverable | Dependencies | Required evidence |
| --- | --- | --- | --- |
| W16 | Public fixture harness, baselines, ablations and preregistration | P2–P4,S04 | F01–F16, resource accounting, disjoint test set |
| W17 | Comparable native trials and independent-maintainer pilot | W16 | Confidence analysis, hidden tests, install/use/rework outcomes |
| W18 | Migration, rollback, diagnostics and setup recipes | P1–P4 | Resumable legacy backfill, downgrade behavior, existing-service recipe |
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
