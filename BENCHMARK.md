# memex v0.9 Benchmark

This document records the reproducible Goal 10 benchmark for memex v0.9.
It is a release-readiness and integration benchmark, not a model leaderboard.

## Result

**Decision: GO for the tested Hermes + memex configuration.**

| Measure | Result |
|---|---:|
| Paired cases | 8 |
| Valid paired runs | 8/8 |
| Treatment failures | 0 |
| Treatment regressions | 0 |
| Context returned in treatment runs | 8/8 |
| Objective verification | File-state checks and pytest |

Both baseline and treatment completed every fixture task. Therefore this
benchmark demonstrates a working, bounded, read-only integration and
non-regression under the tested conditions. It does **not** establish that
memex causally improves every coding task or every model/provider.

## What was tested

Each case used an isolated temporary Git repository. Baseline ran Hermes
without memex. Treatment ran Hermes with the memex `MemoryProvider` and
automatic read-only prefetch.

| Case | Objective verification |
|---|---|
| Unfamiliar repository | Implement a function, add a test, run pytest |
| Architecture investigation | Create repository architecture notes and run pytest |
| Bug investigation | Repair a parser defect and run pytest |
| Regression fix | Repair a failing behavior regression and run pytest |
| Multi-session continuation | Create a handoff plan, then implement it in a fresh step |
| Agent handoff | Write handoff evidence, then complete the fix in a fresh session |
| Stale knowledge | Apply the current decision instead of a superseded value |
| Parallel conflicts | Run two isolated workers and verify divergent edits |

The evaluator checks repository state and test results. An agent's textual
claim that a task succeeded is not sufficient evidence.

## Tested environment

- memex v0.9.0
- Hermes installed in the isolated benchmark environment
- OpenRouter model: `stealth/ox-alpha`
- NVIDIA NIM embedding model: `nvidia/nemotron-3-embed-1b`
- Neo4j 5.x in the `memex-neo4j` container
- WSL/Linux execution for the live runner

The model and providers are part of the test condition, not a recommendation.
Repeat the benchmark for any production provider configuration.

## Reproduce

Prerequisites:

1. WSL/Linux with Python, Docker, and the Hermes benchmark environment.
2. A running Neo4j container named `memex-neo4j`.
3. Provider credentials supplied through environment variables or the local
   ignored credential mechanism supported by `goal10_objective_matrix.sh`.

Run the complete matrix from WSL:

```bash
cd /mnt/d/memex
bash goal10_objective_matrix.sh
```

Run one case while iterating:

```bash
bash goal10_objective_matrix.sh bug-investigation 180
```

Clean evaluation-only Neo4j seed data after a run:

```bash
bash goal10_objective_matrix.sh clean
```

The runner writes temporary repositories, traces, usage data, and aggregate
results below the configured evaluation artifact directory. Do not commit
those artifacts or any credential file.

## Evidence boundary

### Measured

- The provider path can retrieve and inject bounded engineering context before
  a Hermes task.
- Context carries packet, retrieval, provenance, and selection metadata.
- The objective runner can compare fresh baseline and treatment arms.
- The tested treatment arm completed all eight objective cases without a
  treatment-only failure or regression.

### Not established

- Causal improvement: baseline also completed all eight cases.
- Retrieval precision or recall against a labelled corpus.
- User-rated context usefulness.
- Generalization to other models, providers, repositories, or task budgets.
- Production-scale latency, cost, or throughput.
- Universal long-horizon coding-agent reliability.

## Engineering guarantees checked by the benchmark

- Hermes source is not modified by the memex integration.
- Prefetch is read-only in this benchmark.
- Raw Hermes transcripts, prompts, tool results, and `state.db` are not
  ingested into memex.
- Context is bounded and provenance-aware.
- A missing or failed memex request must not be treated as successful task
  evidence.
- Evaluation artifacts are cleaned after the aggregate result is recorded.

## Source of truth

The executable benchmark is:

- `goal10_objective_matrix.py`
- `goal10_objective_matrix.sh`
- `memex/evaluation/objective.py`
- `memex/evaluation/release_readiness.py`

The benchmark is intentionally conservative: a future release should retain
the `GO` label only when it preserves objective verification, paired runs,
failure visibility, and the provider-specific evidence boundary described
above.
