# Current architecture and prerequisite gaps

Status: source-inspection baseline, not runtime reproduction. Inspected commit: `d7614cdaa70947fdf77d43e5235616ff73e9e7fc`. Recheck before implementation because other contributors may change these paths.

## What exists

Filesystem events flow through debounce to symbol extraction and graph writes. Git hooks emit a single commit sidecar consumed by a poller; commit handling synthesizes decisions. Startup and lockfile events build dependency/import structure. Selection and reranking produce bounded provenance-aware packets. Hermes prefetch and MCP use the shared context selector.

Reusable foundations: deterministic structural writes before best-effort semantic episodes; Python call extraction; dependency and impact queries; confidence calculation, human validation and explicit supersession; bounded packets; task/outcome records; deliberate ingestion policy. Preserve these components and their existing tests.

## Gaps and execution implications

| Gap | Existing source evidence | Required response |
| --- | --- | --- |
| Body-only changes can leave call understanding unchanged | [treesitter.py](D:/memex/memex/extractor/treesitter.py:174) compares signatures; [handlers.py](D:/memex/memex/watcher/handlers.py:243) returns on empty symbol delta | Refresh structure from complete source changes, independent of signature deltas |
| Disappeared structural relationships can remain active | [writer.py](D:/memex/memex/graph/writer.py:168) merges CALLS; import writes likewise reinforce existing edges | Replace/reconcile a file's contribution atomically, including an empty result |
| Normal import-statement edits do not consistently refresh imports | [handlers.py](D:/memex/memex/watcher/handlers.py) and [lockfile.py](D:/memex/memex/extractor/lockfile.py) | Refresh imports per changed source file, keep package dependency updates separate |
| Repository scope is not revision isolation | [packet.py](D:/memex/memex/context/packet.py:54); structural identity uses name/file/repository | Add worktree view keys and coherent indexed-generation reads |
| Descriptive provenance is not machine-checkable support | [packet.py](D:/memex/memex/context/packet.py:44), [selection.py](D:/memex/memex/context/selection.py) | Add exact support references and view-specific verification records |
| Missing freshness flags default to current | [selection.py](D:/memex/memex/context/selection.py:164) | Legacy confidence/freshness cannot certify v1 evidence validity |
| Pending commit events can be overwritten/lost | [git_hook.py](D:/memex/memex/watcher/git_hook.py), [commit_poller.py](D:/memex/memex/watcher/commit_poller.py) | Durable event journal, retry and history catch-up |
| Queue completion does not mean indexing completion | [event_router.py](D:/memex/memex/watcher/event_router.py) calls queue completion before asynchronous handlers finish | Publish index completion only after committed structural work |
| Worktree hook installation assumes `.git` is a directory | [git_hook.py](D:/memex/memex/watcher/git_hook.py:12) uses `repo_root/.git/hooks` and overwrites hook files | Discover Git paths, honor `core.hooksPath`, compose with existing hooks |
| Some invalidation/corroboration writes omit repository filtering | [writer.py](D:/memex/memex/graph/writer.py:147), [handlers.py](D:/memex/memex/watcher/handlers.py:88) | Scope every read/write by stable repository and applicable view |
| Host path ends at pretask context delivery | [hermes_provider.py](D:/memex/memex/integrations/hermes_provider.py:276) leaves sync_turn intentionally empty | Add separate action adapters without turning sync_turn into transcript storage |
| Task schema is not a live dependency tracker | [task_outcome.py](D:/memex/memex/context/task_outcome.py:1) | Add task working sets as control state, reuse outcome vocabulary |

These are implementation gaps, not reasons to discard the existing graph. P1 fixes ingestion correctness before a subscription layer amplifies its output.

## Evaluation baseline

[BENCHMARK.md](D:/memex/BENCHMARK.md) reports eight valid paired cases, no treatment-only failures and context returned in all treatment runs. Both arms completed every task. This proves the tested read-only Hermes integration and non-regression, not causal lift, large-scale latency or concurrent shared-checkout correctness.

Keep the eight fixtures as compatibility smoke evidence. They are not the efficacy gate for Live Context. The new suite must mutate evidence after delivery, introduce real action races and include unaffected actions that should stay quiet.

## Verification boundaries

This audit did not run Neo4j, model providers or native agent experiments. Line numbers identify the inspected version and can move. The first-wave executor should reproduce narrow failures using local tests, then update this document with implemented fixes and commit links. Existing older architecture documents are useful history, but their proposed/accepted statuses do not override shipped source or the approved v1 direction.
