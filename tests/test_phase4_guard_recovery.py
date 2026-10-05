"""W14 recovery: an interrupted guarded write is resolved before anything else proceeds.

The defect this pins: recovery ran only when a guard was constructed, so a guard
that already existed could commit a newer write while an older interrupted
write's intent was still on disk, and a later guard's recovery then replayed the
old staged file over the newer committed one.

Interruptions are real: a separate process is stopped (`os._exit`) or held at a
file barrier exactly before the guard's N-th visible worktree mutation, counted
by `phase4_guard_worker.stop_at_mutation`. The guarantee these tests establish is
stated in `memex/runtime/guard.py`; they exercise process interruption only, not
power loss.
"""
import json
import pathlib
import sqlite3
import time

import pytest

from memex.integrations import guard_mcp
from memex.runtime import guard as G
from memex.runtime.guard import Mutation, StaleWrite, WriteGuard
from tests import phase4_guard_worker as worker
from tests.test_phase4_guard import h, outcome, release, spawn, wait_for, world  # noqa: F401

AB = json.dumps([["write", "a.txt", "first-new\n", None], ["write", "b.txt", "second-new\n", None]])


class Interrupted(BaseException):
    """Stands in for the process stopping; nothing in the guard may catch it."""


def outcomes(guard):
    return [r["outcome"] for r in guard.results()]


def text(repo, name):
    path = repo / name
    return path.read_text() if path.exists() else None


def leftovers(guard):
    """Intent files and staged temporaries still on disk."""
    return (list(guard.intent_dir.glob("intent-*.json"))
            + [p for p in guard.root.rglob(".*.memex-*") if ".git" not in p.parts])


def crash(world, spec=AB, index=1, label="half"):
    """A separate process applies `spec` and is stopped before its `index`-th file mutation."""
    process = spawn(world, "commit-stop", label, str(index), "exit", spec)
    process.wait(timeout=60)
    assert process.returncode == 19, process.stderr.read()


def held(process, seconds=0.5):
    """The process is still waiting: it has not exited within a bounded window."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if process.poll() is not None:
            return False
        time.sleep(0.02)
    return True


# --------------------------------------------------------------------------- #

def test_recovery_never_replays_an_old_intent_over_a_newer_guarded_write(world, monkeypatch):
    """The reported sequence, exactly: two guards exist before the interruption."""
    repo, _, clock, registration = world
    first = WriteGuard(registration, clock=clock)
    second = WriteGuard(registration, clock=clock)
    a0, b0 = h(repo, "a.txt"), h(repo, "b.txt")

    def stop():
        raise Interrupted
    worker.stop_at_mutation(first.root, 1, stop, patch=monkeypatch.setattr)
    with pytest.raises(Interrupted):
        first.apply("first", [Mutation("write", "a.txt", a0, b"first-new\n"),
                              Mutation("write", "b.txt", b0, b"second-new\n")])
    monkeypatch.undo()
    assert (text(repo, "a.txt"), text(repo, "b.txt")) == ("first-new\n", "b-old\n"), "a half-applied set"
    assert list(first.intent_dir.glob("intent-*.json")), "its durable intent and staged b.txt remain"

    # The already-running second guard writes newer contents to b.txt from what it reads.
    try:
        second.apply("second", [Mutation("write", "b.txt", guard_mcp.read(second, "b.txt")["sha256"], b"newer\n")])
    except StaleWrite:
        # The interrupted set was completed first; reconsider against the real bytes.
        second.apply("second", [Mutation("write", "b.txt", guard_mcp.read(second, "b.txt")["sha256"], b"newer\n")])
    assert text(repo, "b.txt") == "newer\n"

    WriteGuard(registration, clock=clock)  # a later guard enters, and recovery runs again
    second.apply("second", [Mutation("write", "other.py", h(repo, "other.py"), b"y = 2\n")])
    assert text(repo, "b.txt") == "newer\n", "recovery replayed an old intent over a newer committed write"
    assert text(repo, "a.txt") == "first-new\n"
    seen = outcomes(second)
    assert seen.count("rolled_forward") == 1
    assert seen.index("rolled_forward") < seen.index("committed"), "resolved before the newer write proceeded"
    assert not leftovers(second)


def test_a_process_stopped_between_replacements_is_rolled_forward_and_attributed(world):
    repo, _, clock, registration = world
    crash(world)
    assert (text(repo, "a.txt"), text(repo, "b.txt")) == ("first-new\n", "b-old\n")

    guard = WriteGuard(registration, clock=clock)
    assert (text(repo, "a.txt"), text(repo, "b.txt")) == ("first-new\n", "second-new\n")
    [row] = [r for r in guard.results() if r["outcome"] == "rolled_forward"]
    assert row["holder"].startswith("half:"), "the record names the interrupted writer, not a generic actor"
    assert json.loads(row["result_hashes"]) == {"a.txt": h(repo, "a.txt"), "b.txt": h(repo, "b.txt")}
    assert row["intent"]
    assert not leftovers(guard)


def test_a_conflicting_writer_waits_until_recovery_finishes(world):
    repo, state, clock, registration = world
    b0 = h(repo, "b.txt")
    crash(world)
    clock.set(200.0)  # the stopped writer's lease has expired, so only recovery stands in the way
    recovering = spawn(world, "recover-stop", "rec", "0", "pause")
    wait_for(state, "rec.paused")  # holding the protected section, before its first replacement

    late = spawn(world, "write-now", "late", "b.txt", b0, "late\n")
    wait_for(state, "late.attempting")
    assert held(late), "a conflicting write must not proceed while recovery is unfinished"
    assert text(repo, "b.txt") == "b-old\n"

    release(state, "rec", "paused")
    assert outcome(recovering)["outcome"] == "recovered"
    lost = outcome(late)
    assert lost["outcome"] == StaleWrite.reason, "it reasoned from bytes recovery replaced; it must replan"
    assert text(repo, "b.txt") == "second-new\n"


def test_recovery_stopped_midway_resumes_and_repeats_safely(world):
    repo, _, clock, registration = world
    spec = json.dumps([["write", "a.txt", "A\n", None], ["write", "b.txt", "B\n", None],
                       ["write", "api.py", "C\n", None]])
    crash(world, spec, index=1)
    recovering = spawn(world, "recover-stop", "rec", "1", "exit")  # completes b.txt, dies before api.py
    recovering.wait(timeout=60)
    assert recovering.returncode == 21
    assert (text(repo, "a.txt"), text(repo, "b.txt"), text(repo, "api.py").startswith("def")) == ("A\n", "B\n", True)

    guards = [WriteGuard(registration, clock=clock) for _ in range(3)]  # restart, then repeat
    assert (text(repo, "a.txt"), text(repo, "b.txt"), text(repo, "api.py")) == ("A\n", "B\n", "C\n")
    assert outcomes(guards[-1]).count("rolled_forward") == 1, "repeated recovery records the resolution once"
    assert not leftovers(guards[-1])


def test_an_expired_and_replaced_holder_is_resolved_before_the_next_lease(world):
    repo, _, clock, registration = world
    running = WriteGuard(registration, clock=clock, ttl=10)  # exists before the interruption
    b0 = h(repo, "b.txt")
    crash(world)
    clock.set(200.0)  # the stopped holder's lease has expired

    lease = running.acquire(running.holder("next"), ["b.txt"])
    try:
        with pytest.raises(StaleWrite):
            running.commit(lease, [Mutation("write", "b.txt", b0, b"next\n")])
    finally:
        running.release(lease)
    assert text(repo, "b.txt") == "second-new\n"
    db = sqlite3.connect(registration.runtime_path)
    generation = db.execute("SELECT generation FROM guard_generations WHERE target='b.txt'").fetchone()[0]
    db.close()
    assert generation == 2, "the replacement holder took a newer generation after recovery"

    current = guard_mcp.read(running, "b.txt")["sha256"]
    running.apply("next", [Mutation("write", "b.txt", current, b"next\n")])
    WriteGuard(registration, clock=clock)
    assert text(repo, "b.txt") == "next\n"


def test_an_external_edit_after_interruption_blocks_instead_of_being_overwritten(world):
    repo, _, clock, registration = world
    crash(world)
    (repo / "b.txt").write_text("an editor saved this\n")  # outside the guard

    guard = WriteGuard(registration, clock=clock)
    assert text(repo, "b.txt") == "an editor saved this\n", "an old staged file overwrote an external change"
    assert text(repo, "a.txt") == "first-new\n"
    for path in ("a.txt", "b.txt"):  # every member of the interrupted set is held
        with pytest.raises(G.Unresolved):
            guard.apply("w", [Mutation("write", path, h(repo, path), b"x\n")])
        with pytest.raises(G.Unresolved):
            guard.read(path)
    with pytest.raises(G.Unresolved):
        guard.git_commit("c", ["api.py"], "worktree-wide work waits too")
    guard.apply("w", [Mutation("write", "other.py", h(repo, "other.py"), b"y = 2\n")])  # unrelated proceeds

    WriteGuard(registration, clock=clock)
    assert outcomes(guard).count("unresolved_conflict") == 1, "recorded once, however often it is seen"
    [pending] = guard.unresolved()
    observed = json.loads(pending["observed"]) if isinstance(pending["observed"], str) else pending["observed"]
    assert observed["b.txt"] == h(repo, "b.txt")

    guard.discard_intent(pending["intent"], "operator")  # an explicit, recorded decision
    assert text(repo, "b.txt") == "an editor saved this\n"
    assert outcomes(guard)[-1] == "intent_discarded"
    assert not leftovers(guard)
    clock.set(200.0)  # once the stopped writer's lease expires, b.txt is writable again
    guard.apply("w", [Mutation("write", "b.txt", h(repo, "b.txt"), b"resumed\n")])


SET = json.dumps([["write", "new.txt", "created\n", None], ["delete", "a.txt", None, None],
                  ["rename", "other.py", None, "moved/other.py"], ["write", "b.txt", "b-new\n", None]])
BEFORE = {"new.txt": None, "a.txt": "a-old\n", "other.py": "x = 1\n", "moved/other.py": None, "b.txt": "b-old\n"}
AFTER = {"new.txt": "created\n", "a.txt": None, "other.py": None, "moved/other.py": "x = 1\n", "b.txt": "b-new\n"}


@pytest.mark.parametrize("index", [0, 1, 2, 3])
def test_interrupted_create_delete_rename_sets_end_all_or_nothing(world, index):
    repo, _, clock, registration = world
    crash(world, SET, index=index)
    guard = WriteGuard(registration, clock=clock)
    state = {name: text(repo, name) for name in BEFORE}
    if index == 0:
        assert state == BEFORE, "nothing was replaced, so nothing is applied"
        assert outcomes(guard)[-1] == "rolled_back"
    else:
        assert state == AFTER
        assert outcomes(guard)[-1] == "rolled_forward"
    assert not leftovers(guard)


def test_a_stop_after_replacement_but_before_the_outcome_is_recorded(world):
    repo, _, clock, registration = world
    process = spawn(world, "die-before-record", "late", AB)
    process.wait(timeout=60)
    assert process.returncode == 23
    assert (text(repo, "a.txt"), text(repo, "b.txt")) == ("first-new\n", "second-new\n")

    guards = [WriteGuard(registration, clock=clock) for _ in range(2)]
    rows = [r for r in guards[-1].results() if r["outcome"] == "recovered_applied"]
    assert len(rows) == 1 and rows[0]["holder"].startswith("late:"), "applied, so recorded as applied, once"
    assert json.loads(rows[0]["result_hashes"]) == {"a.txt": h(repo, "a.txt"), "b.txt": h(repo, "b.txt")}
    assert not leftovers(guards[-1])


def test_a_stop_after_the_outcome_is_recorded_is_not_recorded_twice(world):
    repo, _, clock, registration = world
    process = spawn(world, "die-before-intent-cleanup", "done", AB)
    process.wait(timeout=60)
    assert process.returncode == 25
    guard = WriteGuard(registration, clock=clock)
    WriteGuard(registration, clock=clock)
    assert outcomes(guard) == ["committed"]
    assert (text(repo, "a.txt"), text(repo, "b.txt")) == ("first-new\n", "second-new\n")
    assert not leftovers(guard)


def test_a_guard_read_never_sees_half_of_an_interrupted_set(world):
    repo, _, clock, registration = world
    reader = WriteGuard(registration, clock=clock)  # running before the interruption
    crash(world)
    assert guard_mcp.read(reader, "a.txt")["text"] == "first-new\n"
    assert guard_mcp.read(reader, "b.txt")["text"] == "second-new\n"


def test_a_guard_read_waits_for_an_in_flight_set(world):
    repo, state, _, _ = world
    reader = spawn(world, "read", "r", "a.txt", "b.txt")
    wait_for(state, "r.ready")  # its guard is constructed and running before the write starts
    writer = spawn(world, "commit-stop", "w", "1", "pause", AB)
    wait_for(state, "w.paused")  # a.txt replaced, b.txt not yet, inside the protected section
    release(state, "r", "ready")
    assert held(reader), "a read must not observe an in-flight multi-file set"
    release(state, "w", "paused")
    assert outcome(writer)["outcome"] == "committed"
    assert outcome(reader)["texts"] == {"a.txt": "first-new\n", "b.txt": "second-new\n"}


# -- upgrade -------------------------------------------------------------------- #

PREVIOUS_SCHEMA = (  # exactly as committed at 30cc96d
    """CREATE TABLE guard_generations (
        worktree_id TEXT NOT NULL, target TEXT NOT NULL, generation INTEGER NOT NULL,
        PRIMARY KEY(worktree_id, target))""",
    """CREATE TABLE guard_leases (
        worktree_id TEXT NOT NULL, target TEXT NOT NULL, holder TEXT NOT NULL,
        generation INTEGER NOT NULL, expires_at REAL NOT NULL,
        PRIMARY KEY(worktree_id, target))""",
    """CREATE TABLE guard_results (
        worktree_id TEXT NOT NULL, ordering INTEGER PRIMARY KEY AUTOINCREMENT,
        holder TEXT NOT NULL, at REAL NOT NULL, outcome TEXT NOT NULL,
        targets TEXT NOT NULL, result_hashes TEXT NOT NULL DEFAULT '{}')""",
)


def test_a_populated_previous_guard_database_upgrades_under_concurrent_initialization(world):
    repo, state, clock, registration = world
    wt = registration.worktree_id
    db = sqlite3.connect(registration.runtime_path)
    for ddl in PREVIOUS_SCHEMA:
        db.execute(ddl)
    db.execute("INSERT INTO guard_generations VALUES(?,?,3)", (wt, "api.py"))
    db.execute("INSERT INTO guard_leases VALUES(?,?,?,?,?)", (wt, "api.py", "old:1:ab", 3, 1e12))
    db.execute("INSERT INTO guard_results(worktree_id,holder,at,outcome,targets,result_hashes) "
               "VALUES(?,?,?,?,?,?)", (wt, "old:1:ab", 1.0, "committed", '["api.py"]', '{"api.py":"sha256:x"}'))
    db.commit()
    db.close()
    # An intent in the previous format: bare steps with no before/after hashes.
    intent_dir = pathlib.Path(registration.common_dir) / "memex" / "guard" / wt
    intent_dir.mkdir(parents=True, exist_ok=True)
    staged = repo / ".b.txt.memex-0000"
    staged.write_text("stale staged\n")
    (intent_dir / "intent-legacy.json").write_text(json.dumps(
        [{"op": "replace", "from": str(staged), "to": str(repo / "b.txt")}]))

    starters = [spawn(world, "init", label) for label in ("i1", "i2")]
    for label in ("i1", "i2"):
        wait_for(state, f"{label}.ready")
    for label in ("i1", "i2"):
        release(state, label, "ready")
    assert [outcome(p)["outcome"] for p in starters] == ["initialized", "initialized"]

    guard = WriteGuard(registration, clock=clock)
    rows = guard.results()
    assert rows[0]["outcome"] == "committed" and rows[0]["holder"] == "old:1:ab", "history is preserved"
    db = sqlite3.connect(registration.runtime_path)
    assert db.execute("SELECT holder, generation FROM guard_leases").fetchall() == [("old:1:ab", 3)]
    db.close()
    # Its hashes were never recorded, so it cannot be told apart from a newer write: held, not replayed.
    assert text(repo, "b.txt") == "b-old\n"
    assert [r["outcome"] for r in rows].count("unresolved_legacy_intent") == 1
    with pytest.raises(G.Unresolved):
        guard.apply("w", [Mutation("write", "b.txt", h(repo, "b.txt"), b"x\n")])
    guard.apply("w", [Mutation("write", "a.txt", h(repo, "a.txt"), b"fine\n")])
    [pending] = guard.unresolved()
    guard.discard_intent(pending["intent"], "operator")
    assert not staged.exists() and text(repo, "b.txt") == "b-old\n"
    guard.apply("w", [Mutation("write", "b.txt", h(repo, "b.txt"), b"resumed\n")])
