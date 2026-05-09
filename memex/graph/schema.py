from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List

@dataclass
class BaseNode:
    """Base fields for all memex nodes."""
    valid_from: datetime = field(default_factory=datetime.utcnow)
    valid_until: Optional[datetime] = None
    confidence: float = 1.0
    source_commit: Optional[str] = None

@dataclass
class Module(BaseNode):
    path: str = ""
    language: str = ""
    description: Optional[str] = None

@dataclass
class Symbol(BaseNode):
    name: str = ""
    kind: str = ""  # fn, class, const, etc.
    signature: Optional[str] = None
    file: str = ""
    line: int = 0

@dataclass
class Decision(BaseNode):
    text: str = ""
    rationale: Optional[str] = None
    scope: str = "local"  # local, module, project

@dataclass
class Problem(BaseNode):
    text: str = ""
    severity: str = "medium"  # low, medium, high, critical
    status: str = "open"  # open, closed

@dataclass
class AgentSession(BaseNode):
    agent: str = ""  # Claude Code, Gemini CLI, etc.
    started_at: datetime = field(default_factory=datetime.utcnow)
    summary: Optional[str] = None

@dataclass
class Dependency(BaseNode):
    name: str = ""
    version: str = ""
    ecosystem: str = ""  # pypi, npm, etc.
