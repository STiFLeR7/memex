"""Bounded local control plane; independent authenticated delivery streams."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import uuid

from memex.context.live import DeltaChange, PacketBudget, PacketItem, SessionIdentity, TaskSnapshot
from memex.context.revision import RepositoryView


@dataclass(frozen=True)
class TaskState:
    task_id: str
    session: SessionIdentity
    view: RepositoryView
    selected: tuple[str, ...]
    ack_sequence: int
    sequence: int
    baseline: tuple[PacketItem, ...]
    pending: TaskSnapshot | None
    budget: PacketBudget
    expires_at: float


def fingerprint(item):
    # Per-view verification stays in graph; equivalent support is quiet across unrelated views.
    return (item.revision_id,item.status,item.authority,item.reason)


class TaskStore:
    def __init__(self,path,*,authenticate,max_tasks=128,max_attempts=128):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.authenticate=authenticate
        self.max_tasks=max_tasks
        self.max_attempts=max_attempts
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS live_tasks (
                    task_id TEXT PRIMARY KEY, session TEXT NOT NULL, view TEXT NOT NULL,
                    intent TEXT NOT NULL, selected TEXT NOT NULL, budget TEXT NOT NULL,
                    expires REAL NOT NULL, continuity_hash TEXT NOT NULL,
                    sequence INTEGER NOT NULL DEFAULT 0, ack INTEGER NOT NULL DEFAULT 0,
                    baseline TEXT NOT NULL DEFAULT '[]', pending TEXT
                );
                CREATE TABLE IF NOT EXISTS live_attempts (
                    task_id TEXT NOT NULL REFERENCES live_tasks(task_id) ON DELETE CASCADE,
                    attempt_id TEXT NOT NULL, request_hash TEXT NOT NULL, root_attempt TEXT NOT NULL,
                    response TEXT NOT NULL, PRIMARY KEY(task_id,attempt_id)
                );
            """)

    @contextmanager
    def connection(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db: yield db
        finally:
            db.close()

    def authorize(self,session):
        if not self.authenticate(session):
            raise PermissionError("unauthenticated principal/session")

    @staticmethod
    def state(row):
        return TaskState(row["task_id"],SessionIdentity.model_validate_json(row["session"]),
            RepositoryView(**json.loads(row["view"])),tuple(json.loads(row["selected"])),
            row["ack"],row["sequence"],tuple(PacketItem.model_validate_json(json.dumps(x)) for x in json.loads(row["baseline"])),
            TaskSnapshot.model_validate_json(row["pending"]) if row["pending"] else None,
            PacketBudget.model_validate_json(row["budget"]),row["expires"])

    def lookup(self,db,task_id,session,now):
        self.authorize(session)  # Authenticate before existence lookup; do not trust a bare host ID.
        row=db.execute("SELECT * FROM live_tasks WHERE task_id=? AND session=?",(task_id,session.model_dump_json())).fetchone()
        if row is None:
            raise PermissionError("task does not belong to this session")
        if row["expires"]<=now:
            raise ValueError("task expired; open a new baseline")
        return row

    def get(self,task_id,session,*,now):
        self.authorize(session)
        with self.connection() as db:
            return self.state(self.lookup(db,task_id,session,now))

    def create(self,session,view,intent,selected,items,coverage,budget,*,now,ttl):
        self.authorize(session)
        if not 0<ttl<=86400 or len(intent)>2048 or len(selected)>128:
            raise ValueError("task intent, selection or lifetime exceeds bounds")
        token=secrets.token_urlsafe(32)
        task_id=str(uuid.uuid4())
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM live_tasks WHERE expires<=?",(now,))
            if db.execute("SELECT count(*) FROM live_tasks").fetchone()[0]>=self.max_tasks:
                raise RuntimeError("active task capacity exceeded")
            from dataclasses import asdict
            db.execute("INSERT INTO live_tasks(task_id,session,view,intent,selected,budget,expires,continuity_hash) VALUES(?,?,?,?,?,?,?,?)",
                (task_id,session.model_dump_json(),json.dumps(asdict(view))," ".join(intent.split()),json.dumps(selected),budget.model_dump_json(),now+ttl,hashlib.sha256(token.encode()).hexdigest()))
            state=self.state(self.lookup(db,task_id,session,now))
            frame=self.offer_in(db,state,view,items,coverage,last_ack=0,force_full=True)
            frame=frame.model_copy(update={"continuation_token":token})
            db.execute("UPDATE live_tasks SET pending=? WHERE task_id=?",(frame.model_dump_json(),task_id))
            return frame

    def pending(self,task_id,session,*,now):
        return self.get(task_id,session,now=now).pending

    def resume(self,task_id,session,token,*,now):
        self.authorize(session)
        with self.connection() as db:
            row=self.lookup(db,task_id,session,now)
            if not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(),row["continuity_hash"]):
                raise PermissionError("continuity not proven")
            return self.state(row)

    def offer_in(self,db,state,view,items,coverage,*,last_ack,force_full=False):
        old={x.revision_id:x for x in state.baseline}
        new={x.revision_id:x for x in items}
        pending=state.pending
        full=force_full or last_ack!=state.ack_sequence
        if pending:
            projected={x.revision_id:x for x in pending.items}
            if last_ack==state.ack_sequence and not force_full and not pending.resync_required and projected==new:
                return pending  # Retry the exact unacknowledged packet, including its original view.
            full=True  # Supersede an undelivered stale packet explicitly.
        changes=[]
        for rid,previous in old.items():
            item=new.get(rid)
            if item is None:
                replacement=next((x for x in items if x.claim_id==previous.claim_id),None)
                changes.append(DeltaChange(operation="replace" if replacement else "retract",previous_revision=rid,
                    revision=replacement.revision_id if replacement else None,item=replacement,
                    reason="Prior revision is obsolete or no longer applicable to this view/action."))
            elif fingerprint(item)!=fingerprint(previous):
                op="conflict" if item.status=="conflicted" else "uncertain" if item.status in ("unknown","needs_revalidation") else "retract" if item.status=="unsupported" else "replace"
                changes.append(DeltaChange(operation=op,previous_revision=rid,revision=rid,item=item,
                    reason="Recheck prior delivered assertion before this action: "+item.reason))
        replaced={x.revision for x in changes}
        for rid,item in new.items():
            if rid not in old and rid not in replaced:
                changes.append(DeltaChange(operation="add",revision=rid,item=item,reason="New applicable explicit revision."))
        if not full and not changes:
            return None
        frame=TaskSnapshot(task_id=state.task_id,view_id=view.view_id,sequence=state.sequence+1,
            base_sequence=state.ack_sequence,full=full,items=tuple(items),changes=() if full else tuple(changes),
            replaces_revisions=tuple(sorted(set(old)|({x.revision_id for x in pending.items} if pending else set()))) if full else (),
            replaces_sequence=state.sequence if full else None,coverage=tuple(coverage),expires_at=state.expires_at,
            reason="Replace the prior packet; do not continue relying on its revisions." if full else "Scoped corrections for this action.")
        if (len(items)>state.budget.max_items or len(changes)>state.budget.max_items or len(frame.model_dump_json())>state.budget.max_characters):
            frame=frame.model_copy(update={"items":(),"changes":(),"replaces_revisions":(),"resync_required":True,"reason":"packet_budget_exceeded; resynchronize with a smaller working set or larger budget"})
        db.execute("UPDATE live_tasks SET sequence=?,pending=? WHERE task_id=?",(frame.sequence,frame.model_dump_json(),state.task_id))
        return frame

    def ack_delivery(self,receipt,*,now):
        self.authorize(receipt.session)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            state=self.state(self.lookup(db,receipt.task_id,receipt.session,now))
            if receipt.sequence==state.ack_sequence:
                return  # A duplicate successful receipt cannot advance another stream.
            frame=state.pending
            if not frame or receipt.sequence!=frame.sequence or receipt.view_id!=frame.view_id:
                raise ValueError("receipt does not match pending delivery")
            if receipt.outcome=="failed":
                return
            if frame.resync_required:
                raise ValueError("incomplete overflow packet cannot establish a delivery baseline")
            db.execute("UPDATE live_tasks SET ack=?,baseline=?,pending=NULL WHERE task_id=?",(frame.sequence,
                json.dumps([x.model_dump(mode="json") for x in frame.items]),state.task_id))

    def close_task(self,task_id,session,*,now):
        self.authorize(session)
        with self.connection() as db:
            self.lookup(db,task_id,session,now)
            db.execute("DELETE FROM live_tasks WHERE task_id=?",(task_id,))
