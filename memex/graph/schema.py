from pydantic import BaseModel, field_validator, ConfigDict
from datetime import datetime
from typing import Optional

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
        if v is None: return v
        return str(v).replace('\x00', '').replace('\u202e', '')

class Module(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    path: str
    language: str
    description: Optional[str] = None
    created_at: Optional[datetime] = None

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

    @field_validator("text")
    @classmethod
    def text_not_empty(cls, v):
        if not v or not str(v).strip():
            raise ValueError("decision text cannot be empty")
        return str(v).replace('\x00', '')

class Problem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    text: str
    severity: str  # "critical" | "high" | "medium" | "low"
    status: str = "open"
    created_at: Optional[datetime] = None
    surfaced_by: str = "watcher"

    @field_validator("severity")
    @classmethod
    def severity_must_be_valid(cls, v):
        if v not in ("critical", "high", "medium", "low"):
            return "medium" # Coerce to medium
        return v

class AgentSession(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    name: str
    started_at: datetime
    repo_path: str
    summary: Optional[str] = None

class Dependency(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    name: str
    version: str
    ecosystem: str
    last_updated: datetime

# Aliases for prompt compatibility
SymbolNode = Symbol
ModuleNode = Module
DecisionNode = Decision
ProblemNode = Problem
AgentSessionNode = AgentSession
DependencyNode = Dependency
