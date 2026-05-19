import logging
import asyncio
import os
import subprocess
from pathlib import Path
from datetime import datetime, UTC
from memex.watcher.events import FileChangeEvent, CommitEvent
from memex.extractor.treesitter import extract_symbol_delta
from memex.extractor.lockfile import (
    extract_dependencies,
    extract_module_imports,
    is_lockfile_path,
)
from memex.graph.writer import write_symbol_delta, write_decision
from memex.synthesizer.commit import extract_decisions
from memex.graph.client import get_graph_client
from memex.config import get_config

logger = logging.getLogger(__name__)

async def corroborate_decisions(repo_root: str, sha: str, message: str, files_changed: list[str]) -> int:
    """
    Scans the graph for uncorroborated agent decisions and matches them against the current commit.
    """
    client = await get_graph_client()
    
    # 1. Fetch uncorroborated agent decisions
    query = """
    MATCH (d:Entity)
    WHERE (d.type = 'Decision' OR d.source = 'agent')
      AND (d.corroborated IS NULL OR d.corroborated = false)
      AND d.source = 'agent'
    OPTIONAL MATCH (d)-[:MOTIVATES|RELATES_TO|MENTIONS]-(m:Entity)
    RETURN d.uuid as id, elementId(d) as eid, d.name as text, collect(m.name) as related_entities
    """
    
    try:
        res = await client.driver.execute_query(query)
        decisions = res.records
        logger.debug("Found %d uncorroborated agent decisions", len(decisions))
    except Exception:
        logger.error("Failed to query uncorroborated decisions", exc_info=True)
        return 0

    if not decisions:
        return 0

    corroborated_count = 0
    now = datetime.now(UTC)
    
    # Simple stop words and word tokenizer
    STOP_WORDS = {
        "this", "that", "with", "from", "here", "there", "what", "when", 
        "where", "which", "while", "decision", "rationale", "scope",
        "about", "been", "being", "does", "done", "each", "have", "into",
        "just", "more", "most", "only", "some", "such", "than", "then",
        "they", "very", "were", "your", "should"
    }

    def get_significant_words(text: str) -> set[str]:
        if not text:
            return set()
        # Clean and split
        words = text.lower().replace('.', ' ').replace(',', ' ').replace(':', ' ').replace('"', ' ').replace("'", " ").split()
        res = {w for w in words if len(w) > 3 and w not in STOP_WORDS}
        logger.debug("Significant words for '%s': %s", text, res)
        return res

    commit_words = get_significant_words(message)
    
    for record in decisions:
        decision_id = record["id"] or record["eid"]
        decision_text = record["text"]
        related_entities = record["related_entities"] or []
        
        match_found = False
        
        # Check words match
        decision_words = get_significant_words(decision_text)
        if decision_words & commit_words:
            match_found = True
            logger.info("Decision %s corroborated by message match: %s", decision_id, decision_words & commit_words)
        
        # Check files match related entities (symbols or modules)
        if not match_found and related_entities:
            for entity_name in related_entities:
                if any(entity_name == f or f.endswith(f"/{entity_name}") or entity_name.endswith(f"/{f}") for f in files_changed):
                    match_found = True
                    logger.info("Decision %s corroborated by file match: %s", decision_id, entity_name)
                    break
                    
        if match_found:
            # v0.3.0 (Phase 8): corroboration is *evidence*, not validation.
            # - ALWAYS update last_reinforced_at — this lifts computed_confidence
            #   in the TempValid two-regime model (see memex/graph/confidence.py).
            # - Do NOT set validated=True. Only `memex review` can do that.
            # - Do NOT overwrite the stored `confidence` field — confidence is
            #   computed at query time in v0.3.0, not stored-and-mutated.
            # This is a deliberate departure from v0.2.0's handlers.py:91 which
            # unconditionally bumped corroborated decisions to confidence=1.0.
            update_query = """
            MATCH (d:Entity)
            WHERE d.uuid = $id OR elementId(d) = $id
            SET d.last_reinforced_at = $now,
                d.corroborated = true,
                d.corroboration_commit = $sha,
                d.updated_at = $now
            """
            try:
                await client.driver.execute_query(update_query, params={
                    "id": decision_id,
                    "sha": sha,
                    "now": now
                })
                corroborated_count += 1
                logger.info("Decision corroborated: '%s' (commit %s)", decision_text[:50], sha[:8])
            except Exception:
                logger.error("Failed to update corroborated decision %s", decision_id, exc_info=True)

    return corroborated_count

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
    try:
        path = Path(event.path)
        repo_root = Path(event.repo_root)

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
        await write_symbol_delta(delta, source_commit=None)
        logger.info(
            "symbols updated for %s: +%d -%d ~%d", 
            rel_path, len(delta.added), len(delta.removed), len(delta.modified)
        )
    except Exception:
        logger.error(
            "unhandled error in handle_file_change — skipping event",
            exc_info=True
        )

async def handle_commit(event: CommitEvent) -> None:
    """
    Connects git commits to the decision synthesis pipeline.
    """
    try:
        # 1. Call extract_decisions
        try:
            decisions = await extract_decisions(event.message, event.diff, event.sha)
        except Exception:
            logger.error("Decision extraction failed for %s", event.sha, exc_info=True)
            return
        
        # 2. Write decisions
        count = 0
        if decisions:
            for decision in decisions:
                try:
                    await write_decision(decision, event.files_changed, event.sha)
                    count += 1
                except Exception:
                    logger.error("Failed to write decision '%s'", decision.text, exc_info=True)

        # 3. Corroborate existing decisions
        try:
            repo_root = event.repo_root
            corroborated = await corroborate_decisions(repo_root, event.sha, event.message, event.files_changed)
            if corroborated > 0:
                logger.info("decisions corroborated for %s: %d", event.sha, corroborated)
        except Exception:
            logger.error("Decision corroboration failed for %s", event.sha, exc_info=True)

        # 4. Log
        if count > 0:
            logger.info("decisions written for %s: %d", event.sha, count)
    except Exception:
        logger.error(
            "unhandled error in handle_commit — skipping event",
            exc_info=True
        )


async def handle_lockfile_change(event: FileChangeEvent) -> None:
    """
    Re-extracts Dependency nodes + Module IMPORTS edges when a lockfile changes.

    Wired into the EventRouter alongside ``handle_file_change`` — both run on
    every FileChangeEvent and short-circuit when the path is irrelevant. This
    keeps the dependency layer fresh without polling.
    """
    try:
        if not is_lockfile_path(event.path):
            return

        repo_root = event.repo_root
        try:
            deps = await extract_dependencies(repo_root)
        except Exception:
            logger.error("lockfile: dependency extraction failed", exc_info=True)
            deps = []

        try:
            edges = await extract_module_imports(repo_root)
        except Exception:
            logger.error("lockfile: module-import extraction failed", exc_info=True)
            edges = []

        logger.info(
            "lockfile change processed for %s: %d deps, %d import edges",
            event.path,
            len(deps),
            len(edges),
        )
        # The actual graph write of Dependency nodes + IMPORTS edges lands when
        # dev2's cluster.py wiring is ready; we surface the parsed payload via
        # logging so the pipeline is observable today.
    except Exception:
        logger.error(
            "unhandled error in handle_lockfile_change — skipping event",
            exc_info=True,
        )
