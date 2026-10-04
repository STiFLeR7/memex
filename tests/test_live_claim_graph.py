"""Real graph-backed immutable claim/evidence history."""
import asyncio
import importlib.util
import os
import uuid

import pytest
import pytest_asyncio
from graphiti_core.driver.neo4j_driver import Neo4jDriver

from memex.context.live import ClaimRevision, EvidenceRef

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(loop_scope="function")
async def claims():
    assert importlib.util.find_spec("memex.runtime.supports") is not None, "W06 graph store missing"
    from memex.runtime.supports import ClaimStore
    uri = os.getenv("MEMEX_PHASE1_NEO4J_URI")
    if not uri: pytest.skip("isolated native Neo4j required")
    driver = await asyncio.to_thread(Neo4jDriver,uri,None,None)
    yield ClaimStore(driver)
    await driver.close()


def records():
    repo = "p2-" + uuid.uuid4().hex
    e = EvidenceRef(evidence_id="e",repo_id=repo,path="api.py",content_hash="sha256:"+"a"*64,source_kind="source",observed_at=1.0)
    c = ClaimRevision(claim_id="c",revision_id="r",repo_id=repo,assertion="behavior",authority="inferred",support_sets=(("e",),),observed_at=1.0)
    return e,c


@pytest.mark.asyncio
async def test_immutable_concurrent_records_and_explicit_edges(claims):
    e,c = records()
    await asyncio.gather(*(claims.put_evidence(e) for _ in range(3)))
    await claims.put_claim(c)
    with pytest.raises(ValueError,match="immutable"):
        await claims.put_claim(c.model_copy(update={"assertion":"changed"}))
    with pytest.raises(ValueError,match="immutable"):
        await claims.put_evidence(e.model_copy(update={"observed_at":2.0}))
    revisions,ev,complete = await claims.load(c.repo_id)
    assert complete and revisions["r"] == c and ev["e"] == e
    result = await claims.driver.execute_query("MATCH (c:MemexClaimRevision {repo_id:$repo})-[s:USES_EVIDENCE]->() RETURN count(s) AS n",params={"repo":c.repo_id})
    assert result.records[0]["n"] == 1


@pytest.mark.asyncio
async def test_approval_and_supersession_require_authorized_history(claims):
    e,c = records()
    a = EvidenceRef(evidence_id="approval",repo_id=c.repo_id,source_kind="approval",approver="maintainer",observed_at=1.0)
    with pytest.raises(PermissionError): await claims.put_evidence(a)
    await claims.put_evidence(a,allow_approval=True)
    rule = c.model_copy(update={"authority":"human_approved","support_sets":(("approval",),)})
    with pytest.raises(PermissionError): await claims.put_claim(rule)
    await claims.put_claim(rule,allow_approval=True)
    proposal = c.model_copy(update={"revision_id":"r2","supersedes":("r",)})
    with pytest.raises(ValueError): await claims.put_claim(proposal)
    successor = rule.model_copy(update={"revision_id":"r2","supersedes":("r",)})
    await claims.put_claim(successor,allow_approval=True)
    other = successor.model_copy(update={"revision_id":"r3","claim_id":"other","supersedes":("r",)})
    with pytest.raises(ValueError): await claims.put_claim(other,allow_approval=True)


@pytest.mark.asyncio
async def test_missing_evidence_and_bounded_projection(claims):
    e,c = records()
    with pytest.raises(ValueError): await claims.put_claim(c)
    await claims.put_evidence(e)
    await claims.put_claim(c)
    for i in range(3): await claims.put_claim(c.model_copy(update={"revision_id":"r"+str(i)}))
    revisions,_,complete = await claims.load(c.repo_id,max_claims=2)
    assert len(revisions) == 2 and not complete
