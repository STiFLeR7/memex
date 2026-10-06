# Phase 6 preregistered evaluation (S05)

Status: **frozen before any S05 trial.** The definitions, histories, design and gate values below were committed and pushed before the first S05 trial started. Nothing here may change once trials start; any later change is an amendment with a date, a reason and the data seen when it was made.

Why S05 exists: S04 ([22](22_PHASE5_PROTOCOL.md), results in [23](23_PHASE5_VERIFICATION.md) and [24](24_PHASE5_RELEASE_READINESS.md)) failed the efficacy, latency and tail-deadline gates, and left precision without a denominator. 24 requires a new preregistered evaluation after product changes, not a rerun of S04. S05 is that evaluation. Frozen numbers: [27_PHASE6_FROZEN.json](27_PHASE6_FROZEN.json).

## Product changes under test

| Change | Why | Where |
| --- | --- | --- |
| **Hook service.** A stdlib-only client per hook event hands the event to one long-lived local process, which keeps the interpreter, graph connection, schema state, parser workers and parsed structures. With no service, the event runs one-shot and a service is started. | S04's warm check spent seconds per event on interpreter start, imports, connection and parser spawn | `memex/hook_client.py`, `memex/hookd.py`, `host_adapter.serve_hook` |
| **Cheaper capture.** HEAD is read from Git's ref files (Git is asked only for unusual layouts); a check lists sources twice instead of eight times while still requiring two matching listings; an already-published view is not rewritten; unchanged files are not re-parsed. | The core's in-process check was about 3 s in S04 | `runtime/views.py`, `runtime/coordinator.py`, `runtime/actions.py`, `runtime/parsing.py` |
| **Change notice at resume.** A session that acknowledged a baseline before (its live task, or the baseline kept at `SessionEnd`) is told which claims changed since, and which of their evidence files changed, with an instruction to re-read them. Claims that still stand send nobody to re-read anything. | S04's three E failures (C15 Codex, C39 both hosts) received a correct packet but did not re-read the renamed module; D's explicit "re-read" wording did not fail | `host_adapter.changes_since`, `render_packet` |

Adapter versions `claude-code.v2` and `codex.v2`; arms `phase5-arms.v2-hook-service`; harness `phase5-trials.v4-hook-service`; fixtures `phase5-fixtures.v2-holdout`.

## Carried over from S04 unchanged

Part A of [22](22_PHASE5_PROTOCOL.md): the hypothesis, unit, arms and fairness rules, outcome definitions, latency and resource recording, analysis steps 1–7, trial statuses and handling, and leakage controls. Amendment A1 (the change lands between two turns of one session) and A4 (necessary-correction recall from confirmed, correct, timely insertions). Every threshold in 22's gate table, and the analysis code in `memex/evaluation/analysis.py`.

## What S05 changes in the protocol

### P1: precision has a definition with a denominator

Under A1, corrections arrive at resume rather than as denials, so S04's boundary-denial precision had no events (0/0). S05 counts corrections the way A4 counts recall: from the client's own session record.

- **Correction event:** the first confirmed insertion in a trial after the change that is a denial (a tool result in place of the action), or presents itself as a correction (some claim marked not current or changed, or some evidence reported changed: `fixtures.correction_like`), or satisfies the gold rule. **One event per trial**, so a re-delivered correction is not counted twice.
- **Correct:** the history is affected and the event satisfies the gold rule (`fixtures.correct_correction`). Any correction event in a stable history is incorrect.
- **Precision** = correct events ÷ events: `analysis.correction_event`, summary key `precision_s05`, selected by `"precision_definition": "confirmed_insertions"`.
- One rule for every arm and every history. S04's definition stays the default for S04's records.

A claim flagged `needs_revalidation` after a change that does not alter its meaning (the `cosmetic` mechanism) is an incorrect correction under P1. That is intended: it is an interruption the agent did not need.

### P2: held-out histories

The S05 product changes were shaped by S04's confirmatory failures, so S05 does not reuse S04's templates. Four new templates (`geo`, `temps`, `access`, `retries`) were written after S04's results and before any S05 trial: a sentinel replaced by an exception, a unit change, a type change, and an off-by-one change in meaning. Crossed with all 12 mechanisms they give **H01–H48** (28 affected, 20 stable). All 48 pass admission (`tests/test_phase5_fixtures.py`). No agent has run any of them before this freeze. None is added or dropped afterwards.

### P3: concurrency

Trials run **3 at a time** (S04 ran 6). S04 showed that concurrency inflates hook latency; a developer's machine typically runs one or two agent sessions, not six. This choice favors the latency gate and is stated here for that reason.

### P4: what the latency gate measures

The definition is unchanged: from the hook process's creation to its response, for warm unchanged declared checks. In S05 the hook process is the client, so the measurement includes the client's interpreter start and the service's work. When no service is running, the event runs one-shot inside the client's measured time. The first trial of a batch per host therefore includes cold starts; nothing is excluded for that.

## Data seen before this freeze

- **S04's complete confirmatory results** (23), including the three E failures that motivated the change notice.
- **S04 under P1**, computed for this disclosure: E 56/64 (0.875), with all 8 incorrect events on `cosmetic` histories (a comment-only change marks a hash-bound claim `needs_revalidation`); B 54/60, C 56/64, D 56/72.
- **Development smoke trials** with the changed product: D03 (`textkit`/`removed`), arm E, on both hosts, both succeeded after the change notice. Warm declared checks measured 182 ms (Claude Code) and 150 ms (Codex) end to end. Development data, not pooled.
- **Microbenchmarks** on development fixtures: warm declared checks of 135–205 ms end to end through the service, run serially.

## Confirmatory design

- **Histories:** H01–H48 (P2).
- **Arms:** A, B, C, D and E. Ablations are not run.
- **Hosts and models:** Claude Code with `claude-sonnet-5-5`, and Codex through the app-server with `gpt-6-sol` at medium reasoning effort, as in S04.
- **Replicates:** one per (history, arm, host).
- **Trial budget:** 48 × 5 × 2 = **480 trials**, plus the automatic retries Part A allows.
- **Parallelism:** 3 (P3).
- **Code under test:** the commit containing this file, checked out detached in a separate worktree and recorded in the results.

## Expected outcomes, stated before the trials

- **Efficacy is unlikely to pass.** In S04 the strongest baseline (D) had 0/56 stale failures. If it again has none, no reduction is possible and Part A step 5 fails the gate at any sample size. The sample is not enlarged to chase an effect.
- **Precision is likely to fail.** P1 counts the 4 `cosmetic` histories per host as incorrect corrections for E whenever their hash-bound claim is flagged, which bounds E near 56/64 = 0.875 < 0.95, as in S04. The definition is not narrowed to avoid this.
- **Latency is the gate the changes target.** Its result is not known before the trials.

The result is published either way.

## Gate values

Every value is S04's. The only change is the precision definition (P1).

| Gate | Frozen value |
| --- | --- |
| Efficacy | relative reduction ≥ 0.30 vs the strongest of C and D, and the lower 95% bound of the difference > 0 |
| Confidence | history-cluster percentile bootstrap, 10,000 resamples, seed 20261005; template-cluster sensitivity |
| Correction precision (E) | ≥ 0.95, P1 definition |
| False interruptions (E) | ≤ 0.05 of unaffected boundaries |
| Missed-correction tolerance (E) | A4 recall ≥ 0.90 |
| Stable-task non-inferiority | for each of A, C and D: lower 95% bound of (E − comparator) completion on stable histories > −0.10 |
| Warm unchanged action check | p95 < 200 ms end to end (P4) |
| Tail deadline | every E action check answers within 10,000 ms end to end |
| Total-resource allowance | E's mean wall time and mean output tokens per completed trial ≤ 1.25 × the strongest baseline's |
| Isolation and authority | no violation in the mechanism tier |

Every gate is judged per host and pooled; it passes only if it passes in all three.

**Secondary, not a gate:** the paired difference in stale-failure rate between E and the strongest baseline, with its history-clustered interval; and E's stale failures on the `removed` mechanism, where S04's failures were.

## Stopping rules

As S04 (22, "Stopping rules"): no early stop for efficacy or futility and no interim look at confirmatory outcomes; the analysis runs once, after the batch ends. No extension. Usage-limit pauses, environment kills and harness defects are handled and reported as in S04. The batch ends when all 480 trials have a final status, or 14 days after it started; missing trials are then reported as `not_run`.

## Running it

```powershell
$env:MEMEX_PHASE1_NEO4J_URI='bolt://127.0.0.1:17687'
python tests/phase5_trials.py run --split holdout --arms A,B,C,D,E --hosts claude,codex --parallel 3 --out output/phase6/confirmatory
python -m memex.evaluation.analysis output/phase6/confirmatory docs/v1/27_PHASE6_FROZEN.json
```
