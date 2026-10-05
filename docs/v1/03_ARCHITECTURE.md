# Live Context architecture

Status: P1 repository/indexing core and P2 evidence/task/action core are implemented and verified. P3/P4 add the Claude Code and Codex adapters over one shared lifecycle, the outcome trace and opt-in guarded writes (see [20_PHASE4_VERIFICATION.md](20_PHASE4_VERIFICATION.md)). Overlay/retention optimization and efficacy gates remain later work. Contracts are defined in [04_CONTEXT_CONTRACTS.md](04_CONTEXT_CONTRACTS.md).

## Responsibilities

| Component | Responsibility | Proposed ownership boundary |
| --- | --- | --- |
| Repository-view resolver | Identify repository/worktree, capture HEAD and relevant content hashes | New `memex/context/revision.py`; later `memex/runtime/views.py` |
| Change journal and coordinator | Durable events, idempotent replay, per-worktree ordering, completed generations | New `memex/runtime/journal.py`; existing watcher integration |
| Structural index adapter | Reconcile symbols, calls and imports in a view | Existing extractor/graph modules plus `memex/runtime/indexing.py` |
| Support/verification engine | Claim evidence, applicability, dirty propagation, bounded revalidation | New `memex/runtime/supports.py` and `verification.py` |
| Task working-set manager | Task subscriptions, delivered revisions, renewal and resynchronization | New `memex/runtime/tasks.py` |
| Action coordinator | Validate view/targets, compile corrections, manage retry outcomes | New `memex/runtime/actions.py` |
| Host adapters | Translate native sessions/actions/receipts into core contracts | New Claude Code/Codex adapters under integrations |
| Evaluation projection | Join source, delta, action and objective result without raw transcripts | Extend existing evaluation and task/outcome modules |

Proposed file names are responsibility boundaries, not a demand for many empty wrappers. Merge a small component into its closest cohesive module if the accepted interfaces and tests remain clear. Do not restructure unrelated modules.

## Storage decision

Neo4j remains authoritative for durable engineering entities, immutable assertion revisions, supports, source/verification records and temporal history. Graphiti continues to supply semantic episode processing; the deterministic structural path must survive synthesis failures.

A standard-library SQLite store holds the **local control plane**: event journal, per-worktree generation progress, task/session cursors, pending deltas, delivery acknowledgements and optional cooperative leases. This adds no server dependency and does not duplicate semantic knowledge. Use one repository coordinator with configurable local runtime-directory storage, discovered from Git's common directory by default. Do not put SQLite WAL state on an unsupported network filesystem or assume it coordinates separate machines.

Events are at-least-once. Graph writes carry event/view identity and a graph completion record in the same graph transaction. SQLite marks completion only after graph success. A crash between graph commit and journal acknowledgement is recovered by checking that graph record and replaying idempotently. No cross-database atomic transaction is claimed.

## Source/view consistency

`repo_id` is a generated stable local identity associated with the common Git repository. Linked worktrees share it; unrelated clones do not share identity automatically from a remote URL. `worktree_id` identifies an individual registered checkout and survives an explicitly recognized move. Clone federation is outside the first release.

A view includes base commit, content generation, relevant source manifest and completed structural index generation. Use immutable indexed bases plus worktree-specific overlays. Build a candidate overlay, then publish its completed manifest/pointer after all writes succeed. Query readers resolve one published view; they must not combine nodes from a partially written generation. Missing/deleted overlay entries are tombstones, not fallback to stale base facts.

Structural and semantic readiness are separate. A structural view may be ready while explanations are provisional or unavailable. A parse failure is unknown coverage; it must not masquerade as a valid file with no calls/imports.

## Change flow

1. A watcher/Git notification records a scoped change durably. Events are hints, not sufficient evidence of current disk state.
2. The coordinator captures current relevant bytes and verifies a stable read. A repeated change retries within a deadline or records unavailable coverage.
3. Structural extraction computes a file contribution; old-minus-new relationships expire only within that file/view. Invalid parsing preserves historical data but withholds current coverage.
4. The candidate view commits with its completion record. The coordinator advances the published generation monotonically.
5. Reverse support links mark affected claim/view verifications dirty. Necessary supports invalidate a verification; alternative sufficient supports may preserve it. Invalidation follows explicit proof dependencies, not every call-graph neighbor.
6. Active tasks depending on affected claim revisions become dirty. Unrelated sessions remain quiet.
7. Revalidation uses deterministic predicates and explicitly scoped checks. Expensive checks/synthesis occur off the ordinary no-change path. Results remain tagged with exact input hashes and view.

## Action flow

The adapter supplies its session, task, current view, action targets and expected hashes. Unknown/opaque target sets require a conservative scoped check; the runtime must report reduced coverage instead of assuming the action cannot affect evidence.

The coordinator compares the action against published view progress and on-disk relevant support/target hashes. This second check catches watcher lag and external edits. A stale view triggers a bounded refresh; an unsupported or unavailable refresh yields an explicit unknown result.

Compile deltas relative to this session's acknowledged delivery baseline. Replacements, retractions and changed uncertainty come before optional new context. Material corrections return `replan`; compatible unchanged context returns `proceed`; unavailable coverage returns a distinct fail-open result, not fresh certification. Overflow returns `resync_required`.

The host adapter must make the model reconsider a pending action when required. Only a verified guarded-write adapter can compare expected hashes and execute a write inside a cooperating critical section. Hook-only checks retain a check-to-write race. Nothing globally stops external editors, shell writers or independent machines.

## Authority and bounded work

Separate source freshness, applicability and semantic verification. An approved rule remains authoritative until explicit supersession; a violating source edit can create a conflict rather than revoke the rule. LLM proposals never self-promote. A successful unrelated test is not support for all claims.

Limit propagation traversal, revalidation time, packet size, retries and active task retention. If a bound is reached, mark affected coverage incomplete and resynchronize when feasible. Never silently classify unvisited dependencies current. Cycles in explicit derivations form one dirty component and are not recursively traversed forever.

Authorization is checked at graph projection and session/task lookup. Control state contains metadata and bounded normalized intent, not prompt logs. Hashes do not bypass source-access checks.
