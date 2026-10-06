"""W14: guarded writes across real processes, with deterministic interleavings.

Every race here is ordered by file barriers in `phase4_guard_worker.py`, and lease
expiry by a shared file clock, so each test decides the interleaving it proves
rather than hoping a sleep produces it. Real Git and SQLite; no graph backend.
"""
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import time

import pytest

from memex.runtime.guard import (
    WORKTREE, LeaseBusy, LeaseLost, Mutation, StaleWrite, Unsupported, WriteGuard, classify_git, sha256,
)
from memex.runtime.views import discover_repository

WORKER = pathlib.Path(__file__).with_name("phase4_guard_worker.py")


class FileClock:
    def __init__(self, path):
        self.path = path
        self.set(100.0)

    def set(self, value: float):
        self.path.write_text(str(value))

    def __call__(self):
        return float(self.path.read_text())


@pytest.fixture
def world(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    # The guard's own commits use the repository's identity; CI runners have no global one.
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    for name, text in (("api.py", "def send(payload):\n    return payload\n"), ("other.py", "x = 1\n"),
                       ("a.txt", "a-old\n"), ("b.txt", "b-old\n")):
        (repo / name).write_text(text, newline="\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"],
                   check=True)
    state = tmp_path / "state"
    state.mkdir()
    clock = FileClock(state / "clock")
    registration = discover_repository(repo)
    return repo, state, clock, registration


def spawn(world, scenario, label, *args):
    repo, state, _, _ = world
    return subprocess.Popen([sys.executable, str(WORKER), scenario, str(repo), str(state), label, *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def wait_for(state, name, timeout=60):
    deadline = time.time() + timeout
    while not (state / name).exists():
        assert time.time() < deadline, f"barrier {name} never reached"
        time.sleep(0.01)


def release(state, label, point):
    (state / f"go-{label}.{point}").write_text("go")


def outcome(process):
    out, err = process.communicate(timeout=60)
    assert process.returncode == 0, err
    return json.loads(out.strip().splitlines()[-1])


def h(repo, name):
    return sha256((repo / name).read_bytes())


# --------------------------------------------------------------------------- #

def test_ordered_race_on_one_target_commits_once_and_the_loser_replans(world):
    repo, state, _, _ = world
    h0 = h(repo, "api.py")
    a = spawn(world, "write", "A", "api.py", h0, "A-version\n")
    b = spawn(world, "write", "B", "api.py", h0, "B-version\n")
    wait_for(state, "A.ready")
    wait_for(state, "B.ready")  # both writers read the same h0 and are waiting

    release(state, "A", "ready")
    wait_for(state, "A.acquired")
    release(state, "A", "acquired")
    assert outcome(a)["outcome"] == "committed"

    release(state, "B", "ready")
    wait_for(state, "B.acquired")
    release(state, "B", "acquired")
    lost = outcome(b)
    assert lost["outcome"] == StaleWrite.reason, "a writer that read stale bytes must replan, not overwrite"
    assert (repo / "api.py").read_text() == "A-version\n"


def test_simultaneous_race_lets_exactly_one_current_write_through(world):
    repo, state, _, _ = world
    h0 = h(repo, "api.py")
    writers = {label: spawn(world, "write", label, "api.py", h0, f"{label}-version\n") for label in "AB"}
    for label in "AB":
        wait_for(state, f"{label}.ready")
    for label in "AB":  # released together; the guard alone decides
        release(state, label, "ready")
        release(state, label, "acquired")
    results = {label: outcome(p)["outcome"] for label, p in writers.items()}
    assert sorted(results.values()).count("committed") == 1, results
    loser = next(r for r in results.values() if r != "committed")
    assert loser in (StaleWrite.reason, LeaseBusy.reason)
    winner = next(label for label, r in results.items() if r == "committed")
    assert (repo / "api.py").read_text() == f"{winner}-version\n"


def test_a_paused_holder_cannot_write_after_it_is_replaced(world):
    """The fencing token is enforced by the write path, not merely stored."""
    repo, state, clock, registration = world
    h0 = h(repo, "api.py")
    old = spawn(world, "write", "old", "api.py", h0, "old-holder\n")
    release(state, "old", "ready")
    wait_for(state, "old.acquired")  # holds generation 1, then pauses before commit

    clock.set(200.0)  # its lease (ttl 10) is now long expired
    new = spawn(world, "write", "new", "api.py", h0, "new-holder\n")
    release(state, "new", "ready")
    wait_for(state, "new.acquired")
    release(state, "new", "acquired")
    assert outcome(new)["outcome"] == "committed"

    release(state, "old", "acquired")  # the paused holder wakes and tries to commit
    rejected = outcome(old)
    assert rejected["outcome"] == LeaseLost.reason, rejected
    assert (repo / "api.py").read_text() == "new-holder\n"
    db = sqlite3.connect(registration.runtime_path)
    generation = db.execute("SELECT generation FROM guard_generations WHERE target='api.py'").fetchone()[0]
    db.close()
    assert generation == 2


def test_a_crashed_holder_blocks_only_until_its_lease_expires(world):
    repo, state, clock, registration = world
    crashed = spawn(world, "crash-holding", "dead", "api.py")
    wait_for(state, "dead.acquired")
    release(state, "dead", "acquired")
    crashed.wait(timeout=60)
    assert crashed.returncode == 17

    guard = WriteGuard(registration, clock=clock, ttl=10)
    with pytest.raises(LeaseBusy):
        guard.acquire(guard.holder("survivor"), ["api.py"])
    clock.set(300.0)
    guard.apply("survivor", [Mutation("write", "api.py", h(repo, "api.py"), b"recovered\n")])
    assert (repo / "api.py").read_text() == "recovered\n"


def test_a_crash_mid_replacement_is_rolled_forward_on_the_next_entry(world):
    repo, state, clock, registration = world
    crashed = spawn(world, "crash-mid-replace", "half", "a.txt", "b.txt")
    crashed.wait(timeout=60)
    assert crashed.returncode == 19
    assert (repo / "a.txt").read_text() == "first-new\n"
    assert (repo / "b.txt").read_text() == "b-old\n", "the crash really did leave a half-applied set"

    guard = WriteGuard(registration, clock=clock)  # entry runs recovery under the write lock
    assert (repo / "b.txt").read_text() == "second-new\n"
    assert any(r["outcome"] == "rolled_forward" for r in guard.results())
    assert not list(guard.intent_dir.glob("intent-*.json"))


def test_multi_target_acquisition_is_all_or_none_in_any_order(world):
    repo, state, clock, registration = world
    first = spawn(world, "acquire-set", "first", "b.txt", "a.txt")
    second = spawn(world, "acquire-set", "second", "a.txt", "b.txt")
    for label in ("first", "second"):
        wait_for(state, f"{label}.ready")
    for label in ("first", "second"):
        release(state, label, "ready")
    # Exactly one holds both; the other holds nothing. Neither waits on the other.
    deadline = time.time() + 30
    while not any((state / f"{x}.acquired").exists() for x in ("first", "second")):
        assert time.time() < deadline
        time.sleep(0.01)
    db = sqlite3.connect(registration.runtime_path)
    holders = {row[0].split(":")[0] for row in db.execute("SELECT holder FROM guard_leases")}
    db.close()
    assert len(holders) == 1
    for label in ("first", "second"):
        release(state, label, "acquired")
    results = [outcome(first)["outcome"], outcome(second)["outcome"]]
    assert sorted(results) in (["acquired", LeaseBusy.reason], ["acquired", "acquired"])

    guard = WriteGuard(registration, clock=clock)
    held = guard.acquire("x", ["a.txt"])
    with pytest.raises(LeaseBusy):
        guard.acquire("y", ["b.txt", "a.txt"])
    db = sqlite3.connect(registration.runtime_path)
    assert db.execute("SELECT count(*) FROM guard_leases WHERE target='b.txt'").fetchone()[0] == 0
    db.close()
    guard.release(held)


def test_an_external_writer_bypasses_the_guard_and_is_reported_not_prevented(world):
    repo, _, clock, registration = world
    guard = WriteGuard(registration, clock=clock)
    expected = h(repo, "api.py")
    (repo / "api.py").write_text("an editor saved this\n")  # no lease, no guard
    with pytest.raises(StaleWrite) as stale:
        guard.apply("agent", [Mutation("write", "api.py", expected, b"agent\n")])
    assert stale.value.observed == h(repo, "api.py")
    assert (repo / "api.py").read_text() == "an editor saved this\n"


def test_create_delete_rename_and_multi_file_sets(world):
    repo, _, clock, registration = world
    guard = WriteGuard(registration, clock=clock)
    guard.apply("w", [Mutation("write", "NewFile.py", None, b"new\n")])
    assert (repo / "NewFile.py").read_text() == "new\n", "a create keeps the name's real case"
    with pytest.raises(StaleWrite):
        guard.apply("w", [Mutation("write", "NewFile.py", None, b"again\n")])  # must not exist

    guard.apply("w", [Mutation("rename", "other.py", h(repo, "other.py"), to="moved/other.py")])
    assert not (repo / "other.py").exists() and (repo / "moved" / "other.py").read_text() == "x = 1\n"

    a, b = h(repo, "a.txt"), h(repo, "b.txt")
    with pytest.raises(StaleWrite):  # one stale member refuses the whole set
        guard.apply("w", [Mutation("write", "a.txt", a, b"a2\n"), Mutation("write", "b.txt", "sha256:bad", b"b2\n")])
    assert (repo / "a.txt").read_text() == "a-old\n"
    guard.apply("w", [Mutation("write", "a.txt", a, b"a2\n"), Mutation("delete", "b.txt", b)])
    assert (repo / "a.txt").read_text() == "a2\n" and not (repo / "b.txt").exists()


def test_path_aliases_share_one_lease_and_links_cannot_escape(world, tmp_path):
    repo, _, clock, registration = world
    guard = WriteGuard(registration, clock=clock)
    held = guard.acquire("one", ["api.py"])
    aliases = [str(repo / "api.py"), "./sub/../api.py", str(repo / "api.py").replace("\\", "/")]
    if os.name == "nt":
        aliases += ["API.PY", str(repo / "API.py").replace("/", "\\")]
        short = _short_name(repo / "api.py")
        if short and short != str(repo / "api.py"):
            aliases.append(short)
    for alias in aliases:
        assert guard.canonical(alias) == guard.canonical("api.py"), alias
        with pytest.raises(LeaseBusy):
            guard.acquire("two", [alias])
    guard.release(held)

    outside = tmp_path / "outside"
    outside.mkdir()
    link = repo / "escape"
    if os.name == "nt":
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
    else:
        link.symlink_to(outside, target_is_directory=True)
        made = subprocess.CompletedProcess([], 0)
    if made.returncode == 0:
        with pytest.raises(Unsupported):
            guard.canonical("escape/file.py")
    inner = repo / "inner"
    inner.mkdir()
    (inner / "t.py").write_text("t\n")
    alias_dir = repo / "alias"
    if os.name == "nt":
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias_dir), str(inner)], capture_output=True)
    else:
        alias_dir.symlink_to(inner, target_is_directory=True)
        made = subprocess.CompletedProcess([], 0)
    if made.returncode == 0:
        assert guard.canonical("alias/t.py") == guard.canonical("inner/t.py"), "an in-tree link is one target"
    with pytest.raises(Unsupported):
        guard.canonical(".git/config")
    with pytest.raises(Unsupported):
        guard.canonical("../outside/x.py")


def _short_name(path: pathlib.Path):
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, 1024):
            return buffer.value
    except Exception:  # noqa: BLE001 - 8.3 names may be disabled on this volume
        return None
    return None


def test_git_commits_take_the_worktree_and_never_touch_others_changes(world):
    repo, _, clock, registration = world
    guard = WriteGuard(registration, clock=clock)
    (repo / "api.py").write_text("committed by the guard\n")
    (repo / "other.py").write_text("another agent's staged work\n")
    subprocess.run(["git", "-C", str(repo), "add", "other.py"], check=True)
    (repo / "notes.md").write_text("untracked, never touched\n")

    held = guard.acquire("file-writer", ["a.txt"])
    with pytest.raises(LeaseBusy):
        guard.git_commit("committer", ["api.py"], "guarded commit")  # worktree lease waits for file leases
    guard.release(held)

    result = guard.git_commit("committer", ["api.py"], "guarded commit")
    assert result["committed"] == ["api.py"]
    assert result["other_staged_not_committed"] == ["other.py"]
    shown = subprocess.run(["git", "-C", str(repo), "show", "--name-only", "--format=", "HEAD"],
                           capture_output=True, text=True, check=True).stdout.split()
    assert shown == ["api.py"]
    staged = subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--name-only"],
                            capture_output=True, text=True, check=True).stdout.split()
    assert staged == ["other.py"], "the other agent's staged change is still staged, uncommitted"
    assert (repo / "notes.md").exists()

    worktree = guard.acquire("committer", [WORKTREE])
    with pytest.raises(LeaseBusy):
        guard.acquire("file-writer", ["a.txt"])  # and a file lease waits for the worktree
    guard.release(worktree)


def test_shell_git_mutations_are_classified_not_protected():
    assert classify_git("git checkout -b x") == "git_unsupported"
    assert classify_git("cd repo && git reset --hard") == "git_unsupported"
    assert classify_git("git -C repo commit -am x") == "git_outside_guard"
    assert classify_git("git status") is None
    assert classify_git("Get-Content api.py") is None
