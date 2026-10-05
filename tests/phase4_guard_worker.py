"""A separate guarded-writer process for the W14 race tests.

usage: phase4_guard_worker.py <scenario> <repo> <state-dir> <label> [args...]

Every pause point is a file barrier: the worker writes `<label>.<point>` and
waits for `go-<label>.<point>`, so the test decides the interleaving instead of
hoping a sleep produces it. Time comes from a shared file clock, so lease expiry
is a decision of the test too. The outcome is printed as one JSON line.
"""
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from memex.runtime import guard as guard_module  # noqa: E402
from memex.runtime.guard import GuardError, Mutation, WriteGuard  # noqa: E402
from memex.runtime.views import discover_repository  # noqa: E402


class FileClock:
    def __init__(self, path: pathlib.Path):
        self.path = path

    def __call__(self) -> float:
        return float(self.path.read_text())


def barrier(state: pathlib.Path, label: str, point: str, timeout: float = 60) -> None:
    (state / f"{label}.{point}").write_text(str(os.getpid()))
    deadline = time.time() + timeout
    while not (state / f"go-{label}.{point}").exists():
        if time.time() > deadline:
            raise TimeoutError(f"{label} never released from {point}")
        time.sleep(0.01)


def main(argv) -> int:
    scenario, repo, state, label, *args = argv
    state = pathlib.Path(state)
    registration = discover_repository(repo)
    guard = WriteGuard(registration, clock=FileClock(state / "clock"), ttl=10)
    holder = guard.holder(label)
    try:
        if scenario == "write":
            path, expected, content = args[0], args[1], args[2]
            expected = None if expected == "absent" else expected
            barrier(state, label, "ready")
            lease = guard.acquire(holder, [path])
            barrier(state, label, "acquired")
            try:
                result = guard.commit(lease, [Mutation("write", path, expected, content.encode())])
            finally:
                guard.release(lease)
            print(json.dumps({"outcome": "committed", "result": result}))
        elif scenario == "crash-holding":
            guard.acquire(holder, [args[0]])
            barrier(state, label, "acquired")
            os._exit(17)  # dies holding the lease, without releasing anything
        elif scenario == "crash-mid-replace":
            # Complete the first replacement of a two-file commit, then die.
            original = guard_module.WriteGuard._apply

            def partial(steps):
                original(steps[:1])
                os._exit(19)
            guard_module.WriteGuard._apply = staticmethod(partial)
            first, second = args[0], args[1]
            mutations = [Mutation("write", first, guard.observed(first), b"first-new\n"),
                         Mutation("write", second, guard.observed(second), b"second-new\n")]
            guard.apply(label, mutations)
        elif scenario == "acquire-set":
            barrier(state, label, "ready")
            lease = guard.acquire(holder, args)
            barrier(state, label, "acquired")
            guard.release(lease)
            print(json.dumps({"outcome": "acquired", "generations": lease.generations}))
        else:
            raise SystemExit(f"unknown scenario {scenario}")
    except GuardError as exc:
        print(json.dumps({"outcome": exc.reason, "detail": str(exc)}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
