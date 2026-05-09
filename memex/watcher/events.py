from dataclasses import dataclass
from datetime import datetime
from typing import List

@dataclass
class FileChangeEvent:
    path: str          # absolute path
    kind: str          # "modified" | "created" | "deleted"
    timestamp: datetime

@dataclass
class CommitEvent:
    sha: str
    message: str
    diff: str          # output of git diff HEAD~1 HEAD
    files_changed: List[str]
    timestamp: datetime
