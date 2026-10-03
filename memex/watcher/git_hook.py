import json
import os
import subprocess
import sys
import logging
import shlex
import tempfile
import uuid
import time
from contextlib import contextmanager
from datetime import datetime, UTC
from pathlib import Path

logger = logging.getLogger(__name__)

def install_hooks(repo_root: str) -> None:
    """
    Compose Git-configured hooks, resolving the invoking worktree at execution.
    """
    try:
        hooks_dir = Path(subprocess.check_output(
            ["git", "rev-parse", "--path-format=absolute", "--git-path", "hooks"],
            cwd=repo_root, stderr=subprocess.PIPE,
        ).decode().strip())
    except subprocess.CalledProcessError as error:
        raise ValueError(f"Not a git repository: {repo_root}") from error
    hooks_dir.mkdir(parents=True, exist_ok=True)
    with _installation_lock(hooks_dir):
        _install_hooks_locked(hooks_dir)


@contextmanager
def _installation_lock(hooks_dir):
    """OS-released cross-process lock, including concurrent linked worktrees."""
    with open(hooks_dir / ".memex-install.lock", "a+b") as stream:
        stream.write(b"0")
        stream.flush()
        deadline = time.monotonic() + 10
        while True:
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("another process is installing memex hooks")
                time.sleep(0.02)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _install_hooks_locked(hooks_dir):
    python_path = shlex.quote(Path(sys.executable).as_posix())
    marker = "# memex composed hook v1"
    for hook_name in ["post-commit", "post-merge", "post-checkout"]:
        hook_path = hooks_dir / hook_name
        backup = hooks_dir / (hook_name + ".memex-original")
        if hook_path.exists():
            content = hook_path.read_text(encoding="utf-8")
            if marker in content:
                continue
            # Migrate the previous generated hook instead of running it twice.
            legacy = (content.startswith("#!/bin/sh\n") and len(content.splitlines()) == 2
                      and " -m memex.watcher.git_hook emit --repo " in content)
            if not legacy:
                if backup.exists():
                    raise ValueError(f"Refusing to overwrite preserved hook: {backup}")
                hook_path.replace(backup)
                backup.chmod(backup.stat().st_mode | 0o111)
        original = (
            f'"$(dirname "$0")/{backup.name}" "$@" || status=$?\n' if backup.exists() else ""
        )
        hook_content = (
            f'#!/bin/sh\n{marker}\nstatus=0\n{original}'
            'root=$(git rev-parse --show-toplevel) || exit "$status"\n'
            f'{python_path} -m memex.watcher.git_hook emit --repo "$root" || :\n'
            'exit "$status"\n'
        )
        _atomic_write(hook_path, hook_content)
        hook_path.chmod(hook_path.stat().st_mode | 0o111)


def _atomic_write(path: Path, content: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".memex-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)

def emit_commit_event(repo_root: str) -> None:
    """
    Reads the latest git commit and writes a CommitEvent to a sidecar file.
    """
    repo_root = os.path.abspath(repo_root)
    
    # 1. Get current commit metadata
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root).decode().strip()
        message = subprocess.check_output(["git", "log", "-1", "--pretty=%B"], cwd=repo_root).decode().strip()
        
        # Try to get diff between current and previous
        try:
            diff = subprocess.check_output(["git", "diff", "HEAD~1", "HEAD"], cwd=repo_root).decode().strip()
            files_changed = subprocess.check_output(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"], cwd=repo_root).decode().strip().split("\n")
        except subprocess.CalledProcessError:
            # Fallback for initial commit (no HEAD~1)
            diff = subprocess.check_output(["git", "show", "HEAD"], cwd=repo_root).decode().strip()
            files_changed = subprocess.check_output(["git", "show", "--pretty=", "--name-only", "HEAD"], cwd=repo_root).decode().strip().split("\n")
            
        event_data = {
            "sha": sha,
            "message": message,
            "diff": diff,
            "files_changed": files_changed,
            "timestamp": datetime.now(UTC).isoformat()
        }

        memex_dir = Path(repo_root) / ".memex"
        memex_dir.mkdir(exist_ok=True)
        
        spool = memex_dir / "pending_commits"
        spool.mkdir(exist_ok=True)
        payload = json.dumps(event_data)
        _atomic_write(spool / (uuid.uuid4().hex + ".json"), payload)
        # Compatibility with older daemons; the durable spool is authoritative.
        _atomic_write(memex_dir / "pending_commit.json", payload)
        
    except Exception as e:
        logger.warning("Failed to extract git commit metadata: %s", e)
        return

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    
    emit_parser = subparsers.add_parser("emit")
    emit_parser.add_argument("--repo", required=True)
    
    args = parser.parse_args()
    if args.command == "emit":
        # Setup simple logging for the hook execution
        logging.basicConfig(level=logging.WARNING)
        emit_commit_event(args.repo)
