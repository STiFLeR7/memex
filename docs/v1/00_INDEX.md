# Memex v1 planning and execution index

Planning baseline: 3 October 2026. Inspected source commit: `d7614cdaa70947fdf77d43e5235616ff73e9e7fc` on `master`; local source was read without alteration.

**Product direction: approved by the maintainer. P1/P2 implementation: complete and verified on 4 October 2026. P3: complete, verified, hardened and accepted on 5 October 2026. P4: complete and verified on 5 October 2026 on the corrected code: two recovery defects found in review were corrected, and every required native gate was rerun and passed with Claude Code 2.1.289 and codex-cli 0.157.1 live together, in both orderings. P5: executed on 6 October 2026. The confirmatory efficacy result is negative: Live Context did not beat hash-bound notes, latency failed, and v1.0.0 is not releasable (see 24).** The maintainer explicitly approved the Live Context thesis and requested the v1 roadmap and supporting execution documents. These documents define the baseline design. P1 delivers trustworthy repository views; P2 implements immutable evidence, scoped verification and durable task/action corrections; P3 delivers one native action loop on a single host; P4 adds Codex, worktree isolation, shared-checkout coordination and opt-in guarded writes. Statistical efficacy, migration and release remain P5; evaluation targets are not measured product results.

Memex v1 maintains an agent's engineering working context against changing evidence, then supplies corrections before its next relevant action. Simultaneous Claude Code and Codex sessions, both in separate worktrees and in a shared checkout, are required v1 scenarios.

## Reading order

| Document | Purpose |
| --- | --- |
| [01_VISION_AND_SCOPE.md](01_VISION_AND_SCOPE.md) | Product contract, requirements, boundaries and success conditions |
| [02_CURRENT_STATE_AND_GAPS.md](02_CURRENT_STATE_AND_GAPS.md) | Evidence from v0.9 and prerequisite defects |
| [03_ARCHITECTURE.md](03_ARCHITECTURE.md) | Components, storage responsibilities and change/delivery flow |
| [04_CONTEXT_CONTRACTS.md](04_CONTEXT_CONTRACTS.md) | Proposed identities, states, requests, deltas and receipts |
| [05_MULTI_AGENT_CONCURRENCY.md](05_MULTI_AGENT_CONCURRENCY.md) | Claude Code + Codex, worktree isolation, shared-checkout races |
| [06_HOST_INTEGRATIONS.md](06_HOST_INTEGRATIONS.md) | Adapter behavior, capability negotiation and native-client proof |
| [07_EVALUATION.md](07_EVALUATION.md) | Discriminating experiments, denominators and release gates |
| [08_ROADMAP.md](08_ROADMAP.md) | Dependency-ordered work packages, owners and phase exits |
| [09_FIRST_WAVE_IMPLEMENTATION_PLAN.md](09_FIRST_WAVE_IMPLEMENTATION_PLAN.md) | Concrete first changes, tests, commands and handoff |
| [10_MIGRATION_AND_RELEASE.md](10_MIGRATION_AND_RELEASE.md) | Legacy knowledge, opt-in rollout, recovery and OSS onboarding |
| [11_DECISIONS_AND_RISKS.md](11_DECISIONS_AND_RISKS.md) | Architectural decisions, bounded spikes and stop conditions |
| [12_RESEARCH_AND_COMPETITION.md](12_RESEARCH_AND_COMPETITION.md) | Durable primary-source research and differentiation limits |
| [13_PHASE1_EXECUTION_PLAN.md](13_PHASE1_EXECUTION_PLAN.md) | Source-verified W03-W05 implementation and phase exit |
| [14_PHASE1_VERIFICATION.md](14_PHASE1_VERIFICATION.md) | Executed tests, review fixes, decisions, setup and limits |
| [15_PHASE2_EXECUTION_PLAN.md](15_PHASE2_EXECUTION_PLAN.md) | W06-W08 implementation and review plan |
| [16_PHASE2_VERIFICATION.md](16_PHASE2_VERIFICATION.md) | Evidence-to-correction tests, bounded recovery, review fixes and P3 handoff |
| [17_PHASE3_EXECUTION_PLAN.md](17_PHASE3_EXECUTION_PLAN.md) | S01 findings and the W09-W11 implementation plan |
| [18_PHASE3_VERIFICATION.md](18_PHASE3_VERIFICATION.md) | Measured host semantics, the native ordering proof, limitations and P4 handoff |
| [19_PHASE4_EXECUTION_PLAN.md](19_PHASE4_EXECUTION_PLAN.md) | S02 consequences and the W12–W15 plan |
| [20_PHASE4_VERIFICATION.md](20_PHASE4_VERIFICATION.md) | Measured Codex semantics, two-host native gates, the write guard, limitations and P5 handoff |
| [21_PHASE5_EXECUTION_PLAN.md](21_PHASE5_EXECUTION_PLAN.md) | W16–W19 plan: histories, arms, ablations, timing and order |
| [22_PHASE5_PROTOCOL.md](22_PHASE5_PROTOCOL.md) | S04 preregistered protocol: Part A, amendments A1–A4, frozen Part B ([22_PHASE5_FROZEN.json](22_PHASE5_FROZEN.json)) |
| [23_PHASE5_VERIFICATION.md](23_PHASE5_VERIFICATION.md) | Pilot and confirmatory results, migration evidence, final validation and limitations |
| [24_PHASE5_RELEASE_READINESS.md](24_PHASE5_RELEASE_READINESS.md) | Gate-by-gate release decision: not releasable as v1.0.0 |
| [25_ONBOARDING.md](25_ONBOARDING.md) | Existing-Neo4j setup, host recipes, demos, diagnostics and rollback |
| [26_MAINTAINER_PILOT_KIT.md](26_MAINTAINER_PILOT_KIT.md) | Agent-native field pilot: automatic live/shadow crossover and counts-only report (participants pending) |

## Execution rules

- The planning request authorized documents; the subsequent execution requests authorized P1 and then P2 end to end, continuing the commit/push workflow. Implementation used an isolated branch to preserve concurrent work. No deployment or merge is claimed.
- **No Docker for now.** P1/P2 used local tests, temporary Git repositories and a temporary native Neo4j server on loopback. No paid model trials or real provider credentials were used. Planning checks alone do not authorize services.
- Preserve concurrent contributors' edits. Reinspect the current source before execution; this commit is a research baseline, not a requirement to reset the checkout.
- Phase exits require recorded evidence. A checked task, passing mocked test, emitted notification or delivery receipt does not by itself establish native-host action reconsideration.
- Keep the existing Neo4j/Graphiti knowledge substrate and Hermes/MCP compatibility. New local control state is not a second engineering-memory database.
- Use native execution unless the maintainer separately authorizes delegation. File ownership and review boundaries in the roadmap apply regardless of execution method.
- Update phase status and evidence links here when work is implemented. Do not mark a later phase complete because a precursor shipped.

## Phase status

| Phase | Status | Exit evidence |
| --- | --- | --- |
| P0: planning baseline | Complete for this documentation pass | Numbered package, source audit and traceability table |
| P1: trustworthy repository views | Complete, opt-in implementation | [80 phase checks, native graph/daemon evidence and review fixes](14_PHASE1_VERIFICATION.md) |
| P2: evidence validity | Complete, opt-in core | [38 phase checks, 118 combined checks and review regressions](16_PHASE2_VERIFICATION.md) |
| P3: one native action loop | Complete, verified and twice hardened on one host | [Discriminating native trace, 63 phase checks and 686 compatibility checks](18_PHASE3_VERIFICATION.md) |
| P4: concurrent agents and second host | Complete, verified on the corrected code | [Every native gate passed in both orderings on the corrected code; 88 phase checks and 748 compatibility checks](20_PHASE4_VERIFICATION.md) |
| P5: efficacy, migration and OSS release | Executed; **gates failed or pending, not releasable** | [Efficacy failed vs hash-bound notes (E 3/56, D 0/56); latency and tail deadline failed; precision not established; maintainers pending](24_PHASE5_RELEASE_READINESS.md) |

## How to begin

P1 execution is recorded in 13/14, P2 in 15/16, P3 in 17/18, P4 in 19/20. S01 and S02 are closed: their measured findings and design consequences are in 18 and 20. P5 is recorded in 21–26; S04 is closed. Next is a product iteration (persistent hook process, correction wording, a precision definition for resume-time corrections) followed by a new preregistered evaluation, independent installations, real-repository and external benchmarks, and Linux validation. The roadmap contains later work-package specifications; it does not pretend that every later implementation detail has already been source-verified. The owner of each later wave produces its detailed test-first implementation plan after its prerequisite interfaces are accepted, using the contracts and acceptance tests already defined here.

The distinguishing release demonstration is: the agent received valid context, supporting evidence changed while it worked, and Memex corrected the relevant assumption before the agent acted. If the full system cannot beat fresh retrieval and hash-bound notes on that condition, reduce the scope to an incremental release.

## Documentation validation

This pass checked all 13 numbered documents, 39 local file links, balanced Markdown fences, six Python examples and the R01â€“R15 roadmap mapping. The documented pure RepositoryView example was executed in memory: identity/progress, worktree separation, unborn HEAD and invalid-input checks passed. No application modules or tests were created by that check, and no native-client, graph-backed or model benchmark is claimed as run.

The repository previously ignored all `docs/`. A narrow `.gitignore` exception now exposes only `docs/v1/*.md` for normal review; other local documentation remains ignored. The original planning pass did not stage, commit or deploy the package. P1 execution subsequently committed the planning baseline and implementation on `codex/v1-phase1`; see 14 for the execution record.
