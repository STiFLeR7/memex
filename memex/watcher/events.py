from dataclasses import dataclass
from datetime import datetime
from typing import List

@dataclass
class FileChangeEvent:
    path: str          # absolute path
    repo_root: str     # absolute path to repo root
    kind: str          # "modified" | "created" | "deleted"
    timestamp: datetime

@dataclass
class CommitEvent:
    sha: str
    repo_root: str     # absolute path to repo root
    message: str
    diff: str          # output of git diff HEAD~1 HEAD
    files_changed: List[str]
    timestamp: datetime
