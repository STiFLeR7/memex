import logging
import asyncio
import os
import subprocess
import threading
from typing import Optional
from pathlib import Path
from datetime import datetime, UTC
from memex.watcher.events import FileChangeEvent, CommitEvent
from memex.extractor.treesitter import extract_symbol_delta, extract_calls
from memex.extractor.lockfile import (
    extract_dependencies,
    extract_module_imports,
    is_lockfile_path,
)
from memex.graph.writer import (
    write_symbol_delta, write_decision, write_lockfile_delta, write_call_edges,
)
from memex.synthesizer.commit import extract_decisions
from memex.graph.client import get_graph_client
from memex.config import canonical_repo_path
from memex.watcher import health

logger = logging.getLogger(__name__)

_notify_timer: Optional[threading.Timer] = None
_notify_lock = threading.Lock()

def notify_local_server() -> None:
    """
    Sends a POST notification request to http://127.0.0.1:<port>/notify in a background daemon thread
    to alert the local memex server/VS Code extension that the graph has been updated.
    Does not block execution and ignores all connection errors.
    """
    global _notify_timer
    
    with _notify_lock:
        if _notify_timer is not None:
            _notify_timer.cancel()
            
        def send_notification():
            global _notify_timer
            with _notify_lock:
                _notify_timer = None
                
            import urllib.request
            from pathlib import Path
            from memex.config import get_config
            
            port = 7463
            try:
                cfg = get_config()
                port_file = Path(cfg.repo_root) / ".memex" / "port"
                if port_file.exists():
                    content = port_file.read_text().strip()
                    if content.isdigit():
                        port = int(content)
            except Exception:
                pass
                
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/notify",
                    method="POST"
                )
                # Add a short timeout of 1.0s to avoid hanging if server is busy/deadlocks
                with urllib.request.urlopen(req, timeout=1.0) as response:
                    response.read()
            except Exception:
                # Silently ignore connection refused or timeout errors (offline server)
                pass

        _notify_timer = threading.Timer(0.5, send_notification)
        _notify_timer.start()


def cosine_similarity(v1: list[float], v2: list[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = sum(a * a for a in v1) ** 0.5
    norm2 = sum(b * b for b in v2) ** 0.5
    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    return dot / (norm1 * norm2)


async def corroborate_decisions(repo_root: str, sha: str, message: str, files_changed: list[str]) -> int:
    """
    Scans the graph for uncorroborated, unvalidated decisions and matches them against the current commit.
    Uses two-pass corroboration: first matching module paths, then matching text semantic similarity.
    """
    client = await get_graph_client()
    
    # 1. Fetch uncorroborated, unvalidated decisions
    query = """
    MATCH (d:Entity)
    WHERE (d.type = 'Decision' OR d.source = 'agent')
      AND (d.corroborated IS NULL OR d.corroborated = false)
      AND (d.validated IS NULL OR d.validated = false)
    OPTIONAL MATCH (d)-[:MOTIVATES|RELATES_TO|MENTIONS]-(m:Entity)
    WHERE coalesce(m.type, '') = 'Module' OR m.name ENDS WITH '.py' OR m.name ENDS WITH '.js'
    RETURN d.uuid as id, elementId(d) as eid, d.name as text, collect(m.name) as related_entities
    """
    
    try:
        res = await client.driver.execute_query(query)
        decisions = res.records
        logger.debug("Found %d uncorroborated, unvalidated decisions", len(decisions))
    except Exception:
        logger.error("Failed to query uncorroborated decisions", exc_info=True)
        return 0

    if not decisions:
        return 0

    # Get the embedding for the commit message. If it fails, fail open (log warning, skip corroboration).
    try:
        from memex.graph.cluster_summary import _embed_text
        commit_emb = await _embed_text(message)
        if not commit_emb:
            logger.warning("Embedding API returned empty embedding for commit message during corroboration.")
            return 0
    except Exception as e:
        logger.warning("Failed to compute embedding for commit message during corroboration (failing open): %s", e)
        return 0

    corroborated_count = 0
    now = datetime.now(UTC)
    CORROBORATION_SIMILARITY_THRESHOLD = 0.6
    
    for record in decisions:
        decision_id = record["id"] or record["eid"]
        decision_text = record["text"]
        related_entities = record["related_entities"] or []
        
        # Pass 1: File match (linked module must match at least one file changed)
        if not related_entities:
            continue
            
        file_matched = False
        for entity_name in related_entities:
            if any(entity_name == f or f.endswith(f"/{entity_name}") or entity_name.endswith(f"/{f}") for f in files_changed):
                file_matched = True
                break
                
        if not file_matched:
            continue

        # Pass 2: Semantic similarity check
        try:
            decision_emb = await _embed_text(decision_text)
            if not decision_emb:
                continue
        except Exception as e:
            logger.warning("Failed to compute embedding for decision text '%s' (skipping candidate): %s", decision_text, e)
            continue

        sim = cosine_similarity(commit_emb, decision_emb)
        if sim < CORROBORATION_SIMILARITY_THRESHOLD:
            logger.debug("Decision %s similarity %f below threshold %f", decision_id, sim, CORROBORATION_SIMILARITY_THRESHOLD)
            continue

        logger.info("Decision %s corroborated (similarity: %f)", decision_id, sim)

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
        # Canonical join key — must match the MCP server's read-side
        # config.repo_root so predict_impact can find these nodes (B1).
        repo_canon = canonical_repo_path(str(repo_root))

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
        summary = await write_symbol_delta(delta, source_commit=None, repo_root=repo_canon)
        logger.info(
            "symbols updated for %s: +%d -%d ~%d",
            rel_path, len(delta.added), len(delta.removed), len(delta.modified)
        )
        # Health: record a successful index pass + any NL episodes skipped
        # (e.g. Gemini quota) so `memex status`/`doctor` can surface it (Q1/B7).
        skipped = (summary or {}).get("episodes_skipped", 0)
        health.record(repo_canon, indexed_ok=True, episodes_skipped=skipped)

        # 5. Build CALLS edges from the current file content (v0.3.7 Layer 2).
        # Deterministic / LLM-free. Runs after the symbol MERGE so caller nodes
        # exist; callees resolve against whatever is already indexed.
        if event.kind != "deleted":
            ext = rel_path.rsplit(".", 1)[-1].lower()
            lang_map = {"py": "python"}
            language = lang_map.get(ext)
            if language:
                try:
                    calls = extract_calls(rel_path, new_content, language=language)
                    if calls:
                        written = await write_call_edges(calls, repo_root=repo_canon)
                        logger.info(
                            "call edges for %s: %d sites, %d edges written",
                            rel_path, len(calls), written,
                        )
                except Exception:
                    logger.error("call-edge extraction failed for %s", rel_path, exc_info=True)
        notify_local_server()
    except Exception:
        logger.error(
            "unhandled error in handle_file_change — skipping event",
            exc_info=True
        )
        health.record(canonical_repo_path(event.repo_root),
                      handler="handle_file_change", errors=1)

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
        notify_local_server()
    except Exception:
        logger.error(
            "unhandled error in handle_commit — skipping event",
            exc_info=True
        )
        health.record(canonical_repo_path(event.repo_root),
                      handler="handle_commit", errors=1)


async def initial_lockfile_index(repo_root: str) -> dict:
    """One-shot dependency + IMPORTS-edge scan, run at watcher startup.

    Audit B3: `handle_lockfile_change` only fires on a lockfile *change* event,
    so a freshly cloned repo whose lockfile never changes during a session
    never gets `IMPORTS` edges — `predict_impact`'s import dimension stays 0.
    This builds them once up front. Idempotent (the writer MERGEs), so re-runs
    on each daemon start are safe. repo_path is canonicalized to match the
    read-side join key (B1).
    """
    repo_canon = canonical_repo_path(repo_root)
    try:
        deps = await extract_dependencies(repo_canon)
    except Exception:
        logger.error("initial index: dependency extraction failed", exc_info=True)
        deps = []
    try:
        edges = await extract_module_imports(repo_canon)
    except Exception:
        logger.error("initial index: module-import extraction failed", exc_info=True)
        edges = []

    if not deps and not edges:
        return {"deps_written": 0, "edges_written": 0}

    try:
        summary = await write_lockfile_delta(repo_canon, deps, edges)
        logger.info(
            "initial lockfile index for %s: %d deps, %d import edges written",
            repo_canon, summary["deps_written"], summary["edges_written"],
        )
        return summary
    except Exception:
        logger.error("initial index: write_lockfile_delta failed", exc_info=True)
        return {"deps_written": 0, "edges_written": 0}


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

        # Canonical join key so IMPORTS/Dependency repo_path matches the
        # read-side (B1).
        repo_root = canonical_repo_path(event.repo_root)
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
            "lockfile change parsed for %s: %d deps, %d import edges",
            event.path,
            len(deps),
            len(edges),
        )

        # v0.3.1 Deliverable 5: actually persist the parsed payload.
        try:
            summary = await write_lockfile_delta(repo_root, deps, edges)
            logger.info(
                "lockfile change written for %s: %d deps written, %d edges written",
                event.path,
                summary["deps_written"],
                summary["edges_written"],
            )
            notify_local_server()
        except Exception:
            logger.error(
                "lockfile: write_lockfile_delta failed for %s",
                event.path,
                exc_info=True,
            )
    except Exception:
        logger.error(
            "unhandled error in handle_lockfile_change — skipping event",
            exc_info=True,
        )
        health.record(canonical_repo_path(event.repo_root),
                      handler="handle_lockfile_change", errors=1)
