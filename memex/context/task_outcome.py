"""Bounded task and outcome records for evaluation and future promotion.

These records are deliberately not persistence or workflow objects. They link
repository-scoped work to a packet and a structured result without retaining a
transcript.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from memex.context.packet import ProvenanceRef, RepositoryScope

TaskStatus = Literal["planned", "in_progress", "completed", "cancelled"]
OutcomeStatus = Literal["success", "partial", "failed", "unknown"]


def _text(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("must be a non-empty string")
    return value.strip()


def _optional_text(value: str | None) -> str | None:
    return None if value is None else _text(value)


class _StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def to_deterministic_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


class TaskRecord(_StrictRecord):
    """Repository-scoped engineering work, without the original prompt."""

    task_id: str
    repository: RepositoryScope
    normalized_intent: str
    scope: str | None = None
    acceptance_criteria: list[str] = Field(default_factory=list)
    verification_target: str | None = None
    parent_task_id: str | None = None
    owner_agent: str | None = None
    status: TaskStatus = "planned"
    created_at: datetime
    closed_at: datetime | None = None
    source_refs: list[ProvenanceRef]

    _validate_identity = field_validator("task_id", "normalized_intent", mode="before")(_text)
    _validate_optional_identity = field_validator(
        "scope", "verification_target", "parent_task_id", "owner_agent", mode="before"
    )(_optional_text)

    @field_validator("acceptance_criteria")
    @classmethod
    def validate_criteria(cls, values: list[str]) -> list[str]:
        return [_text(value) for value in values]

    @model_validator(mode="after")
    def require_success_target(self) -> "TaskRecord":
        if not self.acceptance_criteria and not self.verification_target:
            raise ValueError("task requires acceptance criteria or verification target")
        if self.closed_at and self.closed_at < self.created_at:
            raise ValueError("closed_at cannot precede created_at")
        if not self.source_refs:
            raise ValueError("task requires provenance")
        return self


class OutcomeRecord(_StrictRecord):
    """Structured task result; never stores the assistant transcript."""

    outcome_id: str
    task_id: str
    session_id: str
    packet_id: str | None = None
    status: OutcomeStatus
    verification_refs: list[ProvenanceRef] = Field(default_factory=list)
    changed_ref: str | None = None
    checks: list[str] = Field(default_factory=list)
    human_intervention: bool = False
    started_at: datetime | None = None
    completed_at: datetime | None = None
    recorded_at: datetime
    summary: str

    _validate_identity = field_validator(
        "outcome_id", "task_id", "session_id", "summary", mode="before"
    )(_text)
    _validate_optional_identity = field_validator(
        "packet_id", "changed_ref", mode="before"
    )(_optional_text)

    @field_validator("checks")
    @classmethod
    def validate_checks(cls, values: list[str]) -> list[str]:
        return [_text(value) for value in values]

    @model_validator(mode="after")
    def require_verification_for_success(self) -> "OutcomeRecord":
        if self.status == "success" and not self.verification_refs and not self.checks:
            raise ValueError("successful outcome requires verification")
        if self.started_at and self.completed_at and self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        return self
