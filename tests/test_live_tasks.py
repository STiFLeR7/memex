"""Durable stream ownership and bounded control-plane state."""
import importlib.util

import pytest
from memex.context.live import SessionIdentity
from memex.context.revision import RepositoryView


def session(harness="claude",principal="owner"):
    return SessionIdentity(harness=harness,native_session_id="native",memex_session_id=harness+"-run",principal_id=principal)


@pytest.fixture
def store(tmp_path):
    assert importlib.util.find_spec("memex.runtime.tasks") is not None, "W07 task store missing"
    from memex.runtime.tasks import TaskStore
    return TaskStore(tmp_path/"tasks.sqlite",authenticate=lambda s:s.principal_id=="owner",max_tasks=2)


def create(store,s=None,now=1.0):
    from memex.context.live import PacketItem,PacketBudget
    view=RepositoryView("repo","wt",None,1,1,"sha256:"+"a"*64)
    item=PacketItem(claim_id="c",revision_id="r",assertion="approved behavior",authority="inferred",status="supported",reason="sufficient_support")
    return store.create(s or session(),view,"fix api",("r",), (item,),(),PacketBudget(),now=now,ttl=60.0)


def receipt(frame,s,outcome="host_accepted",sequence=None):
    from memex.context.live import DeliveryReceipt
    return DeliveryReceipt(task_id=frame.task_id,session=s,sequence=sequence or frame.sequence,view_id=frame.view_id,adapter_version="test.v1",accepted_at=2.0,outcome=outcome)


def test_sessions_receipts_and_replay_are_independent(store):
    a=create(store); b=create(store,session("codex"))
    assert a.sequence==b.sequence==1 and a.base_sequence==0
    assert store.pending(a.task_id,session(),now=2.0)==a
    store.ack_delivery(receipt(a,session(),"failed"),now=2.0)
    assert store.pending(a.task_id,session(),now=2.0)==a
    with pytest.raises(PermissionError):store.ack_delivery(receipt(a,session("codex")),now=2.0)
    store.ack_delivery(receipt(a,session()),now=2.0)
    assert store.get(a.task_id,session(),now=2.0).ack_sequence==1
    assert store.get(b.task_id,session("codex"),now=2.0).ack_sequence==0
    with pytest.raises(ValueError):store.ack_delivery(receipt(a,session(),sequence=8),now=2.0)


def test_restart_expiry_retention_and_continuity(store):
    from memex.runtime.tasks import TaskStore
    a=create(store); create(store,session("codex"))
    with pytest.raises(RuntimeError,match="capacity"):create(store)
    other=TaskStore(store.path,authenticate=lambda s:s.principal_id=="owner",max_tasks=2)
    assert other.pending(a.task_id,session(),now=2.0)==a
    assert other.resume(a.task_id,session(),a.continuation_token,now=2.0).task_id==a.task_id
    with pytest.raises(PermissionError):other.resume(a.task_id,session(),"wrong",now=2.0)
    with pytest.raises(PermissionError):other.get(a.task_id,session(principal="stranger"),now=2.0)
    with pytest.raises(ValueError,match="expired"):other.get(a.task_id,session(),now=61.0)
    assert create(other,now=62.0).sequence==1


def test_concurrent_managers_and_ack_preserve_order(store):
    from concurrent.futures import ThreadPoolExecutor
    from memex.runtime.tasks import TaskStore
    a=create(store)
    other=TaskStore(store.path,authenticate=lambda s:True,max_tasks=2)
    with ThreadPoolExecutor() as pool:
        list(pool.map(lambda db:db.ack_delivery(receipt(a,session()),now=2.0),(store,other)))
    assert store.get(a.task_id,session(),now=2.0).ack_sequence==1
    store.close_task(a.task_id,session(),now=2.0)
    with pytest.raises(PermissionError):store.get(a.task_id,session(),now=2.0)


def test_pending_delta_replay_mismatched_base_and_overflow(store):
    from memex.context.live import PacketBudget
    a=create(store); store.ack_delivery(receipt(a,session()),now=2.0)
    state=store.get(a.task_id,session(),now=2.0)
    item=state.baseline[0].model_copy(update={"status":"needs_revalidation","reason":"source_hash_changed"})
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        delta=store.offer_in(db,state,state.view,(item,),(),last_ack=1)
    assert not delta.full and delta.changes[0].previous_revision=="r"
    state=store.get(a.task_id,session(),now=2.0)
    with store.connection() as db:
        again=store.offer_in(db,state,state.view,(item,),(),last_ack=1)
    assert again==delta
    with store.connection() as db:
        resync=store.offer_in(db,state,state.view,(item,),(),last_ack=0)
    assert resync.full and resync.sequence>delta.sequence and resync.replaces_sequence==delta.sequence
    with store.connection() as db:
        db.execute("UPDATE live_tasks SET budget=? WHERE task_id=?",(PacketBudget(max_characters=256).model_dump_json(),a.task_id))
    state=store.get(a.task_id,session(),now=2.0)
    with store.connection() as db:
        overflow=store.offer_in(db,state,state.view,(item,),(),last_ack=1,force_full=True)
    assert overflow.resync_required and not overflow.changes
    with pytest.raises(ValueError):store.ack_delivery(receipt(overflow,session()),now=2.0)


def test_acceptance_refs_and_explicit_renewal(store):
    from memex.context.live import PacketBudget
    view=RepositoryView("repo","wt",None,1,1,"sha256:"+"a"*64)
    f=store.create(session(),view,"fix",(),(),(),PacketBudget(),now=1.0,ttl=60.0,acceptance_refs=("test:api",))
    state=store.get(f.task_id,session(),now=2.0)
    assert state.acceptance_refs==("test:api",)
    with pytest.raises(PermissionError):store.renew(f.task_id,session(),"bad",now=2.0,ttl=30.0)
    renewed=store.renew(f.task_id,session(),f.continuation_token,now=2.0,ttl=30.0)
    assert renewed.expires_at==32.0
    assert store.pending(f.task_id,session(),now=2.0).expires_at==32.0
