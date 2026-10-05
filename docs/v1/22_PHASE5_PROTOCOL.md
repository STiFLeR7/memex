# Phase 5 preregistered protocol (S04)

Status: **Part A (definitions) was committed before any pilot trial.** Part B (frozen numbers) must be committed and pushed before any confirmatory trial runs. Neither part may be edited after the trials it governs have started; any later change is an amendment with a date, a reason and the data seen when it was made.

Owner: S04 in [11_DECISIONS_AND_RISKS.md](11_DECISIONS_AND_RISKS.md). Plan: [21_PHASE5_EXECUTION_PLAN.md](21_PHASE5_EXECUTION_PLAN.md). Gates: [07_EVALUATION.md](07_EVALUATION.md).

## Part A: definitions (frozen before the pilot)

### Hypothesis and unit

At comparable resources, Live Context (arm E) reduces task failures caused by context that became stale after delivery, compared with fresh per-action retrieval (C) and hash-bound notes (D).

- **Unit: one history on one host.** A history is a template crossed with a mechanism; see 21. Repeated actions and replicates within one history are clustered observations, never independent samples.
- **Primary outcome:** stale-context failure in affected histories.
- **Safeguard outcome:** functional completion in stable histories.

### Arms and fairness

The arms are A–E and the ablations E-noinval, E-norecon and E-full, as defined in 21 and implemented in `memex/evaluation/arms.py`. Within one history and host, every arm runs with the same:
- repository bytes and history;
- prompt, which never mentions memex;
- model, turn limit (30), tools and permission mode;
- fixture hooks.

C, D and E get one check before every declared mutation, the same ability to deny and ask for reconsideration, and the same reconsideration ceiling. Packet budgets are the host adapter's context limit for every packet-bearing arm.

### Trial procedure

`tests/phase5_trials.py` runs every trial.
- **Fresh state:** a fresh repository and graph namespace per trial.
- **Change timing:** the change lands immediately after the agent's first completed tool call.
- **Hidden checks:** these run outside the repository.

Clients:
- **Claude Code:** `-p`, `--permission-mode acceptEdits`, `--setting-sources project,local`, `--strict-mcp-config`.
- **Codex:** an app-server thread with sandbox `workspace-write`, `--disable plugins`, a per-thread hook-trust override, and a pinned reasoning effort.

The harness never approves anything.

### Outcome definitions

Run the final target file against the hidden checks twice: in the world before the change and in the world after it.

- **Success:** every check passes in the world after the change.
- **Stale-context failure** (affected histories only): at least one check fails after the change, while every check passes in the world before it. The patch is a correct implementation of the obsolete contract. This is the attribution rubric. It is mechanical, and no human judgement or agent prose enters it.
- **Functional failure:** checks fail in both worlds. It is reported but not attributed to stale context.

### Action boundaries and corrections

- **Action boundary:** every declared mutation the agent proposes (`Edit`, `Write`, `MultiEdit`, `NotebookEdit`, `apply_patch`), recorded by a fixture hook in every arm.
- **Necessary boundary:** a boundary in an affected history at or after the change.
- **Unaffected boundary:** every other boundary, meaning all boundaries in stable histories and pre-change boundaries in affected ones.
- **Correction event:** an arm's denial at a boundary. For E-norecon it is a deferred correction.

| Metric | Definition |
| --- | --- |
| Correction precision | Correction events at necessary boundaries ÷ all correction events |
| Necessary-correction recall | Trials whose first necessary boundary was denied ÷ affected trials with at least one necessary boundary. An undelivered or missed correction counts as a miss |
| False-interruption rate | Denied unaffected boundaries ÷ all unaffected boundaries |
| Retries | Boundaries after a trial's first denial, reported separately |

### Latency

**Warm unchanged action check:** a `PreToolUse` hook for a declared mutation that proceeded without a decision. It is measured end to end, from the hook process's creation to its response. That includes interpreter start, the graph connection, the source capture and the check. The latency gate is p95 < 200 ms over these checks. The core's in-process check time is reported beside it, but the gate is not judged on it.

Hardware, graph scale (one fixture repository per trial on one local server) and concurrency (trials run in parallel) are recorded.

### Resources

Recorded per trial:
- model tokens as the client reports them;
- cost where the client reports it (Claude Code does, Codex does not);
- wall time;
- characters memex delivered;
- graph refreshes and their time;
- hook latency.

Missing usage is unknown, never zero, and is reported as a known/total count.

### Analysis

1. **Paired units:** (history, host) pairs in which E, C and D all completed, over affected histories.
2. **Strongest comparable baseline:** whichever of C and D has the lower stale-failure rate on the confirmatory units. It is chosen on the same data, which favors the baseline.
3. **Effect:** d = p(baseline) − p(E), and the relative reduction RR = 1 − p(E)/p(baseline).
4. **Confidence:** a percentile bootstrap with 10,000 resamples and seed 20261005, resampling whole histories so every host unit of a history moves together. As a sensitivity analysis, the same is done resampling whole templates.
5. **Efficacy gate passes iff:** RR ≥ 0.30 and the lower 95% bound of d is above 0. If p(baseline) is 0, no reduction is possible and the gate fails.
6. **Stable-task safeguard:** for comparators A, C and D, the paired difference in completion on stable histories, E minus comparator, clustered by history, must have a lower 95% bound above −(the non-inferiority margin fixed in Part B).
7. **Other gates:** precision ≥ 95% and false interruptions ≤ 5%, both for E. Recall is checked against Part B's tolerance, the latency gate as above, and no isolation or authority violation is allowed, from the mechanism tier.

Gates are evaluated per host and pooled. A pooled pass does not hide a per-host failure; both are reported.

### Trial status and handling

Every trial gets exactly one final status:

| Status | Meaning | Handling |
| --- | --- | --- |
| `completed` | The client finished and the change was applied | Scored |
| `timeout` | Over 900 s | Final; excluded from outcome rates, counted and reported |
| `client_error` | The client failed for another reason | Final; as above |
| `unavailable` | Capacity or usage limit | Retried automatically up to 2 times; the last attempt is final |
| `invalid_infrastructure` | Fixture, graph or harness failure | Retried automatically up to 2 times; the last attempt is final |
| `no_change_applied` | The agent made no tool call, so the change never landed | Final; excluded and reported |
| `hook_error` | A memex hook raised | Final; scored, but listed in the report |

Every attempt's record is kept. As a sensitivity analysis, non-completed affected trials are counted as stale failures for E and as successes for its comparators, the worst case for E.

### Leakage controls

- **Nothing in the repository reveals the change or the answer:**
  - hidden checks, gold patches and post-change content live only in the harness;
  - the change plan is stored compressed outside the repository;
  - `tests/test_phase5_fixtures.py` asserts both.
- **Nothing carries over between trials:** each trial has fresh repository, worktree, graph and session identities, so no evidence, claim or session state carries over.
- **No future facts:** no history contains a fact from after its change point.

### Development pilot

The pilot runs the 12 development histories in [21](21_PHASE5_EXECUTION_PLAN.md), across all 8 arms and both hosts. It is reported as development data and never pooled with confirmatory data. The pilot may change the harness only to fix defects. Every such fix is listed in the verification report, with the trials it affected rerun.

## Part B: frozen confirmatory numbers

**Not yet frozen.** To be completed from the pilot, then committed and pushed before any confirmatory trial: the confirmatory histories and replicates, the sample-size justification, the missed-correction tolerance, the stable-task non-inferiority margin, the total-resource allowance, the trial budget and the stopping rules.
