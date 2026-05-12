import json
import os
import subprocess
import sys
import logging
from datetime import datetime, UTC
from pathlib import Path

logger = logging.getLogger(__name__)

def install_hooks(repo_root: str) -> None:
    """
    Installs post-commit and post-merge hooks in the target repository.
    """
    hooks_dir = Path(repo_root) / ".git" / "hooks"
    if not hooks_dir.exists():
        raise ValueError(f"Not a git repository: {repo_root}")

    # Use sys.executable to ensure we use the same Python environment
    python_path = sys.executable
    # Use unix line endings for git hooks even on Windows
    hook_content = f"""#!/bin/sh
"{python_path}" -m memex.watcher.git_hook emit --repo "{os.path.abspath(repo_root)}"
"""

    for hook_name in ["post-commit", "post-merge"]:
        hook_path = hooks_dir / hook_name
        # Open with newline='\n' to ensure LF on all platforms
        with open(hook_path, "w", newline='\n') as f:
            f.write(hook_content)

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
        
        pending_file = memex_dir / "pending_commit.json"
        # Atomic-ish write on same filesystem
        tmp_file = pending_file.with_suffix(".tmp")
        tmp_file.write_text(json.dumps(event_data))
        if pending_file.exists():
            pending_file.unlink()
        tmp_file.rename(pending_file)
        
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
