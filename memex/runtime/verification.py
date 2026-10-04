"""Bounded deterministic proof checks. Source drift alone never disproves prose."""
from memex.context.live import VerificationRecord


def applicable(claim, view, now):
    return (claim.repo_id == view.repo_id
            and (not claim.worktree_ids or view.worktree_id in claim.worktree_ids)
            and claim.observed_at <= now and claim.valid_from <= now
            and (claim.valid_until is None or now < claim.valid_until))


def evaluate(claim, view, evidence, revisions, files, *, now, allowed=lambda p: True, max_nodes=256):
    remaining = max_nodes
    hashes = set()
    visited_evidence = set()

    def revision_check(c, chain):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or c.revision_id in chain:
            return "unknown", True, "dependency_bound_or_cycle"
        if not applicable(c, view, now):
            return "unknown", True, "not_applicable_or_future"
        if c.lifecycle == "revoked":
            return "unsupported", True, "revoked"
        if not c.support_sets:
            return "unknown", True, "no_explicit_support"
        alternatives = []
        for support in c.support_sets:
            members = [evidence_check(evidence.get(eid), chain | {c.revision_id}) for eid in support]
            permitted = all(x[1] for x in members)
            statuses = {x[0] for x in members}
            if permitted and statuses == {"supported"}:
                return "supported", True, "sufficient_support"
            status = next((s for s in ("conflicted", "unsupported", "unknown", "needs_revalidation") if s in statuses), "unknown")
            alternatives.append((status, permitted, ";".join(sorted({x[2] for x in members}))))
        # Prefer a still-possible sufficient set; a false alternative does not defeat it.
        permitted = [x for x in alternatives if x[1]]
        for status in ("needs_revalidation", "unknown", "conflicted", "unsupported"):
            match = next((x for x in permitted if x[0] == status), None)
            if match:
                return match
        return "unknown", False, "access_denied"

    def evidence_check(e, chain):
        nonlocal remaining
        remaining -= 1
        if remaining < 0:
            return "unknown", True, "dependency_bound"
        if e is None:
            return "unknown", True, "missing_evidence"
        if e.path and not allowed(e.path):
            return "unknown", False, "access_denied"
        visited_evidence.add(e.evidence_id)
        if e.repo_id != view.repo_id or e.observed_at > now or (e.worktree_ids and view.worktree_id not in e.worktree_ids):
            return "unknown", True, "not_applicable_or_future"
        if e.source_kind == "approval":
            return "supported", True, "explicit_approval"
        if e.source_kind == "claim":
            predecessor = revisions.get(e.predecessor_revision)
            if predecessor is None:
                return "unknown", True, "missing_predecessor"
            successors = [r for r in revisions.values() if predecessor.revision_id in r.supersedes and applicable(r,view,now)]
            if successors:
                return "needs_revalidation", True, "predecessor_superseded"
            return revision_check(predecessor,chain)
        f = files.get(e.path)
        if f is None:
            return "needs_revalidation", True, "source_removed"
        hashes.add(e.path + "=" + f.content_hash)
        if f.coverage != "complete":
            return "unknown", True, "coverage:" + f.coverage
        if e.source_kind == "test":
            if e.tested_view_id != view.view_id or f.content_hash != e.content_hash:
                return "needs_revalidation", True, "test_scope_changed"
            return ("supported" if e.outcome == "passed" else "unsupported"), True, "test:" + e.outcome
        if e.predicate == "symbol_exists":
            exists = any(s.qualified_name == e.symbol for s in f.symbols)
            return ("supported" if exists else "unsupported"), True, "symbol:" + e.symbol
        return ("supported", True, "hash_matches") if f.content_hash == e.content_hash else ("needs_revalidation", True, "source_hash_changed")

    status, permitted, reason = revision_check(claim,set())
    return VerificationRecord(revision_id=claim.revision_id, view_id=view.view_id,
        authority=claim.authority, status=status, permitted=permitted, checked_at=now,
        evidence_ids=tuple(sorted(visited_evidence)), support_hashes=tuple(sorted(hashes)), reason=reason)
