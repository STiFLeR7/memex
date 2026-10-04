# Phase 2 verification and integration boundary

Status: W06–W08 complete, verified and ready for P3. All independent review findings fixed. Date: 4 October 2026.
Branch: `codex/v1-phase2`, based on verified Phase 1 commit `5640128`. Code-review checkpoint: `16ad4c4`.
Plan: [15_PHASE2_EXECUTION_PLAN.md](15_PHASE2_EXECUTION_PLAN.md).

## Implemented responsibilities

| Work | Implementation | Evidence |
| --- | --- | --- |
| W06 | Strict versioned immutable core records; Neo4j evidence/revision/support/supersession history; deterministic per-view verification | [Support tests](../../tests/test_live_supports.py), [real graph tests](../../tests/test_live_claim_graph.py) |
| W07 | Durable SQLite tasks, bounded normalized intent/acceptance references/dependency IDs, independent delivery cursors, pending packet replay, continuity tokens, renewal and expiry | [Control-plane tests](../../tests/test_live_tasks.py) |
| W08 | Authorized open/check/ack/close core API; scoped disk refresh, explicit uncertainty/retraction/replacement, expected-hash checks, bounded full resync and idempotent action attempts; nonblocking bounded parsing | [Native evidence-to-correction tests](../../tests/test_live_actions.py) |

An evidence hash change makes a hash-bound claim need revalidation. A deterministic `symbol_exists` predicate may recheck current structure. A sufficient alternative survives another failed support; only the successful witness contributes required coverage. Approved constraints use explicit authorized approval evidence and applicability, so changing a governed implementation does not revoke the rule. No inferred revision is automatically promoted.

Neo4j is authoritative for claims/evidence/verification history and explicit OR-of-AND support edges. SQLite stores task-control metadata, pending delivery content and acknowledged working sets; it is not a parallel semantic memory. Snapshots carry the full checked repository view and per-item verification records. Historical verification remains attached to its checked view and principal. Syntactic call edges are never implicit proof dependencies.

## Executed evidence

- Phase 2: **38 passed**, including **24 native graph/action cases**, no skips; one Graphiti dependency deprecation warning.
- Combined Phase 1 + Phase 2 selection: **118 passed**, no skips; same dependency warning.
- Broad compatibility: **669 passed, 1 pre-existing Windows skip, 51 integration cases deselected**, 12 existing dependency/mock warnings. The combined selection separately runs 39 of those integration cases; the remaining 12 legacy integration cases requiring unrelated services/providers were not run. These counts overlap and must not be added as distinct tests.
- Ruff correctness/bugbear checks passed for all changed Python files; `pip check` clean.
- Wheel build and Twine metadata validation passed; archive inspection confirmed all six Phase 2 modules. Version remains **0.9.0**, because completing P2 is not the v1 release.

The isolated test stack used Python 3.12, Graphiti 0.30.2, Neo4j client 6.3.1 and native Neo4j Community 5.26.30 with Java 21. No Docker, model calls, raw transcript capture or real provider credentials. Temporary Git repositories covered shared checkout sessions and linked worktrees. The temporary server ran only on the isolated loopback Bolt port 17687; HTTP/HTTPS and authentication were disabled for this local fixture.

The first background launcher stopped before a later run; that run was interrupted and not counted as successful. The final successful runs used a persistent foreground tool session. This is infrastructure recovery, not a substituted mock graph.

## Mechanism coverage

| Case | Executed behavior |
| --- | --- |
| F01 | Body-only change after accepted initial packet produces `uncertain`, identifying the exact prior delivered revision; expected target hash mismatch requires replan |
| F04 | Unrelated README edits and surviving alternatives produce no material correction |
| F05 | OR-of-AND support keeps a claim supported when another alternative changes or fails parsing |
| F06 | Approved constraint stays active after governed source changes; authority requires trusted approval ingestion |
| F07 | Parser failure is unknown evidence coverage; an initially supported assertion receives an explicit uncertainty correction |
| F08 | Same claim is supported in one worktree and needs revalidation in another, with no global state overwrite |
| F10 | Claude/Codex sessions with the same native host ID retain distinct tasks, acknowledgements and corrections |
| F13 | Action-time disk recapture catches unannounced edits and subscribe/snapshot drift; SQLite pending streams survive manager restart |
| F14 | Real Neo4j exception type and unavailable indexing return unknown freshness; known target drift still requires replan |
| F15 | Packet/dependency bounds request resync; incomplete packets cannot establish accepted baselines; reconsideration stops after two retries |
| F16 | Future observations are excluded; concurrent unsuperseded proposals stay conflicted/inferred; approved predecessors resist inferred supersession |

These are deterministic mechanism tests, not an official external benchmark score, model efficacy measurement or native-host compliance demonstration. F03/F12 have core supersession/compaction coverage here, but full native-host behavior remains later. F09/F11 guarded merge/write coverage remains P4.

## Reproduce

Use a clean project environment with declared dependencies and development checks installed. Start an **isolated** native Neo4j server, separately from production, using the native setup in [14_PHASE1_VERIFICATION.md](14_PHASE1_VERIFICATION.md). Keep its console process alive for the test duration. Set:

```powershell
$env:MEMEX_PHASE1_NEO4J_URI='bolt://127.0.0.1:17687'
python -m pytest tests/test_live_supports.py tests/test_live_claim_graph.py tests/test_live_tasks.py tests/test_live_actions.py tests/test_phase2_review_regressions.py -q
python -m pytest tests -m 'not integration' -q
```

The inherited test environment-variable name is intentional so both phases share one isolated server. If it is absent, native tests skip; such a run does **not** satisfy this phase gate. The selected combined run also includes the Phase 1 tests listed in its verification report. Broad legacy tests use dummy provider values only where required to construct mocked clients, not to authorize provider traffic.

## Integration boundary and execution decisions

`LiveContextEngine` is an opt-in Python core API, not a new public MCP endpoint. It takes an existing registered `RepositoryIndexer`, `ClaimStore`, `TaskStore`, and mandatory trusted authentication/worktree/source authorization callbacks. Host clients do not authenticate themselves by merely supplying `principal_id`; the P3 adapter must bind identities to its verified native session and registration. Authority ingestion likewise requires explicitly trusted approval authorization.

The minimal invocation sequence is: register/index a worktree; ingest explicitly supported immutable evidence and revisions; `open_task(OpenTaskRequest)`; insert the returned packet; `ack_delivery(DeliveryReceipt)` only after host insertion; call `check_action(ActionRequest)` with a **new** attempt ID for a reconsidered action; insert/ack corrections; close or renew the task with continuity proof. An action receipt is exposure metadata, not proof of agent compliance or test success.

Task subscriptions bind selected revision families to each stream. Graph support edges persist dependency identities; action checks re-evaluate bounded explicit proof dependencies. Subscribe/snapshot correctness uses durable baselines plus deterministic catch-up rather than relying on an ephemeral notification. The current implementation rescans at action boundaries; eager background dirty propagation and graph-selection optimization can be added without changing delivery semantics. Cost: warm action checks currently include Git capture and graph work; no P5 latency claim is made.

Proceed means declared evidence/targets were checked at this point in this view. It does not authorize a write or lock arbitrary editors. Cached attempt responses preserve their original checked view and deadline, reject changed requests and revoked source authorization, and require a new attempt after expiry. Target creation checks explicit expected absence, never treating a Git-ignored existing file as absent. Backend outages return assertion-free unknown coverage when source authorization cannot be re-established. They preserve any already-known pending correction and require replan rather than reverting to the acknowledged predecessor.

Retention is bounded: one pending packet, an item/character budget, 128 active tasks by default, 128 cached action attempts per task, at most 24-hour renewable lifetimes. Expiry/close clears control state; graph revision/view retention is a later P4 concern. Full graph selection is bounded and reports incomplete projection rather than certifying a truncated history. Tiny budgets return a fixed resync control envelope instead of silently truncating required corrections.

Core continuation tokens prove baseline continuity; `context_retained=False` forces a replacement snapshot after compaction. Native retention proof, receipt/outcome trace projection, proactive host insertion and guarded writes remain P3/P4 work. Legacy v0.9 retrieval remains compatible and cannot silently acquire v1 supported status; no automatic legacy ingestion was added.

## Final independent review

The fresh whole-branch review of `5640128..16ad4c4` found two critical and three important defects, with no minor findings. All five were reproduced and fixed in `09fc984`, with failing-then-passing regression tests. The final suite results above were recorded after those fixes.

| Finding | Verified correction |
| --- | --- |
| Revoked claims bypassed source authorization | Source authorization runs before lifecycle shortcuts; inaccessible claims cannot be projected |
| Outage and cached replay exposed revoked source text | Source authorization is rechecked; unavailable coverage carries no assertions |
| Outage discarded a known pending replacement | The pending correction and its sequence survive, and the action still requires replan |
| 128 retractions plus additions overflowed before resync | Overflow is detected before schema construction and requests a full resynchronization |
| Synchronous parsing blocked the event loop past the deadline | Parsing uses two bounded process workers and a shared monotonic budget; the 20 ms deadline regression passed |

The snapshot contract was also corrected and tested: snapshots include the full checked repository view and per-item verification records. No review findings remain deferred. Native host delivery/authentication bindings remain P3, guarded writes remain P4, and efficacy/latency evaluation remains P5, as described above.

## Post-execution cleanup

On 4 October 2026, after the user requested cleanup, the generated `output/phase1`, `output/phase2`, and `.superpowers/sdd/15_PHASE2_EXECUTION_PLAN` directories were removed from this execution worktree. These contained temporary environments, downloaded Neo4j/Java runtimes, wheels, and duplicate review/test logs. The empty `output` directory and empty review parents were removed too. Generated Python bytecode, Hypothesis state, pytest caches, and Ruff caches were also removed.

Before deletion, the recorded final test results and review ledger were checked. No Java process using the owned native runtime was active, the isolated Bolt port 17687 was closed, and the deletion targets contained no reparse points. After deletion, all three targets were confirmed absent. Committed source, tests, planning documents, verification reports, and the active execution worktree were preserved. The separate `D:\memex` checkout and its concurrent work were left untouched.

The recorded test counts describe the completed Phase 2 runs; application tests were not rerun for this generated-file cleanup. Recreate the test environment and isolated server using the reproduction instructions when needed.
