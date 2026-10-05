"""Bounded delivery/outcome trace. Exposure, compliance and execution stay separate.

Three dimensions are deliberately distinct columns, because conflating them is
how an integration starts claiming success it never measured:

* ``insertion`` is where a packet got to, and the four values are not
  interchangeable: ``prepared`` means the text exists in the adapter and nowhere
  else, ``emitted`` means it was written to the host's pipe, ``confirmed`` means
  the packet's own marker was found in the intended session's transcript, and
  ``failed`` means no evidence arrived in the bounded window. Only ``confirmed``
  is host-confirmed insertion, and only ``confirmed`` advances the accepted
  baseline. Nothing about the agent follows from any of them.
* ``gate`` is what the adapter did to the pending action, and ``reconsidered``
  is whether a later attempt actually arrived. An agent may ignore a
  correction; a prevented write is not a complied-with correction.
* ``objective`` is an externally checkable result, recorded only when something
  actually checked it.

No raw transcript, hidden reasoning, credential or complete tool output is
stored. Tool input is kept as a digest so attempts can be correlated without
retaining ``old_string``/``new_string`` or a shell command body.
"""
from contextlib import contextmanager, nullcontext
import hashlib
import json
from pathlib import Path
import sqlite3

#: Rows are metadata only. Raising this does not make source text safe to store.
MAX_EVENTS_PER_TASK = 4096


def input_digest(tool_input) -> str:
    """Correlate repeated attempts without retaining their text."""
    canonical = json.dumps(tool_input, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TraceStore:
    """Append-only projection in the existing control-plane database."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS live_trace (
                    ordering INTEGER PRIMARY KEY AUTOINCREMENT,
                    at REAL NOT NULL,
                    harness TEXT NOT NULL,
                    native_session_id TEXT NOT NULL,
                    memex_session_id TEXT NOT NULL,
                    task_id TEXT,
                    attempt_id TEXT,
                    original_attempt_id TEXT,
                    sequence INTEGER,
                    adapter_version TEXT NOT NULL,
                    event TEXT NOT NULL,
                    tool_name TEXT,
                    targets TEXT NOT NULL DEFAULT '[]',
                    scope_complete INTEGER,
                    checked_view_id TEXT,
                    coverage_digest TEXT,
                    delivered_hashes TEXT NOT NULL DEFAULT '{}',
                    observed_hashes TEXT NOT NULL DEFAULT '{}',
                    input_digest TEXT,
                    outcome TEXT,
                    reason TEXT,
                    gate TEXT,
                    insertion TEXT,
                    reconsidered INTEGER,
                    objective TEXT,
                    objective_detail TEXT
                );
                CREATE INDEX IF NOT EXISTS live_trace_task ON live_trace(task_id, ordering);
                CREATE INDEX IF NOT EXISTS live_trace_attempt ON live_trace(attempt_id);
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def record(self, *, at, session, event, adapter_version, task_id=None, attempt_id=None,
               original_attempt_id=None, sequence=None, tool_name=None, targets=(),
               scope_complete=None, checked_view_id=None, coverage=None, delivered_hashes=None,
               observed_hashes=None, tool_input=None, outcome=None, reason=None, gate=None,
               insertion=None, reconsidered=None, objective=None, objective_detail=None, db=None) -> int:
        """Append one event and return its monotonic ordering index.

        With `db`, the event is written inside that caller's open transaction on
        this control plane, so it commits or rolls back with the caller's state.
        """
        coverage_digest = None
        if coverage is not None:
            canonical = json.dumps(sorted(dict(coverage).items()), separators=(",", ":"))
            coverage_digest = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
        with nullcontext(db) if db is not None else self.connection() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            if task_id is not None:
                used = db.execute("SELECT count(*) FROM live_trace WHERE task_id=?", (task_id,)).fetchone()[0]
                if used >= MAX_EVENTS_PER_TASK:
                    # Drop the oldest rather than refuse to record current behavior.
                    db.execute(
                        "DELETE FROM live_trace WHERE ordering IN (SELECT ordering FROM live_trace "
                        "WHERE task_id=? ORDER BY ordering LIMIT 1)", (task_id,))
            cursor = db.execute(
                "INSERT INTO live_trace(at,harness,native_session_id,memex_session_id,task_id,attempt_id,"
                "original_attempt_id,sequence,adapter_version,event,tool_name,targets,scope_complete,"
                "checked_view_id,coverage_digest,delivered_hashes,observed_hashes,input_digest,outcome,"
                "reason,gate,insertion,reconsidered,objective,objective_detail) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (at, session.harness, session.native_session_id, session.memex_session_id, task_id,
                 attempt_id, original_attempt_id, sequence, adapter_version, event, tool_name,
                 json.dumps(list(targets)), None if scope_complete is None else int(scope_complete),
                 checked_view_id, coverage_digest, json.dumps(dict(delivered_hashes or {})),
                 json.dumps(dict(observed_hashes or {})),
                 None if tool_input is None else input_digest(tool_input), outcome, reason, gate,
                 insertion, None if reconsidered is None else int(reconsidered), objective, objective_detail))
            return cursor.lastrowid

    def mark_reconsidered(self, attempt_id: str) -> None:
        """A later attempt arrived for this original action; compliance observed."""
        with self.connection() as db:
            db.execute("UPDATE live_trace SET reconsidered=1 WHERE attempt_id=?", (attempt_id,))

    def record_objective(self, attempt_id: str, objective: str, detail: str = "") -> None:
        """Attach an externally checked result to an attempt that already ran."""
        if objective not in ("passed", "failed", "unknown"):
            raise ValueError("objective result must be passed, failed or unknown")
        with self.connection() as db:
            db.execute("UPDATE live_trace SET objective=?,objective_detail=? "
                       "WHERE attempt_id=? AND event='action_check'",
                       (objective, detail[:1024], attempt_id))

    def events(self, *, task_id=None, native_session_id=None) -> list[dict]:
        query = "SELECT * FROM live_trace"
        clauses, params = [], []
        if task_id is not None:
            clauses.append("task_id=?")
            params.append(task_id)
        if native_session_id is not None:
            clauses.append("native_session_id=?")
            params.append(native_session_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        with self.connection() as db:
            return [dict(row) for row in db.execute(query + " ORDER BY ordering", params)]
