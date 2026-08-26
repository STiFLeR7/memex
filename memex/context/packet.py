"""Bounded, provenance-aware engineering context packets.

This module intentionally contains no retrieval or host integration logic.
It is the small contract shared by future selection and Hermes/MCP adapters.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Freshness = Literal[
    "current",
    "aging",
    "stale",
    "superseded",
    "conflicted",
    "unknown",
]
PacketKind = Literal["prefetch", "handoff", "compression", "mcp_response"]
ProvenanceKind = Literal["watcher", "commit", "agent", "human", "import", "derived"]


def _text(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("must be a non-empty string")
    return value.strip()


def _iso(value: datetime) -> str:
    value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProvenanceRef(_StrictModel):
    """A stable, human-auditable origin reference for a context item."""

    kind: ProvenanceKind
    ref: str
    observed_at: datetime | None = None

    _validate_ref = field_validator("ref")(_text)


class RepositoryScope(_StrictModel):
    project_id: str | None = None
    repo_path: str | None = None

    @field_validator("project_id", "repo_path")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return None if value is None else _text(value)

    @model_validator(mode="after")
    def require_scope(self) -> "RepositoryScope":
        if not self.project_id and not self.repo_path:
            raise ValueError("repository scope requires project_id or repo_path")
        return self


class TaskContext(_StrictModel):
    task_id: str | None = None
    query: str

    @field_validator("task_id", "query")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        return None if value is None else _text(value)


class ExecutionContext(_StrictModel):
    session_id: str | None = None
    agent_id: str | None = None
    harness: str | None = None

    @field_validator("session_id", "agent_id", "harness")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return None if value is None else _text(value)


class PacketBudget(_StrictModel):
    max_items: int = 8
    max_chars: int = 12_000
    actual_chars: int = 0

    @field_validator("max_items", "max_chars")
    @classmethod
    def validate_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("budget values must be positive")
        return value

    @field_validator("actual_chars")
    @classmethod
    def validate_actual_chars(cls, value: int) -> int:
        if value < 0:
            raise ValueError("actual_chars cannot be negative")
        return value


class SelectionMetadata(_StrictModel):
    filters: dict[str, Any] = Field(default_factory=dict)
    candidate_count: int = 0
    ranking_version: str = "v0.9-contract"

    @field_validator("candidate_count")
    @classmethod
    def validate_candidate_count(cls, value: int) -> int:
        if value < 0:
            raise ValueError("candidate_count cannot be negative")
        return value

    _validate_ranking_version = field_validator("ranking_version")(_text)


class ContextItem(_StrictModel):
    item_id: str
    entity_type: str
    ref: str
    summary: str
    scope: str | None = None
    confidence: float
    freshness: Freshness
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    superseded_by: list[str] = Field(default_factory=list)
    source_refs: list[ProvenanceRef]
    relationships: list[str] = Field(default_factory=list)
    selection_reason: list[str]
    score_breakdown: dict[str, float] = Field(default_factory=dict)

    _validate_identity = field_validator("item_id", "entity_type", "ref", "summary")(_text)

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, value: str | None) -> str | None:
        return None if value is None else _text(value)

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        return value

    @model_validator(mode="after")
    def require_explanation_and_provenance(self) -> "ContextItem":
        if not self.source_refs:
            raise ValueError("context item requires provenance")
        if not self.selection_reason or any(not _text(reason) for reason in self.selection_reason):
            raise ValueError("context item requires a selection reason")
        return self


class ProjectionMetadata(_StrictModel):
    destination: str = "context"
    rendered_chars: int = 0
    dropped_items: list[str] = Field(default_factory=list)

    _validate_destination = field_validator("destination")(_text)

    @field_validator("rendered_chars")
    @classmethod
    def validate_rendered_chars(cls, value: int) -> int:
        if value < 0:
            raise ValueError("rendered_chars cannot be negative")
        return value


class EvidenceMetadata(_StrictModel):
    trace_id: str | None = None
    retrieval_id: str | None = None

    @field_validator("trace_id", "retrieval_id")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return None if value is None else _text(value)


class PacketPolicy(_StrictModel):
    redaction_profile: str = "local-default"
    allow_historical: bool = False

    _validate_redaction_profile = field_validator("redaction_profile")(_text)


def _render_projection(
    *,
    packet_id: str,
    kind: PacketKind,
    repository: RepositoryScope,
    task: TaskContext,
    items: Iterable[ContextItem],
) -> str:
    repo = repository.repo_path or repository.project_id or "unknown"
    lines = [
        f"<memex-context packet={packet_id} kind={kind}>",
        f"Repository: {repo}",
        f"Task: {task.query}",
    ]
    for item in items:
        lines.append(
            f"- [{item.entity_type}] {item.summary} "
            f"(ref={item.ref}; confidence={item.confidence:.3f}; freshness={item.freshness})"
        )
        lines.append(f"  Why: {'; '.join(item.selection_reason)}")
        lines.append(
            "  Source: "
            + "; ".join(f"{source.kind}:{source.ref}" for source in item.source_refs)
        )
    lines.append("</memex-context>")
    return "\n".join(lines)


class ContextPacket(_StrictModel):
    """Bounded engineering context selected for one agent task."""

    schema_version: str = "0.9"
    packet_id: str = Field(default_factory=lambda: f"packet-{uuid4().hex}")
    kind: PacketKind = "prefetch"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    repository: RepositoryScope
    task: TaskContext
    execution: ExecutionContext = Field(default_factory=ExecutionContext)
    budget: PacketBudget = Field(default_factory=PacketBudget)
    selection: SelectionMetadata = Field(default_factory=SelectionMetadata)
    items: list[ContextItem] = Field(default_factory=list)
    projection: ProjectionMetadata = Field(default_factory=ProjectionMetadata)
    evidence: EvidenceMetadata = Field(default_factory=EvidenceMetadata)
    outcome_ref: str | None = None
    policy: PacketPolicy = Field(default_factory=PacketPolicy)

    _validate_schema_version = field_validator("schema_version")(_text)
    _validate_packet_id = field_validator("packet_id")(_text)

    @field_validator("outcome_ref")
    @classmethod
    def validate_outcome_ref(cls, value: str | None) -> str | None:
        return None if value is None else _text(value)

    @model_validator(mode="after")
    def validate_packet(self) -> "ContextPacket":
        if len(self.items) > self.budget.max_items:
            raise ValueError("packet contains more items than its budget")

        keys = [item.ref for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("packet contains duplicate context items")

        rendered_chars = len(
            _render_projection(
                packet_id=self.packet_id,
                kind=self.kind,
                repository=self.repository,
                task=self.task,
                items=self.items,
            )
        )
        if rendered_chars > self.budget.max_chars:
            raise ValueError(
                f"packet projection exceeds budget: {rendered_chars} > {self.budget.max_chars}"
            )
        return self

    @classmethod
    def from_items(
        cls,
        *,
        items: Iterable[ContextItem | dict[str, Any]],
        **values: Any,
    ) -> "ContextPacket":
        """Build a packet, deduplicating and dropping lowest-priority tail items.

        Selection order is supplied by the caller. The contract only enforces
        stable first-seen deduplication and hard budgets; it does not invent a
        ranking algorithm.
        """
        repository = RepositoryScope.model_validate(values["repository"])
        task = TaskContext.model_validate(values["task"])
        budget = PacketBudget.model_validate(values.get("budget", {}))
        packet_id = values.get("packet_id") or f"packet-{uuid4().hex}"
        kind = values.get("kind", "prefetch")
        normalized_items = [ContextItem.model_validate(item) for item in items]

        kept: list[ContextItem] = []
        dropped: list[str] = []
        seen_refs: set[str] = set()
        for item in normalized_items:
            if item.ref in seen_refs or len(kept) >= budget.max_items:
                dropped.append(item.item_id)
                continue
            candidate = [*kept, item]
            rendered_chars = len(
                _render_projection(
                    packet_id=packet_id,
                    kind=kind,
                    repository=repository,
                    task=task,
                    items=candidate,
                )
            )
            if rendered_chars > budget.max_chars:
                dropped.append(item.item_id)
                continue
            kept.append(item)
            seen_refs.add(item.ref)

        rendered_chars = len(
            _render_projection(
                packet_id=packet_id,
                kind=kind,
                repository=repository,
                task=task,
                items=kept,
            )
        )
        budget.actual_chars = rendered_chars
        values.update(
            {
                "packet_id": packet_id,
                "kind": kind,
                "repository": repository,
                "task": task,
                "budget": budget,
                "items": kept,
                "projection": {
                    **(values.get("projection") or {}),
                    "rendered_chars": rendered_chars,
                    "dropped_items": dropped,
                },
            }
        )
        return cls(**values)

    def render_text(self) -> str:
        """Render the bounded host-facing projection deterministically."""
        return _render_projection(
            packet_id=self.packet_id,
            kind=self.kind,
            repository=self.repository,
            task=self.task,
            items=self.items,
        )

    def to_deterministic_json(self) -> str:
        """Serialize with stable key ordering for traces and adapter tests."""
        return json.dumps(
            self.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def validate_packet_metadata(packet: ContextPacket) -> list[str]:
    """Return explainability/validity issues before host projection."""

    issues: list[str] = []
    for item in packet.items:
        if not any(source.observed_at is not None for source in item.source_refs):
            issues.append(f"missing_observed_at:{item.ref}")
        if item.freshness == "superseded" and not item.superseded_by:
            issues.append(f"missing_superseded_by:{item.ref}")
        if item.valid_from and item.valid_until and item.valid_until <= item.valid_from:
            issues.append(f"invalid_validity_window:{item.ref}")
    return issues
