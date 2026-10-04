# Repository-view contracts and body-change foundation implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task. Native execution is the baseline; delegation requires separate maintainer authorization. Steps use checkbox syntax for tracking.

**Goal:** Deliver W01 and W02: explicit repository-view identity/progress invariants and a regression fix ensuring body-only changes reach structural extraction.

**Architecture:** Add a pure immutable view contract without starting a runtime service. Keep the existing symbol delta API and make changed-file detection conservative; remove signature-delta gating from call refresh. This is the first P1 wave, not the full evidence-maintenance runtime.

**Tech stack:** Existing Python >=3.11, pytest/pytest-asyncio, Pydantic-based surrounding contracts, tree-sitter and current watcher modules. The new pure contract below uses standard-library dataclasses/hashlib/json.

**Spec:** [01_VISION_AND_SCOPE.md](01_VISION_AND_SCOPE.md), [04_CONTEXT_CONTRACTS.md](04_CONTEXT_CONTRACTS.md), W01/W02 in [08_ROADMAP.md](08_ROADMAP.md).

## Global constraints

- No Docker for now. No provider credentials, service startup or paid model calls in these tasks.
- Preserve the current Neo4j/Graphiti knowledge substrate and Hermes/MCP compatibility.
- Do not retain transcripts or promote inferred claims into approved constraints.
- Never label partial indexing complete; adding this model does not itself create a freshness guarantee.
- Preserve concurrent contributors' edits. Re-read target files; no reset, overwrite or broad staging.
- Planned production/test paths below are repository-relative ownership names. Actual commands run from `D:/memex` or the explicitly selected checkout.

## Review focus

1. Index-progress change must not change content-view identity: pinned by task 1 test `test_index_progress_does_not_change_view_identity`.
2. Unborn repositories must remain representable: pinned by task 1 test `test_unborn_view`.
3. Same commit and manifest in two worktrees must not collapse identity: pinned by task 1 test `test_worktrees_have_distinct_view_identity`.
4. A function body change with unchanged signature must reach the modified/structural path: pinned by task 2 test `test_body_only_change_marks_symbol_modified`.
5. Call refresh must not depend on a nonempty symbol delta: pinned by task 2 test `test_calls_refresh_without_symbol_delta`.

## Task 1: immutable repository-view contract

**Files:** Create `memex/context/revision.py`; create `tests/test_repository_view.py`. Keep `memex/context/packet.py` unchanged in this task so existing callers remain compatible.

**Interfaces:** Consumes normalized repository/worktree IDs and an externally captured manifest digest. Produces `RepositoryView` with the exact fields and `view_id` property shown below. Repository discovery and manifest capture are W05/W04 responsibilities, not hidden behavior of this model.

- [x] Step 1: create the failing tests in `tests/test_repository_view.py`.

```python
from dataclasses import replace

import pytest

from memex.context.revision import RepositoryView


def make_view(**updates):
    fields = dict(
        repo_id="repo-1", worktree_id="worktree-1", head_commit="a" * 40,
        content_generation=2, indexed_generation=1,
        manifest_hash="sha256:" + "b" * 64,
    )
    fields.update(updates)
    return RepositoryView(**fields)


def test_index_progress_does_not_change_view_identity():
    before = make_view()
    after = replace(before, indexed_generation=2)
    assert before.view_id == after.view_id


def test_worktrees_have_distinct_view_identity():
    assert make_view().view_id != make_view(worktree_id="worktree-2").view_id


def test_changed_content_generation_has_new_identity():
    assert make_view().view_id != make_view(content_generation=3).view_id


def test_unborn_view():
    assert make_view(head_commit=None, content_generation=0,
                     indexed_generation=0).head_commit is None


@pytest.mark.parametrize("updates", [
    {"repo_id": ""}, {"worktree_id": ""},
    {"content_generation": -1}, {"indexed_generation": -1},
    {"indexed_generation": 3}, {"manifest_hash": "not-a-digest"},
])
def test_invalid_view_rejected(updates):
    with pytest.raises(ValueError):
        make_view(**updates)
```

- [x] Step 2: run `python -m pytest tests/test_repository_view.py -q`. Before creation of the module, expect collection failure for the missing module. Record the observed failure; do not claim it was run from this planning document.
- [x] Step 3: create the pure contract in `memex/context/revision.py`.

```python
from dataclasses import dataclass
import hashlib
import json
import re


@dataclass(frozen=True)
class RepositoryView:
    repo_id: str
    worktree_id: str
    head_commit: str | None
    content_generation: int
    indexed_generation: int
    manifest_hash: str

    def __post_init__(self) -> None:
        if not self.repo_id.strip() or not self.worktree_id.strip():
            raise ValueError("repository and worktree identities are required")
        if not (0 <= self.indexed_generation <= self.content_generation):
            raise ValueError("invalid indexing progress")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.manifest_hash):
            raise ValueError("manifest_hash requires a SHA-256 digest")

    @property
    def view_id(self) -> str:
        identity = [self.repo_id, self.worktree_id, self.head_commit,
                    self.content_generation, self.manifest_hash]
        encoded = json.dumps(identity, separators=(",", ":")).encode("utf-8")
        return "view:" + hashlib.sha256(encoded).hexdigest()
```

The later serialized v1 request models validate input types, access, temporal fields and capabilities. This internal model intentionally does not discover a checkout, certify a hash or expose an HTTP API.

- [x] Step 4: run `python -m pytest tests/test_repository_view.py tests/test_context_packet.py -q`; verify view invariants and unchanged packet compatibility.
- [x] Step 5: review/stage only the two owned files and commit the accepted change, for example `git add memex/context/revision.py tests/test_repository_view.py`, then `git commit -m "feat(context): define revision-scoped repository views"`. Do not stage unrelated work.

## Task 2: body-change detection and independent call refresh

**Files:** Modify `memex/extractor/treesitter.py` and `memex/watcher/handlers.py`; extend `tests/test_extractor.py`; create `tests/test_body_change_refresh.py`.

**Interfaces:** Preserve `extract_symbol_delta(file_path, old_content, new_content, language=None) -> SymbolDelta`, `handle_file_change(event) -> None`, `extract_calls(...)` and existing write APIs. No packet, graph identity or provider schema change in this task.

- [x] Step 1: append these tests to `tests/test_extractor.py`.

```python
@pytest.mark.asyncio
async def test_body_only_change_marks_symbol_modified():
    delta = await extract_symbol_delta(
        "client.py", "def send():\n    return old_api()\n",
        "def send():\n    return new_api()\n",
    )
    assert [symbol.name for symbol in delta.modified] == ["send"]
    assert not delta.added and not delta.removed


@pytest.mark.asyncio
async def test_identical_content_has_no_symbol_modification():
    source = "def send():\n    return api()\n"
    delta = await extract_symbol_delta("client.py", source, source)
    assert not delta.added and not delta.removed and not delta.modified
```

Create the independent handler test in `tests/test_body_change_refresh.py`:

```python
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from memex.extractor.treesitter import SymbolDelta
from memex.watcher.events import FileChangeEvent
from memex.watcher.handlers import handle_file_change


@pytest.mark.asyncio
async def test_calls_refresh_without_symbol_delta(tmp_path):
    path = tmp_path / "client.py"
    path.write_text("def send():\n    return new_api()\n", encoding="utf-8")
    event = FileChangeEvent(str(path), str(tmp_path), "modified", datetime.now(UTC))
    with (
        patch("memex.watcher.handlers.run_git_command", new=AsyncMock(
            return_value="def send():\n    return old_api()\n")),
        patch("memex.watcher.handlers.extract_symbol_delta", new=AsyncMock(
            return_value=SymbolDelta())),
        patch("memex.watcher.handlers.write_symbol_delta", new=AsyncMock(
            return_value={"episodes_skipped": 0})),
        patch("memex.watcher.handlers.write_call_edges", new=AsyncMock(
            return_value=1)) as write_calls,
        patch("memex.watcher.handlers.canonical_repo_path", return_value=str(tmp_path)),
        patch("memex.watcher.handlers.health"),
        patch("memex.watcher.handlers.notify_local_server"),
    ):
        await handle_file_change(event)
    write_calls.assert_awaited_once()
    assert {(edge.caller, edge.callee) for edge in write_calls.call_args.args[0]} == {
        ("send", "new_api")
    }
```

This deliberately supplies an empty symbol delta to test independence of call refresh. It does not assert that mocked graph writes prove real transaction reconciliation.

- [x] Step 2: run `python -m pytest tests/test_extractor.py tests/test_body_change_refresh.py -q`. Expect the new body-change and handler-gating tests to fail in the inspected baseline; record actual failures.
- [x] Step 3: make conservative changed-file detection in the existing delta loop:

```python
            if old_sym.signature != new_sym.signature or old_content != new_content:
                delta.modified.append(new_sym)
```

This first fix marks all surviving symbols in a changed file modified. It is conservative, simple and preserves the API. It does not claim exact per-symbol semantic dependency attribution. A later span/fingerprint optimization must prove equal coverage before replacing it.

Remove the handler's `if not delta.added and not delta.removed and not delta.modified: return` gate. Keep symbol-write behavior and let existing call extraction run independently. Do not introduce a new early return based on comparison with HEAD: reverting uncommitted content to HEAD can still require graph refresh.

```python
        delta = await extract_symbol_delta(rel_path, old_content, new_content)
        summary = await write_symbol_delta(delta, source_commit=None, repo_root=repo_canon)
        # Existing call-extraction block runs even when delta is empty.
```

- [x] Step 4: run `python -m pytest tests/test_extractor.py tests/test_body_change_refresh.py tests/test_handlers.py -q`. If a selected file unexpectedly contacts a service, stop that check, isolate the network boundary and report it; do not start Docker/provider services to satisfy a local regression test.
- [x] Step 5: review/stage only the four owned files and commit the accepted change, for example `git commit -m "fix(watcher): refresh calls after body-only changes"` after explicitly staging those paths.

## What this wave does not certify

Zero-call/import removals, invalid parse coverage, atomic graph transactions, last-indexed-content snapshots, journal recovery, view-scoped graph identities and native action delivery are W03–W19. This first wave fixes a concrete gate and introduces identity invariants; it does **not** complete P1 or establish the v1 product claim.

## Final wave verification and handoff

- [x] Record exact commands, observed results and accepted commit references.
- [x] Confirm no packet/provider behavior or unrelated source was changed.
- [x] Inspect graph driver's actual transaction API for S03 and prepare the W03–W05 detailed plan using the acceptance cases in the roadmap.
- [x] Update the index status with W01/W02 evidence while leaving P1 incomplete until W03–W05 pass.

The executor does not need to invent later APIs or begin paid benchmark trials to complete this wave.

## Executed results

W01: `786ee1d`. W02: `72b4cb8`. Initial combined wave: 33 passed. Remaining P1 source: `2226de6`. Full Phase 1: 80 passed; compatibility: 655 passed, one existing Windows skip. See [14_PHASE1_VERIFICATION.md](14_PHASE1_VERIFICATION.md) for commands, final review and boundaries.
