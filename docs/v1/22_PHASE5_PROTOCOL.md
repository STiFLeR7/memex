# Phase 5 preregistered protocol (S04)

Status: **Part A (definitions) was committed before any pilot trial (`dc3f19d`). Amendments A1–A3 and Part B (frozen numbers) were committed and pushed after the development pilot and before any confirmatory trial.** Neither part may be edited after the trials it governs have started; any later change is an amendment with a date, a reason and the data seen when it was made.

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

## Amendments made during the development pilot

Part A's text above is unchanged. These amendments replace the parts they name. All were made on development data only, before any confirmatory history was generated or run.

### A1 (2026-10-05): the change lands between two turns of one session

- **Replaces:** "Change timing" under Trial procedure, and the same rule in 21.
- **Data seen:** pilot v1, 26 completed trials, before it was stopped. Stale-context failures: A 0/4, B 1/4, C 0/3, D 0/3, E 0/3, E-noinval 1/3, E-norecon 0/3, E-full 0/3. Records are kept in `output/phase5/pilot/`.
- **Reason:** with the change landing after the agent's first tool call, which was almost always a read, the agent usually read the already-changed code. Delivered context was then rarely stale, so the trials could not tell the arms apart. That is a defect in the harness, not an outcome.
- **New procedure:**
  1. Turn 1 asks the agent to plan the task without editing.
  2. The harness applies the change with the client stopped.
  3. Turn 2 resumes the same session and asks for the implementation. Claude Code resumes with `--resume <session id>`. Codex resumes with `thread/resume` in a new app-server process. Both fire the host's `SessionStart` hook with source `resume`.

  The rule does not depend on the arm. A trial whose agent made no tool call in either turn is `no_change_applied`.
- **Consequence for measurement:** an arm that refreshes context at resume (B's startup hook, and C, D and E) can deliver the change before the agent's first edit. A correction delivered then is not a *denial*. Part A's recall counted only a denied first necessary boundary. A4 below aligns recall with 07.

### A2 (2026-10-05): governed-mechanism fixture defect

D07 (`pricing`/`governed`) failed fixture seeding in all 16 of its trials: both of its claims used the revision id `r1`. These were `invalid_infrastructure` trials, retried twice each as the protocol allows. Commit `7455946` gave the claims unique revision ids. The 64 D07 records and attempts are kept in `output/phase5/pilot2/superseded-D07-seeding/`, and all 16 D07 trials were rerun on the fixed code. No other history is affected: the change is to D07's seed data only.

### A3 (2026-10-05): mode check added while the pilot ran

Commit `520c7d9` added the `off`/`shadow`/`live` mode check to `HostAdapter.dispatch` while the first pilot v2 trials were running from the development checkout. When no mode file exists, the mode is `live`, which is exactly the earlier behavior, and no trial writes a mode file. So no trial was rerun. From the first restart onward, trials run from a separate detached worktree, `D:/memex-phase5-run`, so later development cannot change code under running trials.

### A4 (2026-10-06): necessary-correction recall aligned with 07

- **Disclosure:** this amendment was adopted **after inspecting development data** (pilot v1 and the pilot v2 records then available), and **before any confirmatory trial** was generated or run. It was directed by the maintainer. It changes no gate threshold.
- **Replaces:** the recall row in Part A's metric table ("trials whose first necessary boundary was denied").
- **Reason:** 07 defines recall as *timely necessary corrections ÷ all labeled necessary correction opportunities*, with unavailable or missed deliveries counted as misses. Part A narrowed "timely correction" to a denial. Under A1, corrections arrive at resume, before any edit. So the narrow definition measured where a correction arrived, not whether the agent received a correct one in time.
- **Data seen when adopting it:** pilot v1, 26 trials. Pilot v2: 164 records, of which 112 are affected opportunities, 14 per arm. Under Part A's definition, recall was 0/14 for every arm in pilot v2. The A4 result for the same records is reported beside it in Part B, and was computed after this definition was written.

**New definition.**
- **Denominator:** every gold-labeled necessary correction opportunity. That is each trial of an affected history whose change landed, whatever its final status. Each opportunity is counted once. An opportunity is never excluded because the agent did not attempt an edit.
- **Numerator:** opportunities with at least one correction that meets all three conditions:
  1. **Correct.** It correctly replaces, retracts or revalidates the affected prior claim using the changed evidence, by the mechanical rule in `fixtures.correct_correction`. The text must name the affected claim (`c-contract`, or its original assertion) and do one of these:
     - mark it not current: a `needs_revalidation`, `unsupported`, `unknown` or `conflicted` status, or a `retract`, `replace`, `uncertain` or `conflict` delta;
     - for `superseded`, carry the newly approved assertion, with the old revision no longer shown as supported;
     - state that evidence it cites changed, naming one of the history's changed files.
  2. **Confirmed.** Its insertion into the agent's own session is confirmed by the client's record of that session: the Claude Code transcript or the Codex rollout (`memex/evaluation/transcripts.py`). Only text the client shows as inserted counts. That means hook context added to the session, or the text returned in place of a denied tool's result. A hook that ran, or text it emitted, is not confirmation.
  3. **Timely.** It arrives after the change has landed, and before the first affected mutation. That means either earlier in the session, or as the denial returned for that first mutation attempt.
- **Not sufficient:** a hook firing, generic fresh context, a claim still shown as supported, a correction of another claim, or the absence of a stale edit.
- **Misses:** a missing, unavailable, late, wrong or unconfirmed correction is a miss. This includes a correction that arrives only after the first affected mutation, such as E-norecon's deferred correction.
- **Same rule for every arm,** A through E-full. Confirmation never comes from an arm's own receipt, so D (which has no receipt) and B are judged exactly like E.
- **Interception** (Part A's original definition, a denied first necessary boundary) is kept as a separate diagnostic metric. It is reported but never gated.
- **Harness change:** trials record the session's confirmed insertions (harness `phase5-trials.v3-confirmed-insertions`). Records made before that change get theirs from the client records still on disk, through `tests/phase5_trials.py backfill`, which writes a sidecar beside each record and never modifies the record. The original analysis files are kept unchanged.
- **Tests:** `tests/test_phase5_recall.py` covers:
  - timely resume delivery and action interception;
  - late delivery and incorrect content;
  - missing confirmation and duplicate delivery;
  - an opportunity with no attempted mutation;
  - each arm's text format;
  - parsing of both clients' records.

## Part B: frozen confirmatory numbers

Status: **frozen.** Committed and pushed before any confirmatory trial. The machine-readable copy that the analysis reads is [22_PHASE5_FROZEN.json](22_PHASE5_FROZEN.json). Neither may change after the first confirmatory trial starts.

### Pilot data seen at freeze

**Pilot v1 (change after the first tool call; replaced by A1)**: development data, 26 trials, statuses {'completed': 26}.

| Arm | Stale failure (affected) | Stable completion | Recall, Part A (denial) | Recall, A4 | Interception | False interruption | Warm check p95 ms |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A | 0/4 (0%) | n/a | 0/4 (0%) | 0/4 (0%) | 0/4 (0%) | n/a | n/a |
| B | 1/4 (25%) | n/a | 0/4 (0%) | 0/4 (0%) | 0/4 (0%) | n/a | 1538 |
| C | 0/3 (0%) | n/a | 3/3 (100%) | 3/3 (100%) | 3/3 (100%) | n/a | 3603 |
| D | 0/3 (0%) | n/a | 3/3 (100%) | 3/3 (100%) | 3/3 (100%) | n/a | 1692 |
| E | 0/3 (0%) | n/a | 3/3 (100%) | 3/3 (100%) | 3/3 (100%) | n/a | 3665 |
| E-noinval | 1/3 (33%) | n/a | 0/3 (0%) | 0/3 (0%) | 0/3 (0%) | n/a | 3509 |
| E-norecon | 0/3 (0%) | n/a | 0/3 (0%) | 0/3 (0%) | 0/3 (0%) | n/a | 3479 |
| E-full | 0/3 (0%) | n/a | 3/3 (100%) | 3/3 (100%) | 3/3 (100%) | n/a | 3942 |

**Pilot v2 (two turns, A1)**: development data, 192 trials, statuses {'completed': 192}.

| Arm | Stale failure (affected) | Stable completion | Recall, Part A (denial) | Recall, A4 | Interception | False interruption | Warm check p95 ms |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A | 6/14 (43%) | 10/10 (100%) | 0/14 (0%) | 0/14 (0%) | 0/14 (0%) | 0/10 (0%) | n/a |
| B | 0/14 (0%) | 10/10 (100%) | 0/14 (0%) | 14/14 (100%) | 0/14 (0%) | 0/10 (0%) | 2191 |
| C | 1/14 (7%) | 10/10 (100%) | 0/14 (0%) | 14/14 (100%) | 0/14 (0%) | 0/10 (0%) | 4055 |
| D | 0/14 (0%) | 10/10 (100%) | 0/14 (0%) | 14/14 (100%) | 0/14 (0%) | 0/10 (0%) | 2179 |
| E | 0/14 (0%) | 10/10 (100%) | 0/14 (0%) | 14/14 (100%) | 0/14 (0%) | 0/10 (0%) | 4012 |
| E-noinval | 6/14 (43%) | 10/10 (100%) | 0/14 (0%) | 2/14 (14%) | 0/14 (0%) | 0/10 (0%) | 4313 |
| E-norecon | 0/14 (0%) | 10/10 (100%) | 0/14 (0%) | 14/14 (100%) | 0/14 (0%) | 0/10 (0%) | 4324 |
| E-full | 0/14 (0%) | 10/10 (100%) | 0/14 (0%) | 14/14 (100%) | 0/14 (0%) | 0/10 (0%) | 4809 |

Both analyses are kept:
- Part A's original definitions: `output/phase5/<pilot>/analysis-original-partA.json`, produced by the unchanged pre-A4 code at `7455946`.
- A4 definitions with these frozen values: `analysis-A4.json`.

Observations from the pilot that this freeze does not act on:

- **B also delivers a packet at resume.** B's hook skips the packet only for a session that is still bound, and the `SessionEnd` hook at the end of turn 1 unbinds it. So B behaves as a prefetch at every session start. That is its measured behavior throughout the pilot, and it explains B's 14/14 A4 recall. B is not a gate baseline and is not changed after seeing data.
- **E-noinval's 2 A4 hits are both D04 (`superseded`).** The ablation masks invalidation, not supersession, so the newly approved decision is still delivered.
- **Precision has no denominator under A1.** Part A counts a correction event as a denial at an action boundary, and in pilot v2 no arm denied any edit. A4 changes recall only, so the precision gate will be reported as `not_established` if that holds in the confirmatory trials. It is not reported as passed.
- **Efficacy fails on the pilot,** whether the strongest baseline is taken pooled (D), on Claude Code (C) or on Codex (D): that baseline had no stale-context failures. The warm-check latency gate also fails, by about 20×. Recall (A4), false interruptions, the tail deadline, the resource allowance and stable non-inferiority pass on the pilot.
- **Claude Code arm A has no client transcript reference** in the pilot records, because the session id was not kept for unhooked trials (now fixed). A has no hooks, so it has no insertion to confirm either way.

### Confirmatory design

- **Histories:** all 48 confirmatory histories, C01–C48: the four templates the pilot never used (`scheduler`, `pricing`, `config`, `users`) crossed with all 12 mechanisms. 28 are affected and 20 are stable. All 48 pass admission (`tests/test_phase5_fixtures.py`). None is added or dropped after this commit.
- **Arms:** A, B, C, D and E. The ablations answer a design question, not a gate. They are reported from the pilot only.
- **Hosts and models:** Claude Code with `claude-sonnet-5-5` (Anthropic), and Codex through the app-server with `gpt-6-sol` at medium reasoning effort (OpenAI). These are the two model families.
- **Replicates:** one per (history, arm, host). Replicates would be clustered within history and add no independent units.
- **Trial budget:** 48 × 5 × 2 = **480 trials**, plus the at most 2 automatic retries per trial that Part A allows for `invalid_infrastructure` and `unavailable`.
- **Code under test:** the commit that contains this Part B, checked out detached at `D:/memex-phase5-run`, and recorded in the results.

### Sample-size justification

- **Units:** the efficacy analysis has 28 affected histories × 2 hosts = 56 paired units in 28 history clusters.
- **Power:** by the paired-binary (McNemar) approximation in `analysis.required_units`, at α = 0.05 and 80% power, detecting a baseline stale-failure rate of 25% against 0% for E needs 29 units, 12.5% against 0% needs 61, and 20% against 10% needs 202. Clustering by history only raises these numbers.
- **What the pilot predicts:** in the pilot, the strongest comparable baseline had no stale-context failures (see the table above). If that holds, no sample size can show a 30% relative reduction, and Part A step 5 fails the gate.
- **Why the sample is not larger:**
  - 48 is every confirmatory history the four held-out templates produce.
  - Adding templates after seeing pilot results, to chase an effect, is exactly what the brief forbids.
  - The sample is therefore fixed, and it is adequate only to detect a large baseline failure rate.
- **The result is published either way.**

### Gate values

| Gate | Frozen value | Source |
| --- | --- | --- |
| Efficacy | relative reduction ≥ 0.30 vs the strongest of C and D, **and** the lower 95% bound of the difference > 0 | 07 |
| Confidence | history-cluster percentile bootstrap, 10,000 resamples, seed 20261005; template-cluster sensitivity | Part A |
| Correction precision (E) | ≥ 0.95 | 07 |
| False interruptions (E) | ≤ 0.05 of unaffected boundaries | 07 |
| Missed-correction tolerance (E) | at most 10% of necessary opportunities missed: A4 recall ≥ 0.90 | this freeze; definition A4 |
| Stable-task non-inferiority | for each of A, C and D: the lower 95% bound of (E − comparator) completion on stable histories > −0.10 | this freeze |
| Warm unchanged action check | p95 < 200 ms end to end | 07 |
| Tail deadline | every E action check, denied or not, answers within 10,000 ms end to end | this freeze |
| Total-resource allowance | E's mean wall time and mean output tokens per completed trial ≤ 1.25 × the strongest baseline's, each on the trials whose client reported it | this freeze |
| Isolation and authority | no violation in the mechanism tier | 07 |

Every gate is judged per host and pooled. A gate passes only if it passes in all three.

### Recall

- **Gate:** the recall gate uses A4's definition.
- **Interception:** Part A's denial-based definition is reported beside it for every arm, as a diagnostic.
- **Both definitions in the pilot:** both are reported for the pilot in the table above.
- **The tolerance (0.90) was set before A4** and is unchanged by it.

### Stopping rules

- **No early stop for efficacy or futility,** and no interim look at confirmatory outcomes. The analysis runs once, after the batch ends.
- **No extension.** The sample is never increased, whatever the result.
- **Usage limits:** if a client reports a usage limit, the operator may pause the batch until it resets. Trials that already reached a final `unavailable` status keep it. Only trials that never produced a record are run after the pause.
- **Environment kills:** a trial stopped by the machine (for example, Claude Code's low-memory process reaper) produced no record and no outcome. It is run again, and the number of such interruptions is reported.
- **Harness defects:** if a defect is found, the batch stops. The fix and every trial it affects are rerun and listed as an amendment. The frozen gate values do not change.
- **Ending the batch:** the batch ends when all 480 trials have a final status, or when 14 days have passed since it started. In the second case, the trials still missing are reported as `not_run`.
