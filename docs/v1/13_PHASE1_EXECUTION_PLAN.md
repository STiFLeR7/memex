# Phase 1 remaining implementation plan

Status: executed and verified 4 October 2026; see [14_PHASE1_VERIFICATION.md](14_PHASE1_VERIFICATION.md). Scope: W03–W05, following W01/W02. Native Neo4j tests use an isolated localhost server; no Docker or model calls.

## Verified interfaces and decisions

The installed Graphiti Neo4jDriver exposes `session(database=None)`, returning the native asynchronous Neo4j session. `async with driver.session() as session: await session.execute_write(callback)` is the atomic transaction boundary. Completion markers and published worktree pointers are written inside that transaction, before SQLite acknowledgement. Source capture and Git discovery run outside graph transactions.

Ruling: P1 uses bounded immutable complete structural snapshots as its first repository-view implementation. P4 can optimize immutable bases/worktree overlays. This avoids partial mutable legacy graphs being mistaken for a coherent view, at the cost of additional snapshot storage.

Ruling: New deterministic snapshots use namespaced MemexView/File/Symbol labels and do not claim that existing v0.9 retrieval consumes v1 views. Projection to live task context belongs to P2/P3. Phase 1 remains opt-in.

## W03: atomic structural snapshots

Create `memex/runtime/indexing.py` with `extract_structure(path, bytes)`, qualified symbols, callsites, lexical imports and explicit complete/parse_error/unsupported coverage. Empty successful parsing is distinguishable from failure.

Create `memex/runtime/graph.py` with `StructuralGraphStore(driver)`, `publish(view, contributions) -> bool`, `completed(view_id)`, `coverage_complete(view_id)`, `published_view(repo_id, worktree_id)`. Publication includes all contributions and the completion record atomically. Older generations cannot replace a newer worktree pointer. Empty contributions remove current structure without erasing historical views. Repeated publication is idempotent. Graphiti's actual session API is used; no mocked transaction counts as evidence.

- [x] Write and run `tests/test_live_graph.py`: missing implementation is RED.
- [x] Implement the smallest deterministic snapshot transaction.
- [x] Run native Neo4j tests for body changes, removed last edge/file, replay, rollback and worktree isolation.

## W04: durable journal and coordinator

Create `memex/runtime/journal.py` with `ChangeJournal(path)`, `observe(registration, capture)`, `pending(worktree_id)`, `current(worktree_id)`, `acknowledge(view_id)`. Store metadata only; no source transcript or model outputs. Generation progress never advances before graph publication and never regresses. Reopen recovers pending work.

Create `memex/runtime/coordinator.py` with `RepositoryIndexer(registration, store, journal=None).refresh()`. Recapture disk/HEAD, observe a durable view, publish deterministic structure, then acknowledge. Recheck capture before reporting current coverage; retry a bounded changing view. On restart, a completed graph record is replay-safe even if local acknowledgement failed. Recapturing current state handles missed watcher events and checkout changes without inventing obsolete uncommitted snapshots.

Durable per-commit spool entries survive until the router has successfully processed their handler. Legacy sidecar handling stays compatible. The event router's queue completion follows handler completion. Add opt-in daemon composition for the structural coordinator plus periodic recovery refresh.

- [x] Write/run real Git/SQLite tests and recovery/queue tests before implementation.
- [x] Implement journal, coordinator, durable spool and routing acknowledgements.
- [x] Test graph-success/local-ack crash recovery, rapid commits, missed events, queue.join and fresh-source recapture.

## W05: repository identity and hooks

Create `memex/runtime/views.py`: `discover_repository(path)` resolves common Git directory and per-worktree Git directory, stable persisted local IDs, and runtime state path. `capture_sources(registration)` captures permitted Git source paths, HEAD and hashes with a stable-read check and explicit errors. Reject escaped source paths and exclude credentials/runtime state.

Hook installation obtains Git's configured hook directory, preserves existing scripts as executable backups, installs an idempotent wrapper under an OS-released cross-process lock and discovers the invoking worktree at runtime. Test actual temporary Git repositories, linked `.git` files, configured relative hooksPath, detached/unborn HEAD and preserved existing hooks.

- [x] Write/run `tests/test_live_repository.py` and worktree hook tests: missing behavior is RED.
- [x] Implement discovery/capture and hook composition.
- [x] Run temporary-repository acceptance tests plus native graph/coordinator end-to-end tests.

## Phase exit

- [x] W01–W05 requirements mapped to executed tests and accepted commits.
- [x] Selected compatibility suite and broadened no-provider tests pass.
- [x] Native graph transactions demonstrate atomic rollback/completion and isolated publication.
- [x] Failure/unsupported coverage never becomes a fresh result.
- [x] Fresh whole-branch review completed; important findings fixed and tested.
- [x] Update planning status and verification report; prepare the completed branch for the authorized commit/push.

## Final review amendments

All seven deterministic reproductions failed first and passed after fixes: hook installation serialization, actual commit-handler extraction/partial-write failure outcomes, coalesced active retries, unsupported-file manifest coverage, Python header/module/class call capture, and cancellation-safe commit queue accounting. Unknown permitted Git files are captured and explicitly unsupported; they cannot produce repository-wide current status. Syntactic calls remain unresolved. Detailed evidence and remaining boundaries are in 14.
