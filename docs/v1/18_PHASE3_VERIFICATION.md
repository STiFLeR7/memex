# Phase 3 verification and native host boundary

Status: W09–W11 complete and verified. The native ordering gate passed against the installed client. Date: 5 October 2026.
Branch: `codex/v1-phase3`, based on verified Phase 2 commit `3194450`. Plan: [17_PHASE3_EXECUTION_PLAN.md](17_PHASE3_EXECUTION_PLAN.md).

Commits: `28bebb6` (W10 trace), `203fd0f` (W09 adapter), `774c162` (native gate test), `e612ca9` (W11 projection), `87b1787` (review fixes).

## S01: measured host semantics

Spike S01 ran against **Claude Code 2.1.289** before any adapter was designed, because 06 required the installed client's behavior to be established rather than inferred from hook names. Each finding below was produced by a real headless run with instrumented hooks that recorded the target file's hash at every hook event, so the ordering of "check" against "write" is observed, not assumed.

| # | Measured behavior | Evidence | Consequence |
| --- | --- | --- | --- |
| S01-1 | `PreToolUse` runs in non-interactive `-p` mode | Hook events recorded for `Edit` in a headless run | The gate is reachable from an automated session |
| S01-2 | `permissionDecision:"deny"` prevents the pending mutation | Target hash identical at the denial and at the next hook event; no `PostToolUse` for the denied call | Deny is the gate |
| S01-3 | The denial reason reaches the model as an `is_error` tool result | `PreToolUse:Edit hook error: <permissionDecisionReason>` in the stream | Reconsideration is native, not advisory |
| S01-4 | `deny` is honored under `--permission-mode bypassPermissions` | `a.py` unchanged after a denied parallel edit in that mode | The gate needs no permission-policy change |
| S01-5 | A hook exceeding its configured `timeout` does **not** block | 3 s timeout, 12 s hook, `c.py` written anyway | Fail-open on overrun; the adapter needs its own inner deadline |
| S01-6 | Parallel edit calls were **serialized**, not batch-checked | `PreToolUse(a) → PreToolUse(b) → PostToolUse(b) → PreToolUse(c) → PostToolUse(c)`; a sibling write completed before the next check ran | A check certifies its own declared target at its own moment only |
| S01-7 | `permissionDecision:"defer"` ends a non-interactive turn | `stop_reason: tool_deferred`; the pending action was abandoned, not permitted | `defer` is not a fail-open; abstaining is |
| S01-8 | The model verified a correction it could disprove and overrode it | Assistant text: "the hook's 'changed' claim looks stale", then a correct retry | A correction must be true; memex must gate on measured drift |

Observed `PreToolUse` input fields: `session_id`, `prompt_id`, `transcript_path`, `cwd`, `permission_mode`, `hook_event_name`, `tool_name`, `tool_input`, `tool_use_id`, `effort`. `additionalContext` is capped at 10,000 characters.

S01-7 is the finding that changed the design most. An earlier adapter returned `defer` for unknown freshness; the native run then ended with the revised edit never executing. S01-4 and S01-7 together fix the policy: **the adapter emits `deny` or emits no decision at all.** It subtracts permission and never grants it, so a memex outage cannot auto-approve a tool the user's policy would have questioned, and cannot abandon the action either.

S01-8 is the reason the phase demonstration uses genuine drift. A correction the agent can disprove gets correctly overridden, which is the agent behaving well. Loudness is not the product; being right is.

## Implemented responsibilities

| Work | Implementation | Evidence |
| --- | --- | --- |
| W09 | `memex/integrations/claude_code.py`: capability-file authentication, worktree binding, open/check/ack/close lifecycle, deny gate, target normalization, bounded attempts and deadlines, composable hook installation | [Adapter tests](../../tests/test_live_claude_adapter.py), [native gate](../../tests/test_phase3_native_loop.py) |
| W10 | `memex/runtime/trace.py`: bounded SQLite projection separating delivery exposure, agent compliance and execution, with digest-only tool input | [Adapter tests](../../tests/test_live_claude_adapter.py) |
| W11 | `memex/integrations/mcp_live.py`: bounded authorized resource projection, measured capability negotiation, polling fallback; Hermes untouched | [Compatibility tests](../../tests/test_phase3_compat.py) |

**Authentication.** A hook payload is not an authenticator; anything able to run the entry point could place a principal on stdin. The principal is read from an owner-only capability file under the Git common directory (`<common_dir>/memex/adapters/claude-code.json`). `memex_session_id` is an HMAC of the native session ID under that file's secret, so a forged native session ID cannot be aimed at another principal's delivery stream. The payload's `cwd` must resolve to the registered working tree, compared by path before any identity is allocated.

**Expected hashes.** `ActionRequest.expected_hashes` is derived from the task's own acknowledged baseline, specifically the `support_hashes` of the items memex delivered to that session. A denial is therefore backed by the bytes behind the context the agent was actually given, not by a guess about what it read.

**Action kinds.** `Edit`, `Write`, `MultiEdit` and `NotebookEdit` declare targets and are gated. `Bash` and other opaque tools are recorded with `scope_complete=False` and never denied, because undeclared mutation coverage is not certified. A target outside the registered working tree is recorded and not served, rather than blocked.

## The native ordering proof

One real `claude -p` run in a temporary Git repository, with memex's hooks installed in the repository's own settings file alongside an unrelated hook that plays the concurrent contributor.

The drift is deliberately placed in a file the agent is **not** editing. `api.py` holds `send()`; `validate.py` holds `validate()`; one claim depends on both files as a single necessary (AND) support set. After the agent reads `api.py`, the external writer changes `validate.py` only. The agent's own target bytes are byte-identical across the denial, so the host's stale-read detection cannot see this drift. Only the claim's evidence moved.

An earlier draft of this test drifted the target file itself. The `Edit` tool's own `old_string` match would have caught that, so it proved nothing specific to memex. That draft was discarded.

Recorded trace from the final run:

| # | Event | Gate | Outcome | Insertion | Reconsidered | Objective |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `session_start` | – | – | `accepted` | – | – |
| 2 | `action_check` (`api.py`) | `prevented` | `replan` | `accepted` | `1` | – |
| 3 | `action_check` (`api.py`) | `advisory` | `unavailable` | `none` | – | `passed` |
| 4 | `action_executed` (`api.py`) | `executed` | – | – | – | `unknown` |
| 5 | `session_end` | – | – | – | – | – |

Row 3 carries `original_attempt_id` equal to row 2's `attempt_id`, under a different `attempt_id`: the reconsidered action is a new attempt linked to the original, so the core's two-attempt ceiling applies to it.

The correction the model received, verbatim from the run's stream:

```text
PreToolUse:Edit hook error: memex: outcome=replan reason=context_changed_or_delivery_pending
targets: api.py
- uncertain: c-send-never-raises@r1: validate() returns True for every payload, so send()
  never raises for an empty payload and callers need no error handling
    Recheck prior delivered assertion before this action: hash_matches;source_hash_changed
Re-read each listed target and reconsider this action against its current contents.
Do not reuse the earlier contents.
```

`hash_matches;source_hash_changed` is the mechanism in one line: the target's own hash still matched, and the claim's other support did not.

The model then read `validate.py`, which it had not read before, and recorded the repaired premise in its own words: *"`validate()` raises `ValueError` on an empty payload, so `send()` can raise."* It then issued the revised edit, which executed.

Objective check on the final state, asserted by the test rather than inspected: `api.py` parses; `send`'s first two parameters are `payload, timeout` with no defaults, so the requested parameter is required; and `validate.py` still contains the external contributor's `raise ValueError("payload required")`, so the revised write preserved the concurrent change instead of clobbering it.

The gate passed on three independent runs: twice in the clean environment and once in the system environment, with the same trace structure each time.

### What this proof does and does not establish

It establishes that for a declared edit action in this host mode, memex delivered context, detected real drift in that claim's evidence, prevented the affected mutation before it executed, delivered a correction that reached the same session, and that the agent reconsidered and produced a revised action which executed and satisfied an objective assertion.

It is one scenario with one model and one claim shape. It is not an efficacy measurement, not a latency or cost result, and not a comparison against fresh retrieval or hash-bound notes. Those remain P5 and are the gates that decide whether the defining v1 claim survives.

## Executed evidence

All results below are from this phase; no historical count is reused.

| Run | Command | Result |
| --- | --- | --- |
| Phase 3 mechanism suite | `pytest tests/test_live_claude_adapter.py tests/test_phase3_compat.py -q` | **34 passed**, no skips |
| Native ordering gate | `MEMEX_PHASE3_NATIVE=1 pytest tests/test_phase3_native_loop.py -q` | **1 passed** |
| Phase 1 + Phase 2 inherited | 12 Phase 1/2 files, listed in Reproduce | **74 passed**, no skips |
| Broad compatibility | `pytest tests -m "not integration" -q` | **679 passed, 1 skipped, 76 deselected** |
| Dependency check | `pip check` | No broken requirements |
| Lint | `ruff check --select E,F,B,W --line-length 120` on all changed files | Passed |
| Whitespace | `git diff --check HEAD` | Passed |
| Package | `python -m build`, `twine check` | Both passed; all three new modules present in the wheel |

The 34 and 679 figures overlap: 10 of the 34 are the non-integration compatibility tests counted in the 679. The 679 is Phase 2's 669 plus exactly those 10. The 76 deselected are integration-marked cases, 25 of them added by this phase; the broad selector does not claim they ran.

The one skip is the pre-existing `test_path_traversal_symlink_escape_is_rejected`, disabled on Windows since Phase 1.

### Not run, and why

`tests/test_phase2_e2e.py` (2 integration cases) fails in this environment and was **not** made to pass. Both assert that Graphiti `Episodic` nodes appear in Neo4j, which requires real Gemini LLM and embedding calls. The isolated fixture uses `GEMINI_API_KEY=test-not-used`, so the synthesis call cannot succeed. These belong to the legacy provider-dependent set that Phase 2 also did not run.

This is not a regression. `git diff HEAD --stat` against the Phase 2 baseline is empty for every tracked file: all Phase 3 work landed as new files, and neither failing test imports anything added by this phase.

### Environment

Clean Python 3.12.4 virtual environment created for this phase, `pip install -e . ruff pytest pytest-asyncio hypothesis build twine`, plus `networkx==3.7` and `graspologic-native==1.3.1` for the legacy cluster tests. No global Python installation was modified.

Tested versions: Graphiti 0.30.2, Neo4j driver 6.3.1, FastAPI 0.142.2, Pydantic 2.13.5, pytest 9.1.1, pytest-asyncio 1.3.0, MCP SDK 1.30.0, tree-sitter-language-pack 1.21.0, Ruff 0.16.10. Host client: **Claude Code 2.1.289**; model reported by the run: `claude-opus-5-5`. Codex CLI 0.157.1 is installed but untouched: S02 and W12 are P4.

Native server: Neo4j Community 5.26.30 Windows ZIP with Temurin JDK 21.0.12.1, recreated from the Phase 1 recipe because the previous phase's runtimes were deleted at cleanup. Bolt listened on `127.0.0.1:17687` only; HTTP, HTTPS, authentication and usage reporting were disabled. No Docker was started or used. No real provider credentials and no paid model trials; the native run used the already-authorized local client.

An initial Neo4j start failed because the appended fixture configuration duplicated `server.bolt.enabled`, `server.http.enabled` and `server.https.enabled`, which the shipped `neo4j.conf` already declares. The duplicates were commented out and `neo4j-admin server validate-config` passed before the server was started. This is fixture setup, not a product defect.

## Mechanism coverage

| Case | Executed behavior |
| --- | --- |
| Native mutation-before-edit ordering | Real client run; pending write prevented, target bytes unchanged at denial, execution strictly after the denial |
| Principal cannot be chosen by the payload | A payload naming another principal and harness still derives the registered principal; distinct native sessions derive distinct memex sessions |
| Session/worktree spoofing | A payload naming another working tree is refused, and the refusal writes no memex state into that repository |
| Unregistered repository | Missing capability file refuses the session |
| Unrelated change | A disjoint action is not interrupted |
| Opaque shell action | Recorded with incomplete scope, never denied; the command text is not retained |
| Target outside the registered worktree | Recorded and not served |
| Revoked source access | Refused without disclosing assertion text |
| Revoked access on attempt replay | A cached hook answer stops disclosing the correction once access is revoked |
| Backend outage | Explicit unknown freshness, `insertion='none'`, no receipt |
| Outage with an unacknowledged correction | The pending correction survives and the action still requires replan |
| Adapter deadline exceeded | Fail-open advisory with an explicit reason; no fabricated receipt |
| Cancellation | No receipt and no certificate recorded |
| Attempt replay | Idempotent decision and identical reason text for a repeated `tool_use_id` |
| Bounded reconsideration | The ceiling is reached and the correction tells the agent to stop retrying and surface the conflict |
| Packet overflow | `resync_required`, rendered as a resynchronization request |
| Compaction | A fresh task with a full replacement packet; retention is never assumed |
| Batched actions | Per-target gating: one target prevented while a disjoint sibling proceeds |
| Permission policy and user hooks | Existing hooks and `permissions` survive installation; reinstall does not duplicate; uninstall removes only memex entries |
| Trace hygiene | No source text, edit strings, command bodies or transcript paths in the trace; tool input is a digest |
| Tool surface | Still fourteen tools, asserted against the committed server |
| MCP authorization | Another session sees no resources and cannot read by URI; an unauthenticated principal is refused |
| MCP negotiation | The installed SDK advertises `resources.subscribe=false`; the polling fallback is selected |
| Hermes compatibility | `prefetch` signature, timeout and budget defaults, empty tool schema list and transcript-free `sync_turn` all unchanged |

These are deterministic mechanism tests plus one native-host trace. They are not an external benchmark, a model-efficacy measurement, or evidence about any host other than the one version recorded above.

## Review and fixes

The branch was reviewed against the review-focus list in the execution plan. Four defects were found and fixed in `87b1787`, each with a test that fails without the fix.

| Finding | Severity | Verified correction |
| --- | --- | --- |
| A replayed `tool_use_id` returned the stored correction text without re-entering the core, bypassing its cached-attempt authorization guard, so a principal whose source access had just been revoked still received the assertion | Critical | Replay re-enters the core and only reuses rendered text once that check passes. The regression test returns the full correction without the fix |
| The worktree check called `discover_repository`, which allocates and writes identity files, leaving memex state in an unrelated repository purely to reject it | Important | Working-tree paths are compared before any identity is allocated; a test asserts no `.git/memex` appears in the rejected repository |
| The `compact` branch of `SessionStart` was dead: it cleared the retention flag on the old binding and the following bind immediately reset it | Important | Removed. Every `SessionStart` already opens a task whose first packet is a full replacement, and a test pins that redelivery |
| The observed-hash read was unbounded, so a pathological target could be read entirely into memory inside a hook that has a deadline | Minor | Bounded at the same per-file ceiling the source capture uses |

Two defects were also found and fixed while bringing the mechanism suite from red to green, before review: the adapter overwrote the root denied attempt with the most recent one, which broke the core's retry-chain linkage and disabled the reconsideration ceiling; and the rendered correction was recomputed on replay from a baseline the first correction had already advanced, so a repeated `tool_use_id` produced different text. Both have regression tests.

## Reproduce

Create a clean Python 3.12 environment and install the checkout as described under Environment. Start an isolated native Neo4j server using the Phase 1 recipe in [14_PHASE1_VERIFICATION.md](14_PHASE1_VERIFICATION.md), keeping its console process alive, and comment out the three listener keys the shipped configuration already declares before appending fixture settings.

```powershell
$env:MEMEX_PHASE1_NEO4J_URI='bolt://127.0.0.1:17687'
$env:NEO4J_URI='bolt://127.0.0.1:17687'; $env:NEO4J_USER='neo4j'
$env:NEO4J_PASSWORD='test-not-used'; $env:GEMINI_API_KEY='test-not-used'

python -m pytest tests/test_live_claude_adapter.py tests/test_phase3_compat.py -q
python -m pytest tests/test_live_supports.py tests/test_live_claim_graph.py tests/test_live_tasks.py `
  tests/test_live_actions.py tests/test_phase2_review_regressions.py tests/test_live_repository.py `
  tests/test_live_graph.py tests/test_live_recovery.py tests/test_live_daemon.py `
  tests/test_live_hooks.py tests/test_live_routing.py tests/test_phase1_review_regressions.py -q
python -m pytest tests -m "not integration" -q
python -m pip check; git diff --check HEAD
```

The native gate additionally needs the installed Claude Code CLI and consumes a real model call, so it is opt-in:

```powershell
$env:MEMEX_PHASE3_NATIVE='1'; $env:PYTHONPATH=(Get-Location).Path
python -m pytest tests/test_phase3_native_loop.py -q
```

Without `MEMEX_PHASE3_NATIVE=1` that test skips, and such a run does **not** satisfy this phase gate. Without `MEMEX_PHASE1_NEO4J_URI` the native-graph tests skip, which likewise does not satisfy it.

The fixture environment variable names are inherited deliberately so all three phases share one isolated server. The fabricated Neo4j and Gemini values exist only to let legacy configuration construct its clients; they authorize no provider traffic.

## Limitations

These are properties of the integration as measured, not open work items hidden in prose.

**The gate is a check, not a lock.** `proceed` certifies the declared scope at the moment of the check. Nothing here stops an external editor, a shell command, a Git operation or another machine from writing between the check and the host's write. S01-6 measured sibling edit calls in a single model turn being serialized, with one write completing before the next check ran, so even within one agent turn a check speaks only for its own target at its own moment. Guarded writes with leases and fencing are P4.

**A hook timeout is a hole in the gate.** S01-5 measured the host proceeding when a hook overruns its configured timeout. The adapter keeps an inner deadline and records a fail-open advisory, but a sufficiently slow or wedged check does not prevent the write. The inner deadline must stay comfortably below the configured hook timeout for the gate to hold.

**Opaque actions are not covered.** A `Bash` command can mutate any file without declaring a target. Those actions are recorded and explicitly not certified. memex does not claim to gate them.

**A receipt is exposure, not compliance.** `insertion='accepted'` means the host accepted this hook's output, which is the only insertion signal the hook API exposes. It is not proof that the text entered the model's context, and never proof that the agent complied. Compliance is tracked separately as whether a later attempt arrived, and execution success separately again as an objective result. A host-side rejection the adapter cannot observe would still be recorded as accepted; that residual is why the three dimensions are distinct columns rather than one status.

**MCP subscription is unavailable, not merely unused.** The installed SDK derives `ResourcesCapability` with `subscribe` hardcoded to false whenever a list-resources handler is registered, so this server cannot advertise resource subscription at any protocol version. Clients poll. Had subscription been available, a resource update would still have been only a signal to fetch.

**Scope.** One host, one version, one model, one claim shape, one worktree. Simultaneous Claude Code and Codex operation, per-worktree overlays, shared-checkout coordination and guarded writes are P4. Statistical efficacy, resource accounting, migration and release claims are P5. No v1 release claim follows from this phase.

## Phase 4 handoff

Entry state: `codex/v1-phase3` at `87b1787`, not merged and not deployed. Version remains **0.9.0**; completing P3 is not the v1 release.

What P4 inherits:

- A working native gate and its measured host contract. S01's findings are recorded above with their evidence; do not re-derive them, but **do** re-measure against the Codex client rather than assuming the Claude findings transfer. S02 is still open, and S01-7 is exactly the kind of difference that will not generalize.
- `ClaudeCodeAdapter` and its capability-file trust model, reusable for a second host. The adapter is deliberately thin: the core API was frozen for this phase and was not changed.
- `TraceStore`, whose schema already carries `harness` and distinct native and memex session identities, so two concurrent hosts can be told apart without a migration.
- The abstain policy. A second adapter must preserve it: emitting `allow` would let a host bypass the user's permission policy on a memex outage.

What P4 must build, with no part of it started here:

| Work | Requirement |
| --- | --- |
| W12 | Codex adapter after S02 measures the installed client's real lifecycle, session identity, shell and edit coverage, and ordering. Advisory-only output cannot pass |
| W13 | Per-worktree overlays and integration propagation; F08/F09; no cross-view current-fact leakage |
| W14 | Shared-checkout coordination and opt-in guarded writes with leases, fencing and expiry; F10/F11; explicit bypass limits |
| W15 | Session resume and compaction continuity, and repeated-conflict handling; F12; two-host session ID collision |

The P4 exit needs the full concurrency matrix in both Claude-first and Codex-first orderings. Two cautions from this phase carry forward. First, the two-attempt ceiling and the per-target check are already load-bearing and easy to break: the retry-chain linkage defect found here disabled the ceiling silently, and only a test that drove the loop to its limit caught it. Second, a correction must be true, because S01-8 measured the agent correctly overriding one it could disprove; a guarded-write path that fires on suspicion rather than measured drift will be ignored, and deservedly.

## Cleanup

The isolated Neo4j server started for this phase is the only service started, and only it is stopped at handoff. The recreated JDK and Neo4j runtimes, the clean virtual environment, the built wheel, the S01 probe harness and the captured native streams live under this execution worktree's `output/phase3/` and are not committed, matching Phase 1 and Phase 2 practice. The committed tests and this report are the portable evidence. Recreate the environment and the isolated server from Reproduce when needed.

The separate `D:\memex` checkout and its concurrent uncommitted work were not touched at any point.
