"""Authoritative immutable evidence, revisions and scoped verification in Neo4j."""
import asyncio
import hashlib

from memex.context.live import ClaimRevision, EvidenceRef


class ClaimStore:
    def __init__(self, driver):
        self.driver = driver
        self._lock = asyncio.Lock()
        self._ready = False

    async def ensure_schema(self):
        async with self._lock:
            if self._ready:
                return
            for label, identity in (("MemexEvidence","evidence_id"), ("MemexClaimRevision","revision_id"), ("MemexVerification","verification_id")):
                await self.driver.execute_query(
                    f"CREATE CONSTRAINT {label.lower()}_identity IF NOT EXISTS FOR (n:{label}) REQUIRE (n.repo_id,n.{identity}) IS UNIQUE")
            self._ready = True

    async def _put(self, record, label, identity, *, allow_approval=False):
        # Revalidate even trusted callers using model_copy; serialized authority is not authorization.
        record = type(record).model_validate_json(record.model_dump_json())
        if ((isinstance(record,EvidenceRef) and record.source_kind == "approval")
                or (isinstance(record,ClaimRevision) and record.authority == "human_approved")) and not allow_approval:
            raise PermissionError("approval ingestion requires explicit trusted authorization")
        await self.ensure_schema()
        payload = record.model_dump_json()
        params = {"repo":record.repo_id,"id":getattr(record,identity),"payload":payload}

        async def write(tx):
            result = await tx.run(f"MERGE (n:{label} {{repo_id:$repo,{identity}:$id}}) "
                "SET n.lock_epoch=coalesce(n.lock_epoch,0)+1 RETURN n.payload AS payload",**params)
            old = await result.single()
            if old["payload"] is not None and old["payload"] != payload:
                raise ValueError("immutable record identity already has different contents")
            if isinstance(record,ClaimRevision):
                for eid in {e for group in record.support_sets for e in group}:
                    result = await tx.run("MATCH (e:MemexEvidence {repo_id:$repo,evidence_id:$eid}) RETURN e.payload AS payload",repo=record.repo_id,eid=eid)
                    row = await result.single()
                    if row is None:
                        raise ValueError("missing explicit evidence")
                    e = EvidenceRef.model_validate_json(row["payload"])
                    if record.authority == "human_approved" and e.source_kind == "approval" and e.approver is None:
                        raise ValueError("unapproved approval")
                if record.authority == "human_approved":
                    # Every sufficient proof of approved authority must include an explicit approval.
                    for group in record.support_sets:
                        approvals = await tx.run("MATCH (e:MemexEvidence {repo_id:$repo}) WHERE e.evidence_id IN $ids RETURN e.payload AS payload",repo=record.repo_id,ids=list(group))
                        if not any(EvidenceRef.model_validate_json(r["payload"]).source_kind == "approval" for r in await approvals.data()):
                            raise ValueError("approved claim requires approval in every sufficient set")
                    if not record.support_sets:
                        raise ValueError("approved claim requires approval evidence")
                for rid in record.supersedes:
                    result = await tx.run("MATCH (c:MemexClaimRevision {repo_id:$repo,revision_id:$rid}) RETURN c.payload AS payload",repo=record.repo_id,rid=rid)
                    row = await result.single()
                    if row is None:
                        raise ValueError("missing predecessor")
                    predecessor = ClaimRevision.model_validate_json(row["payload"])
                    if predecessor.claim_id != record.claim_id:
                        raise ValueError("cannot supersede a different claim")
                    if predecessor.authority == "human_approved" and record.authority != "human_approved":
                        raise ValueError("proposal cannot supersede approved authority")
                    if predecessor.observed_at > record.observed_at:
                        raise ValueError("supersession cannot precede its observation")
            await tx.run(f"MATCH (n:{label} {{repo_id:$repo,{identity}:$id}}) SET n.payload=$payload",**params)
            if isinstance(record,ClaimRevision):
                for index,group in enumerate(record.support_sets):
                    await tx.run("MATCH (c:MemexClaimRevision {repo_id:$repo,revision_id:$id}), (e:MemexEvidence {repo_id:$repo}) "
                        "WHERE e.evidence_id IN $ids MERGE (c)-[:USES_EVIDENCE {alternative:$alternative}]->(e)",repo=record.repo_id,id=record.revision_id,ids=list(group),alternative=index)
                await tx.run("MATCH (c:MemexClaimRevision {repo_id:$repo,revision_id:$id}), (p:MemexClaimRevision {repo_id:$repo}) "
                    "WHERE p.revision_id IN $ids MERGE (c)-[:SUPERSEDES]->(p)",repo=record.repo_id,id=record.revision_id,ids=list(record.supersedes))
            elif record.source_kind == "claim":
                await tx.run("MATCH (e:MemexEvidence {repo_id:$repo,evidence_id:$id}), (p:MemexClaimRevision {repo_id:$repo,revision_id:$rid}) MERGE (e)-[:DERIVES_FROM]->(p)",repo=record.repo_id,id=record.evidence_id,rid=record.predecessor_revision)
        async with self.driver.session() as session:
            await session.execute_write(write)

    async def put_evidence(self, evidence, *, allow_approval=False):
        await self._put(evidence,"MemexEvidence","evidence_id",allow_approval=allow_approval)

    async def put_claim(self, claim, *, allow_approval=False):
        await self._put(claim,"MemexClaimRevision","revision_id",allow_approval=allow_approval)

    async def load(self, repo_id, *, max_claims=256, max_evidence=1024):
        await self.ensure_schema()
        revisions = await self.driver.execute_query("MATCH (c:MemexClaimRevision {repo_id:$repo}) RETURN c.payload AS payload ORDER BY c.revision_id LIMIT $limit",params={"repo":repo_id,"limit":max_claims+1})
        evidence = await self.driver.execute_query("MATCH (e:MemexEvidence {repo_id:$repo}) RETURN e.payload AS payload ORDER BY e.evidence_id LIMIT $limit",params={"repo":repo_id,"limit":max_evidence+1})
        cs = [ClaimRevision.model_validate_json(r["payload"]) for r in revisions.records[:max_claims]]
        es = [EvidenceRef.model_validate_json(r["payload"]) for r in evidence.records[:max_evidence]]
        return {c.revision_id:c for c in cs},{e.evidence_id:e for e in es}, len(revisions.records)<=max_claims and len(evidence.records)<=max_evidence

    async def save_verification(self, repo_id, principal_id, verification):
        await self.ensure_schema()
        payload = verification.model_dump_json()
        identity = hashlib.sha256((principal_id+":"+payload).encode()).hexdigest()
        await self.driver.execute_query("MERGE (v:MemexVerification {repo_id:$repo,verification_id:$id}) SET v.payload=$payload,v.principal_id=$principal "
            "WITH v MATCH (c:MemexClaimRevision {repo_id:$repo,revision_id:$revision}), (w:MemexView {view_id:$view}) MERGE (v)-[:VERIFIES]->(c) MERGE (v)-[:AT_VIEW]->(w)",
            params={"repo":repo_id,"id":identity,"payload":payload,"principal":principal_id,"revision":verification.revision_id,"view":verification.view_id})
