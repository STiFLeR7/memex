"""Independent review findings: overflow and nonblocking deadline enforcement."""
from hashlib import sha256
from types import SimpleNamespace
import time

import pytest

from memex.context.live import ActionRequest, DeliveryReceipt, PacketBudget, PacketItem, SessionIdentity
from memex.context.revision import RepositoryView
from memex.runtime.actions import LiveContextEngine
from memex.runtime.coordinator import IndexResult
from memex.runtime.tasks import TaskStore
from memex.runtime.views import SourceCapture


def session():
    return SessionIdentity(harness="fixture",native_session_id="native",memex_session_id="run",principal_id="owner")


def view():return RepositoryView("repo","wt",None,1,1,"sha256:"+"a"*64)


def item(i):return PacketItem(claim_id="c"+str(i),revision_id="r"+str(i),assertion="x",authority="inferred",status="supported",reason="supported")


def test_review_128_retractions_plus_additions_resynchronize(tmp_path):
    store=TaskStore(tmp_path/"runtime.sqlite",authenticate=lambda s:True)
    f=store.create(session(),view(),"task",(),tuple(item(i) for i in range(128)),(),PacketBudget(max_items=128,max_characters=65536),now=1.0,ttl=60.0)
    assert not f.resync_required
    store.ack_delivery(DeliveryReceipt(session=session(),task_id=f.task_id,sequence=f.sequence,view_id=f.view_id,adapter_version="fixture",accepted_at=2.0,outcome="host_accepted"),now=2.0)
    state=store.get(f.task_id,session(),now=2.0)
    with store.connection() as db:
        frame=store.offer_in(db,state,state.view,tuple(item(i) for i in range(128,256)),(),last_ack=state.ack_sequence)
    assert frame.resync_required and not frame.changes
    state=store.get(f.task_id,session(),now=2.0)
    with store.connection() as db:
        full=store.offer_in(db,state,state.view,tuple(item(i) for i in range(128,256)),(),last_ack=state.ack_sequence,force_full=True)
    assert full.full and not full.resync_required and len(full.items)==128


@pytest.mark.asyncio
async def test_review_parser_respects_deadline_and_yields_event_loop(tmp_path,monkeypatch):
    import memex.runtime.actions as actions
    data=b"x=1\n"*100000
    capture=SourceCapture(None,view().manifest_hash,{"api.py":data})
    async def refresh(**_):return IndexResult(view(),{"api.py":"complete"},True)
    async def load(*a,**k):return {},{},True
    registration=SimpleNamespace(root=str(tmp_path),repo_id="repo",worktree_id="wt")
    indexer=SimpleNamespace(registration=registration,refresh=refresh,journal=SimpleNamespace(observe=lambda r,c:view()))
    claims=SimpleNamespace(load=load)
    monkeypatch.setattr(actions,"capture_sources",lambda r:capture)
    tasks=TaskStore(tmp_path/"runtime.sqlite",authenticate=lambda s:True)
    f=tasks.create(session(),view(),"task",(),(),(),PacketBudget(),now=10.0,ttl=60.0)
    tasks.ack_delivery(DeliveryReceipt(session=session(),task_id=f.task_id,sequence=f.sequence,view_id=f.view_id,adapter_version="fixture",accepted_at=10.0,outcome="host_accepted"),now=10.0)
    engine=LiveContextEngine(indexer,claims,tasks,authorize_view=lambda s,r:True,authorize_source=lambda s,p:True,clock=lambda:10.0,deadline_ms=20)
    request=ActionRequest(session=session(),task_id=f.task_id,view=view(),attempt_id="a",action_kind="edit",targets=("api.py",),expected_hashes=(("api.py","sha256:"+sha256(data).hexdigest()),),last_acknowledged=1,scope_complete=True,context_retained=True)
    before=time.monotonic()
    check=await engine.check_action(request)
    elapsed=time.monotonic()-before
    assert elapsed<0.25, f"20 ms deadline blocked loop for {elapsed:.3f}s"
    assert check.outcome=="unavailable"


@pytest.mark.asyncio
async def test_review_coverage_overflow_is_explicit_resync(tmp_path,monkeypatch):
    tasks=TaskStore(tmp_path/"runtime.sqlite",authenticate=lambda s:True)
    f=tasks.create(session(),view(),"task",(),(),(),PacketBudget(),now=10.0,ttl=60.0)
    tasks.ack_delivery(DeliveryReceipt(session=session(),task_id=f.task_id,sequence=f.sequence,view_id=f.view_id,adapter_version="fixture",accepted_at=10.0,outcome="host_accepted"),now=10.0)
    registration=SimpleNamespace(root=str(tmp_path),repo_id="repo",worktree_id="wt")
    engine=LiveContextEngine(SimpleNamespace(registration=registration),None,tasks,authorize_view=lambda s,r:True,authorize_source=lambda s,p:True,clock=lambda:10.0)
    async def project(*a):return view(),(),{f"support{i}.py":"complete" for i in range(1024)},{},True
    monkeypatch.setattr(engine,"_project",project)
    check=await engine.check_action(ActionRequest(session=session(),task_id=f.task_id,view=view(),attempt_id="a",action_kind="create",targets=("new.py",),expected_hashes=(("new.py",None),),last_acknowledged=1,scope_complete=True,context_retained=True))
    assert check.outcome=="resync_required" and len(check.coverage)<=1024
