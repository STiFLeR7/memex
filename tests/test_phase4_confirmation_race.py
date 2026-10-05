"""Concurrent hook processes confirming the same packet record it exactly once.

A native shared-checkout run caught two Codex hook processes (S02: Codex runs
hooks concurrently) each confirming one correction. The core's receipt is
idempotent, so the cursor advanced once, but the trace showed two
confirmations. Here two real processes load the same `emitted` row, meet at a
file barrier, and confirm at the same moment.
"""
import json
import pathlib
import sqlite3
import subprocess
import sys
import types

from memex.integrations.codex import CodexAdapter, register
from memex.runtime.views import discover_repository

WORKER = r'''
import json, pathlib, sys, time, types
sys.path.insert(0, {checkout!r})
from memex.integrations.codex import CodexAdapter, load_capability
from memex.runtime.views import discover_repository

state = pathlib.Path({state!r})
registration = discover_repository({repo!r})

def ack(receipt):
    with open(state / "acks.log", "a") as stream:
        stream.write("ack\n")
    time.sleep(0.2)  # widen the window between acknowledging and recording

engine = types.SimpleNamespace(indexer=types.SimpleNamespace(registration=registration), ack_delivery=ack)
adapter = CodexAdapter(engine, load_capability(registration))
row = [r for r in adapter._connect().execute("SELECT * FROM live_adapter_deliveries")][0]
(state / sys.argv[1]).write_text("ready")
while not (state / "go").exists():
    time.sleep(0.005)
adapter._confirm_delivery(row, adapter.session_identity("thread-1"))
'''


def test_two_processes_confirm_one_packet_once(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    registration = discover_repository(repo)
    capability = register(repo, "owner")
    engine = types.SimpleNamespace(indexer=types.SimpleNamespace(registration=registration))
    adapter = CodexAdapter(engine, capability)
    marker = adapter.delivery_marker("task-1", 2, "view:x")
    packet = f"memex: outcome=replan\n[{marker}]"
    adapter.prepare_delivery("thread-1", task_id="task-1", sequence=2, view_id="view:x", kind="correction",
                             packet=packet, attempt_id="exec-1", binding="turn-1")
    adapter.mark_emitted("task-1:2")

    state = tmp_path / "state"
    state.mkdir()
    script = WORKER.format(checkout=str(pathlib.Path(__file__).resolve().parents[1]), state=str(state),
                           repo=str(repo))
    workers = [subprocess.Popen([sys.executable, "-c", script, f"w{i}"], stderr=subprocess.PIPE, text=True)
               for i in range(2)]
    for i in range(2):
        while not (state / f"w{i}").exists():
            assert all(w.poll() is None for w in workers), [w.stderr.read() for w in workers]
    (state / "go").write_text("go")
    for worker in workers:
        _, err = worker.communicate(timeout=60)
        assert worker.returncode == 0, err

    acks = (state / "acks.log").read_text().splitlines()
    confirmed = [e for e in adapter.trace.events(native_session_id="thread-1") if e["insertion"] == "confirmed"]
    assert len(acks) == 1, "only the process that won the transition acknowledges"
    assert len(confirmed) == 1, "and only it records a confirmation"
    db = sqlite3.connect(registration.runtime_path)
    assert db.execute("SELECT state FROM live_adapter_deliveries").fetchone()[0] == "confirmed"
    db.close()
    assert json.loads(json.dumps(confirmed[0]))["sequence"] == 2
