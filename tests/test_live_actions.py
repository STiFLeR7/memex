"""P2 exit gate: actual Git bytes → Neo4j support → SQLite session correction."""
import asyncio
from hashlib import sha256
import importlib.util
import os
import subprocess

import pytest
import pytest_asyncio
from graphiti_core.driver.neo4j_driver import Neo4jDriver

from memex.context.live import ClaimRevision, EvidenceRef, SessionIdentity
from memex.runtime.coordinator import RepositoryIndexer
from memex.runtime.graph import StructuralGraphStore
from memex.runtime.supports import ClaimStore
from memex.runtime.tasks import TaskStore
from memex.runtime.views import discover_repository

pytestmark=pytest.mark.integration


def digest(content):return "sha256:"+sha256(content).hexdigest()


def identity(harness="claude"):
    return SessionIdentity(harness=harness,native_session_id="same-native",memex_session_id=harness+"-run",principal_id="owner")


@pytest_asyncio.fixture(loop_scope="function")
async def system(tmp_path):
    assert importlib.util.find_spec("memex.runtime.actions") is not None, "W08 action coordinator missing"
    from memex.runtime.actions import LiveContextEngine
    uri=os.getenv("MEMEX_PHASE1_NEO4J_URI")
    if not uri:pytest.skip("isolated native Neo4j required")
    repo=tmp_path/"repo";repo.mkdir()
    subprocess.run(["git","init",str(repo)],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    (repo/"api.py").write_text("def api(): return 1\n")
    (repo/"other.py").write_text("def other(): return 1\n")
    subprocess.run(["git","-C",str(repo),"add","."],check=True)
    registration=discover_repository(repo)
    driver=await asyncio.to_thread(Neo4jDriver,uri,None,None)
    indexer=RepositoryIndexer(registration,StructuralGraphStore(driver))
    result=await indexer.refresh()
    claims=ClaimStore(driver)
    ev=EvidenceRef(evidence_id="e1",repo_id=registration.repo_id,path="api.py",content_hash=digest((repo/"api.py").read_bytes()),source_kind="source",observed_at=1.0)
    c=ClaimRevision(claim_id="c1",revision_id="r1",repo_id=registration.repo_id,assertion="api returns 1",authority="inferred",support_sets=(("e1",),),observed_at=1.0)
    await claims.put_evidence(ev);await claims.put_claim(c)
    tasks=TaskStore(registration.runtime_path,authenticate=lambda s:s.principal_id=="owner")
    engine=LiveContextEngine(indexer,claims,tasks,authorize_view=lambda s,r:True,authorize_source=lambda s,p:True,clock=lambda:10.0)
    yield engine,repo,c,ev,result.view
    await driver.close()


async def open_task(system,harness="claude",**updates):
    from memex.context.live import OpenTaskRequest
    engine,_,_,_,view=system
    data=dict(session=identity(harness),view=view,intent="fix api",selected_revision_ids=("r1",))
    data.update(updates)
    return await engine.open_task(OpenTaskRequest(**data))


def ack(engine,frame,harness="claude"):
    from memex.context.live import DeliveryReceipt
    engine.ack_delivery(DeliveryReceipt(session=identity(harness),task_id=frame.task_id,sequence=frame.sequence,view_id=frame.view_id,adapter_version="fixture.v1",accepted_at=10.0,outcome="host_accepted"))


def action(system,frame,attempt="a1",harness="claude",**updates):
    from memex.context.live import ActionRequest
    _,_,_,e,view=system
    data=dict(session=identity(harness),task_id=frame.task_id,view=view,attempt_id=attempt,action_kind="edit",
              targets=("api.py",),expected_hashes=(("api.py",e.content_hash),),last_acknowledged=frame.sequence,
              scope_complete=True,context_retained=True)
    data.update(updates)
    return ActionRequest(**data)


@pytest.mark.asyncio
async def test_body_change_correction_is_scoped_and_idempotent(system):
    engine,repo,_,_,_=system
    frame=await open_task(system);assert frame.items[0].status=="supported"
    ack(engine,frame)
    stable=await engine.check_action(action(system,frame))
    assert stable.outcome=="proceed" and stable.delta is None
    (repo/"api.py").write_text("def api(): return 2\n")
    request=action(system,frame,"a2")
    changed=await engine.check_action(request)
    assert changed.outcome=="replan" and changed.delta.changes[0].operation=="uncertain"
    assert changed.delta.changes[0].previous_revision=="r1"
    assert changed.delta.items[0].status=="needs_revalidation"
    assert await engine.check_action(request)==changed
    with pytest.raises(ValueError):await engine.check_action(request.model_copy(update={"targets":("other.py",)}))
    ack(engine,changed.delta)
    result=await engine.claims.driver.execute_query("MATCH (v:MemexVerification {repo_id:$repo}) RETURN v.payload AS payload",params={"repo":system[2].repo_id})
    assert len(result.records)>=2


@pytest.mark.asyncio
async def test_unrelated_changes_quiet_alternative_survives_and_rule_active(system):
    engine,repo,c,e,_=system
    stable=e.model_copy(update={"evidence_id":"e2","path":"other.py","content_hash":digest((repo/"other.py").read_bytes())})
    await engine.claims.put_evidence(stable)
    alt=c.model_copy(update={"revision_id":"r2","support_sets":(("e1",),("e2",)),"supersedes":("r1",)})
    await engine.claims.put_claim(alt)
    frame=await open_task(system);ack(engine,frame)
    (repo/"api.py").write_text("def api(): return 2\n")
    check=await engine.check_action(action(system,frame,expected_hashes=(),targets=("other.py",)))
    assert check.outcome=="proceed" and check.delta is None
    approval=EvidenceRef(evidence_id="approval",repo_id=c.repo_id,source_kind="approval",approver="maintainer",observed_at=1.0)
    await engine.claims.put_evidence(approval,allow_approval=True)
    rule=c.model_copy(update={"claim_id":"rule","revision_id":"rule-r1","assertion":"always use explicit timeouts","authority":"human_approved","support_sets":(("approval",),)})
    await engine.claims.put_claim(rule,allow_approval=True)
    f=await open_task(system,selected_revision_ids=("rule-r1",));ack(engine,f)
    (repo/"api.py").write_text("def api(): return 3\n")
    check=await engine.check_action(action(system,f,attempt="rule-action",expected_hashes=()))
    assert check.outcome=="proceed" and check.delta is None


@pytest.mark.asyncio
async def test_parse_error_opaque_scope_and_missing_target_unknown(system):
    engine,repo,_,_,_=system
    frame=await open_task(system);ack(engine,frame)
    opaque=await engine.check_action(action(system,frame,scope_complete=False,targets=(),expected_hashes=()))
    assert opaque.outcome=="unavailable" and ("$scope","unknown") in opaque.coverage
    (repo/"api.py").write_text("def api(\n")
    changed=await engine.check_action(action(system,frame,"parse"))
    assert changed.outcome=="replan" and changed.delta.items[0].status=="unknown"
    assert ("api.py","parse_error") in changed.coverage


@pytest.mark.asyncio
async def test_two_clients_receive_independent_corrections_after_restart(system):
    from memex.runtime.actions import LiveContextEngine
    engine,repo,_,_,_=system
    a=await open_task(system);b=await open_task(system,"codex");ack(engine,a);ack(engine,b,"codex")
    (repo/"api.py").write_text("def api(): return 2\n")
    changed=await engine.check_action(action(system,a));ack(engine,changed.delta)
    restarted=LiveContextEngine(engine.indexer,engine.claims,TaskStore(engine.tasks.path,authenticate=lambda s:True),authorize_view=lambda s,r:True,authorize_source=lambda s,p:True,clock=lambda:10.0)
    cb=await restarted.check_action(action(system,b,harness="codex"))
    assert cb.outcome=="replan" and cb.delta.base_sequence==1
    assert cb.delta.changes[0].previous_revision=="r1"
    with pytest.raises(PermissionError):await restarted.check_action(action(system,a,harness="codex"))


@pytest.mark.asyncio
async def test_explicit_supersession_future_and_conflict(system):
    engine,_,c,e,_=system
    f=await open_task(system);ack(engine,f)
    future=c.model_copy(update={"revision_id":"future","observed_at":20.0,"supersedes":("r1",)})
    await engine.claims.put_claim(future)
    check=await engine.check_action(action(system,f))
    assert check.outcome=="proceed" and check.delta is None
    successor=c.model_copy(update={"revision_id":"r2","supersedes":("r1",),"assertion":"new approved observation"})
    await engine.claims.put_claim(successor)
    check=await engine.check_action(action(system,f,"next"))
    assert check.outcome=="replan" and check.delta.changes[0].operation=="replace"
    assert check.delta.changes[0].previous_revision=="r1"
    ack(engine,check.delta)
    proposal=c.model_copy(update={"revision_id":"proposal","assertion":"contradiction"})
    await engine.claims.put_claim(proposal)
    check=await engine.check_action(action(system,check.delta,"conflict"))
    assert check.outcome=="replan" and any(i.status=="conflicted" for i in check.delta.items)
    assert all(i.authority=="inferred" for i in check.delta.items)


@pytest.mark.asyncio
async def test_budget_overflow_and_compaction_resync(system):
    from memex.context.live import PacketBudget
    engine,_,_,_,_=system
    small=await open_task(system,budget=PacketBudget(max_characters=256))
    assert small.resync_required
    check=await engine.check_action(action(system,small))
    assert check.outcome=="resync_required"
    f=await open_task(system);ack(engine,f)
    check=await engine.check_action(action(system,f,"compact",context_retained=False))
    assert check.delta.full and check.delta.replaces_revisions==("r1",)
    assert check.outcome=="replan"


@pytest.mark.asyncio
async def test_access_checks_hide_claims_and_precede_lookup(system):
    engine,_,_,_,_=system
    engine.authorize_source=lambda s,p:False
    f=await open_task(system)
    assert not f.items
    engine.authorize_view=lambda s,r:False
    with pytest.raises(PermissionError):await engine.open_task(__import__("memex.context.live",fromlist=["OpenTaskRequest"]).OpenTaskRequest(session=identity(),view=system[4],intent="x"))


@pytest.mark.asyncio
async def test_backend_failure_returns_unknown_not_certificate(system,monkeypatch):
    engine,_,_,_,_=system
    f=await open_task(system);ack(engine,f)
    async def broken(*a,**k):raise ConnectionError("offline")
    monkeypatch.setattr(engine.indexer,"refresh",broken)
    check=await engine.check_action(action(system,f))
    assert check.outcome=="unavailable" and check.reason=="index_unavailable"
    assert check.delta is None


@pytest.mark.asyncio
async def test_subscribe_snapshot_gap_recaptured_and_retry_bound(system,monkeypatch):
    engine,repo,_,_,_=system
    original=engine.tasks.create
    def changing(*a,**k):
        (repo/"api.py").write_text("def api(): return 2\n")
        return original(*a,**k)
    monkeypatch.setattr(engine.tasks,"create",changing)
    f=await open_task(system);ack(engine,f)
    first=await engine.check_action(action(system,f,"original"))
    assert first.outcome=="replan"
    ack(engine,first.delta)
    second=await engine.check_action(action(system,first.delta,"retry-1",original_attempt_id="original"))
    third=await engine.check_action(action(system,first.delta,"retry-2",original_attempt_id="original"))
    fourth=await engine.check_action(action(system,first.delta,"retry-3",original_attempt_id="original"))
    assert second.outcome==third.outcome=="replan"
    assert fourth.outcome=="resync_required" and fourth.reason=="reconsideration_limit"


@pytest.mark.asyncio
async def test_surviving_alternative_with_parse_error_is_sufficient(system):
    engine,repo,c,e,_=system
    other=e.model_copy(update={"evidence_id":"e2","path":"other.py","content_hash":digest((repo/"other.py").read_bytes())})
    await engine.claims.put_evidence(other)
    await engine.claims.put_claim(c.model_copy(update={"revision_id":"r2","supersedes":("r1",),"support_sets":(("e1",),("e2",))}))
    f=await open_task(system);ack(engine,f)
    (repo/"api.py").write_text("def api(\n")
    check=await engine.check_action(action(system,f,targets=("other.py",),expected_hashes=()))
    assert check.outcome=="proceed" and check.delta is None


@pytest.mark.asyncio
async def test_real_driver_exception_fail_open_and_known_hash_mismatch_replan(system,monkeypatch):
    from neo4j.exceptions import ServiceUnavailable
    engine,repo,_,_,_=system
    f=await open_task(system);ack(engine,f)
    async def broken(*a,**k):raise ServiceUnavailable("fixture outage")
    monkeypatch.setattr(engine.claims,"load",broken)
    check=await engine.check_action(action(system,f))
    assert check.outcome=="unavailable"
    (repo/"api.py").write_text("def api(): return 2\n")
    check=await engine.check_action(action(system,f,"drift-outage"))
    assert check.outcome=="replan" and check.reason=="expected_hash_mismatch"
    assert check.delta is None


@pytest.mark.asyncio
async def test_new_file_absence_and_unchanged_unsupported_file_scope(system):
    engine,repo,_,_,_=system
    f=await open_task(system);ack(engine,f)
    check=await engine.check_action(action(system,f,targets=("new.py",),expected_hashes=(("new.py",None),)))
    assert check.outcome=="proceed"
    (repo/"README.md").write_text("unrelated description")
    check=await engine.check_action(action(system,f,"docs-edit"))
    assert check.outcome=="proceed" and check.delta is None


@pytest.mark.asyncio
async def test_two_worktrees_keep_same_claim_verification_separate(system,tmp_path):
    from memex.context.live import OpenTaskRequest
    from memex.runtime.actions import LiveContextEngine
    engine,repo,_,_,_=system
    subprocess.run(["git","-C",str(repo),"-c","user.name=Fixture","-c","user.email=fixture@example.invalid","commit","-m","fixture"],check=True,stdout=subprocess.PIPE)
    branch=tmp_path/"second"
    subprocess.run(["git","-C",str(repo),"worktree","add","--detach",str(branch)],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    (branch/"api.py").write_text("def api(): return 2\n")
    reg=discover_repository(branch)
    idx=RepositoryIndexer(reg,StructuralGraphStore(engine.claims.driver))
    bview=(await idx.refresh()).view
    b=LiveContextEngine(idx,engine.claims,engine.tasks,authorize_view=lambda s,r:True,authorize_source=lambda s,p:True,clock=lambda:10.0)
    bf=await b.open_task(OpenTaskRequest(session=identity("codex"),view=bview,intent="branch edit",selected_revision_ids=("r1",)))
    assert bf.items[0].status=="needs_revalidation"
    af=await open_task(system)
    assert af.items[0].status=="supported"
    ack(engine,af)
    assert (await engine.check_action(action(system,af))).outcome=="proceed"


@pytest.mark.asyncio
async def test_expired_idempotent_certificate_requires_new_attempt(system):
    engine,_,_,_,_=system
    f=await open_task(system);ack(engine,f)
    request=action(system,f)
    assert (await engine.check_action(request)).outcome=="proceed"
    engine.clock=lambda:16.0
    assert (await engine.check_action(request)).outcome=="resync_required"


@pytest.mark.asyncio
async def test_dependency_bound_resync_cannot_be_acked(system):
    engine,_,_,_,_=system
    engine.max_nodes=0
    f=await open_task(system)
    assert f.resync_required
    with pytest.raises(ValueError):ack(engine,f)


@pytest.mark.asyncio
async def test_ignored_existing_target_cannot_be_certified_absent(system):
    engine,repo,_,_,_=system
    (repo/".gitignore").write_text("ignored.py\n")
    (repo/"ignored.py").write_text("existing bytes")
    f=await open_task(system);ack(engine,f)
    check=await engine.check_action(action(system,f,targets=("ignored.py",),expected_hashes=(("ignored.py",None),)))
    assert check.outcome in ("replan","unavailable")


@pytest.mark.asyncio
async def test_authorization_revocation_before_cached_projection(system):
    engine,_,_,_,_=system
    f=await open_task(system);ack(engine,f)
    request=action(system,f)
    assert (await engine.check_action(request)).outcome=="proceed"
    engine.authorize_source=lambda s,p:False
    with pytest.raises(PermissionError):await engine.check_action(request)


@pytest.mark.asyncio
async def test_review_revoked_claim_does_not_bypass_source_acl(system):
    engine,_,c,_,_=system
    revoked=c.model_copy(update={"revision_id":"revoked","supersedes":("r1",),"lifecycle":"revoked","assertion":"PRIVATE ASSERTION"})
    await engine.claims.put_claim(revoked)
    engine.authorize_source=lambda s,p:False
    f=await open_task(system)
    assert all(i.assertion!="PRIVATE ASSERTION" for i in f.items)


@pytest.mark.asyncio
async def test_review_outage_and_replay_do_not_reveal_revoked_source_text(system,monkeypatch):
    engine,_,_,_,_=system
    f=await open_task(system);ack(engine,f)
    engine.authorize_source=lambda s,p:False
    async def broken(*a,**k):raise ConnectionError("offline")
    monkeypatch.setattr(engine.indexer,"refresh",broken)
    request=action(system,f,targets=(),expected_hashes=())
    check=await engine.check_action(request)
    assert check.delta is None or not check.delta.items
    retry=await engine.check_action(request)
    assert retry.delta is None or not retry.delta.items


@pytest.mark.asyncio
async def test_review_outage_preserves_known_pending_replacement(system,monkeypatch):
    engine,_,c,_,_=system
    f=await open_task(system);ack(engine,f)
    await engine.claims.put_claim(c.model_copy(update={"revision_id":"r2","supersedes":("r1",),"assertion":"new context"}))
    changed=await engine.check_action(action(system,f,"replacement"))
    assert changed.delta.changes[0].operation=="replace"
    pending=engine.tasks.pending(f.task_id,identity(),now=10.0)
    async def broken(*a,**k):raise ConnectionError("offline")
    monkeypatch.setattr(engine.indexer,"refresh",broken)
    check=await engine.check_action(action(system,f,"outage",targets=(),expected_hashes=()))
    assert check.outcome=="replan"
    assert engine.tasks.pending(f.task_id,identity(),now=10.0)==pending


@pytest.mark.asyncio
async def test_snapshot_exposes_checked_view_and_verification_records(system):
    engine,_,_,_,_=system
    f=await open_task(system)
    assert f.view==engine.indexer.journal.current(engine.indexer.registration.worktree_id)
    assert f.items[0].verification.view_id==f.view_id==f.view.view_id
    assert f.items[0].verification.evidence_ids==("e1",)
    assert f.items[0].verification.status==f.items[0].status
