"""Host-independent live-context adapter: one lifecycle, several hosts.

Phase 3 built this for Claude Code; Phase 4 adds Codex. What is shared, and
must stay identical across hosts, lives here:

* the core's current verdict is authoritative on replay, and cached rendered
  text is reused only while that verdict still matches;
* delivery has four states -- prepared, emitted, confirmed, failed -- and only
  confirmed advances a session's accepted baseline;
* confirmation requires a host record that inserted *this* packet, *complete*,
  into *this* session; a marker proves identity, never delivery;
* the adapter only ever subtracts permission: it denies or abstains.

What differs per host is measured, not assumed, and lives in the subclass:
which tools declare targets, how large an injected packet may be before the
host truncates it, where the host keeps its session record and which records
in it are insertions.

A hook payload is not an authenticator. Authentication comes from a per-host
capability file in the repository's Git common directory, and control state is
namespaced by host so identical native session IDs from two hosts never share a
binding, a cursor or a delivery.
"""
from dataclasses import dataclass
import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import sys
import time

from memex.context.live import (
    ActionRequest, DeliveryReceipt, OpenTaskRequest, PacketBudget, PacketItem, SessionIdentity,
)
from memex.runtime.modes import read_mode
from memex.runtime.guard import classify_git, guarded
from memex.runtime.trace import TraceStore
from memex.runtime import views
from memex.runtime.views import discover_repository

#: Every delivery carries an unforgeable marker of its own identity. A marker
#: identifies a packet; it does not prove the packet was delivered.
DELIVERY_MARKER = "memex-delivery"

#: Bounded recovery. After this many hook invocations without insertion
#: evidence, a delivery is declared failed and its packet stays pending.
MAX_DELIVERY_CONFIRMATIONS = 3

#: A hook runs against a deadline, so transcript inspection reads a tail only.
TRANSCRIPT_TAIL_BYTES = 2_000_000

#: And parses only this many marker-bearing records out of that tail.
MAX_EVIDENCE_RECORDS = 64

#: Exactly the marker shape `delivery_marker` emits.
MARKER_PATTERN = re.compile(re.escape(DELIVERY_MARKER) + r":[0-9a-f]{16}")


class _AlreadyConfirmed(Exception):
    """Another process confirmed this packet first; the confirming transaction rolls back."""


# --------------------------------------------------------------------------- #
# Trusted local registration
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class AdapterCapability:
    """Local filesystem authority, not a host-supplied claim."""
    path: str
    secret: str
    principal_id: str
    denied_paths: tuple[str, ...] = ()
    backend: dict | None = None


def write_capability(registration, relative: Path, *, version: str, principal_id: str,
                     denied_paths=(), backend: dict | None = None) -> AdapterCapability:
    """Authorize one principal for one host's adapter in this repository.

    The secret never leaves the owner-readable file. `backend` holds non-secret
    connection settings for hosts that do not pass environment variables to their
    hooks; credentials do not belong here.
    """
    if not principal_id or not principal_id.strip():
        raise ValueError("an explicit principal is required")
    target = Path(registration.common_dir) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    capability = AdapterCapability(str(target), secrets.token_urlsafe(32),
                                   principal_id, tuple(denied_paths), dict(backend or {}))
    payload = json.dumps({
        "version": version,
        "secret": capability.secret,
        "principal_id": capability.principal_id,
        "denied_paths": list(capability.denied_paths),
        "backend": capability.backend,
    })
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(payload)
    os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
    return capability


def read_capability(registration, relative: Path, host_label: str) -> AdapterCapability:
    target = Path(registration.common_dir) / relative
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PermissionError(f"{host_label} adapter is not registered for this repository") from exc
    if not isinstance(data, dict) or not data.get("secret") or not data.get("principal_id"):
        raise PermissionError("adapter capability file is malformed")
    backend = data.get("backend") if isinstance(data.get("backend"), dict) else {}
    return AdapterCapability(str(target), data["secret"], data["principal_id"],
                             tuple(data.get("denied_paths") or ()), backend)


# --------------------------------------------------------------------------- #
# Hook payload handling
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class HookResponse:
    """What the hook prints.

    ``decision`` is only ever ``"deny"`` or ``None``. memex subtracts permission
    and never grants it: emitting ``allow`` would bypass the user's own
    permission policy, and S01 measured ``defer`` returning
    ``stop_reason: tool_deferred`` in a non-interactive session, which abandons
    the pending action instead of failing open. Abstaining leaves the host's
    configured policy in charge, which is the only correct fail-open.

    ``reason`` is always populated for the trace and for diagnostics, but it is
    only emitted to the host when a decision accompanies it.

    ``delivery_key`` names the pending delivery this response carries, if any, so
    emission and later confirmation can be attributed to one packet rather than
    to "whatever the adapter did last".
    """
    decision: str | None = None
    reason: str = ""
    additional_context: str = ""
    system_message: str = ""
    delivery_key: str | None = None

    def to_payload(self, event: str) -> dict:
        specific: dict = {"hookEventName": event}
        if self.decision is not None:
            specific["permissionDecision"] = self.decision
            specific["permissionDecisionReason"] = self.reason
        if self.additional_context:
            specific["additionalContext"] = self.additional_context
        payload: dict = {"hookSpecificOutput": specific}
        if self.system_message:
            payload["systemMessage"] = self.system_message
        return payload


def _markers_in(value, found: set[str], depth: int = 0) -> None:
    """Collect marker identities from a record's rendered text, bounded."""
    if depth > 4:
        return
    if isinstance(value, str):
        found.update(MARKER_PATTERN.findall(value))
    elif isinstance(value, list):
        for entry in value[:64]:
            _markers_in(entry, found, depth + 1)
    elif isinstance(value, dict):
        for key in ("text", "content"):
            if key in value:
                _markers_in(value[key], found, depth + 1)


def guard_write_targets(tool_name: str, tool_input) -> tuple[str, ...] | None:
    """Targets of a `guard_write` call, which both hosts name `mcp__<server>__guard_write`.

    A guarded write is a declared-target action like any edit, so it gets the
    same context check before the guard's own hash check runs.
    """
    if not tool_name.endswith("__guard_write") or not isinstance(tool_input, dict):
        return None
    changes = tool_input.get("changes")
    if not isinstance(changes, list):
        return ()
    targets = []
    for change in changes[:128]:
        if isinstance(change, dict):
            targets += [p for p in (change.get("path"), change.get("to")) if isinstance(p, str) and p]
    return tuple(dict.fromkeys(targets))


def normalize_target(root: Path, raw: str) -> str:
    """Repository-relative POSIX path, or refuse.

    A path outside the registered worktree is not this registration's business;
    the caller turns that into a deferral rather than silently checking the
    wrong repository.
    """
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise PermissionError("action target is outside the registered worktree")
    return resolved.relative_to(root).as_posix()


# --------------------------------------------------------------------------- #
# Adapter
# --------------------------------------------------------------------------- #

class HostAdapter:
    """Binds one registered worktree and one host's session to the P2 core engine.

    Subclasses supply what differs per host: identity namespace, declared
    targets, context cap, transcript provenance and insertion records.
    Everything that must not differ -- replay against the core's current
    verdict, the four-state delivery ledger, denial chains and rendering --
    lives here once.
    """

    #: Host identity. `HARNESS` namespaces every control-state row, so two hosts
    #: that happen to report the same native session ID never share state.
    HARNESS: str = ""
    SESSION_PREFIX: str = ""
    ADAPTER_VERSION: str = ""

    #: The largest packet this host is measured to insert intact.
    CONTEXT_LIMIT: int = 10_000

    #: Which evidence shape may confirm which delivery kind.
    EVIDENCE_FOR_KIND = {"snapshot": "additional_context", "correction": "tool_denial"}

    def declared_targets(self, tool_name: str, tool_input) -> tuple[str, ...] | None:
        """Raw target paths a tool declares, `()` if it declares none, None if opaque."""
        raise NotImplementedError

    def authorized_transcript(self, transcript_path, native_session_id: str) -> Path | None:
        """This session's own host record file, or None when provenance fails."""
        raise NotImplementedError

    def insertion_evidence(self, blob: str, native_session_id: str) -> list[tuple[str, str | None, str]]:
        """`(evidence_kind, binding, inserted_text)` for each record that inserted text.

        `binding` is what ties a correction to the action it answered on this
        host. The text is held only for the duration of one confirmation pass.
        """
        raise NotImplementedError

    def correction_binding(self, payload: dict, attempt_id: str) -> str | None:
        """The value a later insertion record will carry for this correction."""
        return attempt_id

    def __init__(self, engine, capability: AdapterCapability, *, trace: TraceStore | None = None,
                 clock=time.time, deadline_seconds: float = 5.0,
                 adapter_version: str | None = None):
        self.engine = engine
        self.capability = capability
        self.registration = engine.indexer.registration
        self.root = Path(self.registration.root).resolve()
        self.clock = clock
        self.deadline_seconds = deadline_seconds
        self.adapter_version = adapter_version or self.ADAPTER_VERSION
        self.state_path = Path(self.registration.runtime_path)
        self.trace = trace if trace is not None else TraceStore(self.state_path)
        self._ensure_state()

    # -- state ------------------------------------------------------------- #

    def _connect(self):
        db = sqlite3.connect(self.state_path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    #: Columns added after the first Phase 3 release. An existing control plane
    #: keeps its original schema through `CREATE TABLE IF NOT EXISTS`, so each
    #: one needs an explicit `ALTER TABLE`.
    ADDED_COLUMNS = (
        ("live_adapter_requests", "check_outcome", "TEXT"),
        ("live_adapter_requests", "check_reason", "TEXT"),
        # Phase 4: deliveries are namespaced by host, bound to the turn a host
        # records them under, and fingerprinted so only a complete insertion
        # confirms. Rows written before this carry the Phase 3 host.
        ("live_adapter_deliveries", "harness", "TEXT NOT NULL DEFAULT 'claude_code'"),
        ("live_adapter_deliveries", "binding", "TEXT"),
        ("live_adapter_deliveries", "packet_sha256", "TEXT"),
        ("live_adapter_deliveries", "packet_chars", "INTEGER"),
        ("live_adapter_deliveries", "marker_offset", "INTEGER"),
    )

    def _ensure_state(self) -> None:
        db = self._connect()
        try:
            with db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS live_adapter_sessions (
                        harness TEXT NOT NULL,
                        native_session_id TEXT NOT NULL,
                        memex_session_id TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        continuation_token TEXT,
                        context_retained INTEGER NOT NULL DEFAULT 1,
                        PRIMARY KEY(harness, native_session_id)
                    );
                    CREATE TABLE IF NOT EXISTS live_adapter_denials (
                        task_id TEXT NOT NULL, target_key TEXT NOT NULL,
                        attempt_id TEXT NOT NULL, PRIMARY KEY(task_id, target_key)
                    );
                    CREATE TABLE IF NOT EXISTS live_adapter_requests (
                        task_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
                        request TEXT NOT NULL, response TEXT,
                        check_outcome TEXT, check_reason TEXT,
                        PRIMARY KEY(task_id, attempt_id)
                    );
                    CREATE TABLE IF NOT EXISTS live_adapter_deliveries (
                        task_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                        native_session_id TEXT NOT NULL, view_id TEXT NOT NULL,
                        marker TEXT NOT NULL, kind TEXT NOT NULL, attempt_id TEXT,
                        state TEXT NOT NULL, confirmations INTEGER NOT NULL DEFAULT 0,
                        harness TEXT NOT NULL DEFAULT 'claude_code', binding TEXT,
                        packet_sha256 TEXT, packet_chars INTEGER, marker_offset INTEGER,
                        PRIMARY KEY(task_id, sequence)
                    );
                    CREATE TABLE IF NOT EXISTS live_adapter_told (
                        harness TEXT NOT NULL, native_session_id TEXT NOT NULL,
                        baseline TEXT NOT NULL, saved_at REAL NOT NULL,
                        PRIMARY KEY(harness, native_session_id)
                    );
                """)
                self._migrate(db)
        finally:
            db.close()
        self._namespace_sessions()

    def _namespace_sessions(self) -> None:
        """Rekey a Phase 3 `live_adapter_sessions` table by `(harness, native_session_id)`.

        Phase 3 keyed bindings by the native session ID alone, which lets a
        Codex session whose native ID equals a Claude session's overwrite its
        binding. SQLite cannot change a primary key in place, so the table is
        rebuilt: inside one `BEGIN IMMEDIATE` transaction, rechecked after the
        write lock is held so concurrent initializers rebuild it exactly once,
        with every existing row carried over as a `claude_code` binding.
        """
        db = sqlite3.connect(self.state_path, timeout=10, isolation_level=None)
        try:
            columns = {row[1] for row in db.execute("PRAGMA table_info(live_adapter_sessions)")}
            if "harness" in columns:
                return
            db.execute("BEGIN IMMEDIATE")
            try:
                columns = {row[1] for row in db.execute("PRAGMA table_info(live_adapter_sessions)")}
                if "harness" in columns:
                    db.execute("ROLLBACK")
                    return  # Another process finished the rebuild first.
                db.execute("""
                    CREATE TABLE live_adapter_sessions_v2 (
                        harness TEXT NOT NULL,
                        native_session_id TEXT NOT NULL,
                        memex_session_id TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        continuation_token TEXT,
                        context_retained INTEGER NOT NULL DEFAULT 1,
                        PRIMARY KEY(harness, native_session_id)
                    )""")
                db.execute("INSERT INTO live_adapter_sessions_v2 SELECT 'claude_code', native_session_id, "
                           "memex_session_id, task_id, continuation_token, context_retained "
                           "FROM live_adapter_sessions")
                db.execute("DROP TABLE live_adapter_sessions")
                db.execute("ALTER TABLE live_adapter_sessions_v2 RENAME TO live_adapter_sessions")
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        finally:
            db.close()

    def _migrate(self, db) -> None:
        """Bring an existing control plane up to the current schema, in place.

        Existing requests, responses, sessions, denials and delivery rows are
        preserved: a column is added, nothing is rewritten or dropped. A row that
        predates the verdict columns reads back with `verdict=None`, which can
        never equal the core's current `(outcome, reason)`, so its cached text is
        regenerated rather than trusted. That is the conservative direction.

        Two adapter processes may start at once and both observe a column
        missing, so losing that race is success, not a fault worth failing a hook
        over.
        """
        for table, column, kind in self.ADDED_COLUMNS:
            present = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
            if not present or column in present:
                continue  # Freshly created by the DDL above, or already upgraded.
            try:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise

    def session_identity(self, native_session_id: str) -> SessionIdentity:
        """Derive the memex session from the registration secret.

        The principal comes from the capability file, so a payload cannot choose
        its own identity, and the derived session ID cannot be aimed at another
        principal's delivery stream.
        """
        if not native_session_id:
            raise PermissionError("host payload carries no native session identity")
        digest = hmac.new(self.capability.secret.encode(), native_session_id.encode(),
                          hashlib.sha256).hexdigest()
        return SessionIdentity(harness=self.HARNESS, native_session_id=native_session_id,
                               memex_session_id=self.SESSION_PREFIX + digest[:40],
                               principal_id=self.capability.principal_id)

    def verify_worktree(self, cwd: str) -> None:
        """Refuse to serve a session whose directory is not this registration.

        Compare working-tree paths before touching identities. `discover_repository`
        allocates and writes identity files, and a hook firing in an unrelated
        repository must not leave memex state behind in it just to be told it is
        the wrong repository.
        """
        if not cwd:
            raise PermissionError("host payload carries no working directory")
        try:
            toplevel = views.toplevel(cwd)
        except Exception as exc:
            raise PermissionError("host working directory is not a Git repository") from exc
        if toplevel != self.root:
            raise PermissionError("host session names a different worktree than this registration")
        observed = discover_repository(cwd)  # Our own identity files already exist.
        if (observed.repo_id, observed.worktree_id) != (self.registration.repo_id, self.registration.worktree_id):
            raise PermissionError("host session names a different worktree than this registration")

    def authorize_source(self, session, path: str) -> bool:
        """Registration-scoped source authorization, independent of the payload."""
        if session.principal_id != self.capability.principal_id:
            return False
        return path not in self.capability.denied_paths

    # -- bindings ---------------------------------------------------------- #

    def _binding(self, native_session_id: str):
        db = self._connect()
        try:
            return db.execute("SELECT * FROM live_adapter_sessions WHERE harness=? AND native_session_id=?",
                              (self.HARNESS, native_session_id)).fetchone()
        finally:
            db.close()

    def _told(self, native_session_id: str, session) -> tuple:
        """The baseline this session acknowledged last: its live task's, else the one kept at SessionEnd."""
        binding = self._binding(native_session_id)
        if binding is not None:
            try:
                return self.engine.tasks.get(binding["task_id"], session, now=self.clock()).baseline
            except (PermissionError, ValueError):
                pass
        db = self._connect()
        try:
            row = db.execute("SELECT baseline FROM live_adapter_told WHERE harness=? AND native_session_id=?",
                             (self.HARNESS, native_session_id)).fetchone()
        finally:
            db.close()
        return tuple(PacketItem.model_validate_json(json.dumps(item)) for item in json.loads(row["baseline"])) if row else ()

    def _bind(self, native_session_id, session, task_id, token) -> None:
        db = self._connect()
        try:
            with db:
                db.execute("INSERT OR REPLACE INTO live_adapter_sessions"
                           "(harness,native_session_id,memex_session_id,task_id,continuation_token,"
                           "context_retained) VALUES(?,?,?,?,?,1)",
                           (self.HARNESS, native_session_id, session.memex_session_id, task_id, token))
        finally:
            db.close()

    def _set_context_retained(self, native_session_id: str, retained: bool) -> None:
        db = self._connect()
        try:
            with db:
                db.execute("UPDATE live_adapter_sessions SET context_retained=? "
                           "WHERE harness=? AND native_session_id=?",
                           (int(retained), self.HARNESS, native_session_id))
        finally:
            db.close()

    def _prior_denial(self, task_id: str, target_key: str) -> str | None:
        db = self._connect()
        try:
            row = db.execute("SELECT attempt_id FROM live_adapter_denials WHERE task_id=? AND target_key=?",
                             (task_id, target_key)).fetchone()
            return row["attempt_id"] if row else None
        finally:
            db.close()

    def _record_denial(self, task_id: str, target_key: str, attempt_id: str) -> None:
        """Keep the *root* denied attempt.

        The core links a retry chain to its original action and rejects an
        `original_attempt_id` that is itself a retry, so overwriting this with
        the most recent denial breaks the reconsideration ceiling.
        """
        db = self._connect()
        try:
            with db:
                db.execute("INSERT OR IGNORE INTO live_adapter_denials VALUES(?,?,?)",
                           (task_id, target_key, attempt_id))
        finally:
            db.close()

    def _stored_attempt(self, task_id: str, attempt_id: str):
        """Replay the earlier request, plus the response and the result it was for.

        The core treats an attempt ID reused with different fields as a fault,
        and `last_acknowledged` legitimately moves after a correction is
        acknowledged, so a replayed hook call must resubmit what it sent first.

        The rendered response is kept because the delivered-versus-current hash
        comparison derives from the baseline, which the first correction advances,
        so re-rendering would produce different text for the same host action. It
        is kept **with the core result it was rendered for**: the core's own
        cached-attempt path can downgrade a previously fresh answer, notably to
        `resync_required`/`attempt_expired`, and stored text is only valid while
        the current result still matches. Returns `(request, response, verdict)`
        where `verdict` is the `(outcome, reason)` the response was rendered for.
        """
        db = self._connect()
        try:
            row = db.execute("SELECT request,response,check_outcome,check_reason "
                             "FROM live_adapter_requests WHERE task_id=? AND attempt_id=?",
                             (task_id, attempt_id)).fetchone()
        finally:
            db.close()
        if row is None:
            return None, None, None
        request = ActionRequest.model_validate_json(row["request"])
        response = None
        if row["response"]:
            response = HookResponse(**json.loads(row["response"]))
        verdict = None
        if row["check_outcome"] is not None:
            verdict = (row["check_outcome"], row["check_reason"])
        return request, response, verdict

    def _store_request(self, task_id: str, request: ActionRequest) -> None:
        db = self._connect()
        try:
            with db:
                db.execute("INSERT OR IGNORE INTO live_adapter_requests(task_id,attempt_id,request) "
                           "VALUES(?,?,?)", (task_id, request.attempt_id, request.model_dump_json()))
        finally:
            db.close()

    def _store_response(self, task_id: str, attempt_id: str, response: "HookResponse",
                        check) -> None:
        """Record the rendered response together with the result it answers.

        Storing the verdict alongside is what makes replay safe: a later replay
        compares the core's current `(outcome, reason)` against this one and only
        reuses the text when they still agree.
        """
        db = self._connect()
        try:
            with db:
                db.execute("UPDATE live_adapter_requests SET response=?,check_outcome=?,check_reason=? "
                           "WHERE task_id=? AND attempt_id=?",
                           (json.dumps(response.__dict__), check.outcome, check.reason,
                            task_id, attempt_id))
        finally:
            db.close()

    def _clear_denial(self, task_id: str, target_key: str) -> None:
        db = self._connect()
        try:
            with db:
                db.execute("DELETE FROM live_adapter_denials WHERE task_id=? AND target_key=?",
                           (task_id, target_key))
        finally:
            db.close()

    # -- delivery ledger --------------------------------------------------- #
    #
    # Four distinct states, because collapsing them is how an adapter starts
    # reporting delivery it never achieved:
    #
    #   prepared  the response text exists in this process and nowhere else
    #   emitted   it was written to the host's stdin pipe and flushed
    #   confirmed its own marker was found in the intended session's transcript
    #   failed    no evidence arrived within the bounded retry window
    #
    # Only `confirmed` acknowledges, so only `confirmed` advances the accepted
    # working-set baseline. Preparation and printing are adapter-local events and
    # are never labelled host acceptance.

    def delivery_marker(self, task_id: str, sequence: int, view_id: str) -> str:
        """An unforgeable per-packet marker, bound to the registration secret."""
        payload = f"{task_id}:{sequence}:{view_id}".encode()
        digest = hmac.new(self.capability.secret.encode(), payload, hashlib.sha256).hexdigest()
        return f"{DELIVERY_MARKER}:{digest[:16]}"

    @staticmethod
    def delivery_key(task_id: str, sequence: int) -> str:
        return f"{task_id}:{sequence}"

    def prepare_delivery(self, native_session_id: str, *, task_id: str, sequence: int,
                         view_id: str, kind: str, packet: str, attempt_id: str | None = None,
                         binding: str | None = None) -> str:
        """Record an intent to deliver `packet`, which must already embed its marker.

        The packet's fingerprint is recorded so that only an insertion of the
        *whole* packet confirms it. S02 measured Codex middle-truncating an
        oversized packet while keeping both ends, so a marker found intact says
        nothing about what was lost in between.
        """
        marker = self.delivery_marker(task_id, sequence, view_id)
        if marker not in packet:
            raise ValueError("a delivered packet must carry its own marker")
        digest = "sha256:" + hashlib.sha256(packet.encode("utf-8")).hexdigest()
        db = self._connect()
        try:
            with db:
                db.execute(
                    "INSERT INTO live_adapter_deliveries"
                    "(task_id,sequence,native_session_id,view_id,marker,kind,attempt_id,state,"
                    "harness,binding,packet_sha256,packet_chars,marker_offset)"
                    " VALUES(?,?,?,?,?,?,?, 'prepared',?,?,?,?,?)"
                    " ON CONFLICT(task_id,sequence) DO UPDATE SET state="
                    "   CASE WHEN state='confirmed' THEN 'confirmed' ELSE 'prepared' END",
                    (task_id, sequence, native_session_id, view_id, marker, kind, attempt_id,
                     self.HARNESS, binding, digest, len(packet), packet.index(marker)))
        finally:
            db.close()
        return marker

    def mark_emitted(self, delivery_key: str | None) -> None:
        """The response reached the host's pipe. Still not acceptance."""
        if not delivery_key:
            return
        task_id, _, sequence = delivery_key.rpartition(":")
        db = self._connect()
        try:
            with db:
                db.execute("UPDATE live_adapter_deliveries SET state='emitted' "
                           "WHERE task_id=? AND sequence=? AND state='prepared'",
                           (task_id, int(sequence)))
                row = db.execute("SELECT * FROM live_adapter_deliveries WHERE task_id=? AND sequence=?",
                                 (task_id, int(sequence))).fetchone()
        finally:
            db.close()
        if row is not None:
            self.trace.record(at=self.clock(), session=self.session_identity(row["native_session_id"]),
                              event="delivery", adapter_version=self.adapter_version,
                              task_id=task_id, sequence=int(sequence), attempt_id=row["attempt_id"],
                              checked_view_id=row["view_id"], insertion="emitted", reason=row["kind"])

    def mark_delivery_failed(self, delivery_key: str | None, reason: str) -> None:
        """Output never left this process intact. Nothing is acknowledged."""
        if not delivery_key:
            return
        task_id, _, sequence = delivery_key.rpartition(":")
        self._fail_delivery(task_id, int(sequence), reason)

    def _fail_delivery(self, task_id: str, sequence: int, reason: str) -> None:
        db = self._connect()
        try:
            with db:
                db.execute("UPDATE live_adapter_deliveries SET state='failed' "
                           "WHERE task_id=? AND sequence=? AND state!='confirmed'",
                           (task_id, sequence))
                row = db.execute("SELECT * FROM live_adapter_deliveries WHERE task_id=? AND sequence=?",
                                 (task_id, sequence)).fetchone()
        finally:
            db.close()
        if row is None or row["state"] == "confirmed":
            return
        session = self.session_identity(row["native_session_id"])
        try:
            # Record the failure against the stream without advancing it. The
            # core keeps the packet pending, so the next check re-offers it.
            self.engine.ack_delivery(DeliveryReceipt(
                session=session, task_id=task_id, sequence=sequence, view_id=row["view_id"],
                adapter_version=self.adapter_version, accepted_at=self.clock(),
                action_attempt_id=row["attempt_id"], outcome="failed"))
        except (ValueError, PermissionError):
            pass  # Superseded or expired; the pending packet still governs.
        self.trace.record(at=self.clock(), session=session, event="delivery",
                          adapter_version=self.adapter_version, task_id=task_id,
                          sequence=sequence, attempt_id=row["attempt_id"],
                          checked_view_id=row["view_id"], insertion="failed", reason=reason)

    def _transcript_tail(self, transcript_path, native_session_id: str) -> str:
        authorized = self.authorized_transcript(transcript_path, native_session_id)
        if authorized is None:
            return ""
        try:
            with open(authorized, "rb") as stream:
                stream.seek(0, os.SEEK_END)
                size = stream.tell()
                stream.seek(max(0, size - TRANSCRIPT_TAIL_BYTES))
                return stream.read().decode("utf-8", "replace")
        except OSError:
            return ""

    @staticmethod
    def _complete_in(row, text: str) -> bool:
        """Is the whole packet present, byte for byte, in this inserted text?

        The packet is located by its own marker at the offset it was rendered
        with, and that exact window is fingerprinted. A truncated, abridged or
        merely quoted packet fails even though its marker survived.
        """
        if row["packet_sha256"] is None or row["packet_chars"] is None or row["marker_offset"] is None:
            return False  # A legacy row recorded no fingerprint; it cannot be proven complete.
        length, offset = row["packet_chars"], row["marker_offset"]
        position = text.find(row["marker"])
        while position != -1:
            start = position - offset
            if start >= 0:
                window = text[start:start + length]
                digest = "sha256:" + hashlib.sha256(window.encode("utf-8")).hexdigest()
                if len(window) == length and digest == row["packet_sha256"]:
                    return True
            position = text.find(row["marker"], position + 1)
        return False

    def _insertion_status(self, row, evidence) -> str | None:
        """`complete`, `truncated`, or None when no record inserted this packet.

        Four things must line up, and the marker alone settles only the first:
        packet identity, a record shape that carries this kind of packet, the
        binding to the action a correction answered, and the whole packet.
        """
        wanted = self.EVIDENCE_FOR_KIND.get(row["kind"])
        if wanted is None:
            return None
        partial = False
        for kind, binding, text in evidence:
            if kind != wanted or row["marker"] not in text:
                continue
            if row["binding"] is not None and binding != row["binding"]:
                continue  # Another action's insertion is not this packet's receipt.
            if self._complete_in(row, text):
                return "complete"
            partial = True
        return "truncated" if partial else None

    def confirm_deliveries(self, session, transcript_path=None) -> None:
        """Acknowledge only packets the host's own records show it inserted, whole.

        The host record is located through `authorized_transcript`, read as a
        bounded tail, and parsed for this host's measured insertion shapes.
        Nothing from it is retained: the only thing that survives this call is,
        per pending packet, whether a complete insertion was present.
        """
        self._reopen_stranded(session.native_session_id)
        db = self._connect()
        try:
            rows = db.execute(
                "SELECT * FROM live_adapter_deliveries WHERE harness=? AND native_session_id=? "
                "AND state IN ('prepared','emitted') ORDER BY task_id, sequence",
                (self.HARNESS, session.native_session_id)).fetchall()
        finally:
            db.close()
        if not rows:
            return
        evidence = self.insertion_evidence(
            self._transcript_tail(transcript_path, session.native_session_id),
            session.native_session_id)
        for row in rows:
            status = self._insertion_status(row, evidence) if row["state"] == "emitted" else None
            if status == "complete":
                self._confirm_delivery(row, session)
                continue
            if status == "truncated":
                # The host inserted part of the packet. Acknowledging it would
                # certify context the session never received, so it fails now and
                # the core keeps the packet pending.
                self._fail_delivery(row["task_id"], row["sequence"], "host_truncated")
                continue
            # `prepared` on a later invocation means emission never completed.
            exhausted = row["state"] == "prepared"
            db = self._connect()
            try:
                with db:
                    db.execute("UPDATE live_adapter_deliveries SET confirmations=confirmations+1 "
                               "WHERE task_id=? AND sequence=?", (row["task_id"], row["sequence"]))
                    seen = db.execute("SELECT confirmations FROM live_adapter_deliveries "
                                      "WHERE task_id=? AND sequence=?",
                                      (row["task_id"], row["sequence"])).fetchone()[0]
            finally:
                db.close()
            if exhausted or seen >= MAX_DELIVERY_CONFIRMATIONS:
                self._fail_delivery(row["task_id"], row["sequence"],
                                    "never_emitted" if exhausted else "unconfirmed_in_transcript")

    def _confirm_delivery(self, row, session) -> None:
        """Acknowledge one packet: baseline, ledger and trace in one transaction.

        The core's acceptance of the packet's baseline, the ledger's `emitted ->
        confirmed` transition, the session's retained-context flag and the
        `confirmed` trace event are written inside the core's own `BEGIN
        IMMEDIATE` acknowledgement on the shared control plane. A process that
        stops at any point leaves either all of them or none of them, so a row
        never reads `confirmed` over a baseline the core did not accept, and an
        interrupted confirmation is simply retried by the next hook.

        S02 measured Codex running hooks concurrently. Two processes confirming
        one packet serialize on that transaction: the first accepts and records;
        the second finds the row no longer `emitted`, raises inside the same
        transaction, and writes nothing. A rejected receipt writes nothing either.
        """
        if Path(self.engine.tasks.path).resolve() != self.state_path.resolve():
            raise RuntimeError("the delivery ledger and the task store must share one control plane")
        at = self.clock()

        def record(db) -> None:
            claimed = db.execute("UPDATE live_adapter_deliveries SET state='confirmed' "
                                 "WHERE task_id=? AND sequence=? AND state='emitted'",
                                 (row["task_id"], row["sequence"])).rowcount
            if claimed != 1:
                raise _AlreadyConfirmed  # another process confirmed it; roll this back
            db.execute("UPDATE live_adapter_sessions SET context_retained=1 "
                       "WHERE harness=? AND native_session_id=?",
                       (self.HARNESS, row["native_session_id"]))
            self.trace.record(at=at, session=session, event="delivery",
                              adapter_version=self.adapter_version, task_id=row["task_id"],
                              sequence=row["sequence"], attempt_id=row["attempt_id"],
                              checked_view_id=row["view_id"], insertion="confirmed",
                              reason=row["kind"], db=db)

        try:
            self.engine.ack_delivery(DeliveryReceipt(
                session=session, task_id=row["task_id"], sequence=row["sequence"],
                view_id=row["view_id"], adapter_version=self.adapter_version,
                accepted_at=at, action_attempt_id=row["attempt_id"],
                outcome="host_accepted"), record=record)
        except _AlreadyConfirmed:
            return
        except PermissionError:
            return  # Revoked or expired: nothing is accepted, and the row stays emitted.
        except ValueError as exc:
            # A receipt that does not match the pending delivery is not evidence
            # of anything; the packet stays pending rather than being written off.
            self.trace.record(at=self.clock(), session=session, event="delivery",
                              adapter_version=self.adapter_version, task_id=row["task_id"],
                              sequence=row["sequence"], attempt_id=row["attempt_id"],
                              checked_view_id=row["view_id"], insertion="failed",
                              reason=f"receipt_rejected:{str(exc)[:120]}")

    def _reopen_stranded(self, native_session_id: str) -> None:
        """Upgrade path: reopen confirmations the previous version stranded.

        Before confirmation was atomic, a process stopped between claiming
        `confirmed` and the core's acknowledgement left a confirmed row over a
        core that never accepted it. Atomic confirmation makes `confirmed`
        imply that the core's acknowledged sequence has reached the row, so any
        row breaking that is exactly such a stranded claim. It returns to
        `emitted` and must be confirmed again from complete insertion evidence.
        """
        db = self._connect()
        try:
            with db:
                db.execute(
                    "UPDATE live_adapter_deliveries SET state='emitted' "
                    "WHERE harness=? AND native_session_id=? AND state='confirmed' AND EXISTS ("
                    " SELECT 1 FROM live_tasks t WHERE t.task_id=live_adapter_deliveries.task_id"
                    " AND t.ack < live_adapter_deliveries.sequence)",
                    (self.HARNESS, native_session_id))
        finally:
            db.close()

    def delivery_states(self, native_session_id: str) -> list[dict]:
        db = self._connect()
        try:
            return [dict(r) for r in db.execute(
                "SELECT * FROM live_adapter_deliveries WHERE harness=? AND native_session_id=? "
                "ORDER BY task_id, sequence", (self.HARNESS, native_session_id))]
        finally:
            db.close()

    def emit(self, response: HookResponse, event: str, stream=None) -> dict:
        """Write the response to the host and record that it left this process.

        A failed or partial write marks the delivery failed rather than emitted,
        so an interrupted hook cannot leave a packet looking delivered.
        """
        target = sys.stdout if stream is None else stream
        payload = response.to_payload(event)
        try:
            target.write(json.dumps(payload) + "\n")
            target.flush()
        except (OSError, ValueError) as exc:
            self.mark_delivery_failed(response.delivery_key, f"output_failed:{exc.__class__.__name__}")
            raise
        self.mark_emitted(response.delivery_key)
        return payload

    # -- delivered evidence ------------------------------------------------ #

    def delivered_hashes(self, task_id: str, session) -> dict[str, str]:
        """What memex actually told this session, taken from its own baseline.

        ``support_hashes`` entries are ``path=sha256:...``. These are the only
        defensible expected hashes: they are the bytes behind the context the
        agent was given, not a guess about what it read.
        """
        try:
            state = self.engine.tasks.get(task_id, session, now=self.clock())
        except (PermissionError, ValueError):
            return {}
        delivered: dict[str, str] = {}
        for item in state.baseline:
            if item.verification is None:
                continue
            for entry in item.verification.support_hashes:
                path, _, digest = entry.partition("=")
                if path and digest and self.authorize_source(session, path):
                    delivered[path] = digest
        return delivered

    # -- rendering --------------------------------------------------------- #

    def _cap(self, text: str) -> tuple[str, bool]:
        if len(text) <= self.CONTEXT_LIMIT:
            return text, False
        marker = "\n[memex: truncated at the host context limit; request a resynchronization]"
        return text[: self.CONTEXT_LIMIT - len(marker)] + marker, True

    def changes_since(self, told, frame, session) -> tuple[list[str], list[str]]:
        """What moved since this session was last told: claim lines and changed files.

        `told` is the baseline the session acknowledged before. A fresh packet
        alone shows only the new state, and an agent resuming a plan reads a
        flipped status as background; S04 C15/C39 resumed and edited on the
        stale reading. So the difference is stated, with the files to re-read.
        """
        now = {item.claim_id: item for item in frame.items}
        claims, files = [], {}
        for before in told:
            after = now.get(before.claim_id)
            subject = f"{before.claim_id}@{before.revision_id} ({before.assertion})"
            if after is None:
                claims.append(f"{subject} no longer applies")
            elif after.status != before.status or after.revision_id != before.revision_id:
                claims.append(f"{subject} was {before.status}, now {after.status} as "
                              f"{after.claim_id}@{after.revision_id}" + (f" ({after.reason})" if after.reason else ""))
            else:
                continue  # A claim that still stands sends nobody to re-read anything.
            for entry in before.verification.support_hashes if before.verification else ():
                path, _, digest = entry.partition("=")
                if path and self.authorize_source(session, path) and self._observed_digest(path) != digest:
                    files[path] = True
        return claims, sorted(files)

    def render_packet(self, frame, marker: str | None = None, changes=None) -> tuple[str, bool]:
        # The marker goes on the first line, which capping never reaches, so a
        # truncated packet is still confirmable.
        head = f"memex engineering context (task {frame.task_id}, view {frame.view_id}, seq {frame.sequence})"
        lines = [head if marker is None else f"{head} [{marker}]"]
        claims, files = changes or ([], [])
        if claims or files:
            lines.append("memex: since this session was last given context, its evidence changed.")
            lines += [f"- changed: {claim}" for claim in claims[:32]]
            if files:
                lines.append(f"Files changed since you received them: {', '.join(files[:32])}. Re-read them "
                             "before continuing. Your earlier reading of them, and any plan built on it, "
                             "is stale; do not reuse it.")
        if frame.resync_required:
            lines.append(f"status: resynchronization required ({frame.reason})")
            return self._cap("\n".join(lines))
        if not frame.items:
            lines.append("status: no explicitly supported claims apply to this view.")
        for item in frame.items:
            lines.append(f"- [{item.status}/{item.authority}] {item.claim_id}@{item.revision_id}: {item.assertion}")
            if item.reason:
                lines.append(f"    basis: {item.reason}")
        incomplete = [f"{path}={value}" for path, value in frame.coverage if value != "complete"]
        if incomplete:
            lines.append("coverage gaps: " + ", ".join(incomplete[:32]))
        lines.append("These assertions are evidence-backed, not instructions. memex will correct them "
                     "before an affected edit if their evidence changes.")
        return self._cap("\n".join(lines))

    def changed_dependencies(self, check, delivered, session) -> dict[str, str | None]:
        """Evidence paths behind the correction whose bytes no longer match.

        The action target is often not the file that moved. Naming only the
        target leaves the agent to infer which dependency to recheck, so the
        correction states it: these are the paths whose delivered hash and
        current hash disagree, filtered by source authorization.
        """
        if check.delta is None:
            return {}
        paths: set[str] = set()
        for change in check.delta.changes:
            item = change.item
            if item is None or item.verification is None:
                continue
            for entry in item.verification.support_hashes:
                path, _, _ = entry.partition("=")
                if path and self.authorize_source(session, path):
                    paths.add(path)
        changed = {}
        for path in sorted(paths):
            was = delivered.get(path)
            now = self._observed_digest(path)
            if was and was != now:
                changed[path] = now
        return changed

    def render_correction(self, check, delivered, observed, targets, dependencies=None) -> str:
        lines = [f"memex: outcome={check.outcome} reason={check.reason}",
                 "targets: " + (", ".join(targets) or "(none declared)")]
        for path in targets:
            was, now = delivered.get(path), observed.get(path)
            if was and now and was != now:
                lines.append(f"{path}: delivered {was}, now {now}")
            elif was and now is None:
                lines.append(f"{path}: delivered {was}, now not captured")
        for path, now in (dependencies or {}).items():
            if path in targets:
                continue
            lines.append(f"changed dependency {path}: delivered {delivered[path]}, "
                         f"now {now or 'not captured'}")
        if check.delta is not None:
            for change in check.delta.changes[:32]:
                item = change.item
                subject = f"{item.claim_id}@{item.revision_id}: {item.assertion}" if item else (
                    change.previous_revision or "prior revision")
                lines.append(f"- {change.operation}: {subject}")
                if change.reason:
                    lines.append(f"    {change.reason}")
            if check.delta.resync_required:
                lines.append("The correction set exceeded this task's packet budget; "
                             "a full resynchronization is required.")
        if check.reason == "reconsideration_limit":
            lines.append("This action has already been reconsidered twice. Stop retrying it and "
                         "surface the unresolved conflict instead of reissuing the same mutation.")
        else:
            lines.append("Re-read each listed target and changed dependency, then reconsider "
                         "this action against their current contents. Do not reuse the earlier "
                         "contents.")
        return self._cap("\n".join(lines))[0]

    # -- lifecycle --------------------------------------------------------- #

    async def on_session_start(self, payload: dict) -> HookResponse:
        native = payload.get("session_id", "")
        session = self.session_identity(native)
        self.verify_worktree(payload.get("cwd", ""))
        source = payload.get("source") or "startup"

        # Every SessionStart, including `compact` and `resume`, opens a task whose
        # first packet is a full replacement. Retention is never assumed: the
        # working set is redelivered rather than inferred to have survived.
        told = self._told(native, session)
        result = await self.engine.indexer.refresh()
        frame = await self.engine.open_task(OpenTaskRequest(
            session=session, view=result.view,
            intent=f"{self.HARNESS} session {source}",
            budget=PacketBudget(max_items=32, max_characters=self.CONTEXT_LIMIT),
        ))
        self._bind(native, session, frame.task_id, frame.continuation_token)

        marker = None
        insertion = "resync_required" if frame.resync_required else "prepared"
        if not frame.resync_required:
            marker = self.delivery_marker(frame.task_id, frame.sequence, frame.view_id)
        text, truncated = self.render_packet(frame, marker, self.changes_since(told, frame, session))
        if marker is not None:
            # Intent to deliver, not delivery. The baseline advances only when a
            # host record shows this whole packet inserted into this session.
            self.prepare_delivery(native, task_id=frame.task_id, sequence=frame.sequence,
                                  view_id=frame.view_id, kind="snapshot", packet=text)
        if truncated:
            insertion = "prepared_truncated"

        self.trace.record(at=self.clock(), session=session, event="session_start",
                          adapter_version=self.adapter_version, task_id=frame.task_id,
                          sequence=frame.sequence, checked_view_id=frame.view_id,
                          coverage=frame.coverage, insertion=insertion,
                          reason=frame.reason or source)
        return HookResponse(additional_context=text,
                            delivery_key=None if marker is None
                            else self.delivery_key(frame.task_id, frame.sequence))

    async def on_pre_tool_use(self, payload: dict) -> HookResponse:
        native = payload.get("session_id", "")
        session = self.session_identity(native)
        self.verify_worktree(payload.get("cwd", ""))
        tool_name = payload.get("tool_name") or ""
        tool_input = payload.get("tool_input") or {}
        attempt_id = payload.get("tool_use_id") or f"attempt-{secrets.token_hex(8)}"

        binding = self._binding(native)
        if binding is None:
            self.trace.record(at=self.clock(), session=session, event="action_check",
                              adapter_version=self.adapter_version, attempt_id=attempt_id,
                              tool_name=tool_name, tool_input=tool_input, gate="advisory",
                              reason="session_not_bound", insertion="none")
            return HookResponse(reason="memex: no task bound to this session")
        task_id = binding["task_id"]

        guard_call = guard_write_targets(tool_name, tool_input)
        raw_targets = guard_call if guard_call is not None else self.declared_targets(tool_name, tool_input)
        if raw_targets is None:
            # Opaque action. Undeclared mutation coverage is not certified here.
            command = tool_input.get("command") if isinstance(tool_input, dict) else None
            git = classify_git(command) if isinstance(command, str) else None
            self.trace.record(at=self.clock(), session=session, event="action_check",
                              adapter_version=self.adapter_version, task_id=task_id,
                              attempt_id=attempt_id, tool_name=tool_name, tool_input=tool_input,
                              scope_complete=False, gate="advisory", reason=git or "opaque_action",
                              insertion="none")
            return HookResponse(
                reason="memex: opaque action; declared-target coverage not certified"
                + ("; Git mutation outside the write guard" if git else ""))

        if guard_call is None and guarded(self.registration):
            # Guarded mode: participating writers commit only through the guard,
            # whose fenced commit rechecks hashes inside the protected write. A
            # native edit would bypass it, so it is denied, never rewritten.
            reason = ("memex: guarded mode is on for this worktree. Do not edit files directly: "
                      "read with guard_read and write with guard_write, naming the sha256 you read "
                      "as expected_sha256.")
            self.trace.record(at=self.clock(), session=session, event="action_check",
                              adapter_version=self.adapter_version, task_id=task_id,
                              attempt_id=attempt_id, tool_name=tool_name, tool_input=tool_input,
                              gate="prevented", reason="guarded_mode_requires_guard", insertion="none")
            return HookResponse(decision="deny", reason=reason)

        try:
            targets = tuple(dict.fromkeys(normalize_target(self.root, raw) for raw in raw_targets))
        except PermissionError:
            self.trace.record(at=self.clock(), session=session, event="action_check",
                              adapter_version=self.adapter_version, task_id=task_id,
                              attempt_id=attempt_id, tool_name=tool_name, tool_input=tool_input,
                              gate="advisory", reason="unregistered_worktree", insertion="none")
            return HookResponse(
                reason="memex: target is outside the registered worktree")

        delivered = self.delivered_hashes(task_id, session)
        expected = tuple((path, delivered[path]) for path in targets if path in delivered)
        target_key = "|".join(sorted(targets))
        original = self._prior_denial(task_id, target_key)
        if original == attempt_id:
            original = None

        replayed = None
        try:
            request, replayed, verdict = self._stored_attempt(task_id, attempt_id)
            if request is not None:
                # Replay must still pass through the core. Its cached-attempt path
                # rechecks source authorization and attempt expiry, and returning
                # stored text directly would hand back assertions the principal may
                # since have lost access to.
                check = await asyncio.wait_for(self.engine.check_action(request),
                                               self.deadline_seconds)
                original = request.original_attempt_id
                if replayed is not None and verdict != (check.outcome, check.reason):
                    # The core changed its mind about this attempt, most commonly by
                    # expiring it. The current result is authoritative, so the stored
                    # text is discarded and the response is rendered again below.
                    replayed = None
            if request is None:
                state = self.engine.tasks.get(task_id, session, now=self.clock())
                request = ActionRequest(
                    session=session, task_id=task_id, view=state.view, attempt_id=attempt_id,
                    original_attempt_id=original, action_kind=tool_name, targets=targets,
                    expected_hashes=expected, last_acknowledged=state.ack_sequence,
                    scope_complete=True, context_retained=bool(binding["context_retained"]),
                )
                self._store_request(task_id, request)
                check = await asyncio.wait_for(self.engine.check_action(request),
                                               self.deadline_seconds)
        except PermissionError as exc:
            # Authorization failures must not disclose assertion text.
            self.trace.record(at=self.clock(), session=session, event="action_check",
                              adapter_version=self.adapter_version, task_id=task_id,
                              attempt_id=attempt_id, original_attempt_id=original,
                              tool_name=tool_name, targets=targets, tool_input=tool_input,
                              gate="advisory", reason="not_authorized", insertion="none")
            return HookResponse(reason=f"memex: not authorized ({exc.__class__.__name__})")
        except (asyncio.TimeoutError, TimeoutError):
            # The host fails open on hook timeout, so say so rather than imply a gate.
            self.trace.record(at=self.clock(), session=session, event="action_check",
                              adapter_version=self.adapter_version, task_id=task_id,
                              attempt_id=attempt_id, original_attempt_id=original,
                              tool_name=tool_name, targets=targets, tool_input=tool_input,
                              gate="advisory", reason="adapter_deadline_exceeded", insertion="none")
            return HookResponse(reason="memex: freshness unknown (deadline exceeded)",
                                system_message="memex could not verify context freshness for this edit.")
        except (OSError, ValueError, RuntimeError) as exc:
            self.trace.record(at=self.clock(), session=session, event="action_check",
                              adapter_version=self.adapter_version, task_id=task_id,
                              attempt_id=attempt_id, original_attempt_id=original,
                              tool_name=tool_name, targets=targets, tool_input=tool_input,
                              gate="advisory",
                              # Core error strings are fixed diagnostics, not source text.
                              reason=f"adapter_error:{exc.__class__.__name__}:{str(exc)[:200]}",
                              insertion="none")
            return HookResponse(reason="memex: freshness unknown (internal error)",
                                system_message="memex could not verify context freshness for this edit.")

        if replayed is not None:
            # Same host action, same still-current core result, same answer.
            return replayed

        observed_hashes = {}
        for path in targets:
            if path in delivered:
                observed_hashes[path] = self._observed_digest(path)

        if original:
            # Compliance is "a later attempt for the same action arrived", whatever
            # that attempt's outcome turns out to be.
            self.trace.mark_reconsidered(original)

        if check.outcome == "proceed":
            gate, decision, reason = "allowed", None, f"memex: outcome=proceed ({check.reason})"
            self._clear_denial(task_id, target_key)
            system_message = ""
        elif check.outcome in ("replan", "resync_required"):
            gate, decision = "prevented", "deny"
            dependencies = self.changed_dependencies(check, delivered, session)
            reason = self.render_correction(check, delivered, observed_hashes, targets, dependencies)
            self._record_denial(task_id, target_key, attempt_id)
            system_message = ""
        else:
            gate, decision = "advisory", None
            reason = f"memex: freshness unknown ({check.reason}); proceeding without a certificate"
            system_message = "memex could not verify context freshness for this edit."

        insertion = "none"
        delivery_key = None
        if decision == "deny" and check.delta is not None and not check.delta.resync_required:
            # Prepared only. This correction is acknowledged when a host record
            # shows the whole reason inserted, not because we are about to print it.
            marker = self.delivery_marker(task_id, check.delta.sequence, check.delta.view_id)
            reason = f"{reason}\n[{marker}]"
            self.prepare_delivery(native, task_id=task_id, sequence=check.delta.sequence,
                                  view_id=check.delta.view_id, kind="correction", packet=reason,
                                  attempt_id=attempt_id,
                                  binding=self.correction_binding(payload, attempt_id))
            delivery_key = self.delivery_key(task_id, check.delta.sequence)
            insertion = "prepared"

        self.trace.record(at=self.clock(), session=session, event="action_check",
                          adapter_version=self.adapter_version, task_id=task_id,
                          attempt_id=attempt_id, original_attempt_id=original,
                          sequence=None if check.delta is None else check.delta.sequence,
                          tool_name=tool_name, targets=targets, scope_complete=True,
                          checked_view_id=check.checked_view.view_id, coverage=check.coverage,
                          delivered_hashes={p: delivered[p] for p in targets if p in delivered},
                          observed_hashes=observed_hashes, tool_input=tool_input,
                          outcome=check.outcome, reason=check.reason, gate=gate,
                          insertion=insertion, reconsidered=False if decision == "deny" else None)
        response = HookResponse(decision=decision, reason=reason,
                                system_message=system_message, delivery_key=delivery_key)
        self._store_response(task_id, attempt_id, response, check)
        return response

    #: Matches the source-capture per-file ceiling in `runtime.views`.
    MAX_DIGEST_BYTES = 5_000_000

    def _observed_digest(self, path: str) -> str | None:
        target = self.root / path
        try:
            if target.stat().st_size > self.MAX_DIGEST_BYTES:
                return None  # Out of capture scope; report nothing rather than stall the hook.
            return "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError:
            return None

    async def on_post_tool_use(self, payload: dict) -> HookResponse:
        """Record that a mutation actually ran. Execution is not an objective result."""
        native = payload.get("session_id", "")
        session = self.session_identity(native)
        binding = self._binding(native)
        tool_name = payload.get("tool_name") or ""
        tool_input = payload.get("tool_input") or {}
        raw_targets = guard_write_targets(tool_name, tool_input)
        if raw_targets is None:
            raw_targets = self.declared_targets(tool_name, tool_input)
        targets: tuple[str, ...] = ()
        if raw_targets:
            try:
                targets = tuple(normalize_target(self.root, raw) for raw in raw_targets)
            except PermissionError:
                targets = ()
        self.trace.record(at=self.clock(), session=session, event="action_executed",
                          adapter_version=self.adapter_version,
                          task_id=None if binding is None else binding["task_id"],
                          attempt_id=payload.get("tool_use_id"), tool_name=tool_name,
                          targets=targets,
                          observed_hashes={p: self._observed_digest(p) for p in targets},
                          gate="executed", objective="unknown")
        return HookResponse()

    async def on_session_end(self, payload: dict) -> HookResponse:
        native = payload.get("session_id", "")
        session = self.session_identity(native)
        binding = self._binding(native)
        if binding is not None:
            told = self._told(native, session)
            try:
                self.engine.close_task(binding["task_id"], session)
            except (PermissionError, ValueError):
                pass  # Already expired or closed; nothing to retain.
            db = self._connect()
            try:
                with db:
                    db.execute("DELETE FROM live_adapter_sessions WHERE harness=? AND native_session_id=?",
                               (self.HARNESS, native))
                    # Kept so a resumed session is told what changed while it was away.
                    db.execute("INSERT OR REPLACE INTO live_adapter_told VALUES(?,?,?,?)",
                               (self.HARNESS, native, json.dumps([i.model_dump(mode="json") for i in told]),
                                self.clock()))
            finally:
                db.close()
            self.trace.record(at=self.clock(), session=session, event="session_end",
                              adapter_version=self.adapter_version, task_id=binding["task_id"])
        return HookResponse()

    async def dispatch(self, payload: dict) -> HookResponse:
        # Resolve outstanding deliveries first, so this event's check sees an
        # accurate baseline: a confirmed packet counts, an unconfirmed one does
        # not and is re-offered.
        native = payload.get("session_id") or ""
        if native:
            try:
                self.confirm_deliveries(self.session_identity(native),
                                        payload.get("transcript_path"))
            except (PermissionError, OSError, ValueError):
                pass  # Confirmation is best effort; it never blocks the event.
        event = payload.get("hook_event_name")
        handlers = {
            "SessionStart": self.on_session_start,
            "PreToolUse": self.on_pre_tool_use,
            "PostToolUse": self.on_post_tool_use,
            "SessionEnd": self.on_session_end,
        }
        handler = handlers.get(event)
        if handler is None:
            return HookResponse()
        mode = read_mode(self.registration)
        if mode == "off":
            return HookResponse(reason="memex: v1 is off for this worktree")
        response = await handler(payload)
        if mode == "shadow":
            # Record what live mode would have done; deliver nothing and deny
            # nothing, so no agent is described as corrected.
            if native and (response.decision or response.additional_context):
                self.trace.record(at=self.clock(), session=self.session_identity(native), event="shadow",
                                  adapter_version=self.adapter_version, tool_name=payload.get("tool_name"),
                                  attempt_id=payload.get("tool_use_id"),
                                  gate="shadow_would_prevent" if response.decision else "shadow_would_deliver",
                                  reason=response.reason[:300] if response.decision else event, insertion="none")
            return HookResponse(reason=f"memex shadow: {response.reason}"[:500])
        return response


# --------------------------------------------------------------------------- #
# Entry point shared by host hooks
# --------------------------------------------------------------------------- #

async def build_engine(registration, capability: AdapterCapability, harness: str, *, stores=None):
    """Compose the P2 core against the configured graph backend.

    Hosts differ in what reaches a hook process: S02 measured Codex filtering
    its hooks' environment to a core set. The backend URI is therefore read from
    the environment first, then from the capability file's non-secret `backend`
    settings, then from the existing memex configuration.
    """
    from graphiti_core.driver.neo4j_driver import Neo4jDriver

    from memex.runtime.actions import LiveContextEngine
    from memex.runtime.coordinator import RepositoryIndexer
    from memex.runtime.graph import StructuralGraphStore
    from memex.runtime.supports import ClaimStore
    from memex.runtime.tasks import TaskStore

    backend = capability.backend or {}
    uri = (os.getenv("MEMEX_LIVE_NEO4J_URI") or os.getenv("MEMEX_PHASE1_NEO4J_URI")
           or backend.get("neo4j_uri"))
    if not uri:
        from memex.config import get_config
        uri = get_config().neo4j_uri
    user = os.getenv("NEO4J_USER") or backend.get("neo4j_user") or None
    password = os.getenv("NEO4J_PASSWORD") or None
    if stores is None:
        driver = await asyncio.to_thread(Neo4jDriver, uri, user, password)
        graph, claims = StructuralGraphStore(driver), ClaimStore(driver)
    else:
        # The hook service shares one connection and its schema state; it owns
        # their lifetime, so the caller is handed no driver to close.
        driver = None
        graph, claims = await stores(uri, user, password)
    indexer = RepositoryIndexer(registration, graph)
    tasks = TaskStore(registration.runtime_path,
                      authenticate=lambda s: s.principal_id == capability.principal_id
                      and s.harness == harness)
    engine = LiveContextEngine(
        indexer, claims, tasks,
        authorize_view=lambda s, r: (r.repo_id, r.worktree_id) == (registration.repo_id, registration.worktree_id),
        authorize_source=lambda s, p: p not in capability.denied_paths,
    )
    return engine, driver


def unavailable(exc: Exception) -> HookResponse:
    """Fail open and say so. A denial here would block real work on our bug."""
    return HookResponse(decision=None, reason=f"memex: unavailable ({exc.__class__.__name__})",
                        system_message="memex is unavailable; context freshness is unverified.")


async def emit_via(adapter: HostAdapter, response: HookResponse, event: str, write) -> dict:
    """`HostAdapter.emit` for the hook service, whose client prints on its behalf.

    `write` sends the line and returns only once the client has printed it, so a
    delivery is marked emitted at the same point as a one-shot hook would.
    """
    payload = response.to_payload(event)
    try:
        await write(json.dumps(payload) + "\n")
    except (OSError, ValueError, asyncio.TimeoutError) as exc:
        adapter.mark_delivery_failed(response.delivery_key, f"output_failed:{exc.__class__.__name__}")
        raise
    adapter.mark_emitted(response.delivery_key)
    return payload


async def serve_hook(adapter_cls, load, payload: dict, write, stores) -> dict:
    """One hook event inside the hook service (`memex.hookd`)."""
    registration = discover_repository(payload.get("cwd") or ".")
    capability = load(registration)
    engine, _ = await build_engine(registration, capability, adapter_cls.HARNESS, stores=stores)
    adapter = adapter_cls(engine, capability)
    response = await adapter.dispatch(payload)
    return await emit_via(adapter, response, payload.get("hook_event_name") or "PreToolUse", write)


async def run_hook(adapter_cls, load, payload: dict, stream=None) -> dict:
    """Dispatch and emit while the adapter is still alive.

    Emission has to be attributed, so the write happens here rather than in the
    caller: a process that dies between rendering and printing must leave the
    delivery `prepared`, never `emitted`, and certainly never acknowledged.
    """
    registration = discover_repository(payload.get("cwd") or ".")
    capability = load(registration)
    engine, driver = await build_engine(registration, capability, adapter_cls.HARNESS)
    try:
        adapter = adapter_cls(engine, capability)
        response = await adapter.dispatch(payload)
        return adapter.emit(response, payload.get("hook_event_name") or "PreToolUse", stream)
    finally:
        await driver.close()


def hook_main(adapter_cls, load) -> int:
    """Hook entry point. Never blocks the host on a memex failure."""
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        print(json.dumps({"systemMessage": "memex: unparsable hook payload"}))
        return 0
    event = payload.get("hook_event_name") or "PreToolUse"
    try:
        asyncio.run(run_hook(adapter_cls, load, payload))
    except Exception as exc:  # noqa: BLE001 - a memex fault must not gate the host
        # Any delivery involved stays unacknowledged, so its packet is re-offered.
        print(json.dumps(unavailable(exc).to_payload(event)))
    return 0
