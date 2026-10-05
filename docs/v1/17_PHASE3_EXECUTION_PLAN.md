# Phase 3 execution plan

Status: W09–W11 executed from verified Phase 2 commit `3194450` on `codex/v1-phase3`, then hardened against three findings. See the [verification report](18_PHASE3_VERIFICATION.md) for measured results and the hardening record.
Spec: [06_HOST_INTEGRATIONS.md](06_HOST_INTEGRATIONS.md), [04_CONTEXT_CONTRACTS.md](04_CONTEXT_CONTRACTS.md), [05_MULTI_AGENT_CONCURRENCY.md](05_MULTI_AGENT_CONCURRENCY.md).
Verification report: [18_PHASE3_VERIFICATION.md](18_PHASE3_VERIFICATION.md).

## Constraints

No Docker, paid benchmark campaign, new public MCP tools, global security setting changes, or credential exposure. Neo4j keeps immutable evidence/claims/verification history; SQLite keeps bounded control state and the new delivery/outcome trace. Phase 4 owns Codex, two-host concurrency, leases/fencing and guarded atomic writes; this phase must stay compatible without implementing them. No release, merge or deploy.

The core `LiveContextEngine` API is frozen for this phase. W09 binds a host to it; it does not redesign it.

## S01 completed first: measured host semantics

S01 ran against the installed client before any adapter design. Full traces and per-finding evidence are recorded in [18_PHASE3_VERIFICATION.md](18_PHASE3_VERIFICATION.md). The design consequences are:

| Measured behavior | Design consequence |
| --- | --- |
| `PreToolUse` is synchronous and `permissionDecision:"deny"` stops the pending mutation; the target file hash was unchanged at the next hook event | Deny is the gate. This is the supported route; `allow` plus `additionalContext` is not |
| The denial reason reaches the model as an `is_error` tool result and the model then re-reads and reissues | Reconsideration is native, not advisory. The reason must be machine-readable and name the target |
| `deny` is honored under `--permission-mode bypassPermissions` | The gate does not depend on permission policy, and must not modify it |
| A hook exceeding its configured `timeout` does **not** block; the write proceeded | The adapter needs its own deadline strictly inside the hook timeout, and must record a fail-open advisory rather than a gate |
| Parallel `Edit` calls in one model turn were **serialized**: a sibling write completed before the next `PreToolUse` ran | A check certifies its own declared target at its own moment only. No batch-wide guarantee may be claimed |
| The model verified a correction it could not confirm and overrode it | A correction must be true. memex must gate on real measured drift, never on a bare notification |

`permissionDecision` values are `allow`, `deny`, `ask`, `defer`. Hook input supplies `session_id`, `cwd`, `permission_mode`, `tool_name`, `tool_input`, `tool_use_id`, `transcript_path`, `prompt_id`. `additionalContext` is capped at 10,000 characters.

## Task 1: W09 Claude Code adapter and diagnostics

Produce `memex/integrations/claude_code.py`: a hook entry point plus the adapter that owns host binding and the open/check/ack/close lifecycle.

**Trust.** A hook payload is not an authenticator. Registration writes a capability file under the repository's Git common directory (`<common_dir>/memex/adapters/claude-code.json`, owner-only) holding a random secret and the authorized `principal_id`. The adapter reads its principal from that file, never from stdin. `memex_session_id` is derived as an HMAC of the native session ID under that secret, so a forged native session ID cannot address another stream. The payload `cwd` must resolve through `discover_repository` to the registered `worktree_id`; a mismatch is refused rather than served.

**Lifecycle.** `SessionStart` resolves the view, calls `open_task` and returns the bounded packet as `additionalContext`. Acknowledgement runs only on per-packet evidence that the host inserted it, which the hardening pass implemented as a marker-in-transcript confirmation: preparation and printing are distinct from confirmed insertion and never stand in for it. `PreToolUse` on a declared edit tool normalizes targets relative to the registered root, derives `expected_hashes` from the acknowledged baseline's own `support_hashes` (what memex actually told this session), and calls `check_action` with `attempt_id = tool_use_id`. A reconsidered attempt carries the prior denied `tool_use_id` as `original_attempt_id`, so the engine's two-attempt ceiling applies. `SessionEnd` closes the task.

**Outcome mapping.** `replan` and `resync_required` deny with the correction text, which names the evidence paths whose bytes changed rather than only the action target. A replayed attempt is answered from the core's current verdict, so an expired attempt requests a new attempt ID instead of reusing an earlier non-denial. `proceed` allows. `unavailable` defers to normal permission flow with an explicit unknown-freshness advisory, because fail-open must not look like certification.

**Action kinds.** `Edit`, `Write`, `MultiEdit` and `NotebookEdit` declare their targets and are gated. `Bash` and other opaque tools cannot enumerate targets; they are recorded with `scope_complete=False` and deferred, never denied and never certified. Undeclared mutation coverage is not claimed.

**Preservation.** Hook installation composes with existing user hook configuration and is idempotent and reversible. It never edits `permissions`, never auto-approves a tool and never rewrites tool arguments. Installation into a real user settings file is an explicit opt-in command, not a side effect of import or test.

**Bounds.** A hard adapter deadline below the hook timeout, bounded retries, and no fabricated receipt on timeout or failed insertion.

Write failing tests first, then implement. Commit W09.

## Task 2: W10 delivery and outcome trace projection

Produce `memex/runtime/trace.py`: a bounded SQLite projection in the existing control-plane database.

Record session/task/action identities, a monotonic ordering index, tool name, normalized targets, checked view ID, coverage digest, delivered/observed hashes, adapter version, gate decision, insertion result and objective outcome. Keep three dimensions explicitly separate: delivery exposure, agent compliance, and execution success. A timeout or failed insertion records exactly that and never an acceptance.

Capture no raw transcripts, no hidden reasoning, no credentials and no complete tool outputs. Tool input is retained as a digest for correlation, not as text, so `old_string`/`new_string`/command bodies never enter the trace.

Write failing tests first, then implement. Commit W10.

## Task 3: W11 Hermes and MCP compatibility

Preserve `MemoryProvider.prefetch()`, existing packet formatting and timeout behavior, and transcript-free `sync_turn()`. Add a regression test that fails if any of these change.

Add `memex/integrations/mcp_live.py`: a bounded authorized **resource** projection of the task working set. No new tools; the public tool surface stays at fourteen. Negotiate the client's declared protocol version and capabilities, and provide an explicit polling/read fallback when subscription is unsupported. A resource update is a signal to fetch, not evidence of insertion or reconsideration, and the projection says so.

Write failing tests first, then implement. Commit W11.

## Task 4: native phase exit and verification

The exit requires one real client run in which an initial packet is used, supporting evidence then changes, the materially affected mutation is prevented before it executes, the correction reaches that session, the model proposes a revised action, and the revised action executes and passes an objective check.

The demonstration uses genuine drift, because S01 showed the model correctly rejects a correction it can disprove. It must also require a *different implementation*: the drift is a dependency contract change, and the objective contract is evaluated against both the reconstructed denied proposal, which must fail, and the shipped implementation, which must pass. A patch that satisfies the contract either way cannot show the correction changed anything, which is why two earlier fixtures were discarded.

Required coverage beyond the exit trace:

| Case | Required result |
| --- | --- |
| Native mutation-before-edit ordering | Pending write prevented; target bytes unchanged at denial |
| Unrelated change | No interruption of a disjoint action |
| Failed insertion, timeout, cancellation, replay | No fabricated receipt; advisory on fail-open; idempotent attempt replay |
| Revoked source access; session/worktree spoofing | Refused, with no assertion text disclosed |
| Backend outage | Explicit unknown freshness |
| Outage with a known pending correction | Correction preserved; replan still required |
| Packet overflow and resynchronization | `resync_required`; bounded reconsideration ceiling enforced |
| Parallel/batched actions | Per-target behavior only, matching measured serialization |
| Phase 1/2 and Hermes/MCP compatibility | Existing suites still pass; tool count unchanged |

Run lint, the phase suite against native Neo4j, the Phase 1/2 selection, the broad compatibility suite, package validation and documentation checks. Record commands, results, skips, client versions and limitations as measured, with no reuse of historical counts. Update [00_INDEX.md](00_INDEX.md), [08_ROADMAP.md](08_ROADMAP.md), [06_HOST_INTEGRATIONS.md](06_HOST_INTEGRATIONS.md) and [11_DECISIONS_AND_RISKS.md](11_DECISIONS_AND_RISKS.md) to match measured behavior. Mark Phase 3 complete only if the native ordering gate actually passes.

## Review focus

Host payload trusted as authentication; principal or worktree spoofing; target normalization and worktree escape; expected-hash provenance; attempt identity reuse and the reconsideration ceiling; fail-open paths that could read as certification; fabricated receipts; trace content leaking source text, transcripts or credentials; permission policy or user hook configuration mutated; tool surface growth; claims of protection against writers after the check.
