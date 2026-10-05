# Host integration and delivery plan

Status: the Claude Code adapter is implemented and verified against version 2.1.289; Codex remains planned. Measured host semantics, the native proof and the limitations are in [18_PHASE3_VERIFICATION.md](18_PHASE3_VERIFICATION.md). Research snapshot: 3 October 2026; host measurement: 5 October 2026.

## Core boundary

Adapters translate host events into session/task/action records and translate core results into native host behavior. The core does not parse complete transcripts, scrape hidden reasoning or own the agent loop. Capture normalized intent, action kind/targets, expected hashes and minimal delivery/outcome metadata.

All hosts use the same action contract, including unavailable coverage and resynchronization. A host's MCP connection/session is not its durable agent task identity. Register explicit identity, support cancellation and close/expire abandoned tasks.

## Capability negotiation

| Capability | Meaning |
| --- | --- |
| `session_context` | Insert a bounded initial working set |
| `action_observation` | Observe proposed action and declared targets |
| `action_reconsideration` | Prevent a material stale pending action, supply correction and resume model planning |
| `delivery_ack` | Confirm insertion into the intended session, identifying *which* packet |
| `resume_identity` | Verify session continuity after restart |
| `guarded_write` | Supported writes execute with expected-hash checking in a cooperating guard |

A host with session context only remains a v0.9-style integration. Action observation without reconsideration is advisory. The Live Context product claim requires native evidence of reconsideration; guarded-write claims require the additional write capability. Do not infer capabilities from a hook's name or from an HTTP 200 response.

## Claude Code adapter

Use documented session/prompt/action hooks for registration and action checks. Capture the actual native session ID and working directory. Normalize tool paths relative to the registered checkout; prevent a request naming a different worktree without explicit registration.

S01 measured this on version 2.1.289 and the concern was correct: `additionalContext` arrives alongside the tool result, so allow-plus-correction would let the stale action run first. The supported route is a synchronous `PreToolUse` returning `permissionDecision:"deny"`, which prevented the pending mutation with the target bytes unchanged and delivered the reason to the model as an `is_error` tool result that it then acted on. Deny is honored even under `bypassPermissions`.

Two measured constraints bound the gate. A hook exceeding its configured `timeout` does **not** block, so the write proceeds; the adapter keeps an inner deadline and records a fail-open advisory rather than implying it gated anything. And `permissionDecision:"defer"` ends a non-interactive turn with `stop_reason: tool_deferred`, abandoning the action instead of failing open, so the adapter emits `deny` or no decision at all. It subtracts permission and never grants it: emitting `allow` would bypass the user's own permission policy on a memex outage.

Measured delivery confirmation: each delivery embeds an unforgeable marker of its own identity, and is acknowledged only when the host's own records show it inserting *that* packet into *that* session. Two record shapes qualify on the installed client, and they are the ones it writes because it used the output: an injected-context attachment for an initial packet, and an error tool result naming the denied call for a correction. The client's separate log of a hook's raw stdout is not one of them at any exit code, because it is written whether or not the output was used. Returning from a hook, writing to stdout and exiting zero identify no packet and are not acceptance; a valid marker identifies a packet but still does not prove it was delivered. The transcript is located through the client's own per-session layout rather than trusted from the payload, and a record belonging to another session confirms nothing. A correction names the evidence paths whose bytes changed, not only the action target, so the agent knows which dependency to recheck.

Do not use permission escalation to ask the human about every correction. Return a machine-readable reconsideration reason and bound repeated attempts. Do not alter permission policy, auto-approve tools or rewrite tool arguments merely to deliver context. Claude's documented defer path has version/mode restrictions; it is not a universal interactive-session pause mechanism.

Spike S01 is closed. Parallel edit calls were serialized by the host, each with its own check/execute pair, so a check certifies its own declared target at its own moment and no batch-wide guarantee may be claimed. The required trace, and the agent correctly overriding a correction it could disprove, are recorded in 18.

## Codex adapter

The [official configuration reference](https://developers.openai.com/codex/config-reference) documents lifecycle hooks including PreToolUse. Pin and inspect the installed version instead of assuming all desktop/CLI versions have identical behavior.

Spike S02 checks the actual supported lifecycle contract, synchronous interception, feedback insertion, shell/file tool coverage, session/worktree identity and batched/parallel tool behavior. Implement the same core outcomes without relying on a Claude-specific output schema. If the installed hook API cannot reconsider a pending action, use a supported SDK/tool-wrapper path and record that integration mode; do not call advisory notifications full support.

P4 requires Claude Code and Codex operating simultaneously. Preserve distinct harness/native/Memex session IDs, delivery streams and adapter-version receipts. Each native test runs both event orderings and proves the correction belongs to the right session.

## Hermes compatibility

Retain existing read-only `MemoryProvider.prefetch()` and current packet formatting/timeout behavior. `sync_turn()` remains free of transcript capture. A future live Hermes adapter needs a verified action interception path; pretask prefetch alone does not certify the v1 loop. Report its negotiated support level accurately rather than dropping the existing integration.

## MCP projection

Expose task working-set snapshots and changes through bounded authorized resources and existing compatible retrieval tools. Negotiate the client's protocol version; do not assume the installed Python SDK supports the newest subscription schema.

The [2026-07-28 resource specification](https://modelcontextprotocol.io/specification/2026-07-28/server/resources) documents resource updates through negotiated subscriptions. A resource update is a signal to fetch/check context, not evidence of insertion or action reconsideration.

Measured: the installed MCP SDK (1.30.0) derives `ResourcesCapability` with `subscribe` hardcoded to false whenever a list-resources handler is registered, so this server cannot advertise resource subscription at any protocol version. Negotiation therefore selects the polling fallback, which is implemented rather than merely documented.

Do not broaden the MCP tool count merely to expose every internal runtime operation. Prefer a small projection of the task snapshot and action-check contract with existing authorization.

## Adapter lifecycle and error handling

Register → open task and subscribe → initial snapshot → action check → insertion/reconsideration → delivery receipt → objective outcome when available → close/expire. Snapshot/subscription ordering must replay intervening changes.

Use bounded local calls and a declared deadline. On daemon/network failure, return unknown freshness with a diagnostic, retain unacknowledged corrections for resynchronization and avoid retry storms. No adapter fabricates a successful receipt after timeout. After compaction, either prove retained context or redeliver the bounded working set.

Debug artifacts include IDs, ordering, hashes, adapter version and coverage. Raw credentials, transcripts and complete tool outputs are unnecessary. Host-local integration logs are not automatically ingested as engineering knowledge.
