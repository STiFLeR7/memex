# S05 results

The preregistered S05 evaluation ([27](27_PHASE6_PROTOCOL.md), frozen in commit `23f227a` before any trial) ran to completion on 6 October 2026. The analysis ran once, after the batch ended, with the frozen values in [27_PHASE6_FROZEN.json](27_PHASE6_FROZEN.json):

```
python -m memex.evaluation.analysis output/phase6/confirmatory docs/v1/27_PHASE6_FROZEN.json
```

## Outcome

**S05 does not pass.** Five gates pass and three fail: efficacy, precision and latency. Each failure holds pooled and on each host separately.

| Gate | Pooled | Claude Code | Codex | Needs |
| --- | --- | --- | --- | --- |
| Efficacy | **failed** | failed | failed | E reduces stale failures vs the strongest baseline |
| Precision (P1) | **failed** 56/64 = 0.875 | 28/32 | 28/32 | ≥ 0.95 |
| Recall (A4) | passed 56/56 | 28/28 | 28/28 | ≥ 0.9 |
| False interruption | passed 0/40 | 0/20 | 0/20 | ≤ 0.05 |
| Latency (warm check p95) | **failed** 275 ms | 268 ms | 288 ms | < 200 ms |
| Tail deadline | passed, max 377 ms | 377 ms | 315 ms | every check ≤ 10 000 ms |
| Resources (E vs C) | passed: 0.98× wall time, 0.97× output tokens | 1.19×, 0.99× | 0.92×, 0.96× | ≤ 1.25× |
| Stable non-inferiority | passed vs A and C | passed | passed | CI95 lower bound > −0.10 |

### Against the predictions stated before the trials

- **Efficacy failed, as predicted.** The strongest baseline, C, had no stale-context failures (0/56), so no reduction is possible. B, D and E were also at 0/56. Only A, with no memex context, failed: 44/56 (79%).
- **Precision failed, as predicted.** All 8 of E's incorrect corrections are on the `cosmetic` histories: a comment-only change flags a claim bound to the file's hash. B and C show the same 8. D also has 8 on `alternative` histories, giving 56/72.
- **Latency failed.** This was the gate the S05 changes targeted, and its result was not predicted. The warm end-to-end check p95 fell from about 6 s in S04 to 275 ms, but missed the 200 ms threshold. Inside the service, the check p95 was 110 ms; the rest is the client's start and the round trip, measured with 3 trials running at once.

## Results by arm

| Arm | Host | Trials | Stale failure (affected) | Stable completion | Precision (S05) | Recall (A4) | Interception | False interruption | Warm check p95 ms | Output tokens (mean) | Cost known |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | all | 96 | 44/56 (79%) | 40/40 (100%) | n/a | 0/56 (0%) | 0/56 (0%) | 0/40 (0%) | n/a | 1251 | 48/96 |
| B | all | 96 | 0/56 (0%) | 40/40 (100%) | 56/64 (88%) | 56/56 (100%) | 0/56 (0%) | 0/40 (0%) | 122 | 1494 | 48/96 |
| C | all | 96 | 0/56 (0%) | 40/40 (100%) | 56/64 (88%) | 56/56 (100%) | 0/56 (0%) | 0/40 (0%) | 238 | 1494 | 48/96 |
| D | all | 96 | 0/56 (0%) | 40/40 (100%) | 56/72 (78%) | 56/56 (100%) | 0/56 (0%) | 0/40 (0%) | 140 | 1411 | 48/96 |
| E | all | 96 | 0/56 (0%) | 40/40 (100%) | 56/64 (88%) | 56/56 (100%) | 0/56 (0%) | 0/40 (0%) | 275 | 1455 | 48/96 |

The per-host rows and every bootstrap are in `output/phase6/confirmatory/analysis.json`.

## Conduct

- **Trials:** 480 of 480 completed. Two Codex trials (H03-A, H04-D) ended `unavailable` (the client did not run the trial) and were retried automatically, as the protocol allows; both first attempts are kept as `*.attempt0.json`.
- **Environment kill:** at 36/480, Claude Code's low-memory reaper stopped the batch and the Neo4j fixture. With the maintainer's approval, the run resumed the missing trials only. Recorded in `restarts.jsonl`.
- **Interim look:** while confirming the batch had started, the first 15 or so outcome lines in `batch.log` were seen. After that only counts of finished trials were checked, until the single analysis.
- **Duration:** 12:53 to 16:36 IST, including the restart.
- **Cost:** Claude Code, $31.42 across its 240 trials. Codex reports no cost, so its 240 trials are unknown, not zero.

## What this means

The S05 changes removed S04's large latency gap and kept recall, false interruptions, resources and stable completion within their gates. They did not produce measurable efficacy. That is because the baselines that already give agents correct context did not fail on these histories, not because the arm with memex corrections did worse. Precision is held below threshold by one known behavior: comment-only changes flag hash-bound claims.

Under the stopping rules, S05 is not extended and no gate is changed after the fact.
