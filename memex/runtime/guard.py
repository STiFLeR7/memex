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
  expires and is reclaimable.

Interrupted writes. Before replacing anything, a commit writes an intent file
atomically, recording every step with the target's hash before and after. A
process that stops mid-commit leaves that intent and loses its uncommitted
outcome. **Every** guarded operation -- acquire, read, commit, Git commit, and
the next guard's construction, in any process and on any guard instance that
already exists -- first resolves outstanding intents inside its own `BEGIN
IMMEDIATE` section, before it does anything else:

* the outcome is already recorded: only the leftover files are removed;
* no step was applied: `rolled_back`, staged files removed, nothing changes;
* some or all steps applied, and every target is still exactly in its recorded
  before-or-after state: the remaining steps are applied, `rolled_forward`
  (or `recovered_applied` when nothing remained), under the original holder;
* any target is in neither state -- an external writer changed it -- or a step
  cannot be completed: nothing is written. The intent is held, its targets
  refuse guarded operations with `Unresolved`, and it stays held until the
  files return to a recorded state or an operator calls `discard_intent`.

Established guarantee, for writers that route supported mutations through this
guard on one machine, against process interruption at any point: no guarded
operation proceeds on a target while an interrupted operation on it is
unresolved; a set is never left half-applied once any later guarded operation
has run; recovery never writes over bytes other than the ones the interrupted
commit itself verified, so it cannot overwrite a newer guarded write or an
external edit; a guard read never observes part of a set; each interruption's
resolution is recorded once. This is tested by stopping real processes at each
file boundary. It is **not** a power-loss guarantee: no directory is fsynced,
and the filesystem's rename durability after an OS crash is not tested.
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


class Unresolved(GuardError):
    """An interrupted write on these targets could not be resolved safely and is held."""
    reason = "unresolved_interrupted_write"


#: Outcomes that settle an intent for good.
SETTLED = frozenset({"committed", "rolled_forward", "recovered_applied", "rolled_back", "intent_discarded"})


class _Held(Exception):
    """Recovery must not write: the intent is held with this outcome."""

    def __init__(self, outcome: str, observed: dict):
        super().__init__(outcome)
        self.outcome, self.observed = outcome, observed


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
        with self._transaction(recover=False) as (db, _):
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
                targets TEXT NOT NULL, result_hashes TEXT NOT NULL DEFAULT '{}', intent TEXT)""")
            # Upgrade: a guard_results table from before intents were recorded
            # gains the column; its rows are kept and read back with intent NULL.
            if "intent" not in {row[1] for row in db.execute("PRAGMA table_info(guard_results)")}:
                db.execute("ALTER TABLE guard_results ADD COLUMN intent TEXT")
        with self._transaction():
            pass  # resolve whatever an interrupted writer left

    # -- the critical section ------------------------------------------------ #

    @contextmanager
    def _transaction(self, *, recover: bool = True):
        """`BEGIN IMMEDIATE` on the shared control plane: one writer across processes.

        Yields `(db, held)`, where `held` maps each target of an unresolved
        interrupted write to its intent. Recovery runs first, inside the same
        protected section, and each resolution it makes is committed on its own
        before the caller's work starts, so a caller's refusal cannot undo it.
        """
        db = sqlite3.connect(self.state_path, timeout=self.busy_timeout, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            held = {}
            while True:
                db.execute("BEGIN IMMEDIATE")
                try:
                    changed, settled, held = self._recover(db) if recover else (False, [], {})
                except BaseException:
                    db.execute("ROLLBACK")
                    raise
                if not changed:
                    break
                db.execute("COMMIT")
                for intent in settled:
                    self._discard_files(intent)
            try:
                yield db, held
            except BaseException:
                db.execute("ROLLBACK")
                raise
            db.execute("COMMIT")
        finally:
            db.close()

    @staticmethod
    def _refuse_held(keys, held) -> None:
        blocked = sorted({k for k in keys if k in held or (k == WORKTREE and held) or WORKTREE in held})
        if blocked:
            intents = sorted(set(held.values()))
            raise Unresolved(f"{', '.join(blocked)}: an interrupted guarded write is unresolved "
                             f"(intent {', '.join(intents)}); inspect it, then discard it explicitly")

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
        with self._transaction() as (db, held):
            self._refuse_held(keys, held)
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
        # Releasing writes no file and needs no recovery: every operation that
        # could use the released targets recovers before it proceeds.
        with self._transaction(recover=False) as (db, _):
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

    def read(self, raw: str) -> tuple[str, bytes | None]:
        """A file's bytes as of a point where no guarded set is partly applied.

        The read happens inside the protected section, after recovery, so it can
        neither interleave with a commit's replacements nor see the half of an
        interrupted set that recovery has not completed.
        """
        relative = self.relative(raw)
        with self._transaction() as (_, held):
            self._refuse_held([self.canonical(relative)], held)
            path = self._absolute(relative)
            if not path.exists():
                return relative, None
            if not path.is_file():
                raise Unsupported(f"{relative}: not a regular file")
            return relative, path.read_bytes()

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
            if m.op != "write" and m.expected is None:
                raise Unsupported(f"{m.path}: a {m.op} needs an existing file")
            to = self.relative(m.to) if m.op == "rename" else None
            plan.append((m, self.relative(m.path), to))
        named = [p for _, p, _ in plan] + [t for _, _, t in plan if t]
        keys = {self.canonical(p) for p in named}
        if len(keys) != len(named):
            raise Unsupported("a set names one file more than once")
        intent = None
        try:
            with self._transaction() as (db, held):
                self._refuse_held(keys, held)
                self._fence(db, lease, keys)
                for m, path, to in plan:
                    if self.observed(path) != m.expected:
                        raise StaleWrite(path, m.expected, self.observed(path))
                    if to is not None and self.observed(to) != m.expected_to:
                        raise StaleWrite(to, m.expected_to, self.observed(to))
                intent = self._intent(lease, plan, keys)
                self._write_intent(intent)  # durable before anything visible changes
                self._stage(intent, plan)
                for step in intent["steps"]:
                    self._apply_step(step)
                results = {path: self.observed(path) for path in sorted(named)}
                # Protection is retained through result capture; the lease is
                # released only after the outcome is recorded.
                self._record(db, lease.holder, "committed", keys, results, intent=intent["id"])
        except GuardError as exc:
            with self._transaction() as (db, _):
                self._record(db, lease.holder, exc.reason, keys, {})
            raise
        self._discard_files(intent)  # the outcome is durable; only now is the intent spent
        return results

    def apply(self, label: str, mutations, *, ttl: float | None = None) -> dict:
        """Acquire, commit and release: one bounded guarded write."""
        targets = [m.path for m in mutations] + [m.to for m in mutations if m.op == "rename"]
        lease = self.acquire(self.holder(label), targets, ttl=ttl)
        try:
            return self.commit(lease, mutations)
        finally:
            self.release(lease)

    # -- intents ------------------------------------------------------------- #

    def _intent(self, lease: Lease, plan, keys) -> dict:
        """Every step with its target's hash before and after it, so recovery can tell an
        unapplied step, an applied one, and a file someone else has since changed."""
        intent_id = secrets.token_hex(8)
        steps = []
        for m, path, to in plan:
            if m.op == "write":
                staged = (Path(path).parent / f".{Path(path).name}.memex-{intent_id}").as_posix()
                steps.append({"op": "write", "path": path, "staged": staged,
                              "before": m.expected, "after": sha256(m.content)})
            elif m.op == "delete":
                steps.append({"op": "delete", "path": path, "before": m.expected, "after": None})
            else:
                steps.append({"op": "rename", "path": path, "to": to, "before": m.expected,
                              "before_to": m.expected_to})
        return {"version": 1, "id": intent_id, "holder": lease.holder, "keys": sorted(keys),
                "generations": {k: lease.generations[k] for k in sorted(keys)}, "steps": steps,
                "file": str(self.intent_dir / f"intent-{intent_id}.json")}

    def _write_intent(self, intent: dict) -> None:
        """Atomic: an intent file is either absent or complete, never torn."""
        temporary = self.intent_dir / f"tmp-{intent['id']}.json"
        self._durable(temporary, json.dumps({k: v for k, v in intent.items() if k != "file"}).encode())
        os.replace(temporary, intent["file"])

    def _stage(self, intent: dict, plan) -> None:
        """Write new contents beside their targets. Nothing visible changes yet."""
        for step, (m, _, to) in zip(intent["steps"], plan, strict=True):
            if step["op"] == "write":
                staged = self._absolute(step["staged"])
                staged.parent.mkdir(parents=True, exist_ok=True)
                self._durable(staged, m.content)
            elif step["op"] == "rename":
                self._absolute(to).parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _durable(path: Path, data: bytes) -> None:
        with open(path, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

    def _apply_step(self, step: dict) -> None:
        if step["op"] == "write":
            os.replace(self._absolute(step["staged"]), self._absolute(step["path"]))
        elif step["op"] == "delete":
            os.unlink(self._absolute(step["path"]))
        else:
            os.replace(self._absolute(step["path"]), self._absolute(step["to"]))

    def _peek(self, relative: str) -> str | None:
        """The current hash; a value matching no recorded hash if it is not a regular file."""
        try:
            return self.observed(relative)
        except (Unsupported, OSError):
            return "not-a-regular-file"

    def _step_state(self, step: dict) -> str:
        if step["op"] == "rename":
            source, destination = self._peek(step["path"]), self._peek(step["to"])
            if source is None and destination == step["before"]:
                return "done"
            if source == step["before"] and destination == step["before_to"]:
                return "pending"
            return "conflict"
        current = self._peek(step["path"])
        if current == step["after"]:
            return "done"
        return "pending" if current == step["before"] else "conflict"

    def _observe(self, intent: dict) -> dict:
        paths = {s["path"] for s in intent["steps"]} | {s["to"] for s in intent["steps"] if s.get("to")}
        return {p: self._peek(p) for p in sorted(paths)}

    def _load_intent(self, path: Path) -> dict:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict) and data.get("version") == 1:
            return {**data, "file": str(path)}
        # A previous version's intent: bare steps with no hashes. Whether a newer
        # write followed it cannot be known, so it is held, never replayed.
        legacy = [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []
        keys = set()
        for step in legacy:
            for raw in (step.get("to"), step.get("path")):
                if raw:
                    try:
                        keys.add(self.canonical(raw))
                    except GuardError:
                        keys.add(WORKTREE)
        staged = [s["from"] for s in legacy
                  if s.get("op") == "replace" and ".memex-" in Path(s.get("from", "")).name]
        return {"version": 0, "id": path.stem.removeprefix("intent-"), "holder": "unknown",
                "keys": sorted(keys) or [WORKTREE], "steps": [], "legacy_staged": staged, "file": str(path)}

    def _staged_files(self, intent: dict) -> list[Path]:
        return ([self._absolute(s["staged"]) for s in intent["steps"] if s["op"] == "write"]
                + [Path(p) for p in intent.get("legacy_staged", [])])

    def _discard_files(self, intent: dict) -> None:
        """Remove what an intent left behind. The intent file goes last, so an
        interrupted cleanup is simply repeated by the next recovery."""
        for staged in self._staged_files(intent):
            staged.unlink(missing_ok=True)
        Path(intent["file"]).unlink(missing_ok=True)

    def _resolve(self, intent: dict) -> str:
        """Finish or undo one interrupted commit, or raise `_Held` without writing."""
        if intent["version"] != 1:
            raise _Held("unresolved_legacy_intent", {})
        states = [self._step_state(step) for step in intent["steps"]]
        if "conflict" in states:
            raise _Held("unresolved_conflict", self._observe(intent))
        if all(state == "done" for state in states):
            return "recovered_applied"
        if all(state == "pending" for state in states):
            return "rolled_back"  # nothing visible changed, and the writer never learned of success
        for step, state in zip(intent["steps"], states, strict=True):
            if state == "pending" and step["op"] == "write" and self._peek(step["staged"]) != step["after"]:
                raise _Held("unresolved_conflict", self._observe(intent))
        try:
            for step, state in zip(intent["steps"], states, strict=True):
                if state == "pending":
                    self._apply_step(step)
        except OSError:
            raise _Held("unresolved_io_error", self._observe(intent)) from None
        return "rolled_forward"

    def _recover(self, db):
        """Resolve every interrupted commit. Runs at the start of every protected section.

        Returns `(changed, settled, held)`: whether anything was recorded, the
        intents settled now (their files are removed once this commits), and the
        targets of held intents.
        """
        changed, settled, held = False, [], {}
        for orphan in self.intent_dir.glob("tmp-*.json"):
            orphan.unlink(missing_ok=True)  # a torn intent: its writer had replaced nothing
        for path in sorted(self.intent_dir.glob("intent-*.json")):
            intent = self._load_intent(path)
            recorded = {row[0] for row in db.execute(
                "SELECT outcome FROM guard_results WHERE worktree_id=? AND intent=?",
                (self.worktree_id, intent["id"]))}
            if recorded & SETTLED:
                self._discard_files(intent)  # the outcome is already durable; cleanup was lost
                continue
            try:
                outcome = self._resolve(intent)
            except _Held as hold:
                held.update({key: intent["id"] for key in intent["keys"]})
                if hold.outcome not in recorded:
                    self._record(db, intent["holder"], hold.outcome, intent["keys"], hold.observed,
                                 intent=intent["id"])
                    changed = True
                continue
            self._record(db, intent["holder"], outcome, intent["keys"], self._observe(intent),
                         intent=intent["id"])
            settled.append(intent)
            changed = True
        return changed, settled, held

    def unresolved(self) -> list[dict]:
        """Interrupted writes that are held, with what recovery last observed."""
        with self._transaction() as (db, held):
            found = []
            for intent_id in sorted(set(held.values())):
                row = db.execute("SELECT * FROM guard_results WHERE worktree_id=? AND intent=? "
                                 "ORDER BY ordering DESC LIMIT 1", (self.worktree_id, intent_id)).fetchone()
                found.append({"intent": intent_id, "holder": row["holder"], "outcome": row["outcome"],
                              "targets": json.loads(row["targets"]),
                              "observed": json.loads(row["result_hashes"])})
            return found

    def discard_intent(self, intent_id: str, actor: str) -> None:
        """An operator's explicit decision to abandon a held intent.

        Nothing in the worktree changes: its files stay as they are now, and the
        intent's staged copies are removed. The decision is recorded under the
        actor's name. Only a held intent can be discarded.
        """
        with self._transaction() as (db, held):
            if intent_id not in held.values():
                raise GuardError(f"{intent_id} is not an unresolved interrupted write")
            intent = self._load_intent(self.intent_dir / f"intent-{intent_id}.json")
            self._record(db, actor, "intent_discarded", intent["keys"], self._observe(intent), intent=intent_id)
        self._discard_files(intent)

    def _record(self, db, holder, outcome, keys, results, intent=None) -> None:
        db.execute("INSERT INTO guard_results(worktree_id,holder,at,outcome,targets,result_hashes,intent) "
                   "VALUES(?,?,?,?,?,?,?)", (self.worktree_id, holder, self.clock(), outcome,
                                             json.dumps(sorted(keys)), json.dumps(results), intent))

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
            with self._transaction() as (db, held):
                self._refuse_held([WORKTREE], held)
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


def _mode_path(registration) -> Path:
    return Path(registration.common_dir) / "memex" / "guard-mode.json"


def guarded(registration) -> bool:
    """Is guarded mode on for this worktree? Default mode is context coordination."""
    try:
        modes = json.loads(_mode_path(registration).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(modes, dict) and modes.get(registration.worktree_id) == "guarded"


def set_mode(registration, mode: str) -> None:
    """Switch one worktree between `context` (default) and opt-in `guarded` mode."""
    if mode not in ("context", "guarded"):
        raise ValueError("mode is 'context' or 'guarded'")
    path = _mode_path(registration)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        modes = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        modes = {}
    if mode == "guarded":
        modes[registration.worktree_id] = "guarded"
    else:
        modes.pop(registration.worktree_id, None)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(modes), encoding="utf-8")
    os.replace(temporary, path)


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
