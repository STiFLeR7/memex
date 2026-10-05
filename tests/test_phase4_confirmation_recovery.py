"""A delivery is confirmed if, and only if, the core accepted its baseline.

The defect this pins: the adapter committed `emitted -> confirmed` and only then
called the core's acknowledgement, so a process stopped between the two left a
row saying `confirmed` over a core that never accepted the packet, and every
retry skipped it because the row was no longer `emitted`.

Every test drives the real acknowledgement path: `LiveContextEngine` over a real
`TaskStore` sharing the registration's control plane, a real `CodexAdapter`, and
a rollout carrying the complete insertion. Stops are real (`os._exit` in a
separate process) at named boundaries, ordered by file barriers.
"""
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import time

import pytest

from memex.context.live import DeliveryReceipt, PacketBudget, PacketItem
from memex.context.revision import RepositoryView
from memex.integrations.codex import register
from memex.runtime.views import discover_repository
from tests import phase4_confirm_worker as confirm_worker
from tests.test_live_codex_adapter import TURN, append, code_mode_denial, developer_context, start_rollout

WORKER = pathlib.Path(confirm_worker.__file__)
NATIVE = confirm_worker.NATIVE


def item(assertion):
    return PacketItem(claim_id="c", revision_id="r", assertion=assertion, authority="inferred",
                      status="supported", reason="sufficient_support")


class World:
    def __init__(self, tmp_path, kind):
        self.repo = tmp_path / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.state = tmp_path / "state"
        self.state.mkdir()
        self.kind = kind
        registration = discover_repository(self.repo)
        register(self.repo, "owner")
        self.adapter = confirm_worker.compose(self.repo)
        self.tasks = self.adapter.engine.tasks
        self.session = self.adapter.session_identity(NATIVE)
        self.view = RepositoryView(registration.repo_id, registration.worktree_id, None, 1, 1, "sha256:" + "a" * 64)
        frame = self.tasks.create(self.session, self.view, "fix api", ("r",), (item("send returns payload"),), (),
                                  PacketBudget(), now=1.0, ttl=3600.0)
        if kind == "correction":
            self.receipt(frame)  # the snapshot was accepted earlier; a correction follows it
            frame = self.offer(item("send takes a timeout"))
        self.frame = frame
        self.rollout = start_rollout(tmp_path / "codex-home")
        self.deliver(frame)

    def offer(self, new_item):
        state = self.tasks.get(self.frame.task_id if hasattr(self, "frame") else self._task, self.session, now=1.0)
        with self.tasks.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            return self.tasks.offer_in(db, state, self.view, (new_item,), (), last_ack=state.ack_sequence)

    def receipt(self, frame, outcome="host_accepted"):
        self._task = frame.task_id
        self.adapter.engine.ack_delivery(DeliveryReceipt(
            session=self.session, task_id=frame.task_id, sequence=frame.sequence, view_id=frame.view_id,
            adapter_version="test", accepted_at=1.0, outcome=outcome))

    def deliver(self, frame):
        marker = self.adapter.delivery_marker(frame.task_id, frame.sequence, frame.view_id)
        packet = f"memex {self.kind}: {[i.assertion for i in frame.items]}\n[{marker}]"
        binding = TURN if self.kind == "correction" else None
        self.adapter.prepare_delivery(NATIVE, task_id=frame.task_id, sequence=frame.sequence,
                                      view_id=frame.view_id, kind=self.kind, packet=packet,
                                      attempt_id="exec-1" if binding else None, binding=binding)
        self.adapter.mark_emitted(f"{frame.task_id}:{frame.sequence}")
        append(self.rollout, code_mode_denial(packet) if binding else developer_context(packet))

    def spawn(self, label, mode):
        env = dict(os.environ, CODEX_HOME=str(pathlib.Path(self.rollout).parents[4]))
        return subprocess.Popen([sys.executable, str(WORKER), str(self.repo), str(self.state), label, mode,
                                 self.rollout], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

    def run(self, mode, label="w", code=0):
        process = self.spawn(label, mode)
        _, err = process.communicate(timeout=60)
        assert process.returncode == code, err
        return process

    # -- what the tests assert ------------------------------------------------- #

    def core(self):
        return self.tasks.get(self.frame.task_id, self.session, now=confirm_worker.NOW)

    def row(self):
        return [d for d in self.adapter.delivery_states(NATIVE)
                if (d["task_id"], d["sequence"]) == (self.frame.task_id, self.frame.sequence)][0]

    def confirmations(self):
        return [e for e in self.adapter.trace.events(native_session_id=NATIVE)
                if e["insertion"] == "confirmed" and e["sequence"] == self.frame.sequence]

    def consistent(self):
        """The ledger says confirmed exactly when the core accepted this packet's baseline."""
        core, row = self.core(), self.row()
        if row["state"] == "confirmed":
            assert core.ack_sequence >= self.frame.sequence, "confirmed, but the core never accepted it"
            assert len(self.confirmations()) == 1, "a confirmation is recorded once"
        else:
            assert core.ack_sequence < self.frame.sequence and core.pending.sequence == self.frame.sequence
            assert self.confirmations() == []
        return row["state"]

    def accepted(self):
        core = self.core()
        assert self.consistent() == "confirmed"
        assert core.ack_sequence == self.frame.sequence and core.pending is None
        assert core.baseline == self.frame.items, "the accepted baseline is this packet's working set"


def wait_for(path, timeout=60):
    deadline = time.time() + timeout
    while not path.exists():
        assert time.time() < deadline, f"{path.name} never appeared"
        time.sleep(0.01)


@pytest.fixture(params=["snapshot", "correction"])
def world(request, tmp_path):
    return World(tmp_path, request.param)


# --------------------------------------------------------------------------- #

def test_a_stop_before_core_acknowledgement_recovers_on_restart(world):
    world.run("die-before-ack", code=31)
    assert world.consistent() == "emitted", "nothing was accepted, so nothing may read as confirmed"
    world.run("confirm")
    world.accepted()


@pytest.mark.parametrize("mode", ["die-inside", "die-inside-late"])
def test_a_stop_inside_the_acknowledgement_leaves_neither_half(world, mode):
    """After the core's baseline update, before or after the ledger update: one transaction."""
    world.run(mode, code=32)
    assert world.consistent() == "emitted"
    world.run("confirm")
    world.accepted()


def test_a_stop_after_acknowledgement_leaves_both_halves(world):
    world.run("die-after-ack", code=33)
    world.accepted()
    world.run("confirm")  # restart: nothing left to do, and nothing done twice
    world.accepted()


@pytest.mark.parametrize("winner", ["pause-before-ack", "pause-inside"])
def test_a_confirming_winner_that_stops_midway_does_not_strand_the_packet(world, winner):
    first = world.spawn("first", winner)
    wait_for(world.state / "first.inside")
    second = world.spawn("second", "confirm")  # contends while the winner is stopped midway
    (world.state / "go-first.inside").write_text("go")
    assert first.wait(timeout=60) in (31, 32)
    _, err = second.communicate(timeout=60)
    assert second.returncode == 0, err
    world.accepted()


def test_simultaneous_confirmers_accept_once_and_record_once(world):
    workers = [world.spawn(label, "confirm-at-barrier") for label in ("w0", "w1")]
    for label in ("w0", "w1"):
        wait_for(world.state / f"{label}.ready")
    for label in ("w0", "w1"):
        (world.state / f"go-{label}.ready").write_text("go")
    for process in workers:
        _, err = process.communicate(timeout=60)
        assert process.returncode == 0, err
    world.accepted()


def test_duplicate_recovery_never_advances_the_baseline_twice(world):
    world.run("confirm")
    world.accepted()
    following = world.offer(item("send also retries"))  # the stream moves on
    for _ in range(2):
        world.run("confirm")
    world.receipt(world.frame)  # and a raw duplicate receipt of the accepted packet
    core = world.core()
    assert core.ack_sequence == world.frame.sequence and core.baseline == world.frame.items
    assert core.pending is not None and core.pending.sequence == following.sequence, "the next packet is untouched"
    assert len(world.confirmations()) == 1


def test_revoked_authorization_confirms_nothing(world):
    world.run("revoked")
    assert world.consistent() == "emitted"
    world.run("confirm")
    world.accepted()


def test_a_rejected_receipt_confirms_nothing(world):
    superseding = world.offer(item("send was redesigned"))  # pending moves past the emitted packet
    assert superseding.sequence > world.frame.sequence
    world.run("confirm")
    assert world.row()["state"] == "emitted"
    assert world.confirmations() == []
    core = world.core()
    assert core.ack_sequence < world.frame.sequence and core.pending.sequence == superseding.sequence
    rejected = [e for e in world.adapter.trace.events(native_session_id=NATIVE)
                if e["insertion"] == "failed" and (e["reason"] or "").startswith("receipt_rejected")]
    assert rejected


def test_a_stranded_confirmation_from_a_previous_version_is_recovered(world):
    """The exact state the previous code left behind: confirmed in the ledger, pending in the core."""
    db = sqlite3.connect(world.adapter.state_path)
    db.execute("UPDATE live_adapter_deliveries SET state='confirmed' WHERE task_id=? AND sequence=?",
               (world.frame.task_id, world.frame.sequence))
    db.commit()
    db.close()
    world.run("confirm")
    world.accepted()
    assert json.loads(json.dumps(world.confirmations()[0]))["sequence"] == world.frame.sequence
