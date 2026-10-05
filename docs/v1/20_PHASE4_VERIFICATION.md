# Phase 4 verification: concurrent clients and a second host

Status: W12–W15 complete and verified. Every required native concurrency gate passed with Claude Code and Codex live at the same time, in both Claude-first and Codex-first orderings. Date: 5 October 2026.
Branch: `codex/v1-phase4`, from accepted Phase 3 commit `1390f15`. Plan: [19_PHASE4_EXECUTION_PLAN.md](19_PHASE4_EXECUTION_PLAN.md).

Clients: **Claude Code 2.1.289** (model `claude-opus-5-5`) and **codex-cli 0.157.1** (model `gpt-6-sol`, app-server mode). Version remains **0.9.0**; nothing was merged, deployed or released.

Commits: `0a5f4ad` (plan), `a4facb9` (shared adapter lifecycle, host namespacing, complete-packet rule), `cf537c1` (Codex adapter), `dce2469` (native Codex gate), `ed777e8` (write guard), `885d06d` (guard server and guarded mode), `092a456` (worktree tests), `b6da6f9` (shared-checkout and continuity tests), `5047884` (concurrent-confirmation fix and shared-checkout gate), `fd47af9` (gate diagnostics), `c49461c` (worktrees gate), `e14878e` (guarded race gate).

## S02: measured Codex semantics

S02 ran against the installed client before any adapter code, with an instrumented probe hook recording each event, its identities and the target's hash at every hook invocation. Nothing below is inferred from a hook name or from documentation alone; where the documentation and the client agreed, the measurement is what is cited.

| # | Measured behavior | Evidence | Consequence |
| --- | --- | --- | --- |
| S02-1 | Hooks do **not** run under `codex exec` | Zero hook invocations with hooks supplied by the project layer, and again with hooks supplied as accepted per-invocation session flags with trust bypassed; the edit landed both times | `exec` cannot host the gate |
| S02-2 | Hooks **do** run in app-server threads | `hook/started` and `hook/completed` notifications; probe records | The supported integration mode is an app-server thread, the protocol the IDE extension and desktop app use |
| S02-3 | Untrusted hooks silently do not run in app-server threads; `--dangerously-bypass-hook-trust` is not honored there; `hooks.state` trust cannot be set from session flags | `hooks/list` reports `untrusted`; runs appear only for already-trusted plugin hooks; config warnings name the ignored keys | memex never writes trust. A user trusts memex's hooks through Codex's own `/hooks` review |
| S02-4 | The app-server honors a per-thread `config.bypass_hook_trust` override | Probe hooks ran once it was set in `thread/start`; the app-server's own `config_manager.rs` validates it | Fixtures trust their session-flag hooks for one thread only; nothing is persisted |
| S02-5 | `hooks.json` must be wrapped as `{"hooks": {...}}` | A flat file is rejected: `unknown field SessionStart, expected description or hooks` | The installer writes the wrapped form and composes with existing entries |
| S02-6 | `PreToolUse` fires for `apply_patch` (matcher `apply_patch`, `*` or none) and for shell commands as `Bash`; `permissionDecision:"deny"` blocks | `status=blocked`; target hash identical before and after; no `fileChange` executed | Deny-or-abstain gate, same output shape as Claude, re-measured rather than assumed |
| S02-7 | The denial reaches the model as `Command blocked by PreToolUse hook: <reason>` in a tool-call output | Rollout `custom_tool_call_output` | The correction is model-visible |
| S02-8 | With reads allowed, the model re-reads and reissues; Codex has no Read tool, so reading is a shell command | Probe: deny → `Get-Content api.py` → identical patch reissued → executes. With shell forbidden, the model asked the user to paste the file | Fixtures must allow read-only shell |
| S02-9 | One `apply_patch` call can add, update, delete and move several files under one `PreToolUse` | One hook carrying `*** Add File: notes.txt` and `*** Update File: util.py` | Every patch header is a declared target; an unreadable patch is opaque |
| S02-10 | A hook that **times out** fails open | `hook timed out after 5s`, then the multi-file patch executed | The adapter's deadline stays inside the hook timeout; fail-open is advisory |
| S02-11 | A hook that **exits non-zero** fails open | A crashing probe; the patch executed | Same |
| S02-12 | Hooks run **concurrently** for concurrent tool calls | Interleaved writes corrupted an unlocked probe log | Every adapter write path must be concurrency-safe; one race was later found natively and fixed (below) |
| S02-13 | Hook processes are **not sandboxed**, but their environment is filtered to a core set | The hook wrote inside and outside the workspace, wrote `.git/memex`, opened SQLite there and reached a loopback port; only `PATH`, `HOME`, `USERPROFILE` and similar arrived | Backend settings live in the capability file; a launcher sets `PYTHONPATH` |
| S02-14 | On Windows a quoted interpreter path with arguments fails (exit 1); an unquoted path, a `.cmd` launcher and PowerShell work | Side-by-side `Stop` hooks in one session | The installer writes a launcher script |
| S02-15 | `SessionStart` fires at the first turn (`source=startup`), at the next turn after `thread/compact/start` (`source=compact`) and after `thread/resume` in a new process (`source=resume`) | Probe records; fresh `developer` messages each time | Every `SessionStart` opens a fresh task and redelivers; retention is never assumed |
| S02-16 | Injected context is recorded in the rollout named by `transcript_path` as a `response_item` `developer` message tagged `content_item_kinds:["hooks.additional_context"]`, with the turn's `turn_id`; it is on disk by the next hook invocation | Probe read the rollout at each hook | This is the Codex snapshot insertion record |
| S02-17 | A code-mode `apply_patch`'s nested `tool_use_id` never appears in the rollout; the model-visible output carries the outer script's `call_id` | Search of the rollout for the hook's `tool_use_id` | Corrections are bound to their `turn_id`, not to the call |
| S02-18 | A 62,510-character `additionalContext` became a 10,100-character message beginning `Warning: truncated output (original token count: 15628)`, middle spilled to a file, **with the start and end markers both intact** | Rollout record | A marker cannot prove a complete packet. Confirmation requires the whole packet; the Codex cap is 4,000 characters |
| S02-19 | The app-server's own `hook/completed` `context` entry is the hook's output | Present whether or not insertion happened | Not insertion evidence, like Claude's `hook_success` |
| S02-20 | An interrupt during a turn left the target unchanged | `turn/interrupt` → `interrupted`; target hash unchanged 25 s later | Partially isolated: the interrupt landed during a read, not inside a sleeping `apply_patch` hook |
| S02-21 | Codex calls MCP tools under the user's default approval policy without an approval request | The harness, which declines every approval, declined none; `guard_read` and `guard_write` completed | Guarded writes are reachable on Codex without changing any policy |
| S02-22 | Starting a Codex session in a directory makes the client **persist `trust_level = "trusted"` for that project in the user's own `~/.codex/config.toml`** | Discovered at handoff: 26 entries, one per fixture directory a session started in (for linked worktrees, keyed to the main worktree), each containing only that line; the per-invocation `projects.<path>` override had been ignored (S02-3) | Native fixtures cannot avoid writing the user's global Codex configuration without an isolated `CODEX_HOME`, which needs its own authentication. Recorded as a limitation and a P5 requirement |

The rollout layout is `<CODEX_HOME or ~/.codex>/sessions/YYYY/MM/DD/rollout-<time>-<thread>.jsonl`, and its first record is `session_meta` naming the thread. Both are checked before anything in the file is read.

## Implemented responsibilities

| Work | Implementation | Evidence |
| --- | --- | --- |
| W12 | `memex/integrations/host_adapter.py`: the host-independent lifecycle shared by both adapters. `memex/integrations/codex.py`: `apply_patch` targets, rollout provenance and records, turn-bound corrections, 4,000-character cap, launcher, wrapped hook installation, session flags | [Codex adapter tests](../../tests/test_live_codex_adapter.py), [native Codex gate](../../tests/test_phase4_native_codex.py) |
| W13 | No new module: the P2 core's per-view verification, worktree-scoped claims and evidence, and test-view binding, proven across real linked worktrees | [Worktree tests](../../tests/test_phase4_worktrees.py), [native worktrees gate](../../tests/test_phase4_native_worktrees.py) |
| W14 | Default mode: per-host tasks and cursors over the shared core. Guarded mode: `memex/runtime/guard.py` (leases, fencing enforced at commit, intents, Git) and `memex/integrations/guard_mcp.py` (opt-in two-tool server); both adapters deny native edits in guarded mode | [Guard race tests](../../tests/test_phase4_guard.py), [guarded mode](../../tests/test_phase4_guarded_mode.py), [shared checkout](../../tests/test_phase4_shared_checkout.py), [native shared gate](../../tests/test_phase4_native_shared_checkout.py), [native guarded gate](../../tests/test_phase4_native_guarded.py) |
| W15 | Host-namespaced control state and its migration; fresh redelivery on compaction and resume for both hosts; bounded conflicts | [Migration](../../tests/test_phase4_migration.py), [shared checkout](../../tests/test_phase4_shared_checkout.py), [confirmation race](../../tests/test_phase4_confirmation_race.py) |

### What changed in the shared adapter

**Control state is namespaced by host.** Phase 3 keyed bindings by the native session ID alone, so a second host reporting the same ID would overwrite the first's binding. `live_adapter_sessions` is rebuilt keyed by `(harness, native_session_id)` inside `BEGIN IMMEDIATE`, rechecked under the write lock so concurrent initializers rebuild it once, with every Phase 3 row carried over as `claude_code`. Ledger queries filter by host. Session identities are derived under each host's own capability secret with distinct prefixes (`cc-`, `cx-`).

**Confirmation requires the whole packet, on every host.** S02-18 showed a truncated packet keeping its marker. Each packet's SHA-256 fingerprint, length and marker offset are recorded at preparation, and an insertion confirms only if the exact window around its marker hashes to that fingerprint. A partial insertion fails at once as `host_truncated` and the packet stays pending. This also tightened the accepted Claude adapter; its Phase 3 tests now insert the real rendered packet, and a truncated-packet case was added.

**A packet is confirmed exactly once under concurrent hooks.** The first native shared-checkout run caught two Codex hook processes confirming one correction: the core's receipt is idempotent, so the cursor moved once, but the trace recorded two confirmations. The `emitted → confirmed` transition is now claimed with a conditional update before acknowledging, so only one process wins it. The regression starts two real processes behind a file barrier and fails without the fix with two acknowledgements.

### The write guard

The guard closes the check-to-write gap for one bounded set of mutations from cooperating writers: whole-file create, update, delete and rename inside one worktree, singly or as an all-or-nothing set, and `git commit --only` of named paths under a worktree-wide lease.

| Requirement | How the guard meets it |
| --- | --- |
| Coordinate across processes | The commit runs inside one SQLite `BEGIN IMMEDIATE` transaction on the shared control plane, the cross-process critical section |
| Canonical order or all at once | Acquisition is all-or-none in sorted canonical order under the write lock; a busy target refuses the whole set |
| Recheck hashes inside the protected write | Inside that transaction: every expected hash is rechecked on disk, then files are staged, an intent is made durable, files are replaced and result hashes captured |
| Retain protection through execution and capture | The lease is released only after the outcome is recorded |
| Reject expired or replaced holders before commit | Inside the transaction, each lease's holder, fencing generation and expiry are rechecked; generations only increase per target |
| Recover after crashes without permanent locks | SQLite's lock dies with the process; an expired lease is reclaimable; an intent left mid-replacement is rolled forward on the next guard entry |

The fencing token is enforced by the write path, not stored and trusted: a paused holder that wakes after expiry and replacement fails the generation check at commit, and the race test proves it with a real paused process.

Lease keys collapse case, separators, `.`/`..` segments, 8.3 short names and in-tree links to one target, while real names are kept for I/O and for Git's case-sensitive index. A path resolving outside the worktree through a junction or link, or into `.git`, is refused.

Hosts reach the guard through `guard_mcp.py`, a separate stdio MCP server exposing exactly `guard_read` and `guard_write`, started only for a worktree in guarded mode. The public memex server still has fourteen tools. In guarded mode both adapters deny native declared edits with an instruction to use the guard, and `guard_write` itself receives the ordinary context check before the guard's hash check, so a dependency correction still arrives before a guarded write.

## Native gates

Every native run used real clients, the isolated native Neo4j fixture and real Git. A harness held each live session with a barrier hook after its first read and released the sessions in the order under test, so ordering was chosen and recorded rather than inferred from timing. The harness declines every approval request; every run asserted that none arrived.

Fixture clients were isolated per invocation from the user's plugins and MCP servers: Claude with `--setting-sources project,local --strict-mcp-config`, and Codex with `--disable plugins` plus session-flag hooks and a per-thread trust override. The first shared-checkout run showed why: the user's global memex MCP plugin failed to connect, the agent concluded "memex was offline this session", and its first proposal became defensive, which the gate correctly rejected as non-discriminating. No global configuration was changed.

### Codex alone: semantic reconsideration through the adapter

The Phase 3 discriminating fixture, run through the Codex adapter in an app-server thread. The trace, from the run:

| # | Event | Gate | Outcome | Insertion | Seq |
| --- | --- | --- | --- | --- | --- |
| 1 | `session_start` | – | – | `prepared` | 1 |
| 2–3 | `delivery` (snapshot) | – | – | `emitted`, then `confirmed` | 1 |
| 4–11 | `action_check` (shell reads) | `advisory` | – | `none` | – |
| 12 | `action_check` (`api.py`) | `prevented` | `replan` | `prepared` | 2 |
| 13–14 | `delivery` (correction) | – | – | `emitted`, then `confirmed` | 2 |
| 17 | `action_check` (`api.py`, linked to #12) | `advisory` | `unavailable` | – | – |
| 18 | `action_executed` (`api.py`) | `executed` | – | – | – |

Both ledger rows reached `confirmed` with `confirmations=0`, and the correction's binding equalled the thread's turn. The agent's visible sequence: a patch branching on `validate(payload)` → denied, naming `changed dependency validate.py` → *"Memex flagged a change to `validate.py` and paused the patch. I'll reread it and `api.py` before applying the change."* → re-read → a patch catching `ValueError`. The reconstructed denied proposal fails the changed contract in an isolated copy; the shipped implementation passes.

One fixture caveat is recorded plainly: this agent's earliest shell commands already named `validate.py` (a search), so the steering is weaker than in the Claude run. The change still landed after those reads, and the contract still separates the two implementations.

### Both hosts in one checkout

Claude implements `send()` in `api.py`; Codex implements `relay()` in `relay.py`; both hold the same claim about `validate()`. Both sessions were held live with their packets, the contributor changed `validate()`'s failure contract, and the sessions were released in order.

| Ordering | Both held before change | Second session held live after first released | Result |
| --- | --- | --- | --- |
| Claude-first | ready at +0.0 s (Claude) and +14.9 s (Codex); change at +15.1 s | Codex held 18 s while Claude was corrected and executed | **Passed** |
| Codex-first | ready at +0.0 s (Claude) and +18.6 s (Codex); change at +19.0 s | **Claude held 111 s** while Codex was corrected and executed | **Passed** |

In each ordering, each host independently had its pending mutation prevented, received and confirmed its own correction in its own ledger row, re-read `validate.py`, and shipped a revision that passes its contract while its reconstructed denied proposal fails it. The second host's correction followed the first host's executed revision in the trace (Claude-first: `#12` executed, then `#17` Codex prevented; Codex-first: `#15` executed, then `#17` Claude prevented), so one host's acknowledgement suppressed nothing. The contributor's change survived in both.

### Both hosts in separate worktrees

Claude works in worktree A; Codex works in worktree B of the same repository and itself changes `validate()`'s contract there, unmerged.

| Ordering | Isolation before integration | Integration | Result |
| --- | --- | --- | --- |
| Claude-first | Claude's first edit `proceed` (`#9`), then Codex's B change executes (`#15`) | B committed and merged into A while Claude was held live; Claude's next edit `prevented` (`#17`), corrected, revised | **Passed** |
| Codex-first | Codex's unmerged B change executes **first** (`#10`); Claude's edit in A still `proceed` (`#13`) | Merge while Claude was held; next edit `prevented` (`#15`), corrected, revised | **Passed** |

Codex-first is the sharper isolation proof: B's contract change was already on disk in B when A's edit went through unchallenged. In both orderings Claude's correction came after the merge and against A's actual merged contents. Its pre-merge implementation passes the pre-merge contract and fails the merged one, and its revision passes. B's session was never corrected for A's work, and the two hosts' checked views were disjoint.

### Guarded writes, two live writers on one file

The checkout opts into guarded mode. Each client adds a function to `registry.py` only through `guard_read` and `guard_write`, and both are held after reading the same bytes.

| Ordering | Guard results, in order | Final file |
| --- | --- | --- |
| Claude-first | Claude `committed` → Codex `expected_hash_mismatch` → Codex `committed` | Both `register_alpha` and `register_beta` |
| Codex-first | Codex `committed` → Claude `expected_hash_mismatch` → Claude `committed` | Both functions |

The second writer's first write expected bytes the first writer had replaced, and was refused inside the fenced commit, changing nothing. That writer re-read and committed a version keeping the other's work. No update was lost and no stale write landed. Every executed mutation went through `guard_write`, and both hosts' hooks checked each guarded write.

Ordinary hooks alone could not provide this. The refusal happens inside the guard's protected write, not in a check that precedes a host's own write.

### Regression of the accepted Phase 3 gate

The accepted Claude native ordering gate was rerun on the final Phase 4 code, because the adapter extraction and the complete-packet rule touched it. **1 passed.**

## Mechanism coverage

| Case (05 matrix and brief) | Executed behavior | Where |
| --- | --- | --- |
| Two worktrees, same path, different API | Per-view claims; same relative path keeps distinct evidence and verification identities | worktrees |
| Unmerged B edit | A's facts unchanged; A's affected action proceeds | worktrees, native worktrees |
| Merge into A | A revalidates from merged contents before its affected action | worktrees, native worktrees |
| Conflict resolution | Verified against the resolution's bytes, not either side | worktrees |
| Cherry-pick and rebase | The receiving view revalidates; an unaffected claim stays quiet | worktrees |
| Another worktree's test result | Never certifies a merge, even with identical bytes | worktrees |
| Approved rules and proposals | Shared by scope; a branch proposal cannot supersede approved authority or apply across worktrees | worktrees |
| Coordination privacy | A correction carries no other task's ID, intent or worktree | worktrees |
| Shared checkout, body-only change | Dependent caller corrected despite an unchanged signature, per host | shared checkout, native shared |
| Shared checkout, unrelated change | Neither host interrupted | shared checkout |
| Both received C1 | Independent corrections and receipts; one host's confirmation does not suppress the other's | shared checkout, native shared |
| Shared file changed after read | Stale target hash requests reconsideration | shared checkout |
| Guarded writers race | Ordered and simultaneous: exactly one current write commits; the loser replans | guard, native guarded |
| Paused, replaced holder | Fencing rejects it at commit; generation 2 recorded | guard |
| Crashed holder | Blocks only until expiry; no permanent lock | guard |
| Crash mid-replacement | Rolled forward on next entry | guard |
| Multi-target ordering | All-or-none; opposite orders never deadlock; no partial lease | guard |
| External writer | Detected as a stale write; not prevented, and not claimed to be | guard |
| Create, delete, rename, multi-file | Supported and atomic; one stale member refuses the set | guard |
| Windows aliases and links | Case, separators, `..`, absolute paths and 8.3 names share one lease; escaping junctions and `.git` refused | guard |
| Concurrent Git mutation | Worktree lease excludes file leases both ways; other staged work reported, never committed | guard |
| Shell Git mutation | Classified as outside the guard or unsupported, never certified | guard, guarded mode |
| Session ID collision | Identical native IDs keep separate identities, bindings, ledgers, tasks and cursors | Codex adapter, migration, shared checkout |
| Compaction and resume | Fresh task and redelivery per host; a historical packet does not count | Codex adapter, shared checkout |
| Restart, expiry | Bindings survive restart; a lapsed task yields an advisory, never a certificate | shared checkout |
| Duplicate and concurrent confirmation | Exactly one confirmation and one acknowledgement | confirmation race |
| Failed or truncated insertion | Never acknowledged; truncation fails at once as `host_truncated` | Codex and Claude adapters |
| Backend outage | Unknown freshness; a known pending correction still requires replan | shared checkout |
| Overflow | Resynchronization requested; no delivery prepared | shared checkout |
| Reconsideration ceiling | After two, the conflict is reported, not retried, and no winner is chosen | shared checkout |
| Codex wrong session, forged rollout, prose, diagnostics, wrong turn, malformed | Insufficient evidence in each case | Codex adapter |
| Migration from the accepted Phase 3 schema | State preserved; legacy unfingerprinted packets stay pending; repeated and six-way concurrent initialization safe | migration |

## Executed evidence

All results below are fresh from this phase. No earlier count is reused.

| Run | Command | Result |
| --- | --- | --- |
| Phase 4 deterministic | `pytest tests/test_live_codex_adapter.py tests/test_phase4_migration.py tests/test_phase4_guard.py tests/test_phase4_guarded_mode.py tests/test_phase4_worktrees.py tests/test_phase4_shared_checkout.py tests/test_phase4_confirmation_race.py -q` | **52 passed** |
| Native Codex gate | `MEMEX_PHASE4_NATIVE=1 pytest tests/test_phase4_native_codex.py -q` | **1 passed** (twice: once at introduction, once on final code) |
| Native shared checkout | `MEMEX_PHASE4_NATIVE=1 pytest tests/test_phase4_native_shared_checkout.py -q` | **Claude-first and Codex-first passed** |
| Native worktrees | `MEMEX_PHASE4_NATIVE=1 pytest tests/test_phase4_native_worktrees.py -q` | **Claude-first and Codex-first passed** |
| Native guarded race | `MEMEX_PHASE4_NATIVE=1 pytest tests/test_phase4_native_guarded.py -q` | **Claude-first and Codex-first passed** |
| Phase 3 native regression | `MEMEX_PHASE3_NATIVE=1 pytest tests/test_phase3_native_loop.py -q` | **1 passed** |
| Phase 3 mechanism | `pytest tests/test_live_claude_adapter.py tests/test_phase3_compat.py tests/test_phase3_schema_upgrade.py -q` | **64 passed** |
| Phase 1 + 2 inherited | The 12 files listed in [18](18_PHASE3_VERIFICATION.md#reproduce) | **74 passed** |
| Broad compatibility | `pytest tests -m "not integration" -q` | **712 passed, 1 skipped, 132 deselected** |
| Dependencies | `pip check` | No broken requirements |
| Lint | `ruff check --select E,F,B,W --line-length 120` on every Phase 3 and 4 file | Passed |
| Whitespace | `git diff --check 1390f15 HEAD` | Passed |
| Package | `python -m build`, `twine check` | Both passed; every Phase 3 and 4 module in the wheel |

The broad figure is Phase 3's 686 plus the 26 Phase 4 cases that need no graph backend: 7 migration, 1 confirmation race, 11 guard, 2 guarded mode and 5 Codex parsing and configuration. The 132 deselected are Phase 3's 98, plus 33 Phase 4 integration cases (26 deterministic, 7 native parametrizations), plus the one Phase 3 truncation case added in this phase. All were run explicitly above. The one skip is the pre-existing Windows symlink case.

**Failing first.** The migration tests were run with the session rebuild disabled and failed (2 of 6 run). The confirmation-race test failed before its fix with two acknowledgements. The Phase 3 suite failed against the complete-packet rule until its tests inserted real packets, which is the intended tightening. The guard, Codex adapter and guarded-mode suites were written against modules that did not exist; their first runs exercised new code. The guard caught its own Windows case-folding defect in review before any test ran.

**Native failures, reported.** Three native runs failed before the recorded passes, none for a product reason that was then hidden:
- The first shared-checkout run failed because the user's global memex MCP plugin leaked into the fixture, as described above. Fixture isolation was added.
- One shared-checkout run failed because the Codex model was at capacity (`serverOverloaded`). It was rerun, and the harness now reports that error directly.
- The first native Codex attempt never started, because the npm `codex.CMD` shim re-parsed a `|` inside a hook matcher through `cmd.exe`. The harness now starts Codex's own `codex.js` under `node`.

### Not run, and why

The two Phase 2 end-to-end cases that need real Gemini credentials remain unrun, as in Phases 2 and 3. No statistical efficacy, latency or cost measurement was attempted; those are Phase 5. The interactive Codex TUI was not measured; app-server threads are the measured and supported mode.

## Reproduce

Use the Phase 3 environment and isolated Neo4j fixture recipe in [18](18_PHASE3_VERIFICATION.md#reproduce). Then:

```powershell
$env:MEMEX_PHASE1_NEO4J_URI='bolt://127.0.0.1:17687'
python -m pytest tests/test_live_codex_adapter.py tests/test_phase4_migration.py tests/test_phase4_guard.py `
  tests/test_phase4_guarded_mode.py tests/test_phase4_worktrees.py tests/test_phase4_shared_checkout.py `
  tests/test_phase4_confirmation_race.py -q
$env:MEMEX_PHASE4_NATIVE='1'; $env:PYTHONPATH=(Get-Location).Path
python -m pytest tests/test_phase4_native_codex.py tests/test_phase4_native_shared_checkout.py `
  tests/test_phase4_native_worktrees.py tests/test_phase4_native_guarded.py -q
```

Native runs need an authenticated `claude` and `codex` and consume real model turns. Override the Codex model with `MEMEX_PHASE4_CODEX_MODEL` if the default is at capacity.

## Limitations

These are properties of the integration as measured.

**Codex's supported mode is the app-server.** `codex exec` runs no hooks. The interactive TUI was not measured. Hooks run only once trusted; memex never writes trust, so a real installation needs the user's `/hooks` review, and fixtures rely on a per-thread trust override.

**Confirmation rests on undocumented host records, now on two hosts.** For Codex: the rollout layout, `session_meta`, `response_item` shapes, `content_item_kinds` and the passthrough `turn_id`. For Claude: as recorded in 18. A change to any of them degrades to `failed` and conservative redelivery, never to a false acceptance.

**A Codex correction is bound to its turn, not its call.** The packet marker still identifies the packet, but two corrections in one turn are told apart only by their markers.

**The Codex packet cap is conservative and fixed.** It sits well below the default spill threshold. A user who lowers `additionalContextLimit` may see packets truncated; the complete-packet rule then fails them as `host_truncated` rather than accepting them.

**Fail-open on both hosts.** A timed-out or failing hook lets the action proceed on Claude and on Codex. The gate is a check, not a lock, in default mode.

**Guarded mode protects only cooperating writes through the guard.** Editors, shell redirection, other tools, external Git operations and other machines bypass it. The guard detects their drift as a stale write when a participant next writes; it does not prevent them, and no such claim is made. It coordinates processes on one machine through a local SQLite file, and must not be used on a network filesystem. While a guarded commit replaces files it holds the control plane's write lock, briefly blocking other memex writers. A multi-file commit is crash-consistent through roll-forward, but an outside reader could observe a partially replaced set during the commit window. Only `git commit --only` of named paths is a supported Git mutation; checkout, reset, rebase, merge, stash and the rest are reported unsupported.

**Codex fixture sessions write the user's global configuration.** The client itself persists project trust for every directory a session starts in (S02-22). memex writes none of it, but a native Codex run on a developer machine leaves trust grants behind, including for pytest temporary paths that a later run recreates. A harness that must not touch global state needs an isolated `CODEX_HOME` with its own authentication.

**Fixture-only conditions.** The Claude guarded run allow-listed the two guard tools for that one invocation, as Phase 3 used `acceptEdits`. Both clients ran isolated from the user's plugins. Neither choice is something memex performs on a user's behalf.

**Scope.** One machine, one model per host, one claim shape per scenario, and a single run per ordering. This is mechanism evidence, not efficacy: no comparison against fresh retrieval or hash-bound notes, no confidence analysis, no latency or cost result. Those are Phase 5.

## Phase 5 handoff

Entry state: `codex/v1-phase4` at the head recorded in the commit list, not merged and not deployed. Version **0.9.0**.

Phase 4 satisfies its exit gate. Claude Code and Codex worked simultaneously, both live, in separate worktrees of one repository and in one checkout. Each received context for its own view with independent delivery state, and relevant changes reached each affected agent before its supported mutation executed. All of this held in both orderings. Participating clients could not commit a stale guarded mutation. Phase 4 is ready for Phase 5.

What P5 inherits:

- Two native integrations with measured contracts, and one shared adapter lifecycle. A third host should subclass `HostAdapter` and must measure its own insertion records, truncation behavior and hook trust model rather than inherit these.
- A guarded-write mode with a stated supported set and stated bypasses.
- Native harnesses that hold two live clients in a chosen order, which W16's paired trials can reuse.

What P5 must establish, and Phase 4 did not:

- **Efficacy (W16, W17).** Every native result here is one run per ordering with one model per host. The preregistered comparison against fresh retrieval (C) and hash-bound notes (D) at comparable resources, with S04's frozen sample size and margins, is untouched. The decisive question is still open.
- **Generalization.** Two model families exist only as one model per host. Several repositories, languages and claim shapes are untested.
- **Latency and cost.** No measurement was taken. The gate's per-hook cost includes a Neo4j connection and a full source capture.
- **Migration and rollback (W18).** Adapter schema upgrades are tested, but a downgrade path and legacy backfill are not.
- **Release documentation (W19).** It must carry the limitations above verbatim, especially fail-open, host trust, undocumented record formats and the guard's bypasses.

Four cautions carry forward:

1. **Run native trials in an isolated client home.** Codex persisted project trust in the user's real configuration for every fixture directory (S02-22). W16/W17 harnesses should provision a dedicated `CODEX_HOME` (and Claude config directory) with their own authentication, so trials neither read nor write a maintainer's own client state.
2. **Fixture isolation is part of the measurement.** A user plugin's failure changed an agent's behavior. W17's independent-maintainer pilot will run in uncontrolled environments, and trial protocols must record what else was loaded.
3. **A host's concurrency is a property to measure, not assume.** Claude serialized parallel edits; Codex ran hooks concurrently, and that exposed a real race in code that Phase 3 had accepted.
4. **Identity is still not delivery.** On both hosts the confirmation signal is a record written because the client used the output. Any third host must find its own, and must fail closed if it has none.

## Cleanup

The isolated Neo4j server started for this phase is the only service started, and only it is stopped at handoff. Runtimes, the virtual environment, the S02 probe harness, the built wheel and native evidence remain under the execution worktree's `output/` and are not committed, matching earlier phases. The committed tests and this report are the portable evidence.

Fixture runs necessarily left session history in the user's own clients: Codex rollouts under `~/.codex/sessions/2026/10/05/` and Claude transcripts under `~/.claude/projects/` for temporary fixture directories. They are history, not configuration. They were not deleted, because deleting inside a client's own store was not authorized.

**Global configuration: one side effect, not reverted.** `~/.codex/config.toml` *was* changed during this phase, by the Codex client rather than by memex: it appended 26 `[projects.'<fixture directory>'] trust_level = "trusted"` blocks (S02-22), for the 15 S02 probe repositories under this worktree's `output/phase4/s02` and 11 pytest temporary directories of the native tests. memex wrote no hook trust and no other key. Removing those blocks edits the user's real global configuration, so it was not done without authorization. `output/phase4/remove_fixture_trust.py` removes exactly those blocks, after verifying each holds only that one line and after backing the file up; it was run in dry-run mode only. `~/.codex/hooks.json` was not modified. `~/.claude/settings.json` changed once during the phase, to `model: "opus"`, from the maintainer's own `/model` command; it carries no memex hook. The separate `D:\memex` checkout and its concurrent work were not touched.
