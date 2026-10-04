"""Strict immutable v1 core records. Internal API; hosts do not authenticate themselves."""
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from memex.context.revision import RepositoryView

ID = Annotated[str, Field(min_length=1, max_length=256)]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Status = Literal["supported", "needs_revalidation", "unsupported", "unknown", "conflicted"]
Authority = Literal["observed", "inferred", "human_approved"]


def source_path(value: str) -> str:
    p = PurePosixPath(value)
    if not value or p.is_absolute() or ".." in p.parts or "\\" in value or ":" in value or str(p) != value:
        raise ValueError("source path must be normalized repository-relative POSIX")
    return value


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)
    schema_version: Literal["memex.live.v1"] = "memex.live.v1"


class SessionIdentity(Record):
    harness: ID
    native_session_id: ID
    memex_session_id: ID
    principal_id: ID


class EvidenceRef(Record):
    evidence_id: ID
    repo_id: ID
    source_kind: Literal["source", "approval", "test", "claim"]
    observed_at: float = Field(ge=0)
    path: str | None = None
    content_hash: Digest | None = None
    predicate: Literal["hash", "symbol_exists"] = "hash"
    symbol: str | None = None
    worktree_ids: tuple[ID, ...] = ()
    approver: ID | None = None
    tested_view_id: ID | None = None
    test_definition: ID | None = None
    environment: ID | None = None
    outcome: Literal["passed", "failed"] | None = None
    predecessor_revision: ID | None = None

    @field_validator("path")
    @classmethod
    def valid_path(cls, value):
        return source_path(value) if value is not None else None

    @model_validator(mode="after")
    def required_evidence(self):
        if self.source_kind in ("source", "test") and (self.path is None or self.content_hash is None):
            raise ValueError("source/test requires path and exact input hash")
        if self.predicate == "symbol_exists" and (self.source_kind != "source" or not self.symbol):
            raise ValueError("structural predicate requires a source symbol")
        if self.source_kind == "approval" and not self.approver:
            raise ValueError("approval requires an authorized approver")
        if self.source_kind == "test" and not all((self.tested_view_id, self.test_definition, self.environment, self.outcome)):
            raise ValueError("test requires tested view, definition, environment and outcome")
        if self.source_kind == "claim" and not self.predecessor_revision:
            raise ValueError("derived support requires an exact predecessor revision")
        return self


class ClaimRevision(Record):
    claim_id: ID
    revision_id: ID
    repo_id: ID
    assertion: str = Field(min_length=1, max_length=8192)
    authority: Authority
    support_sets: tuple[tuple[ID, ...], ...] = Field(default=(), max_length=32)
    supersedes: tuple[ID, ...] = Field(default=(), max_length=32)
    worktree_ids: tuple[ID, ...] = ()
    observed_at: float = Field(ge=0)
    valid_from: float = Field(default=0, ge=0)
    valid_until: float | None = Field(default=None, ge=0)
    lifecycle: Literal["active", "revoked"] = "active"

    @model_validator(mode="after")
    def bounded(self):
        if sum(map(len, self.support_sets)) > 128 or any(not s for s in self.support_sets):
            raise ValueError("support sets must be nonempty and bounded")
        if self.revision_id in self.supersedes:
            raise ValueError("revision cannot supersede itself")
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("invalid applicability interval")
        return self


class VerificationRecord(Record):
    revision_id: ID
    view_id: ID
    authority: Authority
    status: Status
    permitted: bool
    checked_at: float
    check_version: Literal["deterministic.v1"] = "deterministic.v1"
    evidence_ids: tuple[ID, ...] = ()
    support_hashes: tuple[str, ...] = ()
    reason: str = ""


class PacketBudget(Record):
    max_items: int = Field(default=32, ge=1, le=128)
    max_characters: int = Field(default=16384, ge=256, le=65536)


class PacketItem(Record):
    claim_id: ID
    revision_id: ID
    assertion: str = Field(min_length=1, max_length=8192)
    authority: Authority
    status: Status
    reason: str = Field(default="", max_length=4096)


class DeltaChange(Record):
    operation: Literal["add", "replace", "retract", "uncertain", "conflict"]
    previous_revision: ID | None = None
    revision: ID | None = None
    item: PacketItem | None = None
    reason: str = Field(max_length=4096)


class TaskSnapshot(Record):
    task_id: ID
    view_id: ID
    sequence: int = Field(ge=1)
    base_sequence: int = Field(ge=0)
    full: bool
    items: tuple[PacketItem, ...] = Field(default=(), max_length=128)
    changes: tuple[DeltaChange, ...] = Field(default=(), max_length=128)
    replaces_revisions: tuple[ID, ...] = Field(default=(), max_length=256)
    replaces_sequence: int | None = None
    coverage: tuple[tuple[str, str], ...] = Field(default=(), max_length=1024)
    expires_at: float
    continuation_token: str | None = None
    resync_required: bool = False
    reason: str = ""


class DeliveryReceipt(Record):
    session: SessionIdentity
    task_id: ID
    sequence: int = Field(ge=1)
    view_id: ID
    adapter_version: ID
    accepted_at: float = Field(ge=0)
    action_attempt_id: ID | None = None
    outcome: Literal["host_accepted", "failed"]


class OpenTaskRequest(Record):
    session: SessionIdentity
    view: RepositoryView
    intent: str = Field(min_length=1, max_length=2048)
    selected_revision_ids: tuple[ID, ...] = Field(default=(), max_length=128)
    acceptance_refs: tuple[ID, ...] = Field(default=(), max_length=128)
    budget: PacketBudget = Field(default_factory=PacketBudget)
    ttl_seconds: float = Field(default=1800.0, gt=0, le=86400)


class ActionRequest(Record):
    session: SessionIdentity
    task_id: ID
    view: RepositoryView
    attempt_id: ID
    original_attempt_id: ID | None = None
    action_kind: ID
    targets: tuple[str, ...] = Field(max_length=128)
    expected_hashes: tuple[tuple[str, Digest | None], ...] = Field(default=(), max_length=128)
    last_acknowledged: int = Field(ge=0)
    scope_complete: bool
    context_retained: bool

    @model_validator(mode="after")
    def normalized_targets(self):
        for p in self.targets: source_path(p)
        for p,_ in self.expected_hashes: source_path(p)
        if len(set(self.targets)) != len(self.targets) or len({p for p,_ in self.expected_hashes}) != len(self.expected_hashes):
            raise ValueError("duplicate action targets/hashes")
        if self.attempt_id == self.original_attempt_id:
            raise ValueError("retry must use a new attempt ID")
        return self


class ActionCheck(Record):
    task_id: ID
    attempt_id: ID
    outcome: Literal["proceed", "replan", "resync_required", "unavailable"]
    checked_view: RepositoryView
    coverage: tuple[tuple[str, str], ...] = Field(default=(), max_length=1024)
    delta: TaskSnapshot | None = None
    reason: str
    freshness_deadline: float
