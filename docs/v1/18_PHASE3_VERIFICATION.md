# Phase 3 verification and native host boundary

Status: W09–W11 complete and verified, then hardened. The strengthened native ordering gate passed against the installed client. Date: 5 October 2026.
Branch: `codex/v1-phase3`, based on verified Phase 2 commit `3194450`. Plan: [17_PHASE3_EXECUTION_PLAN.md](17_PHASE3_EXECUTION_PLAN.md).

Commits: `28bebb6` (W10 trace), `203fd0f` (W09 adapter), `774c162` (first native gate test), `e612ca9` (W11 projection), `87b1787` (review fixes), `032c919` (delivery confirmation and replay hardening), `786b00c` (discriminating native gate).

The hardening pass addressed three findings raised against the original Phase 3 result: an expired replay returned a stale non-denial, acknowledgement preceded host delivery, and the native fixture did not require a different implementation. All three are recorded under [Hardening pass](#hardening-pass) with their reproductions.

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

Two properties make this discriminating rather than a retry.

**The drift is a contract change in a file the agent is not editing.** `api.py` holds `send()`; `validate.py` holds `validate()`; one claim depends on both files as a single necessary (AND) support set. After the agent reads `api.py`, the external writer changes `validate.py` only: `validate()` stops reporting an unusable payload by returning `False` and starts raising `ValueError`. The agent's own target bytes are byte-identical across the denial, so the host's stale-read detection cannot see this, and the agent's proposal still applies cleanly. Only its behaviour is now wrong.

**The objective contract is evaluated against both implementations.** `contract_check.py` requires `send("hello") == {"ok": True, "value": "hello"}` and `send("") == {"ok": False, "error": "invalid"}`. The implementation the agent originally proposed is reconstructed from the denied tool call, applied in an isolated copy against the changed dependency, and must **fail**. The implementation it shipped must **pass**.

Two earlier drafts were discarded for failing to discriminate. The first drifted the target file itself, which the `Edit` tool's own `old_string` match would have caught. The second asked for a required `timeout` parameter, which is a valid patch whether `validate()` returns `False` or raises, so its assertions never required the changed premise to alter the implementation.

Recorded trace from the final run:

| # | Event | Gate | Outcome | Insertion | Seq | Reconsidered | Objective |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `session_start` | – | – | `prepared` | 1 | – | – |
| 2 | `delivery` (snapshot) | – | – | `emitted` | 1 | – | – |
| 3 | `delivery` (snapshot) | – | – | `confirmed` | 1 | – | – |
| 4 | `action_check` (`api.py`) | `prevented` | `replan` | `prepared` | 2 | `1` | – |
| 5 | `delivery` (correction) | – | – | `emitted` | 2 | – | – |
| 6 | `delivery` (correction) | – | – | `confirmed` | 2 | – | – |
| 7 | `action_check` (`api.py`) | `advisory` | `unavailable` | `none` | – | – | `passed` |
| 8 | `action_executed` (`api.py`) | `executed` | – | – | – | – | `unknown` |
| 9 | `session_end` | – | – | – | – | – | – |

Rows 1 to 3 and 4 to 6 are the delivery lifecycle: rendered, written to the host, then confirmed from the host's own transcript. Row 7 carries `original_attempt_id` equal to row 4's `attempt_id` under a different `attempt_id`, so the reconsidered action is a new attempt linked to the original and the core's two-attempt ceiling applies to it.

The correction the model received, verbatim from the run's stream:

```text
PreToolUse:Edit hook error: memex: outcome=replan reason=context_changed_or_delivery_pending
targets: api.py
changed dependency validate.py: delivered sha256:f6c2d1f4dfe95fb0..., now sha256:c6f3646cb616...
- uncertain: c-validate-returns-false@r1: validate() returns False for an unusable payload
  and never raises, so send() can branch on its boolean result without handling exceptions
    Recheck prior delivered assertion before this action: hash_matches;source_hash_changed
Re-read each listed target and changed dependency, then reconsider this action against
their current contents. Do not reuse the earlier contents.
```

`hash_matches;source_hash_changed` is the mechanism in one line: the target's own hash still matched, and the claim's other support did not. Naming the changed dependency explicitly was added during the hardening pass, because the first strengthened run showed the cost of omitting it.

The observable sequence that followed, from the stream:

1. `Read api.py`, then `Edit api.py` proposing `if validate(payload): ... else: failure`.
2. The edit is denied. The target is unchanged on disk.
3. *"memex says validate.py changed, so I'm re-reading it along with api.py."*
4. `Read validate.py`, which it had not read before.
5. *"The contract changed: validate() now raises ValueError instead of returning False."*
6. `Edit api.py` shipping a `try/except ValueError` implementation, which executes.

Objective outcome, asserted by the test rather than inspected. The denied proposal, reconstructed from its tool call and run against the changed `validate.py` in an isolated copy, exits non-zero with a `ValueError`. The shipped implementation exits zero. The two differ textually, and `validate.py` still contains the external contributor's `raise ValueError`, so the revised write preserved the concurrent change instead of clobbering it.

The gate passed on two independent runs of the strengthened fixture, with the same trace structure and the same observable sequence each time.

### What this proof does and does not establish

It establishes that for a declared edit action in this host mode, memex delivered context, confirmed that delivery from the host's own transcript, detected a real contract change in that claim's evidence, prevented the affected mutation before it executed, delivered a correction that reached the same session, and that the agent rechecked the named dependency and produced a semantically different implementation which executed and passed an objective contract the earlier proposal fails.

A new `tool_use_id` and the trace's `reconsidered` flag are **not** treated as semantic reconsideration; the contract outcomes carry that claim.

It is one scenario with one model and one claim shape, and the fixture steers the first proposal by asking the agent to start from the delivered context rather than reading the dependency up front. It is not an efficacy measurement, not a latency or cost result, and not a comparison against fresh retrieval or hash-bound notes. Those remain P5 and are the gates that decide whether the defining v1 claim survives.

## Delivery confirmation semantics

Acknowledgement originally ran before the response was printed, so a crash, a broken stdout or a host timeout in between could advance the accepted baseline for context the session never received. Delivery is now four states, and they are not interchangeable:

| State | Meaning | Advances the baseline |
| --- | --- | --- |
| `prepared` | The text exists in the adapter process and nowhere else | No |
| `emitted` | It was written to the host's pipe and flushed | No |
| `confirmed` | The packet's own marker was found in the intended session's transcript | **Yes** |
| `failed` | No evidence arrived within the bounded window | No |

The confirmation signal was chosen by inspecting what the installed host records rather than by assuming one. Its transcript at `transcript_path` keeps each hook invocation's raw stdout together with an exit code, and keeps a denial's text as a `tool_result`, both tagged with the session ID. Each delivery therefore embeds an HMAC marker of its own `task_id:sequence:view_id` under the registration secret, and confirmation is a membership test for that marker in that session's transcript tail. A later unrelated hook invocation, a successful write to stdout and a zero exit code all identify no particular packet, so none of them is accepted as proof. Nothing from the transcript is retained beyond the resulting boolean: no transcript text, no reasoning, no tool output.

Recovery is bounded. After `MAX_DELIVERY_CONFIRMATIONS` invocations without evidence a delivery is declared failed, which records the failure against the stream without advancing it, so the core keeps the packet pending and the next check re-offers it. A delivery still `prepared` on a later invocation means emission never completed and is failed immediately, regardless of any marker that may appear.

This has a deliberate consequence worth stating plainly: until the initial packet is confirmed, the first declared edit in a session is denied and the packet is re-offered. An agent that never received its context does not get a clean certificate. In a real session the host inserts the packet at `SessionStart` and the next hook invocation confirms it, which the native run demonstrates.

## Executed evidence

All results below are from the hardening pass; no earlier count is reused.

| Run | Command | Result |
| --- | --- | --- |
| Phase 3 mechanism suite | `pytest tests/test_live_claude_adapter.py tests/test_phase3_compat.py -q` | **50 passed**, no skips |
| Native ordering gate | `MEMEX_PHASE3_NATIVE=1 pytest tests/test_phase3_native_loop.py -q` | **1 passed** (twice, independently) |
| Phase 1 + Phase 2 inherited | 12 Phase 1/2 files, listed in Reproduce | **74 passed**, no skips |
| Broad compatibility | `pytest tests -m "not integration" -q` | **679 passed, 1 skipped, 92 deselected** |
| Dependency check | `pip check` | No broken requirements |
| Lint | `ruff check --select E,F,B,W --line-length 120` on all changed files | Passed |
| Whitespace | `git diff --check HEAD` | Passed |
| Package | `python -m build`, `twine check` | Both passed; all three new modules present in the wheel |

The Phase 3 suite is 40 adapter cases plus 10 compatibility cases. The 50 and 679 figures overlap by those 10 non-integration compatibility cases; the 679 is Phase 2's 669 plus exactly those 10. The 92 deselected are integration-marked cases, 41 of them added by Phase 3; the broad selector does not claim they ran.

The one skip is the pre-existing `test_path_traversal_symlink_escape_is_rejected`, disabled on Windows since Phase 1.

### Not run, and why

`tests/test_phase2_e2e.py` (2 integration cases) fails in this environment and was **not** made to pass. Both assert that Graphiti `Episodic` nodes appear in Neo4j, which requires real Gemini LLM and embedding calls. The isolated fixture uses `GEMINI_API_KEY=test-not-used`, so the synthesis call cannot succeed. These belong to the legacy provider-dependent set that Phase 2 also did not run.

This is not a regression, but the earlier wording here overstated the evidence and is corrected. Phase 3 **does** change tracked files: six planning documents were edited, and the hardening pass additionally edits `memex/integrations/claude_code.py`, `memex/runtime/trace.py` and the Phase 3 tests, all of which are Phase 3's own. The accurate statement is narrower: **no pre-existing application module or pre-existing test was modified.** At the original Phase 3 commit the only changes to tracked files were under `docs/v1/`; every code change was a new file. Neither failing test imports anything Phase 3 added, and both fail for a missing provider credential rather than for anything in this branch.

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
| Attempt replay | Idempotent decision and identical reason text for a repeated `tool_use_id` while the core's verdict is unchanged |
| Expired attempt replay | The core's downgrade to `attempt_expired` wins; the earlier non-denial is never reused |
| Fresh attempt after expiry | A new attempt ID is checked afresh and recovers |
| Expiry with revoked access | Authorization refusal still takes precedence and discloses no assertion |
| Expiry and retry linkage | The denied attempt stays the root of its chain, so the ceiling still applies |
| Delivery prepared only | Rendered but unprinted context does not advance the accepted baseline |
| Delivery emitted only | Written to the host's pipe but unconfirmed does not advance it either |
| Delivery confirmed | The packet's own marker in the intended session's transcript advances it exactly once |
| Broken output stream | The delivery is marked failed, not emitted, and nothing is acknowledged |
| Interrupted before output | A delivery still `prepared` on a later invocation is failed regardless of any marker |
| Unconfirmed correction | Stays pending, is re-offered, and the next attempt is denied again |
| Duplicate confirmation | Idempotent; one confirmed event and no further advance |
| Wrong-sequence receipt | Recorded as failed with the packet still pending, never as accepted |
| Wrong session evidence | Another session's transcript does not confirm this packet |
| Adapter restart | A fresh adapter re-reads the ledger and does not trust an unconfirmed delivery |
| Changed dependency naming | The correction names the evidence path that moved, not only the action target |
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

## Hardening pass

Three findings were raised against the original Phase 3 result and addressed end to end. Each was reproduced against the actual source before being fixed, and each fix has a regression that fails without it.

### 1. Expired replay discarded the core's updated result

**Reproduction.** The adapter re-entered `check_action` for a stored attempt and then returned the previously rendered `HookResponse` unconditionally. The core's cached-attempt path downgrades an attempt past its freshness deadline to `resync_required` with `attempt_expired; submit a new action-attempt ID`. Driving that through the adapter with a controlled clock produced `HookResponse(decision=None, reason='memex: outcome=proceed (checked_declared_scope)')`: a stale non-denial that authorized reuse of an attempt the core had just invalidated. The original suite missed it because its clock was fixed, so nothing ever expired.

**Correction.** The rendered response is stored with the `(outcome, reason)` it was rendered for. On replay the adapter compares the core's current verdict against that one and reuses the stored text only while they agree; otherwise the response is rendered again from the current, authoritative result. Expiry enforcement and the denied-attempt linkage were not touched.

Regressions: an expired replay must deny and name `attempt_expired`; a fresh attempt ID recovers and proceeds; a replay inside the window is still byte-identical; revoked authorization during an expiry still refuses first and discloses no assertion; and a denied attempt remains the root of its retry chain across an expiry, so the reconsideration ceiling still applies.

### 2. Acknowledgement preceded host delivery

**Reproduction.** `on_session_start` and the deny path of `on_pre_tool_use` recorded `DeliveryReceipt(outcome="host_accepted")` before returning, and `main()` printed the response afterwards. Any failure in between advanced the accepted working-set baseline for context the session never received. The same applied to both the initial snapshot and corrections.

**Correction.** Delivery became the four states described under [Delivery confirmation semantics](#delivery-confirmation-semantics), with acknowledgement moved behind per-packet transcript evidence and printing moved into the adapter so emission can be attributed. Preparation and printing are never labelled host acceptance.

Fault injection covers failure after preparation, a broken output stream, output never emitted, missing evidence to exhaustion, duplicate confirmation, a receipt for a sequence the core has no pending packet for, another session's evidence, adapter restart over the same control plane, and redelivery of an unconfirmed correction. Each asserts the accepted baseline did not move.

### 3. The native fixture did not require a different patch

**Reproduction.** The fixture asked for a required `timeout` parameter, which is a valid change whether `validate()` returns `False` or raises. Its assertions checked the parameter list, so they would have passed on the stale proposal as readily as on a revised one. It also asserted no revalidation of the dependency it claimed had changed.

**Correction.** The dependency now changes its failure contract, and the gate evaluates the objective contract against both the reconstructed denied proposal and the shipped implementation, requiring the first to fail and the second to pass. Details are under [The native ordering proof](#the-native-ordering-proof).

The first strengthened run failed at the revalidation assertion, and the cause was memex's own output rather than the agent: the correction named `targets: api.py` while the file that had moved was `validate.py`, leaving the agent to infer which dependency to recheck. It inferred correctly and shipped a working implementation, but had no reason to re-read anything. Corrections now name the evidence paths whose bytes changed, and the agent then re-read `validate.py` and said why. That is a product fix the fixture earned, not a test accommodation.

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

**Confirmed insertion is still not compliance.** `insertion='confirmed'` means this packet's own marker was found in the intended session's transcript, which is genuine per-packet evidence that the text reached that session. It is not proof that the model attended to it, and never proof that the agent complied. Compliance is tracked separately as whether a later attempt arrived, and execution success separately again as an objective result.

Two residuals remain. Confirmation depends on the host's transcript format, which is an implementation detail of the client rather than a documented contract, so a format change would degrade confirmation to `failed` and cause conservative redelivery rather than a silent false acceptance. And a packet confirmed as present in the transcript may still be dropped from the model's context by compaction, which is why every `SessionStart` redelivers a full working set instead of assuming retention.

**MCP subscription is unavailable, not merely unused.** The installed SDK derives `ResourcesCapability` with `subscribe` hardcoded to false whenever a list-resources handler is registered, so this server cannot advertise resource subscription at any protocol version. Clients poll. Had subscription been available, a resource update would still have been only a signal to fetch.

**Scope.** One host, one version, one model, one claim shape, one worktree. Simultaneous Claude Code and Codex operation, per-worktree overlays, shared-checkout coordination and guarded writes are P4. Statistical efficacy, resource accounting, migration and release claims are P5. No v1 release claim follows from this phase.

## Phase 4 handoff

Entry state: `codex/v1-phase3` at `786b00c`, not merged and not deployed. Version remains **0.9.0**; completing P3 is not the v1 release.

Phase 3 satisfies its exit gate: the strengthened native run prevents a materially affected mutation, delivers a correction whose insertion is confirmed from the host's own transcript, and the agent's revised implementation passes an objective contract that its original proposal fails. The phase is ready for Phase 4 to begin.

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

Three carry-forward items the hardening pass added:

- **Delivery confirmation is host-specific.** The marker-in-transcript mechanism reads a Claude Code implementation detail. A Codex adapter must find its own per-packet evidence and must not inherit this one. If no such evidence exists on that host, the honest outcome is to retain pending state and record the capability limitation, not to acknowledge optimistically.
- **The core's current verdict is always authoritative.** The replay defect existed because the adapter cached a rendered answer and trusted it over the core. Any second adapter that caches rendered output needs the same verdict comparison, and leases in W14 will add more state with the same hazard.
- **A correction must name what moved.** Naming only the action target cost the first strengthened run its revalidation step. The same applies to lease conflicts and merge corrections in P4: say which artefact changed, not merely that something did.

The P4 exit needs the full concurrency matrix in both Claude-first and Codex-first orderings. Two cautions from this phase carry forward. First, the two-attempt ceiling and the per-target check are already load-bearing and easy to break: the retry-chain linkage defect found here disabled the ceiling silently, and only a test that drove the loop to its limit caught it. Second, a correction must be true, because S01-8 measured the agent correctly overriding one it could disprove; a guarded-write path that fires on suspicion rather than measured drift will be ignored, and deservedly.

## Cleanup

The isolated Neo4j server started for this phase is the only service started, and only it is stopped at handoff. The recreated JDK and Neo4j runtimes, the clean virtual environment, the built wheel, the S01 probe harness and the captured native streams live under this execution worktree's `output/phase3/` and are not committed, matching Phase 1 and Phase 2 practice. The committed tests and this report are the portable evidence. Recreate the environment and the isolated server from Reproduce when needed.

The separate `D:\memex` checkout and its concurrent uncommitted work were not touched at any point.
