# Phase 5 release readiness (W19)

Decision: **not releasable as v1.0.0.** Mandatory gates failed (efficacy, latency, tail deadline), one could not be established (precision), and required evidence is still pending (independent maintainers) or not run (external benchmarks; native host clients on Linux). Version, changelog, package metadata and README stay at 0.9.0. Nothing was merged, tagged, published or deployed.

Evidence: [23_PHASE5_VERIFICATION.md](23_PHASE5_VERIFICATION.md). Frozen protocol: [22_PHASE5_PROTOCOL.md](22_PHASE5_PROTOCOL.md) and [22_PHASE5_FROZEN.json](22_PHASE5_FROZEN.json). Gates: [07](07_EVALUATION.md) and the release checklist in [10](10_MIGRATION_AND_RELEASE.md).

## Gate by gate

Confirmatory figures are for arm E on 56 affected and 40 stable paired units, pooled over both hosts. A gate passes only if it passes pooled and on each host.

| Gate | Command | Denominator | Result | Status |
| --- | --- | --- | --- | --- |
| Mechanism F01–F16 | `python -m memex.evaluation.mechanism` | 16 fixtures | 16/16 passed | **passed** |
| Isolation and authority | same | 10 tests | no violation | **passed** |
| Native delivery, both clients | Phase 3/4 native gates; Phase 5 confirmed insertions | 56 E opportunities | Correct correction confirmed in the agent's session before the affected edit, 56/56, on both hosts | **passed** |
| Efficacy: ≥30% relative reduction vs strongest of C/D, lower CI bound of difference > 0 | `python -m memex.evaluation.analysis output/phase5/confirmatory docs/v1/22_PHASE5_FROZEN.json` | 56 paired units, 28 history clusters | Strongest baseline D: 0/56 stale failures. E: 3/56. Difference (D − E) CI95 −0.143 to 0.000; template-clustered −0.107 to 0.000 | **failed** |
| Precision ≥ 95% | same | correction events at action boundaries | 0/0: no arm denied any edit under A1 | **not established** (counts as not passed) |
| False interruptions ≤ 5% | same | 40 unaffected boundaries | 0/40 | **passed** |
| Missed-correction tolerance: A4 recall ≥ 0.90 | same | 56 opportunities | 56/56 | **passed** |
| Warm unchanged action check p95 < 200 ms | same | 96 E checks | 6,146 ms (core in-process 3,190 ms) | **failed** |
| Tail deadline: every check ≤ 10,000 ms | same | 96 E checks | max 11,200 ms | **failed** |
| Total-resource allowance ≤ 1.25× strongest baseline | same | 96 E vs 96 D trials, usage known for all | wall 1.07×, output tokens 1.04× | **passed** |
| Stable-task non-inferiority vs A, C, D (margin 0.10) | same | 40/39/40 stable units | difference 0.000, CI lower bound 0.000 for each | **passed** |
| Generalization: repositories, hosts, model families | trial design | 7 template repositories, 2 hosts, 2 model families | Two hosts and two model families covered; the repositories are synthetic templates, not real projects | **passed for hosts and models**; real repositories **not run** |
| Compatibility | `pytest tests -m "not integration" -q` | full suite | 919 passed, 1 skipped, 172 deselected | **passed** |
| Migration resumable; rollback returns intact legacy projections | `pytest tests/test_phase5_migration.py` | 13 tests (15 with parametrization) | all passed on the live graph | **passed** |
| Onboarding: existing-Neo4j recipe, per-host recipes, demos | [25](25_ONBOARDING.md); core demo test | — | written; core demo passes; native demo is the E trial | **passed (Windows)** |
| Platforms | Docker `python:3.12-slim` + `neo4j:5.26`, commit `8c56d0c` | full suites | Windows validated; Linux test suites pass (915 broad, 205 Phase 5, mechanism 16/16, isolation passed); native clients on Linux and macOS not validated | Linux suites **passed**; Linux native clients and macOS **not run** |
| Independent installations (amended 6 Oct: ≥3, each ≥4 weeks or ≥50 agent sessions, automatic counts-only evidence) | `memex v1 pilot` and `pilot_kit reports`, [26](26_MAINTAINER_PILOT_KIT.md) | 0 installations | no participant recruited | **pending** |
| External container benchmarks | — | — | SWE-Milestone not run by maintainer decision (≈115 images; agents inside containers need API keys); evaluation stays local; no official score claimed | **not run** |
| Public report with failures, costs, confidence limits | 23 and this file | — | written | **passed** |

## Resources and failures

**Trials:**
- 192 pilot trials plus 480 confirmatory trials;
- 1 `hook_error` (a C hook timed out and failed open);
- interruptions: 3 operator or environment events, logged and rerun.

**Confirmatory cost:**
- 8.5 hours of agent wall time;
- about 691,000 output tokens;
- Claude Code $31.41;
- Codex cost unknown, because the client does not report it.

## What the result means

- **Live Context delivered.** With corrections arriving at resume, every packet-bearing arm that told the agent the current state of the evidence (B, C, D, E) avoided most stale failures. Arm A (no hooks) failed 79% of affected tasks. E delivered what the design promised: correct, confirmed, timely corrections, with no false interruptions and no loss on stable tasks.
- **E was not better than simple hash-bound notes.** That is what the efficacy gate asks, so v1's central claim, that Live Context beats the strongest comparable baseline, is **not supported** by this evaluation. It has to be published as such.
- **E's failures point to the correction's wording.** All of them were on renamed functions, where the correction was delivered but the agent did not re-read the changed file. D's explicit "re-read these files" wording succeeded there.
- **Latency is an engineering defect, not a trial outcome.** Each hook starts a new interpreter, connects to the graph and re-captures sources.

## Evidence still needed for any release

**Update, 6 October 2026:** the persistent hook process, faster source capture, the resume change notice and the precision definition are implemented, and S05 is frozen in [27](27_PHASE6_PROTOCOL.md) before any of its trials. S05 has reported ([28](28_PHASE6_RESULTS.md)): recall, false interruption, tail deadline, resources and stable non-inferiority pass; efficacy, precision and latency fail. Item 1 is therefore not met. The maintainer chose to publish a release candidate with this evidence in the repository, with further evaluation and the field pilot to follow.

1. **A new, preregistered evaluation after product changes.** It must not be a rerun of this one until it passes. The candidate changes are:
   - a persistent hook process, for latency and the tail deadline;
   - correction wording that names the files to re-read;
   - a decided precision definition for corrections delivered at resume, amended before any trial.
2. **Independent installations reporting real use.** The amended, agent-native pilot in 26 is ready; it needs participants.
3. **Real-repository tasks and the external benchmark:**
   - SWE-Milestone, only if the maintainer later accepts its download size and in-container API keys;
   - labeled extensions second.

   Docker is now available for this work.
4. **Native host clients on Linux.** The test suites already pass there.
