# Research basis and competitive boundaries

Research snapshot: 3 October 2026. Primary source documentation, repositories/source files and papers were inspected during the approved research. Public implementations were not installed or independently benchmarked. Undated repository pages are access-date snapshots, not inferred release dates. Findings support a bounded product hypothesis, not an exhaustive uniqueness claim.

## Competitive evidence

| System | Established capability | Implication for Memex v1 |
| --- | --- | --- |
| [Graphiti/Zep](https://github.com/getzep/graphiti), [paper](https://arxiv.org/abs/2501.13956) | Incremental temporal graphs, episode provenance and contradiction-based relationship expiry | Temporal context and invalidation are inherited foundations |
| [Letta Code](https://github.com/letta-ai/letta-code), [Context Repositories announcement](https://www.letta.com/blog/context-repositories/) | Git-backed context, progressive disclosure, worktrees and background reflection | Persistence and proactive learning alone are incremental |
| [Mem0](https://github.com/mem0ai/mem0) | General persistent memory, entity/temporal retrieval; current README describes April 2026 ADD-only extraction | Distinguish product/managed claims from OSS implementation; generic recall is crowded |
| [Supermemory Claude plugin](https://github.com/supermemoryai/claude-supermemory) | Automatic capture, project-scoped recall and prompt-driven memory selection | Automatic relevant recall is insufficient differentiation |
| [GitNexus](https://github.com/abhigyanpatwari/GitNexus), [hook source](https://github.com/abhigyanpatwari/GitNexus/blob/main/gitnexus/hooks/claude/gitnexus-hook.cjs) | Code graph, impact analysis, branch-pinned indexes, pre-tool enrichment and stale-index hints | Graph + branch + hook is already a direct competitor |
| [Codebase Memory MCP](https://github.com/DeusData/codebase-memory-mcp), [paper](https://arxiv.org/abs/2603.27277) | Persistent AST graph and background changed-file reindexing | Watching code does not by itself define the leap |
| [Augment Context Engine MCP](https://www.augmentcode.com/product/context-engine-mcp) | Task-aware context; vendor says local edits appear in next queries | Broad live/task-aware context positioning is crowded; implementation is proprietary |
| [Serena](https://github.com/oraios/serena), [Sourcegraph MCP](https://sourcegraph.com/mcp) | Symbol/reference/navigation and repository history/search | Strong repository-exploration baselines must not be crippled in evaluation |
| [Mnemos injection source](https://github.com/polyxmedia/mnemos/blob/main/internal/injection/injection.go) | Pre-edit delivery, per-memory exposure tracking and cross-channel suppression | Session-exact exposure accounting matters; pre-edit memory is already available |
| [Agent Memory System](https://github.com/rrrrrredy/agent-memory-system), [product contract](https://github.com/rrrrrredy/agent-memory-system/blob/main/docs/product-contract.md) | Content-addressed sources, immutable revisions, stale loadouts and receipts | Evidence/revisions/receipts are established primitives, not unique Memex inventions |
| [SCAR](https://github.com/Daily-Nerd/Scar) | Code-anchored negative knowledge delivered before edits | "Remember why this code is unusual" is already a focused product |
| [Sensei](https://github.com/globulario/sensei) | Architecture briefings, contracts/invariants and pre-edit governance; closure protocol unfinished | Architectural obligations also have close competitors |

The strongest proposed contribution is a complete revision/worktree-aware maintenance loop: supporting evidence changes after delivery, dependent claims are revalidated, and the particular affected session receives a replacement/retraction before its next relevant action. This behavior was not established end to end in the reviewed sources. It remains a hypothesis requiring comparison with simple source-hash notes and fresh action-time retrieval.

Do not publish "first proactive memory," "first context graph," "no competitors," or comparable claims. Commercial pages establish stated product behavior, not independently verified guarantees. Small maintainer fixtures establish bounded feasibility, not general coding efficacy.

## Host and protocol sources

- [Git worktree reference](https://git-scm.com/docs/git-worktree): shared repository data with distinct worktree state; supports the separate-view identity design.
- [Claude Code hooks](https://code.claude.com/docs/en/hooks#pretooluse-decision-control): native decisions and context timing must be distinguished. An allow plus additionalContext is not proof of model reconsideration before an edit.
- [Codex configuration reference](https://developers.openai.com/codex/config-reference): current lifecycle hooks include pre-tool events; installed-version behavior still requires a capability spike.
- [MCP resources, 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/server/resources): resources are host-controlled; subscriptions carry update signals, not guaranteed agent prompt insertion. Negotiate SDK/client versions instead of blindly using the newest method names.

## Evaluation sources

| Source | What it tests | How to use it |
| --- | --- | --- |
| [SWE-Milestone](https://github.com/DeepCommit-ai/SWE-Milestone) | Dependent milestone DAGs in continuous software-evolution sessions | Native continuity and integration benchmark; current harness uses Docker |
| [SWE-Chain](https://arxiv.org/abs/2605.14415) | Chained release-level upgrades inheriting prior agent code | Long-horizon maintenance and accumulated constraints |
| [SWE-CI](https://arxiv.org/abs/2603.03823) | Continuous maintenance across real repository histories | Regression/maintainability outcomes beyond retrieval accuracy |
| [SWE-EVO](https://arxiv.org/abs/2512.18470) | Release-level evolution with broad regression testing | External generalization; pin paper/dataset version |
| [SlopCodeBench](https://github.com/SprocketLab/slop-code-bench) | Iterative specification changes and code erosion | Architectural sustainability; dataset size changes, so pin a commit |
| [LongMemEval-V2](https://github.com/xiaowu0162/LongMemEval-V2) | Dynamic state, workflow/gotcha knowledge and premise awareness in web/enterprise trajectories | Supplementary memory abilities; not coding-outcome proof |
| [SWE-MeM](https://arxiv.org/abs/2606.28434) | Learned proactive/on-demand trajectory compression | Distinguish internal trajectory optimization from external evidence validity |

Existing Memex [BENCHMARK.md](D:/memex/BENCHMARK.md) supplies compatibility evidence, with both baseline/treatment completing all eight fixtures. Preserve that evidence boundary. The v1 mutation suite is a new experimental condition and must not be represented as an official external leaderboard score.

## Research maintenance

Before public comparison, recheck mutable competitor repositories and host contracts, record inspected versions, and remove unsupported novelty language. Reproduce selected comparators where practical at the same resources; do not borrow incomparable vendor benchmark percentages.

The earlier detailed [competition screen](D:/memex/output/v1-research/competition.md) and [thesis report](D:/memex/output/v1-research/v1-thesis.md) remain supporting local artifacts. This document intentionally carries the decision-relevant sources into the durable planning directory so execution does not require those output artifacts.
