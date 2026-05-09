import logging
import asyncio
import os
from pathlib import Path
from memex.watcher.events import FileChangeEvent, CommitEvent
from memex.extractor.treesitter import extract_symbol_delta
from memex.graph.writer import write_symbol_delta, write_decision
from memex.synthesizer.commit import extract_decisions

logger = logging.getLogger(__name__)

async def run_git_command(args: list[str], cwd: str) -> str:
    """Run git command asynchronously to avoid blocking the event loop."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, args, stdout, stderr)
    return stdout.decode(errors="ignore")

async def handle_file_change(event: FileChangeEvent) -> None:
    """
    Connects filesystem changes to the symbol extraction pipeline.
    """
    path = Path(event.path)
    repo_root = None
    
    current = path.parent
    while current != current.parent:
        if (current / ".git").exists():
            repo_root = current
            break
        current = current.parent
    
    if not repo_root:
        logger.warning("Could not find repo root for %s", event.path)
        return

    rel_path = os.path.relpath(event.path, repo_root)
    # Git requires forward slashes for paths regardless of OS
    git_rel_path = Path(rel_path).as_posix()
    
    # 1. Read current content
    new_content = ""
    if event.kind != "deleted":
        try:
            new_content = path.read_text(errors="ignore")
        except FileNotFoundError:
            # File was deleted after the event but before we read it
            logger.debug("File not found during read: %s", event.path)
            return
        except Exception:
            logger.error("Failed to read current content of %s", event.path, exc_info=True)
            return

    # 2. Read previous content from git asynchronously
    old_content = ""
    try:
        old_content = await run_git_command(
            ["git", "show", f"HEAD:{git_rel_path}"],
            cwd=str(repo_root)
        )
    except Exception:
        # File might be new or untracked
        old_content = ""

    # 3. Call extract_symbol_delta
    delta = await extract_symbol_delta(rel_path, old_content, new_content)
    
    if not delta.added and not delta.removed and not delta.modified:
        return

    # 4. Call write_symbol_delta
    try:
        await write_symbol_delta(delta, source_commit=None)
        logger.info(
            "symbols updated for %s: +%d -%d ~%d", 
            rel_path, len(delta.added), len(delta.removed), len(delta.modified)
        )
    except Exception:
        logger.error("Failed to write symbol delta for %s", rel_path, exc_info=True)

async def handle_commit(event: CommitEvent) -> None:
    """
    Connects git commits to the decision synthesis pipeline.
    """
    # 1. Call extract_decisions
    try:
        decisions = await extract_decisions(event.message, event.diff, event.sha)
    except Exception:
        logger.error("Decision extraction failed for %s", event.sha, exc_info=True)
        return
    
    if not decisions:
        logger.debug("No decisions extracted from commit %s", event.sha)
        return

    # 2. Write decisions
    count = 0
    for decision in decisions:
        try:
            await write_decision(decision, event.files_changed, event.sha)
            count += 1
        except Exception:
            logger.error("Failed to write decision '%s'", decision.text, exc_info=True)

    # 3. Log
    logger.info("decisions written for %s: %d", event.sha, count)
