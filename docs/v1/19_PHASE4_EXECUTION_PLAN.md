# Phase 4 execution plan

Status: W12–W15 executed from accepted Phase 3 commit `1390f15` on `codex/v1-phase4`, and the native gates passed in both orderings. Review then found that guard recovery (planned below as roll-forward "on the next guard entry") and delivery confirmation were not safe across interruption; both were corrected, and the native rerun on the corrected code is pending. Measured results, including what deviated from this plan, are recorded in [20_PHASE4_VERIFICATION.md](20_PHASE4_VERIFICATION.md).
Spec: [05_MULTI_AGENT_CONCURRENCY.md](05_MULTI_AGENT_CONCURRENCY.md), [06_HOST_INTEGRATIONS.md](06_HOST_INTEGRATIONS.md), [04_CONTEXT_CONTRACTS.md](04_CONTEXT_CONTRACTS.md).

## Constraints

No Docker, paid benchmark campaign, merge, deploy or release; version stays 0.9.0. Real Git, SQLite and the isolated native Neo4j fixture. Neo4j/Graphiti stays the engineering knowledge store; SQLite stays bounded local control state. The real global Claude and Codex configurations are not modified: every demonstration uses reversible fixture configuration (repository-local Claude settings, per-invocation Codex session flags and per-thread overrides). Statistical efficacy is Phase 5.

The core `LiveContextEngine` contract (`open_task`, `check_action`, `ack_delivery`, `close_task`) is reused unchanged. Phase 4 adds a second host, a shared adapter base, a cooperating write guard and migrations; it does not redesign the core.

## S02 first: measured Codex semantics

S02 ran against **codex-cli 0.157.1** before any adapter code. Full evidence is in 20; the design consequences:

| Measured behavior | Design consequence |
| --- | --- |
| Hooks do **not** run under `codex exec`, whether supplied by the project layer or per-invocation session flags | `exec` cannot host the gate. The supported route is an **app-server** thread (the protocol the IDE extension and desktop app use) |
| In app-server threads, untrusted hooks silently do not run; `--dangerously-bypass-hook-trust` is not honored there; `hooks.state` trust cannot be set from session flags; the app-server accepts a per-thread `config.bypass_hook_trust` override | Fixtures supply hooks as session flags and trust them only for that thread. A real installation is trusted by the user through Codex's own `/hooks` review; memex never writes trust |
| `PreToolUse` fires for `apply_patch` (matcher `apply_patch`, `*` or none) and for shell as `Bash`; `permissionDecision:"deny"` returns `status=blocked` and the target bytes stay unchanged | Same deny-or-abstain gate as Claude. Output schema is compatible; behavior is re-measured, not assumed |
| The denial reaches the model as a tool output `Command blocked by PreToolUse hook: <reason>`; with reads allowed, the model re-read the file and reissued the patch, which then executed | Native reconsideration exists. Codex has no Read tool: reading is a shell command, so a fixture must allow read-only shell |
| One `apply_patch` call can add, update, delete and move several files; one `PreToolUse` carries the whole patch | Targets are parsed from every `*** Add/Update/Delete File:` and `*** Move to:` header; the check covers all of them or the action is opaque |
| A hook that times out or exits non-zero **fails open**; the patch executes | Inner adapter deadline strictly below the configured hook timeout; fail-open is recorded as advisory, never as a gate |
| Hooks run **concurrently** for concurrent tool calls | Every adapter write path is safe under concurrent processes |
| Hook processes are not sandboxed, but their environment is filtered to a core set | Backend settings come from the local registration file and a generated launcher, not from environment variables |
| A quoted interpreter path in a hook command fails on Windows (exit 1); an unquoted path or a `.cmd` launcher works | The installer writes a launcher script |
| `SessionStart` fires at the first turn with `source=startup`, after compaction with `source=compact`, and after `thread/resume` in a new process with `source=resume` | Every `SessionStart` opens a fresh task and redelivers a full working set, as for Claude |
| Injected context is recorded in the rollout (`transcript_path`) as a `response_item` `developer` message tagged `content_item_kinds:["hooks.additional_context"]`; a denial as a `function_call_output`/`custom_tool_call_output` carrying the turn's `turn_id`; both are on disk by the next hook invocation | These are the Codex insertion records. Correlation is session file + `turn_id` + packet marker + record shape. The nested `tool_use_id` of a code-mode `apply_patch` never appears in the rollout, so it cannot be used |
| A 62,510-character `additionalContext` became a 10,100-character message beginning `Warning: truncated output`, middle spilled to a file, **with both start and end markers intact** | Marker presence cannot prove a complete packet. Confirmation requires the entire rendered packet verbatim in the insertion record, and Codex packets are capped well below the spill threshold |
| The app-server's own `hook/completed` `context` entry is the hook's output | Not insertion evidence, for the same reason Claude's `hook_success` is not |

## Task 1: shared adapter base and migrations (W12/W15 groundwork)

Extract the host-independent lifecycle from `claude_code.py` into `memex/integrations/host_adapter.py`: capability binding, worktree verification, request/response replay with verdict comparison, the four-state delivery ledger, rendering, denial chains, and the hook lifecycle. `ClaudeCodeAdapter` keeps its public import path and behavior; its Phase 3 tests run unchanged.

Namespace control state by host. `live_adapter_sessions` is keyed by `(harness, native_session_id)` and `live_adapter_deliveries` gains `harness` and `turn_id`. Existing Phase 3 rows are migrated in place to `harness='claude_code'` by a guarded table rebuild inside `BEGIN IMMEDIATE`, idempotent and safe under concurrent initialization. Tests use the exact Phase 3 schemas, populated.

## Task 2: W12 Codex adapter

`memex/integrations/codex.py`: `CodexAdapter(HostAdapter)` with its own capability file, `cx-` session derivation, `apply_patch` target parsing, opaque `Bash`, a Codex context cap, rollout provenance (`<CODEX_HOME or ~/.codex>/sessions/**/rollout-*-<session_id>.jsonl`), and structured rollout parsing:

- snapshot: `response_item` `message`, role `developer`, `content_item_kinds` containing `hooks.additional_context`, whose text contains the **entire** rendered packet and does not begin with the truncation warning;
- correction: `response_item` `function_call_output` or `custom_tool_call_output` whose text contains `PreToolUse hook` and the entire rendered correction, with `turn_id` equal to the denied call's turn.

Failing-then-passing tests cover wrong-session files, a forked file naming another session, the truncated record, hook output entries, prose quoting markers, non-error outputs, wrong turn, malformed lines, expired replay, revoked access, concurrent hook invocations, and identical native session IDs across hosts.

## Task 3: W14 shared-checkout coordination and the guard

Default mode needs no new mechanism: each host session has its own task, cursor and receipts, and the core already compiles corrections against each baseline. Tests prove independent corrections for two hosts holding the same claim, quiet unrelated edits, and that one host's acknowledgement does not suppress the other's correction.

Guarded mode is new: `memex/runtime/guard.py`, opt-in per registration.

- **Supported set:** whole-file create, update, delete and rename of regular files inside the registered worktree, alone or as an atomic multi-file set; and `git commit --only` of explicitly named paths under a worktree-wide lease. Everything else (checkout, reset, rebase, merge, stash, shell writes, editors) is reported unsupported/advisory.
- **Leases and fencing:** a lease row per canonical target with a per-target monotonic generation. Acquisition is all-or-none in canonical order inside `BEGIN IMMEDIATE`.
- **Fenced commit:** inside one SQLite write transaction, which is the cross-process critical section, the guard rechecks every lease's holder, generation and expiry, rechecks expected hashes on disk, stages content to temporary files, records a durable intent, replaces, captures result hashes and only then commits. A paused holder whose lease expired and was replaced fails the generation check at commit, so the token is enforced on the write path. A crash releases the SQLite lock automatically; expired leases are reclaimable; an interrupted multi-file replace is rolled forward from its intent record on the next guard entry.
- **Paths:** canonicalized with resolution and case normalization; links or junctions resolving outside the worktree are rejected; Windows aliases map to one lease key.
- **Git:** a `$worktree` lease conflicts with every file lease in that worktree. The guarded commit commits only named paths, reports other staged paths as an ownership advisory, and never stages, resets or discards anything else.

Hosts reach the guard through `memex/integrations/guard_mcp.py`, a separate opt-in stdio MCP server exposing `guard_read` and `guard_write`, so the fourteen-tool public server is unchanged. In guarded mode the host hooks deny native edits to supported targets with an instruction to use the guard, so participating clients cannot commit through an unguarded path.

Race tests use real processes and deterministic barriers, not sleeps alone: two writers on one target, a paused holder replaced after expiry, a crash with a held lease, all-or-none multi-target acquisition with opposite orderings, an external writer bypassing the guard, path aliases, and concurrent Git commit against a file write.

## Task 4: W13 worktree isolation and integration

Real linked worktrees of one repository, one engine per worktree. Tests: the same path with different APIs gets per-view claims; an unmerged edit in B leaves A's facts unchanged; after merge, cherry-pick, rebase and conflict resolution A revalidates against its actual contents before an affected action; B's test evidence does not certify A; same relative path and symbol stay distinct; a branch-local inferred proposal neither applies across worktrees nor supersedes an approved rule; correction text never names another task's scope.

## Task 5: W15 continuity and conflicts

Tests: compaction and resume redeliver and confirm a full working set for both hosts; a historical transcript packet does not count after `SessionStart`; identical native session IDs across hosts keep separate bindings, cursors and deliveries; restart, expiry, duplicate receipts and failed insertion; after two reconsiderations the response reports an unresolved conflict, does not retry, and does not choose a winner.

## Task 6: native gates

1. Codex single-host reconsideration with the Phase 3 discriminating fixture, through the Codex adapter.
2. Separate worktrees, both clients active: Claude in A and Codex in B. B's unmerged contract change does not reach A; integration into A forces revalidation before A's affected action. Run Claude-first and Codex-first.
3. Same checkout, both clients active and both holding the same claim: an external contract change; each host's affected mutation is prevented and corrected independently and the revisions pass the contract. Run Claude-first and Codex-first.
4. Guarded writes, both clients active: both route through the guard against one target; only the current matching write commits, the other replans.

Barrier hooks hold each client after its initial delivery is confirmed so both sessions are demonstrably active together, and release them in the chosen order. A sequential single-client run does not satisfy a concurrency gate.

## Validation and finish

Phase 4 tests, Phase 1–3 suites, broad compatibility, lint, `pip check`, build, `twine check`, documentation links. Update 00, 05, 06, 08, 11 and write 20 with fresh commands, versions, integration modes and limitations. Mark Phase 4 complete only if the native concurrency evidence exists. Focused commits; push `codex/v1-phase4`.
