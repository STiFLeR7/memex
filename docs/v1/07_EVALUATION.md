# Evaluation and v1 evidence gates

Status: P1/P2 deterministic mechanism checks are executed (see [16_PHASE2_VERIFICATION.md](16_PHASE2_VERIFICATION.md)); efficacy targets remain proposed and unmeasured preregistration criteria. Local engineering checks are permitted without Docker. Paid/native model runs and service-backed experiments are a later execution step, not part of writing these documents.

## Hypothesis

At comparable total resources, explicit evidence maintenance and session-relative corrections reduce failures caused by context becoming stale after delivery, compared with current prefetch, fresh per-action retrieval and simple hash-bound notes.

Primary unit: an independently defined task history with a controlled evidence-change opportunity. Repeated seeds and individual actions within a history are clustered observations, not independent samples. Primary outcome: task failure attributable to obsolete engineering context under a predetermined constraint/applicability rubric, corroborated by hidden functional tests. Overall test success is a second primary safeguard against trading stale failures for unrelated regressions.

## Experimental arms

| Arm | Condition |
| --- | --- |
| A | Native agent, ordinary repository exploration and normal AGENTS/docs |
| B | Memex v0.9-style initial prefetch |
| C | Fresh per-action retrieval, same action opportunities and delivered-context budget |
| D | Hash-bound notes, source-change warning and re-read/reconsideration |
| E | Full Live Context: support maintenance, scoped revalidation, session-relative deltas |

Arms C, D and E have equivalent reconsideration opportunities so a benefit cannot be attributed solely to blocking a stale action. Give all arms identical allowed repository history and rules at each timestamp. Compare total cost as well as delivered context: extraction, indexing, semantic calls, model calls and repeated agent actions count. A fixed prompt-token cap alone is insufficient fairness.

Ablations: E without invalidation; E without action reconsideration; E with full packets instead of deltas. Pin source/fixture, client/model, prompt, dependencies and harness versions. Keep future facts and evaluator-only answers unavailable to all agents. Use controlled clocks to test temporal boundaries.

## Controlled corpus

| Fixture | Mutation/opportunity | Gold behavior |
| --- | --- | --- |
| F01 | Body-only implementation changes after recall | Relevant dependent assumption rechecked |
| F02 | Last call/import removed | Removed structure stops supporting current context |
| F03 | Approved decision superseded | Old delivered revision explicitly corrected |
| F04 | Unrelated file edited | No material interruption |
| F05 | One alternative support changes, another remains valid | Claim remains supported when sufficient evidence survives |
| F06 | Governed file changes without policy supersession | Approved rule remains active; violation can be conflict |
| F07 | Evidence parsing fails | Unknown coverage, not empty certified structure |
| F08 | Two worktrees disagree on same symbol | Isolated correct per-view context |
| F09 | Branch result merged with conflict resolution | Revalidation on actual merged contents |
| F10 | Claude and Codex share checkout and receive same claim | Independent corrections and receipts |
| F11 | Target changes between read and guarded write | Expected-hash mismatch prevents participating stale write |
| F12 | Compaction/handoff/resume | Correct working set re-established |
| F13 | Watcher misses an event or daemon restarts | Action hash check/catch-up recovers drift |
| F14 | Service/host delivery unavailable | No fresh-success receipt; explicit fail-open coverage |
| F15 | Delta budget or propagation bound exceeded | Resynchronization/incomplete coverage reported |
| F16 | Conflicting inferred proposals/future-dated fact | No automatic authority promotion or future leakage |

Start with 12 independently labeled histories covering these mechanisms, allowing multiple cases per history for debugging only. Freeze those as development data; choose a disjoint confirmatory set sized from pilot rates and a preregistered power calculation. Cover several repositories, two model families and both host clients. Do not choose the sample size after looking for a favorable significance result.

## Metrics and definitions

Correction precision = materially correct correction/retraction events divided by all material correction/retraction events. Necessary-correction recall = timely necessary corrections divided by all labeled necessary correction opportunities. Unavailable/missed deliveries count as misses for this metric, rather than disappearing from the denominator.

False interruption rate = unaffected labeled action boundaries interrupted divided by all unaffected labeled action boundaries. Report repeated retries separately. Stale-context-induced failure rate uses eligible task histories as denominator; report stable histories separately. Evidence/receipt availability is not a success outcome.

Measure no-change action-check latency, changed-support revalidation latency, event-to-dirty latency, delivered tokens/characters, total spend, retry count, invalidation fan-out, acknowledgement failure, indexing recovery and isolation violations. State hardware, graph scale, concurrent sessions and cold/warm conditions. Report p50/p95 and tail failures; exclude neither timeouts nor pathological histories silently.

Use paired comparisons with task/repository clustering and confidence intervals. Human adjudication of attribution is blinded to treatment where feasible and follows the frozen rubric. A prose claim that the patch succeeded is not evidence. Record objective file states/tests and action ordering.

## Proposed release gates

| Gate | Target | Decision rule |
| --- | --- | --- |
| Mechanism | Required F01â€“F16 semantics pass | Any isolation or authority violation blocks release |
| Native delivery | Prevent/reconsider affected action in both supported clients | Notifications/mock receipts alone fail |
| Efficacy | At least 30% relative reduction in stale-context-induced task failures vs strongest comparable baseline | Improvement supported by preregistered confidence analysis |
| Precision | At least 95% material correction precision | Report recall; do not improve precision by withholding needed corrections |
| Quiet behavior | At most 5% false interruptions | Denominator is unaffected action boundaries |
| Latency | Warm unchanged local action-check p95 below 200 ms | Declared environment; changed revalidation reported separately |
| Generalization | Multiple repos, two host clients, two model families | No single-model-only reliability claim |
| Compatibility | Existing packet/provider/governance checks preserved | Baseline test suite and migration evidence |

After the pilot, freeze a missed-correction tolerance, non-inferiority margin for stable-task completion, tail deadline and total-cost allowance **before** confirmatory trials. S04 in the decision register owns this protocol. Without these values and sample-size justification, P5 cannot start confirmatory testing or claim its gates met.

## External and real-world evaluation

[SWE-Milestone](https://github.com/DeepCommit-ai/SWE-Milestone) tests dependent milestones in persistent sessions; [SWE-Chain](https://arxiv.org/abs/2605.14415) tests inherited package upgrades. Add controlled mutation conditions as explicitly labeled extensions rather than presenting them as official benchmark scores.

[SWE-CI](https://arxiv.org/abs/2603.03823), [SWE-EVO](https://arxiv.org/abs/2512.18470) and [SlopCodeBench](https://github.com/SprocketLab/slop-code-bench) examine long-horizon maintenance/evolution. Pin dataset versions and license/fixture assumptions. Their results complement, rather than replace, the defining mutation-before-action suite.

[LongMemEval-V2](https://github.com/xiaowu0162/LongMemEval-V2) supplies premise/dynamic-state tests but is not coding-outcome validation. No memory QA leaderboard is sufficient for release.

The current SWE-Milestone harness requires Docker. Under the maintainer's current constraint, defer that reproduction and use temporary Git repos/local tests for deterministic fixtures. Mark external results not run; never substitute synthetic traces while claiming official execution.

Recruit 3â€“5 independent maintainers for repeated tasks over several weeks. Use randomized eligible tasks or controlled crossover and blinded patch review where feasible. Report install success, retained use, regression/rework, helpful/false interventions, cost and failures. OSS stars are a secondary adoption signal, not proof of engineering value.

**Amended 6 October 2026 (maintainer decision, before any participant data existed):** agents, not people, are memex's users, so the evidence must not depend on hand-kept logs. An independent installation is a repository the project does not control, installed by someone other than the project. It counts once it has **at least 4 complete pilot weeks or at least 50 agent sessions** under the automatic live/shadow crossover (`memex v1 pilot`), with evidence collected automatically and reported as counts only. The gate needs **at least 3** such installations. Independence, real use, the crossover control and the reporting of failures are unchanged; only the manual logging burden and the fixed-person framing are removed. The project's own trials, including real-repository replays, never count toward this gate. See [26_MAINTAINER_PILOT_KIT.md](26_MAINTAINER_PILOT_KIT.md).

If E fails to improve on C/D, reduce the runtime or ship an incremental release. Publish the negative result rather than changing the primary metric afterward.
