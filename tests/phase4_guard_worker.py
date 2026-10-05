"""A separate guarded-writer process for the W14 race and recovery tests.

usage: phase4_guard_worker.py <scenario> <repo> <state-dir> <label> [args...]

Every pause point is a file barrier: the worker writes `<label>.<point>` and
waits for `go-<label>.<point>`, so the test decides the interleaving instead of
hoping a sleep produces it. Time comes from a shared file clock, so lease expiry
is a decision of the test too. The outcome is printed as one JSON line.

Interruptions are placed by counting the guard's own visible mutations of
worktree files (`os.replace` onto, or `os.unlink` of, a worktree file), not by
naming its private methods, so a stop lands on the same file boundary whatever
the guard's internal structure.
"""
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from memex.integrations import guard_mcp  # noqa: E402
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


def stop_at_mutation(root: pathlib.Path, count: int, act, patch=setattr) -> None:
    """Call `act()` just before the guard's `count`-th visible worktree mutation (0-based).

    A visible mutation is a replace onto, or an unlink of, a worktree file.
    Staged temporaries (`.<name>.memex-*`) and Git metadata are not visible.
    In-process tests pass `monkeypatch.setattr` as `patch` so it is undone.
    """
    root = root.resolve()
    real_replace, real_unlink = os.replace, os.unlink
    seen = [0]

    def visible(path) -> bool:
        resolved = pathlib.Path(path).resolve()
        if not resolved.is_relative_to(root):
            return False
        parts = resolved.relative_to(root).parts
        return bool(parts) and parts[0] != ".git" and ".memex-" not in resolved.name

    def before(path):
        if visible(path):
            seen[0] += 1
            if seen[0] - 1 == count:
                act()

    def replace(src, dst, *args, **kwargs):
        before(dst)
        return real_replace(src, dst, *args, **kwargs)

    def unlink(path, *args, **kwargs):
        before(path)
        return real_unlink(path, *args, **kwargs)

    patch(os, "replace", replace)
    patch(os, "unlink", unlink)


def mutations(guard, spec):
    """`spec` is a JSON list of [op, path, content-or-null, rename-destination-or-null]."""
    built = []
    for op, path, content, to in spec:
        built.append(Mutation(op, path, guard.observed(guard.relative(path)),
                              None if content is None else content.encode(),
                              to, guard.observed(guard.relative(to)) if to else None))
    return built


def main(argv) -> int:
    scenario, repo, state, label, *args = argv
    state = pathlib.Path(state)
    registration = discover_repository(repo)
    clock = FileClock(state / "clock")

    def make():
        return WriteGuard(registration, clock=clock, ttl=10)

    try:
        if scenario == "write":
            guard = make()
            holder = guard.holder(label)
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
        elif scenario == "write-now":
            # Announce the attempt first: construction itself enters the protected section.
            path, expected, content = args[0], args[1], args[2]
            (state / f"{label}.attempting").write_text(str(os.getpid()))
            guard = make()
            result = guard.apply(label, [Mutation("write", path, None if expected == "absent" else expected,
                                                  content.encode())])
            print(json.dumps({"outcome": "committed", "result": result}))
        elif scenario == "crash-holding":
            guard = make()
            guard.acquire(guard.holder(label), [args[0]])
            barrier(state, label, "acquired")
            os._exit(17)  # dies holding the lease, without releasing anything
        elif scenario == "crash-mid-replace":
            # Complete the first replacement of a two-file commit, then die.
            guard = make()
            first, second = args[0], args[1]
            stop_at_mutation(guard.root, 1, lambda: os._exit(19))
            guard.apply(label, mutations(guard, [["write", first, "first-new\n", None],
                                                 ["write", second, "second-new\n", None]]))
        elif scenario == "commit-stop":
            # args: <stop-index> <exit|pause> <mutation spec JSON>
            index, action, spec = int(args[0]), args[1], json.loads(args[2])
            guard = make()
            built = mutations(guard, spec)

            def act():
                if action == "pause":
                    barrier(state, label, "paused")
                else:
                    os._exit(19)
            stop_at_mutation(guard.root, index, act)
            print(json.dumps({"outcome": "committed", "result": guard.apply(label, built)}))
        elif scenario == "recover-stop":
            # Construct a guard (which recovers) and stop at a recovery mutation.
            index, action = int(args[0]), args[1]

            def act():
                if action == "pause":
                    barrier(state, label, "paused")
                else:
                    os._exit(21)
            stop_at_mutation(pathlib.Path(registration.root), index, act)
            make()
            print(json.dumps({"outcome": "recovered"}))
        elif scenario == "die-before-record":
            # The files are replaced; die before the outcome is recorded.
            guard = make()
            original = WriteGuard._record

            def record(self, db, holder, outcome, *rest, **kwargs):
                if outcome == "committed":
                    os._exit(23)
                return original(self, db, holder, outcome, *rest, **kwargs)
            WriteGuard._record = record
            guard.apply(label, mutations(guard, json.loads(args[0])))
        elif scenario == "die-before-intent-cleanup":
            guard = make()
            real_unlink = os.unlink

            def unlink(path, *a, **k):
                if pathlib.Path(path).name.startswith("intent-"):
                    os._exit(25)
                return real_unlink(path, *a, **k)
            os.unlink = unlink
            guard.apply(label, mutations(guard, json.loads(args[0])))
        elif scenario == "read":
            guard = make()
            barrier(state, label, "ready")
            print(json.dumps({"outcome": "read",
                              "texts": {p: guard_mcp.read(guard, p).get("text") for p in args}}))
        elif scenario == "init":
            barrier(state, label, "ready")
            make()
            print(json.dumps({"outcome": "initialized"}))
        elif scenario == "acquire-set":
            guard = make()
            holder = guard.holder(label)
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
