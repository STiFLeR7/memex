# Migration, operations and release

Status: planned rollout. v0.9 remains the compatibility baseline until v1 gates pass.

## Legacy knowledge policy

Existing graph entities, episodes and approved decisions remain intact. Import them into v1 projections as `legacy_unverified` coverage unless exact source support and applicable view can be reconstructed. Existing timestamps/confidence are preserved but do not become a current-evidence certificate. A previous approved decision retains its authority; source freshness and applicability remain separate.

Backfill explicit support only from permitted reconstructable sources. Do not invent historical evidence bytes, approval events or old working-tree states. If a legacy entity lacks a unique scoped origin, retain it for explicit historical retrieval and withhold it from verified current context until resolved.

## Migration sequence

1. Detect current schema/runtime versions; record the configured repository registry and graph connection without exposing credentials.
2. Back up the affected graph schema/data through the existing supported administrative process. The migration must support dry-run counts before any write.
3. Allocate stable repository/worktree identities, resolve linked Git paths and record the mapping. Do not infer one identity for unrelated clones merely because their remote URLs match.
4. Add namespaced v1 labels/properties and idempotent migration markers. Keep legacy projections intact; do not overwrite legacy symbol identities with worktree overlays.
5. Build a complete structural view from current permitted source bytes. Publish only after its graph completion record commits.
6. Import eligible legacy claims with explicit verification coverage. Build supports in bounded batches with resume/checkpoint records.
7. Enable shadow action checks for one checkout. Shadow mode records what would change without claiming that the agent was corrected.
8. Enable live reconsideration on a supported host after its native proof; then enable the second host and concurrency matrix.
9. Enable opt-in cooperating guarded writes only where both client capabilities are verified. Advisory/default context checks retain their explicit limitations.

Each step has an idempotency key, schema version, completed count and resume point. Crash recovery rechecks graph completion records before advancing local journal state. Schema expansion is additive for the v1 rollout; destructive pruning is a separate decision after rollback evidence exists.

## Configuration baseline

Implementation exposes a feature mode equivalent to `off`, `shadow`, `live` and an independent guarded-write option. Exact CLI/config spelling is frozen in W18 after existing configuration conventions are inspected; the semantic modes above are fixed.

Expose runtime directory, action-check deadline, packet bounds, retry ceiling, task idle expiry, journal/receipt retention and supported integration capabilities through normal diagnostics. Baseline automatic reconsideration ceiling is two attempts per original action. Task idle expiry is 24 hours; closed-task receipt retention is seven days by default. These are configurable control-state defaults, not permission to expire approved engineering knowledge.

Acknowledged journal records can be compacted after seven days only when no recovery, active task, view or evidence dependency still needs them. Pending/unapplied work cannot be discarded by age. Bound queue growth and return incomplete/unavailable coverage under backpressure. Retained graph history follows existing governance; do not delete supports referenced by current assertions or retained task outcomes.

## Diagnostics

Expose repository/worktree mapping, observed versus indexed generations, structural/semantic readiness, pending event count, replay errors, dirty tasks, stale delivery baselines, adapter capability/version, last failed insertion, bounded-retry exhaustion and guarded-write coverage. A graph connection's health alone is not a context freshness result.

Recommended diagnostic responses are actionable: "index is two generations behind; current action checked as unknown," "client has advisory hooks only," or "session must resynchronize after compaction." Keep ordinary product messages concise; do not show internal graph/query jargon unless requested.

## Rollback

Disable live adapters and guarded writes first; release/expire cooperating leases. Preserve the journal, migration mapping, evidence and diagnostic records for recovery. Existing Hermes/MCP readers resume their legacy projection; legacy entities are not deleted during rollout.

Rollback cannot claim that a pre-v1 reader understands v1 verification fields. Its context is explicitly legacy behavior. Re-enabling v1 requires checking current source bytes, catching up unapplied events and resynchronizing task baselines. Old live receipts do not establish that a restarted model still retains the context.

## OSS onboarding

Provide one documented setup using an existing reachable Neo4j service, one native-client recipe per supported integration, capability diagnostics and a small mutation-before-edit demo. Docker is not required for this integration recipe. Do not silently change the datastore to make a demo self-contained.

Provide a deterministic core demo using temporary Git repos and test doubles for contributors without graph/model services. Label it a core-contract demo; it is not the native-client or real-graph proof. Explain setup/model cost before optional live trials. Verify Windows and Linux path/worktree registration with declared tested versions; do not advertise unsupported platforms as validated.

## Release checklist

- [ ] P1–P4 mandatory correctness, authority, isolation and native delivery gates have evidence.
- [x] P5 preregistration, comparable-resource trials and stable-workload safeguard are recorded ([22](22_PHASE5_PROTOCOL.md), [23](23_PHASE5_VERIFICATION.md)). The efficacy gate itself failed ([24](24_PHASE5_RELEASE_READINESS.md)).
- [ ] Two simultaneous host clients pass the worktree/shared-checkout matrix.
- [x] Migration is resumable; rollback returns to intact legacy projections ([23](23_PHASE5_VERIFICATION.md)).
- [ ] Existing provider, MCP and write-governance behavior is covered.
- [ ] Capability tables distinguish prefetch, advisory delivery, reconsideration and guarded writes.
- [ ] At least 3 independent installations, each with 4 complete pilot weeks or 50 agent sessions, report automatic counts-only evidence of corrections and continued use (gate amended 6 October 2026, see [07](07_EVALUATION.md)). **Pending:** the automatic pilot is ready ([26](26_MAINTAINER_PILOT_KIT.md)), no participants yet.
- [x] Public report includes failures, costs, confidence limits and external benchmarks not run ([23](23_PHASE5_VERIFICATION.md), [24](24_PHASE5_RELEASE_READINESS.md)).
- [ ] Version/package/changelog/README are updated only after release evidence exists.
- [ ] The v1 claim is limited to maintained engineering evidence in supported scopes/hosts.

Under the current no-Docker constraint, container-based external benchmarks remain not run. This does not prevent local implementation; it prevents claiming those external results until actually reproduced through an authorized available environment.
