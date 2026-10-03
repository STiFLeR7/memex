"""Git-derived identity and stable permitted-source capture."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid


@dataclass(frozen=True)
class RepositoryRegistration:
    root: str
    common_dir: str
    git_dir: str
    repo_id: str
    worktree_id: str
    runtime_path: str


@dataclass(frozen=True)
class SourceCapture:
    head_commit: str | None
    manifest_hash: str
    files: dict[str, bytes]


def git_output(root: str | Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], stderr=subprocess.PIPE,
    ).decode("utf-8").strip()


def _identity(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        for _ in range(50):
            value = path.read_text(encoding="ascii").strip()
            if value:
                uuid.UUID(value)
                return value
            time.sleep(0.01)  # Another registering process may still be writing.
        raise RuntimeError("incomplete repository identity file")
    value = str(uuid.uuid4())
    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    return value


def discover_repository(path: str | Path) -> RepositoryRegistration:
    root = Path(git_output(Path(path).resolve(), "rev-parse", "--show-toplevel")).resolve()
    common = Path(git_output(root, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    git_dir = Path(git_output(root, "rev-parse", "--absolute-git-dir")).resolve()
    repo_id = _identity(common / "memex" / "repository-id")
    worktree_id = _identity(git_dir / "memex" / "worktree-id")
    return RepositoryRegistration(
        str(root), str(common), str(git_dir), repo_id, worktree_id,
        str(common / "memex" / "runtime.sqlite"),
    )


_EXCLUDED_PARTS = {".git", ".memex", ".venv", "venv", "node_modules", "__pycache__"}


def _capture_once(registration: RepositoryRegistration) -> SourceCapture:
    root = Path(registration.root)
    try:
        head = git_output(root, "rev-parse", "--verify", "HEAD")
    except subprocess.CalledProcessError:
        head = None
    raw = subprocess.check_output([
        "git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard",
    ])
    files: dict[str, bytes] = {}
    for name in sorted(set(raw.decode("utf-8").split("\0")) - {""}):
        relative = Path(name)
        if (relative.is_absolute() or ".." in relative.parts
                or any(part.lower() in _EXCLUDED_PARTS for part in relative.parts)):
            continue
        if relative.name.lower().startswith(".env") or "cred" in relative.name.lower():
            continue
        source = root / relative
        if not source.resolve().is_relative_to(root):
            raise ValueError("source path escapes registered worktree")
        try:
            if source.stat().st_size > 5_000_000 or len(files) >= 10_000:
                raise ValueError("source capture budget exceeded")
            content = source.read_bytes()
        except FileNotFoundError:
            continue  # A tracked file may be deliberately deleted in the overlay.
        if len(content) > 5_000_000 or len(files) >= 10_000:
            raise ValueError("source capture budget exceeded")
        files[relative.as_posix()] = content
    manifest = [(name, hashlib.sha256(content).hexdigest()) for name, content in files.items()]
    digest = hashlib.sha256(json.dumps(manifest, separators=(",", ":")).encode()).hexdigest()
    return SourceCapture(head, "sha256:" + digest, files)


def capture_sources(registration: RepositoryRegistration) -> SourceCapture:
    """Require two matching captures, including HEAD; reject detected drift.

    This is a bounded stability check, not a filesystem lock against arbitrary
    concurrent writers. Action-time hash checks and guarded writes are later phases.
    """
    before = _capture_once(registration)
    for _ in range(2):
        after = _capture_once(registration)
        if (before.head_commit, before.manifest_hash) == (after.head_commit, after.manifest_hash):
            return after
        before = after
    raise RuntimeError("repository changed during source capture")
