# Phase 5 execution plan

Status: executing W16–W19 from `master` at `4db0266` on `codex/v1-phase5`, in a separate worktree. Measured results go in [23_PHASE5_VERIFICATION.md](23_PHASE5_VERIFICATION.md). The release decision goes in [24_PHASE5_RELEASE_READINESS.md](24_PHASE5_RELEASE_READINESS.md). The statistical protocol is [22_PHASE5_PROTOCOL.md](22_PHASE5_PROTOCOL.md).
Spec: [07_EVALUATION.md](07_EVALUATION.md), [10_MIGRATION_AND_RELEASE.md](10_MIGRATION_AND_RELEASE.md), [11_DECISIONS_AND_RISKS.md](11_DECISIONS_AND_RISKS.md) (S04).

## Constraints

No Docker, merge, release tag, package publication or site deployment; version stays 0.9.0 until every mandatory gate has evidence. Native trials use the Claude Code and Codex logins already on the machine: no new login, no copied credentials. Codex runs only through its app-server, because `codex exec` runs no hooks (S02). Client configuration written by trials is tracked and removed with `tests/phase4_client_config.py`. The 26 older trust entries and all unrelated settings stay unchanged.

Gates are not edited after results are seen. Repeated actions and seeds within one history are not independent samples. A skipped, failed or unavailable trial is reported, never counted as a pass. If E does not beat C or D, that is the published result.

## Environment

- `uv sync --python 3.12` in the worktree, CPython 3.12.13. The lock file is gitignored, so its hash and key versions are recorded in 23.
- Neo4j Community 5.26.30 with Temurin JDK 21, under the worktree's uncommitted `output/phase5/native`, listening only on `127.0.0.1:17687`, with no authentication and no HTTP.
- Claude Code 2.1.289 and codex-cli 0.157.1, as installed.

## W16: evaluation harness and S04

### Two tiers of evidence

1. **Mechanism tier (deterministic).** Each of F01–F16 has an executable check against the real runtime: real Git, SQLite and the native Neo4j fixture, but no model. Most mechanisms already have Phase 1–4 tests. `memex/evaluation/mechanism.py` maps every fixture to the tests that establish it. `tests/test_phase5_mechanism.py` adds the checks that were missing, and the report lists each fixture's evidence. This tier backs the mechanism gate; it says nothing about efficacy.
2. **Native comparison tier.** Real agents perform tasks in fixture repositories while evidence changes under them. This tier backs the efficacy, precision, quiet-behavior, latency and generalization gates.

### Histories

A history is a small repository, a task, one controlled change and hidden tests. It is generated from a **template** (a small codebase with a dependency contract) and a **mechanism** (how the change arrives). Templates are distinct codebases, so different templates count as different repositories.

| Mechanism | Fixture | Change | Label |
| --- | --- | --- | --- |
| `direct` | F01 | The dependency's behavior changes in place | affected |
| `helper` | F01 | Body-only change in a helper the dependency calls | affected |
| `removed` | F02 | The dependency function is renamed; the old name no longer exists | affected |
| `superseded` | F03 | An approved decision is superseded by a newer approved decision | affected |
| `governed` | F06 | A governed file changes; the approved rule still applies | affected |
| `merged` | F09 | The change arrives by merging a branch, with conflict resolution | affected |
| `checkout` | F13 | The change arrives through a branch checkout, not a file event | affected |
| `unrelated` | F04 | An unrelated file is edited | stable |
| `alternative` | F05 | One alternative support changes; another still holds | stable |
| `worktree` | F08 | The change lands in another worktree and is not merged | stable |
| `cosmetic` | — | A comment-only edit to a supporting file | stable |
| `control` | — | No change | stable |

F07, F10, F11, F12, F14, F15 and F16 are established in the mechanism tier and the Phase 4 native gates. They are not efficacy trial conditions: a parse failure, an outage or a budget overflow is a coverage behavior, not a task outcome an arm can improve.

Each history carries a verified **gold** patch for its post-change world and a **stale** patch written from the original contract. Before any trial, a history is admitted only if its hidden tests pass the gold patch and, for an affected history, fail the stale patch through the specific stale probe. Histories that do not discriminate are rejected before trials, not after.

### Arms

Every arm gets the same task prompt, the same repository and history, the same README, the same tools, the same turn limit and the same model. The prompt never mentions memex.

| Arm | At session start | Before each declared mutation |
| --- | --- | --- |
| A | Nothing beyond the repository and its docs | Nothing |
| B | The v0.9-style initial packet of claims | Nothing |
| C | The same packet | A fresh retrieval (a new packet from current evidence). If it differs from what this session was last given, the edit is denied with the fresh packet; otherwise it proceeds silently |
| D | The same claims as hash-bound notes, each citing its files' hashes | If any cited file's hash changed since the session last saw it, the edit is denied with a re-read warning naming the notes and files, and the hashes are rebound |
| E | The same packet, with a confirmed delivery receipt | The full Live Context check: scoped revalidation and a session-relative correction |

C, D and E have the same opportunities: one check before every declared mutation, the same ability to deny and ask for reconsideration, and the same two-reconsiderations-per-action ceiling. C is deliberately strong: it detects change by comparing content, which is the best a stateless retrieval can do. C's and E's packets share one budget.

Ablations, as variants of E:
- **E-noinval:** support changes never invalidate, so action checks always proceed.
- **E-norecon:** a correction is delivered after the action, as context on the next tool result, but the pending action is never denied.
- **E-full:** a correction carries the full current working set instead of a delta.

### Controlled timing and leakage

- **Change timing:** the change lands immediately after the agent's first completed tool call of any kind, through a separate fixture process. The rule doesn't depend on the arm. If the agent's first call is the edit itself, the change still follows it, and the history is scored as usual.
- **Fresh state per trial:**
  - every trial gets a new temporary repository, so a new repository and worktree identity;
  - a new graph namespace, keyed by that identity;
  - a new host session.
- **Nothing for the agent to find:**
  - hidden tests and gold patches live only in the harness, outside the repository;
  - the post-change file content exists only inside the fixture writer.
- **Fixed inputs:** each trial records the fixture, template and harness versions, client and model versions, the prompt hash and hardware.

### Trace joins

A trial's record joins, by time and attempt identity:
- the change event;
- deliveries (packet, correction, warning) and their insertion evidence where the arm has it;
- every mutation proposal, recorded by a fixture hook in every arm, including A;
- each arm's decision;
- reconsideration: a later attempt on the same target after a denial;
- executions;
- the final file state and the hidden-test result.

Exposure, acknowledgement, compliance and the objective outcome stay separate fields.

### Measurement

Per trial:
- stale-context failure (the stale probe fails on the final state);
- functional completion (all hidden tests pass);
- correction events, and whether each was material;
- unaffected action boundaries, and which were interrupted;
- retries;
- hook latency (end to end, process creation to response, and the in-process check);
- delivered characters;
- indexing work (source captures, files hashed);
- model usage as reported by the client;
- wall time;
- failures and timeouts.

Missing usage is recorded as unknown, never zero.

### Analysis

Defined in 22: paired comparisons within history, clustered by history and by template, with bootstrap confidence intervals. The 12 development histories run first, as the pilot. The pilot sets sample size and margins, which are frozen and committed in 22 before any confirmatory history is generated. Confirmatory histories use templates the pilot never used.

## W17: native comparisons and maintainer pilot

- **Pilot:** 12 development histories × arms A–E plus ablations × Claude Code and Codex. Its results are reported as pilot, never as confirmatory.
- **Freeze:** sample size, margins, budget and stopping rules are frozen in 22, committed and pushed.
- **Confirmatory trials:** on disjoint templates, both hosts, two model families (Anthropic Claude through Claude Code, OpenAI GPT through Codex). Analyzed only as frozen.
- **Maintainer pilot kit:** under `docs/v1/pilot/`:
  - install and host recipes;
  - an observation log schema;
  - weekly check-ins;
  - consent and data-minimization rules;
  - a randomization sheet;
  - an analysis script.

  Real participants are required; until they report, the evidence is pending.

## W18: migration, rollback, diagnostics, onboarding

- **`memex v1 migrate`:**
  - detects versions;
  - dry-run counts before any write;
  - stable repository and worktree mapping;
  - additive v1 labels with migration markers;
  - eligible legacy decisions and entities imported into a separate `legacy_unverified` projection, preserving approved authority, timestamps and confidence;
  - bounded batches with checkpoint, resume and idempotency keys.

  Legacy nodes are never rewritten.
- **`memex v1 mode`:** `off`, `shadow` or `live` per worktree, with the guarded-write option independent. Shadow records what would have been corrected without claiming a correction.
- **`memex v1 rollback`:**
  - disables live adapters and guarded writes;
  - expires cooperating leases;
  - keeps the journal, mapping and evidence;
  - legacy readers keep identical results.

  Re-enabling catches up and forces resynchronization.
- **`memex v1 doctor`:** actionable freshness and capability diagnostics.
- **Onboarding:**
  - an existing-Neo4j setup recipe;
  - one recipe per host;
  - a mutation-before-edit demo;
  - a clearly labeled deterministic contributor demo using temporary Git and test doubles.

  Windows is validated here. Linux is reported untested unless a Linux environment that isn't Docker becomes available.

## W19: release readiness

24 takes each gate in 07 in turn and records:
- its command;
- its denominator;
- its confidence result;
- its resources and failures;
- whether it passed, failed, is pending or was not run.

Container benchmarks remain not run. Version, changelog, package and README change only if every mandatory gate passes.

## Order

1. Commit this plan, the pre-pilot part of 22 and the fixture and arm code with their deterministic tests.
2. Run the pilot.
3. Freeze and commit 22, then run the confirmatory trials. W18 is built while trials run.
4. Write 23, 24, the pilot kit and the index updates, then commit and push.
