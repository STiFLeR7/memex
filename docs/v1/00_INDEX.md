# Memex v1 planning and execution index

Planning baseline: 3 October 2026. Inspected source commit: `d7614cdaa70947fdf77d43e5235616ff73e9e7fc` on `master`; local source was read without alteration.

**Product direction: approved by the maintainer. P1 implementation: complete and verified on 4 October 2026.** The maintainer explicitly approved the Live Context thesis and requested the v1 roadmap and supporting execution documents. These documents define the baseline design. P1 delivers internal repository-view/indexing contracts; remaining task/action interfaces are planned and evaluation targets are not measured product results.

Memex v1 maintains an agent's engineering working context against changing evidence, then supplies corrections before its next relevant action. Simultaneous Claude Code and Codex sessions, both in separate worktrees and in a shared checkout, are required v1 scenarios.

## Reading order

| Document | Purpose |
| --- | --- |
| [01_VISION_AND_SCOPE.md](D:/memex/docs/v1/01_VISION_AND_SCOPE.md) | Product contract, requirements, boundaries and success conditions |
| [02_CURRENT_STATE_AND_GAPS.md](D:/memex/docs/v1/02_CURRENT_STATE_AND_GAPS.md) | Evidence from v0.9 and prerequisite defects |
| [03_ARCHITECTURE.md](D:/memex/docs/v1/03_ARCHITECTURE.md) | Components, storage responsibilities and change/delivery flow |
| [04_CONTEXT_CONTRACTS.md](D:/memex/docs/v1/04_CONTEXT_CONTRACTS.md) | Proposed identities, states, requests, deltas and receipts |
| [05_MULTI_AGENT_CONCURRENCY.md](D:/memex/docs/v1/05_MULTI_AGENT_CONCURRENCY.md) | Claude Code + Codex, worktree isolation, shared-checkout races |
| [06_HOST_INTEGRATIONS.md](D:/memex/docs/v1/06_HOST_INTEGRATIONS.md) | Adapter behavior, capability negotiation and native-client proof |
| [07_EVALUATION.md](D:/memex/docs/v1/07_EVALUATION.md) | Discriminating experiments, denominators and release gates |
| [08_ROADMAP.md](D:/memex/docs/v1/08_ROADMAP.md) | Dependency-ordered work packages, owners and phase exits |
| [09_FIRST_WAVE_IMPLEMENTATION_PLAN.md](D:/memex/docs/v1/09_FIRST_WAVE_IMPLEMENTATION_PLAN.md) | Concrete first changes, tests, commands and handoff |
| [10_MIGRATION_AND_RELEASE.md](D:/memex/docs/v1/10_MIGRATION_AND_RELEASE.md) | Legacy knowledge, opt-in rollout, recovery and OSS onboarding |
| [11_DECISIONS_AND_RISKS.md](D:/memex/docs/v1/11_DECISIONS_AND_RISKS.md) | Architectural decisions, bounded spikes and stop conditions |
| [12_RESEARCH_AND_COMPETITION.md](D:/memex/docs/v1/12_RESEARCH_AND_COMPETITION.md) | Durable primary-source research and differentiation limits |
| [13_PHASE1_EXECUTION_PLAN.md](13_PHASE1_EXECUTION_PLAN.md) | Source-verified W03-W05 implementation and phase exit |
| [14_PHASE1_VERIFICATION.md](14_PHASE1_VERIFICATION.md) | Executed tests, review fixes, decisions, setup and limits |

## Execution rules

- The planning request authorized documents; the subsequent execution request authorized all of P1 plus commit/push. Implementation used an isolated branch to preserve concurrent work. No deployment or merge is claimed.
- **No Docker for now.** P1 used local tests, temporary Git repositories and a temporary native Neo4j server on loopback. No paid model trials or real provider credentials were used. Planning checks alone do not authorize services.
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
| P2: evidence validity | Not started | Support and scope lifecycle tests |
| P3: one native action loop | Not started | Mutation-before-edit native-host trace |
| P4: concurrent agents and second host | Not started | Two-host concurrency matrix |
| P5: efficacy, migration and OSS release | Not started | Preregistered results and release checklist |

## How to begin

P1 execution is recorded in 13 and 14. Next produce the detailed P2 implementation plan from W06-W08, after reading 01, 03 and 04. The roadmap contains later work-package specifications; it does not pretend that every later implementation detail has already been source-verified. The owner of each later wave produces its detailed test-first implementation plan after its prerequisite interfaces are accepted, using the contracts and acceptance tests already defined here.

The distinguishing release demonstration is: the agent received valid context, supporting evidence changed while it worked, and Memex corrected the relevant assumption before the agent acted. If the full system cannot beat fresh retrieval and hash-bound notes on that condition, reduce the scope to an incremental release.

## Documentation validation

This pass checked all 13 numbered documents, 39 local file links, balanced Markdown fences, six Python examples and the R01–R15 roadmap mapping. The documented pure RepositoryView example was executed in memory: identity/progress, worktree separation, unborn HEAD and invalid-input checks passed. No application modules or tests were created by that check, and no native-client, graph-backed or model benchmark is claimed as run.

The repository previously ignored all `docs/`. A narrow `.gitignore` exception now exposes only `docs/v1/*.md` for normal review; other local documentation remains ignored. The original planning pass did not stage, commit or deploy the package. P1 execution subsequently committed the planning baseline and implementation on `codex/v1-phase1`; see 14 for the execution record.
