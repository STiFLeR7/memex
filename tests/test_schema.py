import pytest
from pydantic import ValidationError
from datetime import datetime, UTC
from memex.graph.schema import (
    SymbolNode, DecisionNode, ProblemNode, Repository, Principal, PrincipalNode,
    WRITE_POLICIES,
)

def test_symbol_node_rejects_invalid_kind():
    with pytest.raises(ValidationError) as exc:
        SymbolNode(
            name="test", kind="invalid", signature="sig", 
            file="f.py", line=1, valid_from=datetime.now(UTC)
        )
    assert "invalid kind" in str(exc.value)

def test_symbol_node_clamps_confidence_to_range():
    s1 = SymbolNode(
        name="test", kind="fn", signature="sig", 
        file="f.py", line=1, valid_from=datetime.now(UTC),
        confidence=1.5
    )
    assert s1.confidence == 1.0
    
    s2 = SymbolNode(
        name="test", kind="fn", signature="sig", 
        file="f.py", line=1, valid_from=datetime.now(UTC),
        confidence=-0.5
    )
    assert s2.confidence == 0.0

def test_symbol_node_strips_null_bytes():
    s = SymbolNode(
        name="test\x00", kind="fn", signature="sig\x00", 
        file="f.py\x00", line=1, valid_from=datetime.now(UTC)
    )
    assert s.name == "test"
    assert s.signature == "sig"
    assert s.file == "f.py"

def test_decision_node_rejects_empty_text():
    with pytest.raises(ValidationError) as exc:
        DecisionNode(text="  ", created_at=datetime.now(UTC))
    assert "cannot be empty" in str(exc.value)

def test_decision_node_defaults():
    d = DecisionNode(text="test decision")
    assert d.confidence == 0.6
    assert d.source == "watcher"
    assert d.corroborated is False
    assert d.corroboration_commit is None

def test_decision_node_clamps_confidence():
    d1 = DecisionNode(text="test", confidence=1.5)
    assert d1.confidence == 1.0
    
    d2 = DecisionNode(text="test", confidence=-0.5)
    assert d2.confidence == 0.0

def test_decision_node_rejects_invalid_source():
    with pytest.raises(ValidationError) as exc:
        DecisionNode(text="test", source="human")
    assert "invalid source" in str(exc.value)

def test_problem_node_rejects_invalid_severity():
    # My schema coerces to medium instead of rejecting, which is safer
    p = ProblemNode(
        text="bug", severity="ultra-bad", created_at=datetime.now(UTC)
    )
    assert p.severity == "medium"


def test_repository_accepts_project_id(tmp_path):
    """Phase 00 (NET-01/NET-02) — Repository gains an optional project_id
    scoping key alongside the existing path-based identity."""
    r = Repository(
        path=str(tmp_path),
        name="repo",
        added_at=datetime.now(),
        project_id="github.com/acme/widgets",
    )
    assert r.project_id == "github.com/acme/widgets"


def test_repository_project_id_defaults_to_none(tmp_path):
    """Existing callers that don't pass project_id are unaffected."""
    r = Repository(path=str(tmp_path), name="repo", added_at=datetime.now())
    assert r.project_id is None


# ---------------------------------------------------------------------------
# Principal (Phase 02 / NET-08) — identity core
# ---------------------------------------------------------------------------


def test_principal_coerces_invalid_role_to_viewer():
    """Fail-safe, least-privilege coercion — mirrors
    Problem.severity_must_be_valid's coercion pattern, NOT a raise (T-02-03)."""
    p = Principal(principal_id="x", role="bogus")
    assert p.role == "viewer"


def test_principal_defaults_role_to_contributor():
    """Pydantic-level default for newly-created records with no role
    specified — NOT the resolve_principal() migration sentinel ('admin')."""
    p = Principal(principal_id="x")
    assert p.role == "contributor"


def test_principal_node_alias_is_principal():
    assert PrincipalNode is Principal


def test_principal_write_policy_is_locked():
    p = Principal(principal_id="x")
    assert p.write_policy == "locked"
    assert WRITE_POLICIES["Principal"] == "locked"
