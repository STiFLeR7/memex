"""Phase 2 support semantics, independent of inference and host clients."""
import importlib.util
from hashlib import sha256

import pytest


def test_phase2_contracts_exist():
    assert importlib.util.find_spec("memex.context.live") is not None, "W06 strict contracts missing"


@pytest.fixture
def api():
    assert importlib.util.find_spec("memex.context.live") is not None, "W06 contracts missing"
    from memex.context import live
    from memex.runtime.verification import evaluate
    return live, evaluate


def digest(content):
    return "sha256:" + sha256(content).hexdigest()


def setup(api, content=b"def api(): return 1\n", **updates):
    m, evaluate = api
    from memex.context.revision import RepositoryView
    from memex.runtime.indexing import extract_structure
    view = RepositoryView("repo", "wt", None, 1, 1, digest(content))
    ev = m.EvidenceRef(evidence_id="e1", repo_id="repo", path="api.py", content_hash=digest(content),
                       source_kind="source", observed_at=1.0)
    claim = m.ClaimRevision(claim_id="c1", revision_id="r1", repo_id="repo", assertion="api behavior",
                            authority="inferred", support_sets=(("e1",),), observed_at=1.0, **updates)
    files = {"api.py": extract_structure("api.py", content)}
    return m, evaluate, view, ev, claim, files


def test_strict_schema_and_immutable_records(api):
    m, _, _, e, c, _ = setup(api)
    assert m.ClaimRevision.model_validate_json(c.model_dump_json()) == c
    with pytest.raises(ValueError): m.ClaimRevision.model_validate_json(c.model_dump_json()[:-1]+',"typo":1}')
    with pytest.raises(ValueError): e.path = "secret.py"
    for path in ("../x.py", "C:/secret.py", "/root.py", "a\\b.py"):
        with pytest.raises(ValueError): m.EvidenceRef(evidence_id="e", repo_id="repo", path=path,
            content_hash=digest(b""), source_kind="source", observed_at=1.0)
    with pytest.raises(ValueError): m.SessionIdentity(harness="claude", native_session_id="a", memex_session_id="b", principal_id="")


def test_hash_drift_is_revalidation_not_false(api):
    _, evaluate, view, e, c, files = setup(api)
    result = evaluate(c, view, {e.evidence_id:e}, {c.revision_id:c}, files, now=2.0)
    assert result.status == "supported" and result.authority == "inferred"
    from memex.runtime.indexing import extract_structure
    files["api.py"] = extract_structure("api.py", b"def api(): return 2\n")
    assert evaluate(c,view,{"e1":e},{"r1":c},files,now=2.0).status == "needs_revalidation"


def test_or_of_and_support_and_empty_not_truth(api):
    m, evaluate, view, e, c, files = setup(api)
    stable = e.model_copy(update={"evidence_id":"e2", "path":"stable.py"})
    files["stable.py"] = files["api.py"]
    from memex.runtime.indexing import extract_structure
    files["api.py"] = extract_structure("api.py",b"def api(): return 2\n")
    c = c.model_copy(update={"support_sets":(("e1",),("e2",))})
    assert evaluate(c,view,{"e1":e,"e2":stable},{"r1":c},files,now=2.0).status == "supported"
    c = c.model_copy(update={"support_sets":(("e1","e2"),)})
    assert evaluate(c,view,{"e1":e,"e2":stable},{"r1":c},files,now=2.0).status == "needs_revalidation"
    assert evaluate(c.model_copy(update={"support_sets":()}),view,{}, {},files,now=2.0).status == "unknown"


def test_structural_predicate_parse_error_and_access(api):
    _, evaluate, view, e, c, files = setup(api)
    e = e.model_copy(update={"predicate":"symbol_exists", "symbol":"api"})
    from memex.runtime.indexing import extract_structure
    files["api.py"] = extract_structure("api.py", b"def api(): return 2\n")
    assert evaluate(c,view,{"e1":e},{"r1":c},files,now=2.0).status == "supported"
    files["api.py"] = extract_structure("api.py", b"def api(\n")
    assert evaluate(c,view,{"e1":e},{"r1":c},files,now=2.0).status == "unknown"
    denied = evaluate(c,view,{"e1":e},{"r1":c},files,now=2.0,allowed=lambda p:False)
    assert not denied.permitted and denied.status == "unknown"


def test_approval_test_scope_future_and_derivation_bound(api):
    m, evaluate, view, e, c, files = setup(api)
    approval = m.EvidenceRef(evidence_id="e1",repo_id="repo", source_kind="approval", approver="maintainer", observed_at=1.0)
    rule = c.model_copy(update={"authority":"human_approved"})
    assert evaluate(rule,view,{"e1":approval},{"r1":rule},files,now=2.0).status == "supported"
    assert evaluate(rule,view,{"e1":approval},{"r1":rule},files,now=0.0).status == "unknown"
    tested = e.model_copy(update={"source_kind":"test", "tested_view_id":"old-view", "test_definition":"unit/api", "environment":"py312", "outcome":"passed"})
    assert evaluate(c,view,{"e1":tested},{"r1":c},files,now=2.0).status == "needs_revalidation"
    derived = m.EvidenceRef(evidence_id="e1",repo_id="repo",source_kind="claim", predecessor_revision="r1",observed_at=1.0)
    assert evaluate(c,view,{"e1":derived},{"r1":c},files,now=2.0).status == "unknown"
    assert evaluate(c,view,{"e1":e},{"r1":c},files,now=2.0,max_nodes=0).status == "unknown"
    c = c.model_copy(update={"worktree_ids":("other",)})
    assert evaluate(c,view,{"e1":e},{"r1":c},files,now=2.0).status == "unknown"
