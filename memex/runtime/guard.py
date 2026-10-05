"""Opt-in guarded writes: leases with fencing, enforced on the write path itself.

Default mode coordinates *context*: it detects drift and asks agents to
reconsider, but a gap remains between a check and the host's own write. This
module closes that gap for one clearly bounded set of mutations, for writers
that cooperate by routing them here:

* whole-file create, update, delete and rename of regular files inside one
  registered worktree, singly or as an all-or-nothing set;
* `git commit --only` of explicitly named paths, under a worktree-wide lease.

Everything else -- checkout, reset, rebase, merge, stash, shell redirection,
editors, other machines -- bypasses it and is reported as unsupported or
advisory, never as protected.

How the guarantee is made, rather than asserted:

* A **lease** is temporary ownership of a target; its **fencing generation**
  identifies the current holder and only ever increases per target.
* The commit runs inside one SQLite `BEGIN IMMEDIATE` transaction, which is the
  cross-process critical section. Inside it the guard rechecks each lease's
  holder, generation and expiry, rechecks the expected hash of every target on
  disk, writes a durable intent, replaces the files and captures their result
  hashes, all before committing. A holder that paused, expired and was replaced
  fails the generation check *at commit*, so the token is enforced by the write
  path and not merely stored.
* A crash releases the SQLite lock with the process. A crashed holder's lease
  expires and is reclaimable. An intent left by a crash mid-replacement is
  rolled forward on the next guard entry, because every check had already
  passed before the first file was replaced.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import time

#: The worktree-wide target. Its lease conflicts with every file lease.
WORKTREE = "$worktree"

#: Git operations the guard performs. Everything else is unsupported here.
SUPPORTED_GIT = ("commit",)
UNSUPPORTED_GIT = ("checkout", "switch", "reset", "rebase", "merge", "stash", "cherry-pick",
                   "revert", "restore", "rm", "mv", "clean", "pull", "am", "apply")


class GuardError(Exception):
    """Base for refusals. Each carries a machine-readable `reason`."""
    reason = "guard_error"


class StaleWrite(GuardError):
    """A target's bytes are not what the writer expected: reconsider, do not retry blindly."""
    reason = "expected_hash_mismatch"

    def __init__(self, path, expected, observed):
        super().__init__(f"{path}: expected {expected}, observed {observed}")
        self.path, self.expected, self.observed = path, expected, observed


class LeaseBusy(GuardError):
    reason = "lease_held_by_another_writer"


class LeaseLost(GuardError):
    """The lease expired or was replaced before commit; fencing rejected the write."""
    reason = "lease_expired_or_replaced"


class Unsupported(GuardError):
    reason = "unsupported_mutation"


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Mutation:
    """One supported change. `expected` is the target's current hash, or None for absent."""
    op: str                      # "write" | "delete" | "rename"
    path: str
    expected: str | None = None
    content: bytes | None = None
    to: str | None = None        # rename destination
    expected_to: str | None = None


@dataclass(frozen=True)
class Lease:
    holder: str
    generations: dict = field(default_factory=dict)  # canonical target -> generation
    expires_at: float = 0.0


class WriteGuard:
    """Cooperating write coordinator for one registered worktree."""

    def __init__(self, registration, *, clock=time.time, ttl: float = 30.0, busy_timeout: float = 30.0):
        self.registration = registration
        self.root = Path(registration.root).resolve()
        self.worktree_id = registration.worktree_id
        self.clock = clock
        self.ttl = ttl
        self.busy_timeout = busy_timeout
        self.state_path = Path(registration.runtime_path)
        self.intent_dir = Path(registration.common_dir) / "memex" / "guard" / self.worktree_id
        self.intent_dir.mkdir(parents=True, exist_ok=True)
        with self._transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS guard_generations (
                worktree_id TEXT NOT NULL, target TEXT NOT NULL, generation INTEGER NOT NULL,
                PRIMARY KEY(worktree_id, target))""")
            db.execute("""CREATE TABLE IF NOT EXISTS guard_leases (
                worktree_id TEXT NOT NULL, target TEXT NOT NULL, holder TEXT NOT NULL,
                generation INTEGER NOT NULL, expires_at REAL NOT NULL,
                PRIMARY KEY(worktree_id, target))""")
            db.execute("""CREATE TABLE IF NOT EXISTS guard_results (
                worktree_id TEXT NOT NULL, ordering INTEGER PRIMARY KEY AUTOINCREMENT,
                holder TEXT NOT NULL, at REAL NOT NULL, outcome TEXT NOT NULL,
                targets TEXT NOT NULL, result_hashes TEXT NOT NULL DEFAULT '{}')""")
            self._recover(db)

    # -- the critical section ------------------------------------------------ #

    @contextmanager
    def _transaction(self):
        """`BEGIN IMMEDIATE` on the shared control plane: one writer across processes."""
        db = sqlite3.connect(self.state_path, timeout=self.busy_timeout, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.execute("ROLLBACK")
                raise
            db.execute("COMMIT")
        finally:
            db.close()

    # -- paths --------------------------------------------------------------- #

    def canonical(self, raw: str) -> str:
        """One lease key per file, whatever alias names it.

        Relative and absolute spellings, `.`/`..` segments, separators, case on
        case-insensitive filesystems, 8.3 short names and links that stay inside
        the worktree all collapse to the resolved target. A path that resolves
        outside the worktree, or into Git's own metadata, is refused.
        """
        if raw == WORKTREE:
            return WORKTREE
        key = self.relative(raw)
        return os.path.normcase(key).replace("\\", "/") if os.name == "nt" else key

    def relative(self, raw: str) -> str:
        """The real repository-relative path, with its real case, for I/O and Git."""
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root):
            raise Unsupported(f"{raw}: resolves outside the registered worktree")
        relative = resolved.relative_to(self.root)
        if not relative.parts or relative.parts[0].lower() == ".git":
            raise Unsupported(f"{raw}: not a worktree file the guard writes")
        return relative.as_posix()

    def _absolute(self, relative: str) -> Path:
        return self.root / relative

    # -- leases -------------------------------------------------------------- #

    def holder(self, label: str) -> str:
        return f"{label}:{os.getpid()}:{secrets.token_hex(4)}"

    def acquire(self, holder: str, targets, *, ttl: float | None = None) -> Lease:
        """All-or-none, in canonical order, under the write lock.

        A live lease held by another writer refuses the whole set; nothing is
        partially acquired, so two writers taking overlapping sets in opposite
        orders cannot deadlock. An expired lease is reclaimable, which is how a
        crashed holder stops blocking anyone.
        """
        keys = sorted({self.canonical(t) for t in targets})
        if not keys:
            raise Unsupported("a lease needs at least one target")
        now, expires = self.clock(), self.clock() + (ttl or self.ttl)
        with self._transaction() as db:
            live = {row["target"]: row for row in db.execute(
                "SELECT * FROM guard_leases WHERE worktree_id=? AND expires_at>?", (self.worktree_id, now))}
            for key in keys:
                row = live.get(key)
                if row is not None and row["holder"] != holder:
                    raise LeaseBusy(f"{key} is leased by another writer")
            others = {k for k, row in live.items() if row["holder"] != holder}
            if WORKTREE in keys and others:
                raise LeaseBusy("a worktree-wide lease needs every file lease released")
            if WORKTREE in others:
                raise LeaseBusy("a worktree-wide operation holds the worktree")
            generations = {}
            for key in keys:
                db.execute("INSERT INTO guard_generations VALUES(?,?,1) ON CONFLICT(worktree_id,target) "
                           "DO UPDATE SET generation=generation+1", (self.worktree_id, key))
                generation = db.execute("SELECT generation FROM guard_generations WHERE worktree_id=? AND target=?",
                                        (self.worktree_id, key)).fetchone()[0]
                db.execute("INSERT OR REPLACE INTO guard_leases VALUES(?,?,?,?,?)",
                           (self.worktree_id, key, holder, generation, expires))
                generations[key] = generation
        return Lease(holder, generations, expires)

    def release(self, lease: Lease) -> None:
        with self._transaction() as db:
            for key, generation in lease.generations.items():
                db.execute("DELETE FROM guard_leases WHERE worktree_id=? AND target=? AND holder=? AND generation=?",
                           (self.worktree_id, key, lease.holder, generation))

    def _fence(self, db, lease: Lease, keys) -> None:
        now = self.clock()
        for key in keys:
            if key not in lease.generations:
                raise LeaseLost(f"{key} was never leased by this writer")
            row = db.execute("SELECT * FROM guard_leases WHERE worktree_id=? AND target=?",
                             (self.worktree_id, key)).fetchone()
            if (row is None or row["holder"] != lease.holder or row["generation"] != lease.generations[key]
                    or row["expires_at"] <= now):
                raise LeaseLost(f"{key}: lease generation {lease.generations[key]} is no longer current")

    # -- the fenced commit --------------------------------------------------- #

    def observed(self, relative: str) -> str | None:
        path = self._absolute(relative)
        if not path.exists():
            return None
        if not path.is_file():
            raise Unsupported(f"{relative}: not a regular file")
        return sha256(path.read_bytes())

    def commit(self, lease: Lease, mutations) -> dict:
        """Apply `mutations` atomically if, and only if, the lease and every hash are current.

        Returns result hashes by real relative path. A refusal leaves every
        target untouched and is recorded after the protected section unwinds.
        """
        plan = []
        for m in mutations:
            if m.op not in ("write", "delete", "rename"):
                raise Unsupported(f"{m.op}: not a supported mutation")
            if m.op == "write" and m.content is None:
                raise Unsupported(f"{m.path}: a write needs content")
            to = self.relative(m.to) if m.op == "rename" else None
            plan.append((m, self.relative(m.path), to))
        paths = {p for _, p, _ in plan} | {t for _, _, t in plan if t}
        keys = {self.canonical(p) for p in paths}
        try:
            with self._transaction() as db:
                self._fence(db, lease, keys)
                for m, path, to in plan:
                    if self.observed(path) != m.expected:
                        raise StaleWrite(path, m.expected, self.observed(path))
                    if to is not None and self.observed(to) != m.expected_to:
                        raise StaleWrite(to, m.expected_to, self.observed(to))
                steps = self._stage(plan)
                intent = self.intent_dir / f"intent-{secrets.token_hex(8)}.json"
                self._durable(intent, json.dumps(steps).encode())
                self._apply(steps)
                intent.unlink()
                results = {path: self.observed(path) for path in sorted(paths)}
                # Protection is retained through result capture; the lease is
                # released only after the outcome is recorded.
                self._record(db, lease.holder, "committed", keys, results)
        except GuardError as exc:
            with self._transaction() as db:
                self._record(db, lease.holder, exc.reason, keys, {})
            raise
        return results

    def apply(self, label: str, mutations, *, ttl: float | None = None) -> dict:
        """Acquire, commit and release: one bounded guarded write."""
        targets = [m.path for m in mutations] + [m.to for m in mutations if m.op == "rename"]
        lease = self.acquire(self.holder(label), targets, ttl=ttl)
        try:
            return self.commit(lease, mutations)
        finally:
            self.release(lease)

    def _stage(self, plan):
        """Write new contents beside their targets. Nothing visible changes yet."""
        steps = []
        for m, path, to in plan:
            target = self._absolute(path)
            if m.op == "write":
                target.parent.mkdir(parents=True, exist_ok=True)
                staged = target.with_name(f".{target.name}.memex-{secrets.token_hex(4)}")
                self._durable(staged, m.content)
                steps.append({"op": "replace", "from": str(staged), "to": str(target)})
            elif m.op == "delete":
                steps.append({"op": "delete", "path": str(target)})
            else:
                destination = self._absolute(to)
                destination.parent.mkdir(parents=True, exist_ok=True)
                steps.append({"op": "replace", "from": str(target), "to": str(destination)})
        return steps

    @staticmethod
    def _durable(path: Path, data: bytes) -> None:
        with open(path, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _apply(steps) -> None:
        """Idempotent, so an interrupted run can be completed by replaying it."""
        for step in steps:
            if step["op"] == "replace":
                if os.path.exists(step["from"]):
                    os.replace(step["from"], step["to"])
            elif step["op"] == "delete" and os.path.exists(step["path"]):
                os.unlink(step["path"])

    def _recover(self, db) -> None:
        """Roll forward any intent a crash left mid-replacement."""
        for intent in sorted(self.intent_dir.glob("intent-*.json")):
            try:
                steps = json.loads(intent.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            self._apply(steps)
            intent.unlink()
            self._record(db, "recovery", "rolled_forward", [], {})

    def _record(self, db, holder, outcome, keys, results) -> None:
        db.execute("INSERT INTO guard_results(worktree_id,holder,at,outcome,targets,result_hashes) "
                   "VALUES(?,?,?,?,?,?)", (self.worktree_id, holder, self.clock(), outcome,
                                           json.dumps(sorted(keys)), json.dumps(results)))

    def results(self) -> list[dict]:
        db = sqlite3.connect(self.state_path, timeout=self.busy_timeout)
        db.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in db.execute(
                "SELECT * FROM guard_results WHERE worktree_id=? ORDER BY ordering", (self.worktree_id,))]
        finally:
            db.close()

    # -- Git ------------------------------------------------------------------ #

    def git_commit(self, label: str, paths, message: str, *, ttl: float | None = None) -> dict:
        """Commit exactly `paths`, under a worktree-wide lease.

        `git commit --only` records the named paths' working-tree contents and
        leaves every other staged change in the index untouched. Other staged
        paths are reported as an ownership advisory; they are never staged,
        committed, reset or discarded here.
        """
        keys = [self.relative(p) for p in paths]  # Git's index is case-sensitive: real names
        if not keys:
            raise Unsupported("a guarded commit names its paths")
        lease = self.acquire(self.holder(label), [WORKTREE], ttl=ttl)
        try:
            with self._transaction() as db:
                self._fence(db, lease, [WORKTREE])
                staged = self._git("diff", "--cached", "--name-only").splitlines()
                others = sorted(set(staged) - set(keys))
                untracked = [k for k in keys if not self._git("ls-files", "--", k)]
                if untracked:
                    self._git("add", "--", *untracked)  # only the paths this writer named
                self._git("-c", "core.hooksPath=", "commit", "--only", "-q", "-m", message, "--", *keys)
                head = self._git("rev-parse", "HEAD")
                self._record(db, lease.holder, "git_commit", [WORKTREE, *keys], {"HEAD": head})
            return {"head": head, "committed": keys, "other_staged_not_committed": others}
        finally:
            self.release(lease)

    def _git(self, *args) -> str:
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True,
                              text=True).stdout.strip()


def classify_git(command: str) -> str | None:
    """Advisory classification of a shell Git mutation the guard does not perform."""
    words = command.replace("&&", " ").replace(";", " ").replace("|", " ").split()
    valued = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
    for index, word in enumerate(words):
        if not (word.lower().endswith("git") or word.lower().endswith("git.exe")):
            continue
        position = index + 1
        while position < len(words) and words[position].startswith("-"):
            position += 2 if words[position] in valued else 1  # global options before the verb
        verb = words[position] if position < len(words) else ""
        if verb in SUPPORTED_GIT:
            return "git_outside_guard"
        if verb in UNSUPPORTED_GIT:
            return "git_unsupported"
    return None
