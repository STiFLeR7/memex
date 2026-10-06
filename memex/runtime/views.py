"""Git-derived identity and stable permitted-source capture."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
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


#: Set to a dict by the long-lived hook service (`memex.hookd`) so repeated
#: hooks skip re-running Git for the same directory. One-shot hooks never cache.
DISCOVERY_CACHE: dict | None = None


def toplevel(path: str | Path) -> Path:
    key = ("toplevel", str(path))
    if DISCOVERY_CACHE is not None and key in DISCOVERY_CACHE:
        return DISCOVERY_CACHE[key]
    value = Path(git_output(Path(path).resolve(), "rev-parse", "--show-toplevel")).resolve()
    if DISCOVERY_CACHE is not None:
        DISCOVERY_CACHE[key] = value
    return value


def _cached_registration(path: str | Path) -> RepositoryRegistration | None:
    """A remembered registration, valid only while its identity files still say the same."""
    found = DISCOVERY_CACHE.get(("registration", str(path))) if DISCOVERY_CACHE is not None else None
    if found is None:
        return None
    try:
        if ((Path(found.common_dir) / "memex" / "repository-id").read_text(encoding="ascii").strip() == found.repo_id
                and (Path(found.git_dir) / "memex" / "worktree-id").read_text(encoding="ascii").strip()
                == found.worktree_id):
            return found
    except OSError:
        pass
    DISCOVERY_CACHE.clear()
    return None


def discover_repository(path: str | Path) -> RepositoryRegistration:
    cached = _cached_registration(path)
    if cached is not None:
        return cached
    root = toplevel(path)
    common = Path(git_output(root, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    git_dir = Path(git_output(root, "rev-parse", "--absolute-git-dir")).resolve()
    repo_id = _identity(common / "memex" / "repository-id")
    worktree_id = _identity(git_dir / "memex" / "worktree-id")
    registration = RepositoryRegistration(
        str(root), str(common), str(git_dir), repo_id, worktree_id,
        str(common / "memex" / "runtime.sqlite"),
    )
    if DISCOVERY_CACHE is not None:
        DISCOVERY_CACHE[("registration", str(path))] = registration
    return registration


_EXCLUDED_PARTS = {".git", ".memex", ".venv", "venv", "node_modules", "__pycache__"}


_OBJECT_ID = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?")


def _head_from_files(registration: RepositoryRegistration) -> str | None:
    """HEAD's commit read from Git's own files, or None when only Git can say.

    Saves a process per capture. Anything unusual (unborn branch, reftable,
    symbolic chains) returns None and the caller asks Git.
    """
    try:
        head = (Path(registration.git_dir) / "HEAD").read_text(encoding="utf-8").strip()
        if _OBJECT_ID.fullmatch(head):
            return head  # Detached.
        if not head.startswith("ref: refs/"):
            return None
        ref = head[5:]
        for base in (Path(registration.git_dir), Path(registration.common_dir)):
            loose = base / ref
            if loose.is_file():
                value = loose.read_text(encoding="utf-8").strip()
                return value if _OBJECT_ID.fullmatch(value) else None
        packed = Path(registration.common_dir) / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                value, _, name = line.partition(" ")
                if name == ref and _OBJECT_ID.fullmatch(value):
                    return value
    except OSError:
        pass
    return None


def _list_sources(registration: RepositoryRegistration) -> list[str]:
    raw = subprocess.check_output([
        "git", "-C", str(registration.root), "ls-files", "-z", "--cached", "--others", "--exclude-standard",
    ])
    return sorted(set(raw.decode("utf-8").split("\0")) - {""})


def _capture_once(registration: RepositoryRegistration, names: list[str] | None = None) -> SourceCapture:
    root = Path(registration.root)
    head = _head_from_files(registration)
    if head is None:
        try:
            head = git_output(root, "rev-parse", "--verify", "HEAD")
        except subprocess.CalledProcessError:
            head = None
    files: dict[str, bytes] = {}
    for name in _list_sources(registration) if names is None else names:
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


def capture_sources(registration: RepositoryRegistration, *, relist: bool = True) -> SourceCapture:
    """Require two matching captures, including HEAD; reject detected drift.

    This is a bounded stability check, not a filesystem lock against arbitrary
    concurrent writers. Action-time hash checks and guarded writes are later phases.

    `relist=False` lists the files once and reads them twice. It is only for
    callers that finish with `unchanged_since`, whose fresh listing is the second.
    """
    names = None if relist else _list_sources(registration)
    before = _capture_once(registration, names)
    for _ in range(2):
        after = _capture_once(registration, names)
        if (before.head_commit, before.manifest_hash) == (after.head_commit, after.manifest_hash):
            return after
        before = after
    raise RuntimeError("repository changed during source capture")


def unchanged_since(registration: RepositoryRegistration, capture: SourceCapture) -> bool:
    """One more read agreeing with an earlier stable capture: nothing moved in between."""
    after = _capture_once(registration)
    return (after.head_commit, after.manifest_hash) == (capture.head_commit, capture.manifest_hash)
