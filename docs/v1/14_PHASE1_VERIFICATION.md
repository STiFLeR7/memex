# Phase 1 verification and execution record

Date: 4 October 2026. Status: W01-W05 implemented and verified on `codex/v1-phase1`. P2-P5 remain planned; this is not a v1.0 release or native-agent action-delivery claim.

Research inspected `d7614cd`; the managed execution worktree started from the already-existing `da00ed6` site commit. Concurrent edits in the original checkout were preserved. Implementation commits: `786ee1d` (view contract and approved planning baseline), `72b4cb8` (body-change refresh), `2226de6` (remaining runtime, integration and review fixes). Two additional native acceptance cases and this execution record follow in the phase-evidence commit.

## Delivered behavior

| Work | Implementation | Executed evidence |
| --- | --- | --- |
| W01 | Immutable RepositoryView identity; index progress excluded from identity | `test_repository_view.py`: progress, worktree distinction, unborn HEAD and invalid inputs |
| W02 | Changed file bodies mark surviving symbols modified; call refresh independent of symbol deltas | `test_extractor.py`, `test_body_change_refresh.py`, existing handler tests |
| W03 | Namespaced immutable Neo4j structural snapshots and unique identities; atomic contribution/completion/pointer transaction | `test_live_graph.py`: last call/import/file removal, replay, rollback, concurrent initial publication, worktree isolation, parse failure and unresolved attribute calls |
| W04 | SQLite observations and generations; graph-before-local acknowledgement; stable-source recapture; periodic catch-up; durable per-event spool; active retry coalescing; completion-aware queue | `test_live_recovery.py`, `test_live_routing.py`, `test_live_hooks.py`, final-review regressions |
| W05 | Git common-dir/worktree-dir identity; persisted local IDs; linked worktrees; configured hook location; preserved original hooks and OS-released installation lock | Real Git tests in `test_live_repository.py`/`test_live_hooks.py`; Windows case aliases, detached/unborn HEAD, hooksPath with spaces |

The graph publication transaction exposes either the prior complete publication or the new publication. A transaction failure leaves no candidate completion/pointer; journal progress does not advance. A crash after graph commit but before SQLite acknowledgement replays the same view safely. Obsolete uncommitted snapshots are superseded after recapturing and publishing current content, rather than inventing lost historical file bodies.

Publication completion and coverage are separate. `MemexView.completed` means the transaction finished, including explicit failure/unsupported coverage records. `coverage_complete` and `IndexResult.is_current` remain false for partial coverage. A concurrent journal observation or detected source drift also prevents a current result.

## Final executed checks

| Check | Observed result |
| --- | --- |
| Phase 1 suite, including native graph and actual daemon composition | **80 passed**, no skips; one Graphiti/Pydantic deprecation warning |
| Broader suite selected with `-m "not integration"` | **655 passed**, one existing Windows safety-test skip, 27 integration-marked tests deselected; 12 dependency/test-mock warnings |
| Independent-review reproduction suite | **7 failed before fixes; 7 passed after fixes** |
| Ruff correctness rules on changed application/test files | Passed |
| Clean test environment `pip check` | No broken requirements |
| Git whitespace/diff check | Passed |
| Wheel build, new-module inclusion and Twine metadata check | Passed; all eight new context/runtime modules packaged |
| Numbered planning/evidence documents | 15 validated: balanced fences, dedented Python examples and relative document links |

The counts overlap between the phase and compatibility suites; they are separate runs, not a sum of unique tests. Fifteen P1 integration-marked cases ran in the phase suite. The compatibility selector does not claim that all other integration/provider tests ran. The skipped existing safety test is `test_path_traversal_symlink_escape_is_rejected`, explicitly disabled on Windows; the new P1 source-escape test ran and passed.

The daemon test executes real watchdog filesystem observation, Git commits/hooks, commit polling, routing, SQLite generations and native Neo4j publication. Only legacy semantic/model/decay boundaries are isolated. It proves structural publication after edits and rapid commits, legacy handler invocation, durable-event acknowledgement and shutdown cleanup. It does not prove LLM decision quality or agent reconsideration.

### Reproduction

Create a clean Python 3.12 environment and install the checkout with `pip install -e . ruff`, plus the declared dev tools (`hypothesis`, `build`, `twine`). The broader legacy cluster tests additionally used `networkx==3.7` and `graspologic-native==1.3.1` with its SciPy dependency. No global Python installation was changed by the clean environment.

Tested versions: Graphiti 0.30.2, Neo4j Python driver 6.3.1, tree-sitter-language-pack 1.20.0, pytest 9.1.1, pytest-asyncio 1.4.0, FastAPI 0.142.2, Pydantic 2.13.5. Earlier narrow/native checks also passed with Graphiti 0.29.0 and Neo4j driver 6.2.0. This is evidence for those tested versions, not every version permitted by dependency ranges.

Native server: Neo4j Community 5.26.30 Windows ZIP with Java 21.0.12.1, temporary data/config under the execution worktree's `output/phase1/native`. Bolt listened only on `127.0.0.1:17687`; HTTP/HTTPS and usage reporting were disabled. The fixture used this isolated test server without authentication. Docker was never started or used. The runtime instance is stopped at handoff; tests require starting an isolated server again.

For the native fixture, set `MEMEX_PHASE1_NEO4J_URI=bolt://127.0.0.1:17687`. Legacy configuration checks also used that `NEO4J_URI`, `NEO4J_USER=neo4j`, a fabricated test password and `GEMINI_API_KEY=test-not-used`. No real provider credentials or paid model calls were used. Test registries are redirected by the existing fixture to temporary files.

From the execution checkout, using the clean environment's Python:

```powershell
python -m pytest tests/test_repository_view.py tests/test_extractor.py tests/test_body_change_refresh.py tests/test_handlers.py tests/test_context_packet.py tests/test_live_repository.py tests/test_live_graph.py tests/test_live_recovery.py tests/test_live_daemon.py tests/test_live_hooks.py tests/test_live_routing.py tests/test_git_hook.py tests/test_commit_poller.py tests/test_event_router.py tests/test_phase1_review_regressions.py -q
python -m pytest tests -m "not integration" -q
python -m pip check
git diff --check
```

Phase logs were retained locally in `output/phase1/final-phase1-suite.log`, `final-compatibility-suite.log`, `red-final-review.log` and `green-final-review.log`. Binaries, temporary databases, environments and raw logs are not committed. The committed tests and this record are the portable evidence.

## Independent review and one fix pass

A fresh reviewer inspected the whole owned change against `da00ed6`, without editing source, Docker or paid-provider calls. Five findings were Important. The queue-cancellation finding was labelled Minor by the reviewer and regraded Important because it could leave `queue.join()` indefinitely blocked. All entered the same test-first fix pass; none remain deferred.

| Finding | Reproduction / verified change |
| --- | --- |
| Concurrent installers could replace the original backup with a recursive wrapper | `test_concurrent_hook_installation_preserves_original`: serialize inspection/preservation/replacement with an OS file lock |
| Actual commit handler swallowed extraction or partial-write failures, causing spool deletion | Two `test_actual_commit_handler_failure_remains_durable` cases: explicit false processing outcomes retain the event |
| Time-based retries launched another handler while the first was active | `test_slow_commit_retry_is_coalesced_but_failure_can_retry`: coalesce by durable event path; failed completed attempts remain retryable |
| Unknown source languages were omitted and an empty snapshot could appear complete | `test_unknown_sources_and_manifests_participate_in_capture`: all permitted Git-listed files participate, with explicit unsupported coverage; native readiness case also verifies false current status |
| Python headers/module/class calls were omitted despite complete coverage | `test_python_headers_and_nonfunction_calls_are_captured`: retain unresolved calls in enclosing/module/class-body scopes, including definition headers |
| Cancellation before a commit coroutine started leaked queue accounting | `test_prestart_commit_cancellation_does_not_leak_queue_accounting`: task completion callbacks account for every consumed/coalesced commit item |

Additional negative checks reproduce concurrent observation/readiness mismatch and fabricated attribute binding. Both failed before correction. Real concurrent graph publication originally created duplicate worktree nodes; namespaced uniqueness constraints eliminate that failure. The isolated test graph was cleared after that RED demonstration before testing the constrained schema.

## Decisions and rulings

| Decision made during execution | Reason / cost if wrong |
| --- | --- |
| Fabricated test configuration instead of copying credentials | Isolated baseline lacked required config; 44 selected baseline tests passed after setup. Production credential/provider configuration is not validated by these tests. |
| Named isolated branch from the managed detached worktree | Preserve simultaneous contributors' original checkout. Work remains on a branch requiring later integration; no merge is implied. |
| Complete W03-W05 through a supplemental source-verified plan | User authorized all P1, while 09 specified only W01/W02. Additional runtime interfaces required new acceptance evidence; 13 records them. |
| Immutable full structural snapshots first | Avoid mixed mutable graph generations. Cost: snapshot storage and full scans; P4 base/overlay optimization and later retention/resource gates remain necessary. |
| Additive structural namespace, opt-in watcher composition | Preserve existing Neo4j/Graphiti and v0.9 contracts. Cost: P2/P3 must explicitly project v1 evidence; old retrieval is not automatically upgraded. |
| Retain syntactic calls as unresolved, without guessed CALLS bindings | A unique bare name can still denote an imported attribute. Cost: precise dependency/name resolution remains separate work. |
| Preserve hooks using a serialized wrapper and executable backup | Appending after an unconditional exit cannot compose behavior. Original bytes, argument forwarding and exit status are preserved; a hook inspecting its own `$0` sees the backup path. |
| Later task/action delivery and retrieval integration left planned | Reviewer declined these later-phase claims; P1 makes none. Cost: no before-action correction or efficacy proof yet. |
| P4 overlays and arbitrary-writer fencing left planned | Reviewer declined these future mechanisms; P1 tests identity/publication isolation only. Cost: no shared-checkout write protection claim. |
| Python binding left unresolved | Reviewer accepted this boundary. Cost: call-name evidence cannot be treated as proven support. |
| Legacy semantic quality left under its existing contract | Review fixes processing failure/retry behavior, not model quality. Delivery is at least once; retries after failure/crash may repeat semantic work. Cost: no exactly-once model-execution claim. |
| Inherited site work and independent environment failures excluded from implementation ownership | Site commit was already present; clean dependencies and valid Git fixtures resolved test-environment failures. Cost: this phase is not a website audit. |

## Operating boundary and next phase

Enable the internal structural runtime with `MEMEX_LIVE_CONTEXT=1` before the existing `memex watch` command. Startup and a five-second recovery rescan recapture registered worktrees; file/commit notifications trigger earlier refresh. Pause markers are respected. Default operation remains the existing watcher path.

Local IDs/journal reside under Git metadata; linked worktrees share the repository journal but retain separate worktree IDs. Commit spool entries reside in each worktree's `.memex/pending_commits/`. Router success removes only that worktree's validated spool path after handlers finish. Failed attempts are retried; source facts are recaptured rather than accepted from event payloads.

Capture follows permitted tracked/untracked Git files, excluding ignored/runtime/credential paths. Limits are 5 MB per file and 10,000 files; a capture failure returns no current result. Non-Python files, including unrecognized manifests/assets, are explicit unsupported coverage. Repository-wide current status therefore stays false in mixed/unsupported repositories. P2 must use checked evidence scope rather than claiming whole-repository readiness from a subset. Two matching captures detect drift but are not an atomic filesystem lock against arbitrary writers.

P1 emits no public action certificate, task stream or host delivery receipt. Legacy semantic retrieval remains separate. P2 now needs its detailed execution plan for immutable support/verification records, scoped working sets and replayable corrections (W06-W08). Native Claude/Codex action ordering, shared-write coordination, independent pilots and resource/efficacy gates remain P3-P5.
