"""Immutable repository-content identity, separate from index progress."""

from dataclasses import dataclass
import hashlib
import json
import re


@dataclass(frozen=True)
class RepositoryView:
    repo_id: str
    worktree_id: str
    head_commit: str | None
    content_generation: int
    indexed_generation: int
    manifest_hash: str

    def __post_init__(self) -> None:
        if not self.repo_id.strip() or not self.worktree_id.strip():
            raise ValueError("repository and worktree identities are required")
        if not (0 <= self.indexed_generation <= self.content_generation):
            raise ValueError("invalid indexing progress")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.manifest_hash):
            raise ValueError("manifest_hash requires a SHA-256 digest")

    @property
    def view_id(self) -> str:
        identity = [self.repo_id, self.worktree_id, self.head_commit,
                    self.content_generation, self.manifest_hash]
        encoded = json.dumps(identity, separators=(",", ":")).encode("utf-8")
        return "view:" + hashlib.sha256(encoded).hexdigest()
