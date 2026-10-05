"""Claude Code host adapter: a measured deny-and-reconsider gate.

S01 measured the installed client rather than trusting hook names. The findings
that shape this module:

* ``PreToolUse`` is synchronous and ``permissionDecision:"deny"`` stops the
  pending mutation before it touches disk. That is the gate. Returning ``allow``
  with ``additionalContext`` would let the stale write land first, so this
  adapter never uses that path for a material correction.
* ``deny`` is honored even under ``bypassPermissions``, so the gate does not
  depend on permission policy. This module therefore never edits permissions,
  never auto-approves and never rewrites tool arguments.
* A hook that overruns its configured ``timeout`` does **not** block: the write
  proceeds. So the adapter keeps a hard deadline strictly inside the hook
  timeout and, when it cannot finish, records a fail-open advisory instead of
  pretending it gated anything.
* Parallel edit calls were serialized by the host, each with its own
  check/execute pair. A check therefore certifies its own declared target at its
  own moment. Nothing here protects a file from a writer that arrives after the
  check returns.
* The model verified a correction it could disprove and overrode it. Corrections
  must be true, which is why every denial is backed by a real hash comparison
  against what memex actually delivered to this session.

A hook payload is not an authenticator: anything able to run this entry point
could put a principal in stdin. Authentication comes from a capability file in
the repository's Git common directory, and the payload's ``cwd`` must resolve to
the registered worktree.
"""
from dataclasses import dataclass
import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import sys
import time

from memex.context.live import (
    ActionRequest, DeliveryReceipt, OpenTaskRequest, PacketBudget, SessionIdentity,
)
from memex.runtime.trace import TraceStore
from memex.runtime.views import discover_repository, git_output

ADAPTER_VERSION = "claude-code.v1"
HARNESS = "claude_code"

#: Tools that declare the files they mutate. Everything else is opaque.
DECLARED_EDIT_TOOLS = {
    "Edit": "file_path",
    "Write": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}

#: The host caps injected context at 10,000 characters.
HOST_CONTEXT_LIMIT = 10_000

#: Every delivery carries an unforgeable marker of its own identity. Confirmation
#: means finding *that* marker in *that* session's transcript, which is why a
#: later unrelated hook call, a successful write to stdout or a zero exit code
#: cannot stand in for it: none of them identify a packet.
DELIVERY_MARKER = "memex-delivery"

#: Bounded recovery. After this many hook invocations without transcript
#: evidence, a delivery is declared failed and its correction stays pending.
MAX_DELIVERY_CONFIRMATIONS = 3

#: A hook runs against a deadline, so transcript inspection reads a tail only.
TRANSCRIPT_TAIL_BYTES = 2_000_000

CAPABILITY_RELATIVE = Path("memex") / "adapters" / "claude-code.json"


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


def capability_path(registration) -> Path:
    return Path(registration.common_dir) / CAPABILITY_RELATIVE


def register(root, principal_id: str, *, denied_paths=()) -> AdapterCapability:
    """Authorize one principal for this repository's Claude Code adapter.

    The secret never leaves the owner-readable file. A host session is bound to
    it, so a forged native session ID cannot address another principal's stream.
    """
    if not principal_id or not principal_id.strip():
        raise ValueError("an explicit principal is required")
    registration = discover_repository(root)
    target = capability_path(registration)
    target.parent.mkdir(parents=True, exist_ok=True)
    capability = AdapterCapability(str(target), secrets.token_urlsafe(32),
                                   principal_id, tuple(denied_paths))
    payload = json.dumps({
        "version": ADAPTER_VERSION,
        "secret": capability.secret,
        "principal_id": capability.principal_id,
        "denied_paths": list(capability.denied_paths),
    })
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(payload)
    os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
    return capability


def load_capability(registration) -> AdapterCapability:
    target = capability_path(registration)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PermissionError("Claude Code adapter is not registered for this repository") from exc
    if not isinstance(data, dict) or not data.get("secret") or not data.get("principal_id"):
        raise PermissionError("adapter capability file is malformed")
    return AdapterCapability(str(target), data["secret"], data["principal_id"],
                             tuple(data.get("denied_paths") or ()))


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


def declared_targets(tool_name: str, tool_input: dict) -> tuple[str, ...] | None:
    """Return declared raw target paths, or None for an opaque action."""
    field = DECLARED_EDIT_TOOLS.get(tool_name)
    if field is None:
        return None
    raw = (tool_input or {}).get(field)
    if not isinstance(raw, str) or not raw:
        return ()
    return (raw,)


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

class ClaudeCodeAdapter:
    """Binds one registered worktree and host session to the P2 core engine."""

    def __init__(self, engine, capability: AdapterCapability, *, trace: TraceStore | None = None,
                 clock=time.time, deadline_seconds: float = 5.0,
                 adapter_version: str = ADAPTER_VERSION):
        self.engine = engine
        self.capability = capability
        self.registration = engine.indexer.registration
        self.root = Path(self.registration.root).resolve()
        self.clock = clock
        self.deadline_seconds = deadline_seconds
        self.adapter_version = adapter_version
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
    )

    def _ensure_state(self) -> None:
        db = self._connect()
        try:
            with db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS live_adapter_sessions (
                        native_session_id TEXT PRIMARY KEY,
                        memex_session_id TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        continuation_token TEXT,
                        context_retained INTEGER NOT NULL DEFAULT 1
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
                        PRIMARY KEY(task_id, sequence)
                    );
                """)
                self._migrate(db)
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
        return SessionIdentity(harness=HARNESS, native_session_id=native_session_id,
                               memex_session_id="cc-" + digest[:40],
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
            toplevel = Path(git_output(cwd, "rev-parse", "--show-toplevel")).resolve()
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
            return db.execute("SELECT * FROM live_adapter_sessions WHERE native_session_id=?",
                              (native_session_id,)).fetchone()
        finally:
            db.close()

    def _bind(self, native_session_id, session, task_id, token) -> None:
        db = self._connect()
        try:
            with db:
                db.execute("INSERT OR REPLACE INTO live_adapter_sessions"
                           "(native_session_id,memex_session_id,task_id,continuation_token,context_retained)"
                           " VALUES(?,?,?,?,1)",
                           (native_session_id, session.memex_session_id, task_id, token))
        finally:
            db.close()

    def _set_context_retained(self, native_session_id: str, retained: bool) -> None:
        db = self._connect()
        try:
            with db:
                db.execute("UPDATE live_adapter_sessions SET context_retained=? WHERE native_session_id=?",
                           (int(retained), native_session_id))
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
                         view_id: str, kind: str, attempt_id: str | None = None) -> str:
        """Record an intent to deliver and return the marker to embed."""
        marker = self.delivery_marker(task_id, sequence, view_id)
        db = self._connect()
        try:
            with db:
                db.execute(
                    "INSERT INTO live_adapter_deliveries"
                    "(task_id,sequence,native_session_id,view_id,marker,kind,attempt_id,state)"
                    " VALUES(?,?,?,?,?,?,?, 'prepared')"
                    " ON CONFLICT(task_id,sequence) DO UPDATE SET state="
                    "   CASE WHEN state='confirmed' THEN 'confirmed' ELSE 'prepared' END",
                    (task_id, sequence, native_session_id, view_id, marker, kind, attempt_id))
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

    def _transcript_tail(self, transcript_path: str | None) -> str:
        if not transcript_path:
            return ""
        try:
            with open(transcript_path, "rb") as stream:
                stream.seek(0, os.SEEK_END)
                size = stream.tell()
                stream.seek(max(0, size - TRANSCRIPT_TAIL_BYTES))
                return stream.read().decode("utf-8", "replace")
        except OSError:
            return ""

    def confirm_deliveries(self, session, transcript_path: str | None) -> None:
        """Acknowledge only packets whose own marker is in this session's transcript.

        The transcript is read for one membership test per pending marker and
        nothing from it is retained: no transcript text, no reasoning, no tool
        output. This is the only signal the host exposes that identifies which
        packet entered which session.
        """
        db = self._connect()
        try:
            rows = db.execute(
                "SELECT * FROM live_adapter_deliveries WHERE native_session_id=? "
                "AND state IN ('prepared','emitted') ORDER BY task_id, sequence",
                (session.native_session_id,)).fetchall()
        finally:
            db.close()
        if not rows:
            return
        blob = self._transcript_tail(transcript_path)
        for row in rows:
            if row["state"] == "emitted" and blob and row["marker"] in blob:
                self._confirm_delivery(row, session)
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
        try:
            self.engine.ack_delivery(DeliveryReceipt(
                session=session, task_id=row["task_id"], sequence=row["sequence"],
                view_id=row["view_id"], adapter_version=self.adapter_version,
                accepted_at=self.clock(), action_attempt_id=row["attempt_id"],
                outcome="host_accepted"))
        except ValueError as exc:
            # A receipt that does not match the pending delivery is not evidence
            # of anything; the packet stays pending rather than being written off.
            self.trace.record(at=self.clock(), session=session, event="delivery",
                              adapter_version=self.adapter_version, task_id=row["task_id"],
                              sequence=row["sequence"], attempt_id=row["attempt_id"],
                              checked_view_id=row["view_id"], insertion="failed",
                              reason=f"receipt_rejected:{str(exc)[:120]}")
            return
        except PermissionError:
            return
        db = self._connect()
        try:
            with db:
                db.execute("UPDATE live_adapter_deliveries SET state='confirmed' "
                           "WHERE task_id=? AND sequence=?", (row["task_id"], row["sequence"]))
                db.execute("UPDATE live_adapter_sessions SET context_retained=1 "
                           "WHERE native_session_id=?", (row["native_session_id"],))
        finally:
            db.close()
        self.trace.record(at=self.clock(), session=session, event="delivery",
                          adapter_version=self.adapter_version, task_id=row["task_id"],
                          sequence=row["sequence"], attempt_id=row["attempt_id"],
                          checked_view_id=row["view_id"], insertion="confirmed",
                          reason=row["kind"])

    def delivery_states(self, native_session_id: str) -> list[dict]:
        db = self._connect()
        try:
            return [dict(r) for r in db.execute(
                "SELECT * FROM live_adapter_deliveries WHERE native_session_id=? "
                "ORDER BY task_id, sequence", (native_session_id,))]
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

    @staticmethod
    def _cap(text: str) -> tuple[str, bool]:
        if len(text) <= HOST_CONTEXT_LIMIT:
            return text, False
        marker = "\n[memex: truncated at the host context limit; request a resynchronization]"
        return text[: HOST_CONTEXT_LIMIT - len(marker)] + marker, True

    def render_packet(self, frame, marker: str | None = None) -> tuple[str, bool]:
        # The marker goes on the first line, which capping never reaches, so a
        # truncated packet is still confirmable.
        head = f"memex engineering context (task {frame.task_id}, view {frame.view_id}, seq {frame.sequence})"
        lines = [head if marker is None else f"{head} [{marker}]"]
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
        result = await self.engine.indexer.refresh()
        frame = await self.engine.open_task(OpenTaskRequest(
            session=session, view=result.view,
            intent=f"claude_code session {source}",
            budget=PacketBudget(max_items=32, max_characters=HOST_CONTEXT_LIMIT),
        ))
        self._bind(native, session, frame.task_id, frame.continuation_token)

        marker = None
        insertion = "resync_required" if frame.resync_required else "prepared"
        if not frame.resync_required:
            # Intent to deliver, not delivery. The baseline advances only when this
            # packet's own marker is later found in this session's transcript.
            marker = self.prepare_delivery(native, task_id=frame.task_id,
                                           sequence=frame.sequence, view_id=frame.view_id,
                                           kind="snapshot")
        text, truncated = self.render_packet(frame, marker)
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

        raw_targets = declared_targets(tool_name, tool_input)
        if raw_targets is None:
            # Opaque action. Undeclared mutation coverage is not certified here.
            self.trace.record(at=self.clock(), session=session, event="action_check",
                              adapter_version=self.adapter_version, task_id=task_id,
                              attempt_id=attempt_id, tool_name=tool_name, tool_input=tool_input,
                              scope_complete=False, gate="advisory", reason="opaque_action",
                              insertion="none")
            return HookResponse(
                reason="memex: opaque action; declared-target coverage not certified")

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
            # Prepared only. This correction is acknowledged when its marker shows
            # up in the transcript, not because we are about to print it.
            marker = self.prepare_delivery(native, task_id=task_id,
                                           sequence=check.delta.sequence,
                                           view_id=check.delta.view_id,
                                           kind="correction", attempt_id=attempt_id)
            reason = f"{reason}\n[{marker}]"
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
        raw_targets = declared_targets(tool_name, payload.get("tool_input") or {})
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
            try:
                self.engine.close_task(binding["task_id"], session)
            except (PermissionError, ValueError):
                pass  # Already expired or closed; nothing to retain.
            db = self._connect()
            try:
                with db:
                    db.execute("DELETE FROM live_adapter_sessions WHERE native_session_id=?", (native,))
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
        return await handler(payload)


# --------------------------------------------------------------------------- #
# Hook installation that preserves existing configuration
# --------------------------------------------------------------------------- #

def hook_command(python_executable: str | None = None) -> str:
    executable = python_executable or sys.executable
    return f'"{executable}" -m memex.integrations.claude_code'


def hook_settings(*, timeout: int = 20, python_executable: str | None = None) -> dict:
    """The hook entries memex needs, for review before installation."""
    entry = {"type": "command", "command": hook_command(python_executable), "timeout": timeout}
    return {
        "SessionStart": [{"hooks": [entry]}],
        "PreToolUse": [{"matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash", "hooks": [entry]}],
        "PostToolUse": [{"matcher": "Edit|Write|MultiEdit|NotebookEdit", "hooks": [entry]}],
        "SessionEnd": [{"hooks": [entry]}],
    }


def install_hooks(settings_path, *, timeout: int = 20, python_executable: str | None = None) -> dict:
    """Compose memex hooks into a settings file, preserving everything else.

    Permission policy is never touched, existing hook entries are kept, and a
    repeated install does not duplicate memex entries.
    """
    path = Path(settings_path)
    settings = {}
    if path.exists():
        settings = json.loads(path.read_text(encoding="utf-8") or "{}")
    hooks = settings.setdefault("hooks", {})
    command = hook_command(python_executable)
    for event, groups in hook_settings(timeout=timeout, python_executable=python_executable).items():
        existing = hooks.setdefault(event, [])
        if any(h.get("command") == command for group in existing for h in group.get("hooks", [])):
            continue
        existing.extend(groups)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return settings


def uninstall_hooks(settings_path, *, python_executable: str | None = None) -> dict:
    """Remove only memex entries, leaving other hooks and settings intact."""
    path = Path(settings_path)
    if not path.exists():
        return {}
    settings = json.loads(path.read_text(encoding="utf-8") or "{}")
    command = hook_command(python_executable)
    hooks = settings.get("hooks", {})
    for event in list(hooks):
        groups = []
        for group in hooks[event]:
            remaining = [h for h in group.get("hooks", []) if h.get("command") != command]
            if remaining:
                groups.append({**group, "hooks": remaining})
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    if not hooks:
        settings.pop("hooks", None)
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return settings


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

async def _build_engine(registration, capability):
    """Compose the P2 core against the configured graph backend."""
    from graphiti_core.driver.neo4j_driver import Neo4jDriver

    from memex.runtime.actions import LiveContextEngine
    from memex.runtime.coordinator import RepositoryIndexer
    from memex.runtime.graph import StructuralGraphStore
    from memex.runtime.supports import ClaimStore
    from memex.runtime.tasks import TaskStore

    uri = os.getenv("MEMEX_LIVE_NEO4J_URI") or os.getenv("MEMEX_PHASE1_NEO4J_URI")
    if not uri:
        from memex.config import get_config
        uri = get_config().neo4j_uri
    user = os.getenv("NEO4J_USER") or None
    password = os.getenv("NEO4J_PASSWORD") or None
    driver = await asyncio.to_thread(Neo4jDriver, uri, user, password)
    indexer = RepositoryIndexer(registration, StructuralGraphStore(driver))
    tasks = TaskStore(registration.runtime_path,
                      authenticate=lambda s: s.principal_id == capability.principal_id
                      and s.harness == HARNESS)
    engine = LiveContextEngine(
        indexer, ClaimStore(driver), tasks,
        authorize_view=lambda s, r: (r.repo_id, r.worktree_id) == (registration.repo_id, registration.worktree_id),
        authorize_source=lambda s, p: p not in capability.denied_paths,
    )
    return engine, driver


async def _run(payload: dict, stream=None) -> dict:
    """Dispatch and emit while the adapter is still alive.

    Emission has to be attributed, so the write happens here rather than in
    `main`: a process that dies between rendering and printing must leave the
    delivery `prepared`, never `emitted`, and certainly never acknowledged.
    """
    registration = discover_repository(payload.get("cwd") or ".")
    capability = load_capability(registration)
    engine, driver = await _build_engine(registration, capability)
    try:
        adapter = ClaudeCodeAdapter(engine, capability)
        response = await adapter.dispatch(payload)
        return adapter.emit(response, payload.get("hook_event_name") or "PreToolUse", stream)
    finally:
        await driver.close()


def main(argv=None) -> int:
    """Hook entry point. Never blocks the host on a memex failure."""
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        print(json.dumps({"systemMessage": "memex: unparsable hook payload"}))
        return 0
    event = payload.get("hook_event_name") or "PreToolUse"
    try:
        asyncio.run(_run(payload))
    except Exception as exc:  # noqa: BLE001 - a memex fault must not gate the host
        # Fail open and say so. A denial here would block real work on our bug.
        # Any delivery involved stays unacknowledged, so its packet is re-offered.
        response = HookResponse(
            decision=None,
            reason=f"memex: unavailable ({exc.__class__.__name__})",
            system_message="memex is unavailable; context freshness is unverified.",
        )
        print(json.dumps(response.to_payload(event)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
