"""Actual Git hook execution and durable per-commit delivery."""
import asyncio
import json
from pathlib import Path

import pytest

from memex.watcher.git_hook import install_hooks
from memex.watcher.commit_poller import CommitPoller
from tests.test_live_repository import repository, git  # noqa: F401


def configure_python(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))


def test_configured_hooks_preserve_existing_and_are_idempotent(repository, monkeypatch):
    configure_python(monkeypatch)
    hooks = repository / "custom hooks"
    hooks.mkdir()
    git(repository, "config", "core.hooksPath", "custom hooks")
    existing = hooks / "post-commit"
    original = b'#!/bin/sh\nprintf "original\\n" >> hook-proof.txt\nexit 0\n'
    existing.write_bytes(original)
    existing.chmod(0o755)
    install_hooks(str(repository))
    installed = existing.read_bytes()
    install_hooks(str(repository))
    assert existing.read_bytes() == installed
    assert (hooks / "post-commit.memex-original").read_bytes() == original
    git(repository, "commit", "--allow-empty", "-qm", "hook test")
    assert (repository / "hook-proof.txt").read_text().strip() == "original"
    assert len(list((repository / ".memex/pending_commits").glob("*.json"))) == 1


def test_shared_hooks_emit_to_invoking_linked_worktree(repository, tmp_path, monkeypatch):
    configure_python(monkeypatch)
    linked = tmp_path / "linked"
    git(repository, "worktree", "add", "-qb", "linked", str(linked))
    install_hooks(str(linked))
    for root in [repository, linked]:
        git(root, "commit", "--allow-empty", "-qm", "own commit")
        data = json.loads(next((root / ".memex/pending_commits").glob("*.json")).read_text())
        assert data["sha"] == git(root, "rev-parse", "HEAD")


@pytest.mark.asyncio
async def test_rapid_commits_survive_polling_and_restart(repository, monkeypatch):
    configure_python(monkeypatch)
    install_hooks(str(repository))
    shas = []
    for number in range(3):
        git(repository, "commit", "--allow-empty", "-qm", f"rapid {number}")
        shas.append(git(repository, "rev-parse", "HEAD"))
    spool = repository / ".memex/pending_commits"
    assert len(list(spool.glob("*.json"))) == 3
    queue = asyncio.Queue()
    poller = CommitPoller(str(repository), queue)
    task = asyncio.create_task(poller.run())
    try:
        events = [await asyncio.wait_for(queue.get(), 3) for _ in shas]
        assert {event.sha for event in events} == set(shas)
        assert all(Path(event.event_path).exists() for event in events)
    finally:
        task.cancel()
        await task
    # No handler acknowledged these events: a new poller must replay them.
    queue = asyncio.Queue()
    task = asyncio.create_task(CommitPoller(str(repository), queue).run())
    try:
        replay = [await asyncio.wait_for(queue.get(), 3) for _ in shas]
        assert {event.sha for event in replay} == set(shas)
    finally:
        task.cancel()
        await task
