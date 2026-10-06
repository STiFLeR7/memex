# Phase 5 verification: proof, migration and onboarding

Status: **W16–W18 executed, W19 reported. The confirmatory efficacy result is negative.** Live Context (arm E) did not reduce stale-context failures against the strongest comparable baseline. Version stays 0.9.0. Gate-by-gate decisions are in [24_PHASE5_RELEASE_READINESS.md](24_PHASE5_RELEASE_READINESS.md). Plan: [21](21_PHASE5_EXECUTION_PLAN.md). Protocol: [22](22_PHASE5_PROTOCOL.md).

Branch `codex/v1-phase5`. Windows 11 (build 26200, later 26300 after an update), Intel 16-CPU, Python 3.12.13, Neo4j Community 5.26.30 on JDK 21 as an isolated native fixture (page cache capped at 256 MB). No Docker was used in this phase. Clients: Claude Code 2.1.289 with `claude-sonnet-5-5`, and Codex 0.157.1 through the app-server with `gpt-6-sol` at medium reasoning effort. Both used the existing logins: no fresh login and no credential copying.

## Commits

| Commit | What |
| --- | --- |
| `4803667` | Fixture histories, comparison arms and analysis (W16) |
| `dc3f19d` | Plan (21) and protocol Part A (22), before the pilot |
| `d8f51f1` | Two-turn trial design (A1) |
| `520c7d9` | `off`/`shadow`/`live` modes (W18) |
| `eacdbcf` | Migration, rollback and diagnostics (W18) |
| `94f5642` | Host install commands and the deterministic contributor demo (W18) |
| `33179d6` | Onboarding (25) and the maintainer pilot kit (26) (W17/W18) |
| `35392c0` | F01–F16 mechanism tier and report generator (W16) |
| `7455946` | Governed-fixture defect fix (A2) |
| `d048648` | **Protocol frozen**: Part B and amendments A1–A4, pushed before any confirmatory trial |
| this commit | Results, this report, 24 and the index updates |

## W16: the evaluation harness

- **Mechanism tier (F01–F16):** each fixture's gold behavior is tied to executable tests on the real runtime (`python -m memex.evaluation.mechanism`). Result: ****16/16 fixtures passed; isolation and authority passed** (`output/phase5/mechanism-final.json`)**. A skipped test counts as a failure.
- **Native tier:** 7 template repositories (3 development, 4 confirmatory), 12 mechanisms each, admitted only if their hidden checks tell the gold patch from the stale one. The change lands between a planning turn and a resumed implementation turn (A1). Hidden checks run outside the repository, in the worlds before and after the change.
- **Arms:** A (no hooks), B (initial packet), C (fresh retrieval at each edit), D (hash-bound notes), E (Live Context), plus the ablations E-noinval, E-norecon and E-full in the pilot.
- **Recall:** amendment A4 counts a correction only if it is correct, confirmed in the client's own session record, and in time. The rule is the same for every arm. Denial-based interception is kept as a diagnostic.

## W17: development pilot

These are development data, never pooled with the confirmatory results. Pilot v2: 192 trials (12 histories × 8 arms × 2 hosts), all completed. Pilot v1 (26 trials, before A1) is in 22.

| Arm | Stale failure (affected) | Stable completion | Recall (A4) | Interception | False interruption |
| --- | --- | --- | --- | --- | --- |
| A | 6/14 | 10/10 | 0/14 | 0/14 | 0/10 |
| B | 0/14 | 10/10 | 14/14 | 0/14 | 0/10 |
| C | 1/14 | 10/10 | 14/14 | 0/14 | 0/10 |
| D | 0/14 | 10/10 | 14/14 | 0/14 | 0/10 |
| E | 0/14 | 10/10 | 14/14 | 0/14 | 0/10 |
| E-noinval | 6/14 | 10/10 | 2/14 | 0/14 | 0/10 |
| E-norecon | 0/14 | 10/10 | 14/14 | 0/14 | 0/10 |
| E-full | 0/14 | 10/10 | 14/14 | 0/14 | 0/10 |

The ablations show what drives stale failures in this design. Masking invalidation (E-noinval) brings them back to arm A's level, because the resumed agent is told the obsolete claim is still supported. Any arm that delivers current evidence at resume avoids most of them.

## W17: confirmatory trials

Run once, from the frozen worktree at `d048648`, and analyzed once with `python -m memex.evaluation.analysis output/phase5/confirmatory docs/v1/22_PHASE5_FROZEN.json`. Design: 48 histories (C01–C48, four held-out templates × 12 mechanisms, 28 affected and 20 stable) × arms A–E × 2 hosts = 480 trials.

**Statuses:** 479 `completed` and 1 `hook_error`. The hook error was C46-C-codex: a C action-check hook timed out (`TimeoutError`) and failed open. The trial is scored and listed, as Part A requires. No `timeout`, `client_error`, `unavailable` or `invalid_infrastructure` trial remains. No automatic retry was needed.

| Arm | Host | Stale failure (affected) | Stable completion | Recall (A4) | Interception | False interruption | Warm check p95 (ms) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A | Claude Code | 22/28 | 20/20 | 0/28 | 0/28 | 0/20 | n/a |
| A | Codex | 22/28 | 20/20 | 0/28 | 0/28 | 0/20 | n/a |
| B | Claude Code | 1/28 | 20/20 | 28/28 | 0/28 | 0/20 | 2,611 |
| B | Codex | 5/28 | 20/20 | 26/28 | 0/28 | 0/20 | 3,477 |
| C | Claude Code | 1/28 | 20/20 | 28/28 | 0/28 | 0/20 | 5,874 |
| C | Codex | 2/28 | 19/19 | 28/28 | 0/28 | 0/19 | 5,324 |
| D | Claude Code | **0/28** | 20/20 | 28/28 | 0/28 | 0/20 | 2,923 |
| D | Codex | **0/28** | 20/20 | 28/28 | 0/28 | 0/20 | 2,602 |
| E | Claude Code | 1/28 | 20/20 | 28/28 | 0/28 | 0/20 | 6,146 |
| E | Codex | 2/28 | 20/20 | 28/28 | 0/28 | 0/20 | 5,889 |

Pooled over both hosts: A 44/56 (79%), B 6/56, C 3/56, D **0/56**, E 3/56.

**Efficacy conclusion: E did not outperform C or D.** D, hash-bound notes, is the strongest comparable baseline pooled and on each host, with no stale-context failures. No relative reduction is possible against a baseline that never fails, so the gate fails by Part A's rule. In point terms, E had more failures than D. The history-clustered 95% interval for the difference (D − E) is −0.143 to 0.000. The template-clustered sensitivity interval is −0.107 to 0.000. Both baselines are reported as run: neither was weakened, and success was not redefined.

**Where E failed:** all 3 of E's stale failures are on the `removed` mechanism, where the dependency's function is renamed (C15 on Codex; C39 on both hosts). In every one, memex delivered a correct, confirmed correction before the edit. A4 recall counts it as timely. Yet the agent still wrote code against the old function. B and C failed on the same mechanism. D's resume warning explicitly tells the agent to re-read the changed file, and it had no failures.

So a correct and timely correction was not enough in these cases: the wording and the instruction to act on it mattered. This is a product finding for future work. It is not grounds to reinterpret the result.

**Other gates (E):**

| Gate | Pooled | Claude Code | Codex |
| --- | --- | --- | --- |
| Precision ≥ 0.95 | not established (0/0) | not established | not established |
| False interruptions ≤ 0.05 | passed, 0/40 | passed, 0/20 | passed, 0/20 |
| Recall (A4) ≥ 0.90 | passed, 56/56 | passed, 28/28 | passed, 28/28 |
| Warm check p95 < 200 ms | **failed**, 6,146 ms | failed, 6,146 ms | failed, 5,889 ms |
| Tail deadline: every check ≤ 10,000 ms | **failed**, max 11,200 ms | failed, 11,200 ms | failed, 10,726 ms |
| Resources ≤ 1.25× strongest baseline | passed: wall 1.07×, tokens 1.04× | passed: 1.08×, 1.01× | passed: 1.06×, 1.06× |
| Stable non-inferiority vs A, C, D (margin 0.10) | passed: difference 0, CI lower bound 0 | passed | passed |

**Latency:** the end-to-end warm check includes starting a Python process, connecting to the graph and re-capturing sources. The core's in-process check alone had a p95 of about 3.1–3.2 s. Latency in the confirmatory batch was higher than in the pilot (about 4 s), because 6 trials ran at once instead of 2 or 3.

**Precision** has no denominator. Under A1, no arm denied any edit: corrections arrived at resume. This was disclosed at freeze and is not reported as a pass.

**Resources:**
- 8.5 hours of agent wall time across 480 trials;
- about 691,000 output tokens, with usage known for all trials;
- Claude Code reported $31.41 for its 240 trials; Codex reports no cost, so its cost is **unknown**, not zero.

**Interruptions to the batch:** none affected an outcome, and all are logged in `output/phase5/confirmatory/restarts.jsonl`.
1. The operator raised parallelism from 2 to 3. The 3 trials in flight were rerun.
2. The operator raised parallelism from 3 to 6. The 3 trials in flight were rerun.
3. Windows rebooted for an update at 10:08. Four trials finished in the shutdown second (two `git init` failures and two killed clients). They are kept as `.attempt-reboot` files and were rerun, under Part B's rule for trials stopped by the machine.

## W17: maintainer pilot

The kit is ready: schema, validator, summary, consent and data minimization ([26](26_MAINTAINER_PILOT_KIT.md)). **No independent maintainer has been recruited or has reported, so this evidence is pending.** The project's own trials are not a substitute.

## W18: migration, rollback, diagnostics and onboarding

| Evidence | Test or command | Result |
| --- | --- | --- |
| Dry run counts and writes nothing | `test_a_dry_run_reports_counts_and_writes_nothing` | passed |
| Legacy data, authority and history preserved | `test_migration_preserves_legacy_data_authority_and_history` | passed |
| A v0.9 reader gives identical results after migration and after rollback | `test_the_v09_reader_sees_identical_results_after_migration_and_rollback` | passed |
| Interrupted migration resumes; a repeat writes nothing | `test_an_interrupted_migration_resumes_and_a_repeat_writes_nothing` | passed |
| A process killed mid-migration resumes from its checkpoint | `test_a_process_killed_mid_migration_resumes_from_its_checkpoint` | passed |
| Legacy knowledge never enters a verified packet | `test_legacy_knowledge_never_enters_a_verified_packet` | passed |
| Worktrees share a repository mapping; clones do not | `test_worktrees_share_a_repository_and_clones_do_not` | passed |
| Schema change is additive | `test_migration_schema_is_additive` | passed |
| Re-enabling live forces resynchronization | `test_modes_switch_and_re_enabling_live_forces_resynchronization` | passed |
| Rollback disables v1, expires leases, keeps recovery state | `test_rollback_disables_v1_expires_leases_and_keeps_recovery_state` | passed |
| `off` and `shadow` deliver and deny nothing | `test_off_and_shadow_deliver_and_deny_nothing` (3 modes) | passed |
| Diagnostics are actionable, including on a fresh install | `test_diagnostics_are_actionable`, `test_doctor_on_a_fresh_install_says_what_to_do_next` | passed |
| Deterministic contributor demo | `python -m memex.evaluation.core_demo`; `test_core_demo_holds_the_stale_edit_and_nothing_else` | passed |
| Mutation-before-edit demo, native | `tests/phase5_trials.py one D01 E claude` (25) | ran in every E trial; see the native tier |

Onboarding, host recipes and demos are in [25](25_ONBOARDING.md). Windows is validated.

**Linux (added 6 October 2026, Docker):** Debian (python:3.12-slim) under Docker's WSL2 kernel, Python 3.12.15, Neo4j 5.26.31, commit `8c56d0c` with the Windows lockfile: broad suite 915 passed (11 platform skips), Phase 5 205 passed, mechanism 16/16 with isolation and authority passed, core demo as expected; the migration suite passed again in a fresh container. Claude Code and Codex were not run natively on Linux. The run found two test-portability defects, both fixed in `8c56d0c` with no product change: the migration fixture reused temporary paths across fresh containers and left its legacy nodes behind, which doubled counts on a second run; and the Windows-only client-config cleanup tests ran on Linux, where the tool deliberately writes nothing. **macOS is not validated.**

## Final validation

| Suite | Command | Result |
| --- | --- | --- |
| Broad compatibility | `pytest tests -m "not integration" -q` | **919 passed, 1 skipped**, 172 deselected (integration) |
| Phase 5 | `pytest tests/test_phase5_*.py -q` (live graph) | **200 passed** (live graph) |
| Mechanism tier | `python -m memex.evaluation.mechanism` | **16/16 fixtures passed; isolation and authority passed** (`output/phase5/mechanism-final.json`) |
| Lint | `ruff check --select E,F,B,W --line-length 120` on Phase 5 code | **All checks passed** |
| Dependencies | `uv pip check` | **All installed packages are compatible** |
| Package | `uv build` and `twine check` | **sdist and wheel built at 0.9.0; both PASSED** |

## Evidence location

The trial records, analyses, insertion sidecars, logs and the run's restart log are in `output/phase5/` in this worktree. They are copied to `C:\Users\stifl\memex-phase5-artifacts`. Raw transcripts are not kept in the evidence: only memex's own inserted texts and their times.

## Limitations

- **Synthetic repositories:** the native tier uses small template repositories with one dependency contract each, not real projects.
- **Resume timing (A1):** under A1 the change always lands while the session is stopped, so the resume packet does most of the work. Mid-turn changes, where action-time interception would matter, were not part of the confirmatory design.
- **Recall amendment (A4):** A4 was adopted after inspecting development data. It was disclosed and frozen before the confirmatory trials.
- **Arm B also re-delivers at resume:** its session is unbound at the end of each turn, so B is a stronger baseline than a pure one-time prefetch.
- **Confirmed insertion depends on undocumented formats:** confirmation relies on the clients' undocumented transcript and rollout formats.
- **Container benchmarks not run:** SWE-Milestone was not run, by maintainer decision, because its harness needs about 115 Docker images and runs agents inside its containers with API keys. Evaluation stays local. No official benchmark score is claimed.
