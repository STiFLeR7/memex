"""A separate confirming hook process for the delivery-confirmation recovery tests.

usage: phase4_confirm_worker.py <repo> <state-dir> <label> <mode> <rollout>

The worker composes the real core acknowledgement path -- `LiveContextEngine`
over a real `TaskStore` in the registration's control plane -- with a real
`CodexAdapter`, and runs `confirm_deliveries` once. Only the graph is absent,
because acknowledgement never touches it. Modes stop it at one boundary:

* `confirm`            run to completion
* `confirm-at-barrier` wait at `<label>.ready`, then run to completion
* `die-before-ack`     stop when the core acknowledgement is about to start
* `pause-before-ack`   wait at `<label>.inside` there, then stop
* `die-inside`         stop inside the acknowledgement transaction, after the
                       core's baseline update and before the ledger records
* `die-inside-late`    stop inside it after the ledger records, before commit
* `pause-inside`       wait at `<label>.inside` inside it, then stop
* `die-after-ack`      stop as soon as the acknowledgement returns
* `revoked`            the principal's authorization has been revoked
"""
import json
import os
import pathlib
import sys
import time
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from memex.integrations.codex import CodexAdapter, load_capability  # noqa: E402
from memex.runtime.actions import LiveContextEngine  # noqa: E402
from memex.runtime.tasks import TaskStore  # noqa: E402
from memex.runtime.views import discover_repository  # noqa: E402

NATIVE = "01a10ae6-2393-7313-8213-1f153abc89b2"
NOW = 50.0


def barrier(state: pathlib.Path, label: str, point: str, timeout: float = 60) -> None:
    (state / f"{label}.{point}").write_text(str(os.getpid()))
    deadline = time.time() + timeout
    while not (state / f"go-{label}.{point}").exists():
        if time.time() > deadline:
            raise TimeoutError(f"{label} never released from {point}")
        time.sleep(0.01)


def compose(repo, revoked=False):
    registration = discover_repository(repo)
    tasks = TaskStore(registration.runtime_path,
                      authenticate=lambda s: not revoked and s.principal_id == "owner" and s.harness == "codex")
    engine = LiveContextEngine(
        types.SimpleNamespace(registration=registration), None, tasks,
        authorize_view=lambda s, r: (r.repo_id, r.worktree_id) == (registration.repo_id, registration.worktree_id),
        authorize_source=lambda s, p: True, clock=lambda: NOW)
    return CodexAdapter(engine, load_capability(registration), clock=lambda: NOW)


def main(argv) -> int:
    repo, state, label, mode, rollout = argv
    state = pathlib.Path(state)
    adapter = compose(repo, revoked=mode == "revoked")
    tasks = adapter.engine.tasks
    original = tasks.ack_delivery

    def wrapped(receipt, *, now, record=None):
        if mode == "die-before-ack":
            os._exit(31)
        if mode == "pause-before-ack":
            barrier(state, label, "inside")
            os._exit(31)
        if mode in ("die-inside", "die-inside-late", "pause-inside"):
            def inner(db):
                if mode == "die-inside-late":
                    record(db)
                if mode == "pause-inside":
                    barrier(state, label, "inside")
                os._exit(32)
            return original(receipt, now=now, record=inner)
        result = original(receipt, now=now, **({"record": record} if record else {}))
        if mode == "die-after-ack":
            os._exit(33)
        return result

    tasks.ack_delivery = wrapped
    if mode == "confirm-at-barrier":
        barrier(state, label, "ready")
    adapter.confirm_deliveries(adapter.session_identity(NATIVE), rollout)
    print(json.dumps({"outcome": "done"}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
