"""Opt-in P2 core: deterministic scoped context checks, never a write token."""
import asyncio
from collections import Counter
import hashlib
import time
from pathlib import Path

from neo4j.exceptions import Neo4jError, DriverError

from memex.context.live import ActionCheck, PacketItem
from memex.runtime.indexing import FileStructure
from memex.runtime.parsing import parse_deadline, parse_sources
from memex.runtime.verification import applicable, evaluate
from memex.runtime.views import capture_sources


class LiveContextEngine:
    def __init__(self,indexer,claims,tasks,*,authorize_view,authorize_source,clock=time.time,
                 deadline_ms=5000,max_nodes=256,max_claims=256,max_evidence=1024):
        self.indexer=indexer
        self.claims=claims
        self.tasks=tasks
        self.authorize_view=authorize_view
        self.authorize_source=authorize_source
        self.clock=clock
        self.deadline_ms=deadline_ms
        self.max_nodes=max_nodes
        self.max_claims=max_claims
        self.max_evidence=max_evidence

    def authorize(self,session,view):
        self.tasks.authorize(session)
        registration=self.indexer.registration
        if ((view.repo_id,view.worktree_id)!=(registration.repo_id,registration.worktree_id)
                or not self.authorize_view(session,registration)):
            raise PermissionError("repository/worktree not authorized for this session")

    async def _project(self,session,selected):
        result=await self.indexer.refresh()
        capture=await asyncio.to_thread(capture_sources,self.indexer.registration)
        if (capture.head_commit,capture.manifest_hash)!=(result.view.head_commit,result.view.manifest_hash):
            raise RuntimeError("source changed after index publication")
        if result.view.indexed_generation!=result.view.content_generation or result.reason not in ("","incomplete_coverage"):
            raise RuntimeError("index generation not current")
        revisions,evidence,complete=await self.claims.load(result.view.repo_id,max_claims=self.max_claims,max_evidence=self.max_evidence)
        # Follow explicit claim families, including scoped successors. Never project legacy facts implicitly.
        families={revisions[rid].claim_id for rid in selected if rid in revisions}
        if selected and len({rid for rid in selected if rid in revisions})!=len(set(selected)):
            complete=False
        now=self.clock()
        candidates=[c for c in revisions.values() if applicable(c,result.view,now)]
        superseded={rid for c in candidates for rid in c.supersedes}
        active=[c for c in candidates if c.revision_id not in superseded and (not selected or c.claim_id in families)]
        conflicts=Counter(c.claim_id for c in active if c.lifecycle=="active")
        files={f.path:f for f in await parse_sources(capture.files)}
        items=[]
        coverage={}
        for claim in sorted(active,key=lambda c:c.revision_id):
            verification=evaluate(claim,result.view,evidence,revisions,files,now=now,
                allowed=lambda p:self.authorize_source(session,p),max_nodes=self.max_nodes)
            if "dependency_bound" in verification.reason:
                complete=False
            if not verification.permitted:
                coverage["$authorization"]="unknown"
                continue
            if conflicts[claim.claim_id]>1:
                verification=verification.model_copy(update={"status":"conflicted","reason":"concurrent_unsuperseded_revisions"})
            if "dependency_bound" in verification.reason:
                complete=False
            await self.claims.save_verification(result.view.repo_id,session.principal_id,verification)
            items.append(PacketItem(verification=verification,claim_id=claim.claim_id,revision_id=claim.revision_id,assertion=claim.assertion,
                authority=claim.authority,status=verification.status,reason=verification.reason))
            for eid in verification.evidence_ids:
                e=evidence[eid]
                if e.path and self.authorize_source(session,e.path):
                    coverage[e.path]=files[e.path].coverage if e.path in files else "unavailable"
        after=await asyncio.to_thread(capture_sources,self.indexer.registration)
        if (after.head_commit,after.manifest_hash)!=(capture.head_commit,capture.manifest_hash):
            raise RuntimeError("source changed during verification")
        return result.view,tuple(items),coverage,files,complete and len(items)<=128

    async def _bounded_project(self,session,selected,deadline):
        token=parse_deadline.set(deadline)
        try:
            return await asyncio.wait_for(self._project(session,selected),max(0,deadline-time.monotonic()))
        finally:
            parse_deadline.reset(token)

    async def open_task(self,request):
        self.authorize(request.session,request.view)
        view,items,coverage,_,complete=await self._bounded_project(request.session,request.selected_revision_ids,time.monotonic()+self.deadline_ms/1000)
        if not complete:
            coverage["$dependencies"]="unknown"
        frame=self.tasks.create(request.session,view,request.intent,request.selected_revision_ids,
            items if complete else (),tuple(sorted(coverage.items())),request.budget,now=self.clock(),ttl=request.ttl_seconds,acceptance_refs=request.acceptance_refs)
        if not complete:
            frame=frame.model_copy(update={"resync_required":True,"reason":"dependency_projection_bound_or_missing; no complete snapshot"})
            with self.tasks.connection() as db:
                db.execute("UPDATE live_tasks SET pending=? WHERE task_id=?",(frame.model_dump_json(),frame.task_id))
        return frame

    def ack_delivery(self,receipt,*,record=None):
        state=self.tasks.get(receipt.task_id,receipt.session,now=self.clock())
        self.authorize(receipt.session,state.view)
        self.tasks.ack_delivery(receipt,now=self.clock(),record=record)

    def close_task(self,task_id,session):
        state=self.tasks.get(task_id,session,now=self.clock())
        self.authorize(session,state.view)
        self.tasks.close_task(task_id,session,now=self.clock())

    async def check_action(self,request):
        self.authorize(request.session,request.view)
        state=self.tasks.get(request.task_id,request.session,now=self.clock())
        self.authorize(request.session,state.view)
        for path in set(request.targets)|{p for p,_ in request.expected_hashes}:
            if not self.authorize_source(request.session,path):
                raise PermissionError("action source target is not authorized")
        request_hash=hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        with self.tasks.connection() as db:
            cached=db.execute("SELECT * FROM live_attempts WHERE task_id=? AND attempt_id=?",(state.task_id,request.attempt_id)).fetchone()
            if cached:
                return self._cached(cached,request_hash,request.session)
        failure=""
        files_available=True
        deadline=time.monotonic()+self.deadline_ms/1000
        try:
            view,items,coverage,files,complete=await self._bounded_project(request.session,state.selected,deadline)
        except (OSError,RuntimeError,ValueError,TimeoutError,Neo4jError,DriverError) as exc:
            # No fresh certificate on capture, publication or graph failure.
            failure="index_unavailable"
            view=state.view
            items=()  # Source authorization cannot be re-established during outage.
            files_available=False
            coverage={"$index":"unavailable"}
            files={}
            complete=True
            try:
                capture=await asyncio.wait_for(asyncio.to_thread(capture_sources,self.indexer.registration),max(0,deadline-time.monotonic()))
                files={path:FileStructure(path,"sha256:"+hashlib.sha256(content).hexdigest(),"unavailable",(),(),()) for path,content in capture.files.items()}
                files_available=True
                view=self.indexer.journal.observe(self.indexer.registration,capture)
            except (OSError,RuntimeError,ValueError,TimeoutError):
                pass
            # Keep failure details in server logs, not potentially private source text in a packet.
            import logging
            logging.getLogger(__name__).warning("Live context check unavailable: %s",type(exc).__name__)
        for path in set(request.targets)|{p for p,_ in request.expected_hashes}:
            if not self.authorize_source(request.session,path):
                raise PermissionError("action source target is not authorized")
        root_path=Path(self.indexer.registration.root)
        exists={}
        for path in set(request.targets)|{p for p,_ in request.expected_hashes}:
            target=root_path/path
            if not target.resolve().is_relative_to(root_path):
                raise PermissionError("action target escapes registered worktree")
            exists[path]=target.exists()
        mismatch=False
        for path,expected in request.expected_hashes:
            actual=files[path].content_hash if path in files else "not_captured" if exists[path] else None
            if files_available and actual!=expected:
                mismatch=True
        expected_by_path=dict(request.expected_hashes)
        for path in request.targets:
            coverage[path]=files[path].coverage if path in files else "complete" if not failure and not exists[path] and path in expected_by_path and expected_by_path[path] is None else "unavailable"
        if not request.scope_complete:
            coverage["$scope"]="unknown"
        if not complete:
            coverage["$dependencies"]="unknown"
        if len(coverage)>1024:
            coverage=dict(sorted(coverage.items())[:1023])
            coverage["$coverage_bound"]="unknown"
            complete=False
        with self.tasks.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            state=self.tasks.state(self.tasks.lookup(db,request.task_id,request.session,self.clock()))
            cached=db.execute("SELECT * FROM live_attempts WHERE task_id=? AND attempt_id=?",(state.task_id,request.attempt_id)).fetchone()
            if cached:
                return self._cached(cached,request_hash,request.session)
            count=db.execute("SELECT count(*) FROM live_attempts WHERE task_id=?",(state.task_id,)).fetchone()[0]
            root=request.original_attempt_id or request.attempt_id
            retry_count=0
            if request.original_attempt_id:
                original=db.execute("SELECT root_attempt FROM live_attempts WHERE task_id=? AND attempt_id=?",(state.task_id,request.original_attempt_id)).fetchone()
                if original is None or original[0]!=request.original_attempt_id:
                    raise ValueError("retry must reference a recorded original action")
                retry_count=db.execute("SELECT count(*) FROM live_attempts WHERE task_id=? AND root_attempt=? AND attempt_id!=?",(state.task_id,root,root)).fetchone()[0]
            limit=count>=self.tasks.max_attempts or retry_count>=2
            if limit:
                return ActionCheck(task_id=state.task_id,attempt_id=request.attempt_id,outcome="resync_required",checked_view=view,
                    coverage=tuple(sorted(coverage.items())),reason="reconsideration_limit" if retry_count>=2 else "action_history_capacity",freshness_deadline=self.clock())
            # A backend outage must neither reveal baseline text nor erase pending knowledge.
            delta=None if failure else self.tasks.offer_in(db,state,view,items,tuple(sorted(coverage.items())),last_ack=request.last_acknowledged,
                force_full=not request.context_retained or not complete)
            if not complete and delta:
                delta=delta.model_copy(update={"resync_required":True,"reason":"dependency_projection_bound_or_missing"})
                db.execute("UPDATE live_tasks SET pending=? WHERE task_id=?",(delta.model_dump_json(),state.task_id))
            if delta and delta.resync_required:
                outcome,reason="resync_required",delta.reason
            elif mismatch:
                outcome,reason="replan","expected_hash_mismatch"
            elif failure and state.pending and state.ack_sequence>0:
                outcome,reason="replan","known_correction_pending_backend_unavailable"
            elif failure:
                outcome,reason="unavailable",failure
            elif delta:
                outcome,reason="replan","context_changed_or_delivery_pending"
            elif any(v!="complete" for v in coverage.values()) or any(i.status!="supported" for i in items):
                outcome,reason="unavailable","unknown_or_unsupported_scope"
            else:
                outcome,reason="proceed","checked_declared_scope"
            response=ActionCheck(task_id=state.task_id,attempt_id=request.attempt_id,outcome=outcome,checked_view=view,
                coverage=tuple(sorted(coverage.items())),delta=delta,reason=reason,freshness_deadline=self.clock()+self.deadline_ms/1000)
            db.execute("INSERT INTO live_attempts VALUES(?,?,?,?,?)",(state.task_id,request.attempt_id,request_hash,root,response.model_dump_json()))
            return response

    def _cached(self,row,request_hash,session):
        if row["request_hash"]!=request_hash:
            raise ValueError("action-attempt identity reused with a different request")
        response=ActionCheck.model_validate_json(row["response"])
        for path,_ in response.coverage:
            if not path.startswith("$") and not self.authorize_source(session,path):
                raise PermissionError("cached projection source authorization revoked")
        if response.freshness_deadline<=self.clock():
            return response.model_copy(update={"outcome":"resync_required","reason":"attempt_expired; submit a new action-attempt ID"})
        return response
