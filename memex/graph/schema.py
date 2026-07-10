from pydantic import BaseModel, field_validator, ConfigDict
from datetime import datetime
from typing import Optional

# ---------------------------------------------------------------------------
# v0.3.0 cross-node fields (added to every node in Phase 5.9 scaffolding)
#
# write_policy:        node-type ACL enforced by tools_write.py (Phase 9)
#                      Defaults are set per node class below; ARCHITECTURE §7
# access_count:        increments on every retrieval; feeds Phase 7 reranker
#                      rehearsal_boost. NOT a confidence signal.
# last_reinforced_at:  feeds Phase 8 TempValid two-regime computed-confidence.
#                      Updates only on re-ingest or explicit validation.
# ---------------------------------------------------------------------------


class Symbol(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    kind: str  # "fn" | "class" | "const"
    signature: str
    file: str
    line: int
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    confidence: float = 1.0
    source_commit: Optional[str] = None
    # v0.3.0
    write_policy: str = "locked"
    access_count: int = 0
    last_reinforced_at: Optional[datetime] = None

    @field_validator("kind")
    @classmethod
    def kind_must_be_valid(cls, v):
        if v not in ("fn", "class", "const"):
            raise ValueError(f"invalid kind: {v}")
        return v

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v):
        return max(0.0, min(1.0, float(v)))

    @field_validator("name", "signature", "file")
    @classmethod
    def no_null_bytes(cls, v):
        if v is None:
            return v
        return str(v).replace('\x00', '').replace('‮', '')


class Module(BaseModel):
    model_config = ConfigDict(extra="ignore")

    path: str
    language: str
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    # v0.3.0
    cluster_name: Optional[str] = None  # foreign key → Cluster.name
    write_policy: str = "locked"
    access_count: int = 0
    last_reinforced_at: Optional[datetime] = None

    @field_validator("path")
    @classmethod
    def no_null_bytes(cls, v):
        return str(v).replace('\x00', '')


class Decision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    rationale: Optional[str] = None
    scope: Optional[str] = None
    created_at: Optional[datetime] = None
    source_commit: Optional[str] = None
    confidence: float = 0.6
    source: str = "watcher"
    corroborated: bool = False
    corroboration_commit: Optional[str] = None
    # v0.3.0 — validation + TempValid decay
    validated: bool = False
    validated_at: Optional[datetime] = None
    base_confidence: float = 0.6
    supersedes: Optional[str] = None
    excluded: bool = False
    write_policy: str = "open"
    access_count: int = 0
    last_reinforced_at: Optional[datetime] = None
    # Phase 01 — which specific agent/harness wrote this node (e.g.
    # "claude-code", "gemini-cli"), distinct from the `source` category
    # field above ("agent" vs "watcher"). Documentation-only here: this
    # class is never instantiated by the agent-write path in
    # `tools_write.py` (raw Cypher SET instead); only `synthesizer/commit.py`
    # constructs `Decision(...)` directly, for watcher-sourced nodes.
    harness: Optional[str] = None

    @field_validator("text")
    @classmethod
    def text_not_empty(cls, v):
        if not v or not str(v).strip():
            raise ValueError("decision text cannot be empty")
        return str(v).replace('\x00', '')

    @field_validator("confidence", "base_confidence")
    @classmethod
    def confidence_in_range(cls, v):
        return max(0.0, min(1.0, float(v)))

    @field_validator("source")
    @classmethod
    def source_must_be_valid(cls, v):
        if v not in ("agent", "watcher"):
            raise ValueError(f"invalid source: {v}")
        return v


class Problem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    severity: str  # "critical" | "high" | "medium" | "low"
    status: str = "open"
    created_at: Optional[datetime] = None
    surfaced_by: str = "watcher"
    # v0.3.0
    base_confidence: float = 0.7
    write_policy: str = "open"
    access_count: int = 0
    last_reinforced_at: Optional[datetime] = None
    # Phase 01 — see Decision.harness docstring above; same rationale.
    harness: Optional[str] = None

    @field_validator("severity")
    @classmethod
    def severity_must_be_valid(cls, v):
        if v not in ("critical", "high", "medium", "low"):
            return "medium"  # Coerce to medium
        return v


class AgentSession(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    started_at: datetime
    repo_path: str
    summary: Optional[str] = None
    # v0.3.0
    write_policy: str = "self"
    access_count: int = 0
    last_reinforced_at: Optional[datetime] = None


class Dependency(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    version: str
    ecosystem: str
    last_updated: datetime
    # v0.3.0
    write_policy: str = "locked"
    access_count: int = 0
    last_reinforced_at: Optional[datetime] = None


class Repository(BaseModel):
    model_config = ConfigDict(extra="ignore")

    path: str
    name: str
    added_at: datetime
    active: bool = True
    # v0.3.0
    write_policy: str = "locked"
    access_count: int = 0
    last_reinforced_at: Optional[datetime] = None
    # Phase 00 — path-independent project scoping key (NET-01/NET-02).
    # Resolved via memex.config.resolve_project_id(); None when
    # unresolvable (unchanged single-dev behavior).
    project_id: Optional[str] = None

    @field_validator("path")
    @classmethod
    def path_must_be_absolute(cls, v):
        from pathlib import Path
        p = Path(v)
        if not p.is_absolute():
            raise ValueError(f"path must be absolute: {v}")
        return str(p)


# ---------------------------------------------------------------------------
# New v0.3.0 node types
# ---------------------------------------------------------------------------


class Cluster(BaseModel):
    """Hierarchical group above Module. Populated by hybrid Leiden in
    `memex/graph/cluster.py` (graspologic; deferred to dev2 — MSVC required).
    Schema lands here in Phase 5.9 so downstream code can reference the type."""
    model_config = ConfigDict(extra="ignore")

    name: str
    repo_path: str
    description: Optional[str] = None
    module_count: int = 0
    created_at: Optional[datetime] = None
    # v0.3.0 cross-node
    write_policy: str = "locked"
    access_count: int = 0
    last_reinforced_at: Optional[datetime] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v):
        if not v or not str(v).strip():
            raise ValueError("cluster name cannot be empty")
        return str(v).replace('\x00', '')


class ClusterSummary(BaseModel):
    """HDBSCAN-clustered topic summary of Decisions within a Module.
    ADDITIVE — Decisions remain queryable in parallel (RAPTOR collapsed-tree).
    Pipeline deferred to dev2 (requires hdbscan + graspologic edges)."""
    model_config = ConfigDict(extra="ignore")

    text: str
    topic_label: str
    source_count: int
    module_path: str
    cluster_id: str  # HDBSCAN cluster ID within the module
    repo_path: str
    base_confidence: float = 0.6  # set to max(source.base_confidence) at write time
    created_at: Optional[datetime] = None
    # v0.3.0 cross-node
    write_policy: str = "locked"
    access_count: int = 0
    last_reinforced_at: Optional[datetime] = None

    @field_validator("text")
    @classmethod
    def text_not_empty(cls, v):
        if not v or not str(v).strip():
            raise ValueError("cluster summary text cannot be empty")
        return str(v).replace('\x00', '')

    @field_validator("base_confidence")
    @classmethod
    def confidence_in_range(cls, v):
        return max(0.0, min(1.0, float(v)))


class Principal(BaseModel):
    """A human developer (or CI/service account acting as one) — Phase 02
    identity core (NET-08). Distinct from `harness`/agent-type identity
    (Phase 01, e.g. Decision.harness) — do not conflate the two.

    `role` defaults to "contributor" here at the Pydantic-model level, for
    records created explicitly (e.g. by future admin tooling) with no role
    specified. This is NOT the same as the migration sentinel used by
    `resolve_principal()` for pre-Phase-02 legacy key records that have no
    `role` field at all — those resolve to "admin" instead, via an explicit
    presence check in `memex/watcher/registry.py` (see 02-RESEARCH.md
    Common Pitfalls #1). Conflating these two "missing role" cases would
    silently downgrade every pre-upgrade admin-equivalent key to
    "contributor" on upgrade — exactly what NET-11 exists to prevent.
    """

    model_config = ConfigDict(extra="ignore")

    principal_id: str
    display_name: Optional[str] = None
    role: str = "contributor"
    created_at: Optional[datetime] = None
    active: bool = True
    write_policy: str = "locked"

    @field_validator("role")
    @classmethod
    def role_must_be_valid(cls, v):
        if v not in ("viewer", "contributor", "admin"):
            return "viewer"  # Fail-safe: coerce to least-privilege, don't raise (T-02-03)
        return v


# Aliases for prompt compatibility
SymbolNode = Symbol
ModuleNode = Module
DecisionNode = Decision
ProblemNode = Problem
AgentSessionNode = AgentSession
DependencyNode = Dependency
RepositoryNode = Repository
ClusterNode = Cluster
ClusterSummaryNode = ClusterSummary
PrincipalNode = Principal


# ---------------------------------------------------------------------------
# Default write policy per node type (Phase 9 ACL enforcement reads this)
# ---------------------------------------------------------------------------

WRITE_POLICIES: dict[str, str] = {
    "Module":          "locked",
    "Symbol":          "locked",
    "Dependency":      "locked",
    "Cluster":         "locked",
    "ClusterSummary":  "locked",
    "Repository":      "locked",
    "Decision":        "open",
    "Problem":         "open",
    "AgentSession":    "self",
    "Principal":       "locked",
}


# ---------------------------------------------------------------------------
# Phase 9 — Write governance ACL exception
# Raised by tools_write.check_write_policy() when a caller violates the
# node-type ACL (see ARCHITECTURE-v0.3.0.md §7 Layer A).
# ---------------------------------------------------------------------------


class MemexWritePolicyError(Exception):
    """Raised when an MCP write tool attempts to mutate a node whose
    `write_policy` forbids the caller (e.g. agent writing a `locked` Module
    node, or one session writing another session's `self`-scoped data)."""

    def __init__(self, node_type: str, principal_id: str, policy: str):
        self.node_type = node_type
        self.caller = principal_id
        self.principal_id = principal_id
        self.policy = policy
        super().__init__(
            f"{node_type} nodes have write_policy='{policy}' — caller "
            f"'{principal_id}' is not permitted to modify them"
        )


def check_write_policy(
    node_type: str,
    principal_id: str,
    role: str = "contributor",
    owner: Optional[str] = None,
) -> None:
    """Enforces Layer A ACL from ARCHITECTURE §7, with Phase 02 (NET-09) role
    awareness layered on top of the pre-existing locked/open/self tiers.

    - `locked` nodes: only an `admin` or `system` role may mutate; any other
      role (default `"contributor"`) is denied.
    - `open` nodes: anyone may mutate, regardless of role.
    - `self` nodes: only the principal that owns the node may mutate it,
      UNLESS `role="admin"` is passed (admin may override self-ownership).
      Pass `owner=<original_principal_id>` when the policy is `self`; if
      `owner` is `None` and policy is `self`, the check is permissive (used
      at node creation time where the principal IS the owner).
    """
    policy = WRITE_POLICIES.get(node_type, "open")
    if policy == "locked" and role not in ("admin", "system"):
        raise MemexWritePolicyError(node_type, principal_id, policy)
    if (
        policy == "self"
        and owner is not None
        and principal_id != owner
        and role != "admin"
    ):
        raise MemexWritePolicyError(node_type, principal_id, policy)
