"""Conservative boundary for deliberate engineering-knowledge promotion."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from memex.context.packet import ProvenanceRef, RepositoryScope

CandidateKind = Literal["decision", "problem", "outcome", "task", "engineering_event"]
PersistSink = Callable[["IngestionCandidate"], Any]

_TRANSCRIPT_PATTERN = re.compile(r"(?:^|\n)\s*(?:user|assistant|system|tool)\s*:", re.IGNORECASE)
_SECRET_PATTERNS = (
    re.compile(r"\b(?:ghp|github_pat|sk)-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:bearer|token|password|api[_-]?key)\s*[=:]\s*\S+", re.IGNORECASE),
)


def _text(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("must be a non-empty string")
    return value.strip()


class IngestionCandidate(BaseModel):
    """Structured candidate; raw transcripts and tool payloads have no field."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    kind: CandidateKind
    repository: RepositoryScope
    summary: str
    source_refs: list[ProvenanceRef]
    evidence_refs: list[ProvenanceRef] = Field(default_factory=list)
    task_id: str | None = None
    session_id: str | None = None
    explicit_promotion: bool = False
    verified: bool = False

    _validate_required_text = field_validator("candidate_id", "summary", mode="before")(_text)

    @field_validator("summary")
    @classmethod
    def bound_summary(cls, value: str) -> str:
        if len(value) > 2_000:
            raise ValueError("summary exceeds 2000 characters")
        return value

    @field_validator("task_id", "session_id", mode="before")
    @classmethod
    def optional_text(cls, value: str | None) -> str | None:
        return None if value is None else _text(value)

    @model_validator(mode="after")
    def validate_promotion_requirements(self) -> "IngestionCandidate":
        if not self.source_refs:
            raise ValueError("candidate requires provenance")
        if not any(source.observed_at is not None for source in self.source_refs):
            raise ValueError("candidate requires observed provenance")
        if self.kind == "outcome" and not self.task_id:
            raise ValueError("outcome candidate requires task_id")
        if self.kind in {"outcome", "engineering_event"} and self.verified and not self.evidence_refs:
            raise ValueError("verified candidate requires evidence")
        return self


@dataclass(frozen=True)
class IngestionResult:
    status: Literal["rejected", "persisted", "failed"]
    reason: str
    candidate_id: str
    persisted_ref: str | None = None


def _candidate_text(candidate: IngestionCandidate) -> str:
    values = [
        candidate.summary,
        candidate.repository.project_id,
        candidate.repository.repo_path,
        candidate.task_id,
        candidate.session_id,
        *(source.ref for source in candidate.source_refs),
        *(source.ref for source in candidate.evidence_refs),
    ]
    return "\n".join(value for value in values if value)


def _policy_reason(candidate: IngestionCandidate) -> str | None:
    candidate_text = _candidate_text(candidate)
    if not candidate.explicit_promotion:
        return "explicit_promotion_required"
    if _TRANSCRIPT_PATTERN.search(candidate_text) or "tool_call" in candidate_text.lower() or "tool_result" in candidate_text.lower():
        return "raw_transcript_rejected"
    if any(pattern.search(candidate_text) for pattern in _SECRET_PATTERNS):
        return "sensitive_content_rejected"
    if candidate.kind in {"outcome", "engineering_event"} and not candidate.verified:
        return "verified_outcome_required"
    if candidate.kind in {"outcome", "engineering_event"} and not candidate.evidence_refs:
        return "evidence_required"
    return None


def ingest_candidate(candidate: IngestionCandidate, persist: PersistSink) -> IngestionResult:
    """Authorize then explicitly persist one structured candidate.

    The caller supplies the governed sink. No Hermes hook calls this function,
    and this boundary never reads or stores transcripts.
    """

    reason = _policy_reason(candidate)
    if reason:
        return IngestionResult("rejected", reason, candidate.candidate_id)
    try:
        persisted = persist(candidate)
        persisted_ref = candidate.candidate_id if persisted is None else str(persisted)
        return IngestionResult("persisted", "accepted", candidate.candidate_id, persisted_ref)
    except Exception:
        return IngestionResult("failed", "persistence_failed", candidate.candidate_id)
