"""Phase 9 — `explain_change` MCP tool (tool 11).

Cross-references a commit's diff with Decision/Problem nodes linked to the
affected files, then asks Gemini Pro to synthesise a grounded explanation.

Per ARCHITECTURE-v0.3.0.md §6: this is a synthesis task, not extraction —
Pro, not Flash. Output capped at ~2000 tokens via the formatter's char budget.
"""

import asyncio
import logging
import re
from typing import List, Dict, Any, Optional

from memex.config import get_config
from memex.graph.client import get_graph_client

logger = logging.getLogger(__name__)

# Mirror formatter.py token-budget convention (chars ≈ tokens × 4)
TOKEN_BUDGET = 2000
CHAR_BUDGET = TOKEN_BUDGET * 4

# S1 hardening: commit SHAs are hex-only, 4-40 chars (`git show` accepts
# abbreviated SHAs). Reject anything else BEFORE it reaches `git show`.
# Without this, an attacker-controlled MCP input could pass `--upload-pack=`
# or `--exec=…` as the SHA and have git treat it as a flag despite our use
# of execve (no shell interpretation, but git's own option parser is the
# vulnerable surface).
_COMMIT_SHA_RE = re.compile(r"^[A-Fa-f0-9]{4,40}$")


def _truncate(text: str, char_budget: int = CHAR_BUDGET) -> str:
    """Hard char-budget truncation with a clear marker."""
    if len(text) <= char_budget:
        return text
    marker = "\n\n[truncated — response exceeded token budget]"
    return text[: char_budget - len(marker)] + marker


async def _git_show(commit_sha: str, repo_path: Optional[str] = None) -> Optional[str]:
    """Returns the unified diff for `commit_sha` via `git show`, or None if
    the commit cannot be resolved. Uses asyncio subprocess per codebase
    convention so the MCP event loop isn't blocked.
    """
    cmd = ["git"]
    if repo_path:
        cmd.extend(["-C", repo_path])
    # `--` separator + regex validation at the public entry point form a
    # belt-and-braces defense against argument injection (see S1 in the
    # third review). With this separator git treats everything after it as
    # a positional argument, not a flag.
    cmd.extend(["show", "--no-color", "--unified=3", "--", commit_sha])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", errors="replace").strip()
            logger.info("git show failed for %s: %s", commit_sha, err[:200])
            return None
        return stdout.decode("utf-8", errors="replace")
    except FileNotFoundError:
        logger.warning("git executable not found on PATH — explain_change unavailable")
        return None
    except Exception:
        logger.error("git show subprocess failed", exc_info=True)
        return None


def _extract_changed_files(diff_text: str) -> List[str]:
    """Pull `diff --git a/<path> b/<path>` paths out of a unified diff."""
    files: List[str] = []
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            # Format: diff --git a/path b/path
            parts = line.split()
            if len(parts) >= 4:
                # Strip the b/ prefix
                bpath = parts[3]
                if bpath.startswith("b/"):
                    bpath = bpath[2:]
                files.append(bpath)
    return files


async def _query_linked_decisions_and_problems(
    files: List[str], repo: Optional[str]
) -> List[Dict[str, Any]]:
    """Query the graph for Decision / Problem nodes linked to any of the
    affected files. We look both for direct module links and for symbols
    living in those files.
    """
    if not files:
        return []

    client = await get_graph_client()
    query = """
    UNWIND $files AS fpath
    MATCH (m:Entity)
    WHERE (m.name = fpath OR m.path = fpath OR m.file = fpath)
      AND ($repo IS NULL OR m.repo_path = $repo)
    OPTIONAL MATCH (d:Entity)-[r:MOTIVATES|RELATES_TO|MENTIONS|CAUSED_BY]-(m)
    WHERE r.expired_at IS NULL
      AND (d.type IN ['Decision', 'Problem'] OR d.name CONTAINS 'Decision' OR d.name CONTAINS 'Problem')
    RETURN DISTINCT
        coalesce(d.type,
            CASE WHEN d.name CONTAINS 'Problem' THEN 'Problem' ELSE 'Decision' END
        ) AS node_type,
        d.name AS text,
        coalesce(d.uuid, elementId(d)) AS node_id,
        m.name AS module,
        coalesce(d.created_at, datetime()) AS date
    ORDER BY date DESC
    LIMIT 25
    """
    try:
        res = await client.driver.execute_query(query, params={"files": files, "repo": repo})
        return [r.data() for r in res.records if r.get("node_id")]
    except Exception:
        logger.error("explain_change graph lookup failed", exc_info=True)
        return []


def _build_prompt(commit_sha: str, diff_text: str, context_nodes: List[Dict[str, Any]]) -> str:
    """Compose a grounded synthesis prompt for Gemini Pro."""
    # Trim diff so the prompt itself fits comfortably
    diff_excerpt = diff_text if len(diff_text) <= 15000 else diff_text[:15000] + "\n[diff truncated]"

    context_lines: List[str] = []
    if context_nodes:
        for n in context_nodes:
            ntype = n.get("node_type", "Node")
            text = (n.get("text") or "").replace("\n", " ")
            module = n.get("module") or "unknown module"
            context_lines.append(f"- [{ntype}] {text} (module: {module})")
    else:
        context_lines.append("- (no related Decisions or Problems found in the graph)")

    context_block = "\n".join(context_lines)

    return (
        f"You are explaining a code change to an engineer joining the project. "
        f"Ground every claim in the diff and the graph context below — do NOT "
        f"invent decisions or rationale that aren't supported.\n\n"
        f"## Commit\n{commit_sha}\n\n"
        f"## Diff\n```\n{diff_excerpt}\n```\n\n"
        f"## Linked graph context (Decisions / Problems on affected files)\n"
        f"{context_block}\n\n"
        f"## Instructions\n"
        f"Produce a Markdown explanation with these sections:\n"
        f"1. **What changed** — concise list of the actual edits in the diff.\n"
        f"2. **Why (grounded)** — link each significant change back to a "
        f"Decision or Problem above when one applies. If none applies, say so.\n"
        f"3. **Risk** — single line about regression risk based on the linked nodes.\n"
        f"Keep the whole response under {TOKEN_BUDGET} tokens."
    )


async def _call_gemini_pro(prompt: str) -> str:
    """Synchronous Gemini Pro call wrapped in asyncio.to_thread.

    Mirrors the 3-attempt exponential backoff in
    ``memex/synthesizer/commit.py`` so transient 429 / rate-limit responses
    don't immediately surface as user-facing failures.
    """
    from google import genai

    config = get_config()
    model_id = getattr(config, "pro_model", "gemini-2.5-pro")
    client = genai.Client(api_key=config.gemini_api_key)

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=model_id,
                contents=prompt,
            )
            return getattr(response, "text", "") or ""
        except Exception as e:
            err_str = str(e).lower()
            last_exc = e
            if attempt < 2 and ("429" in err_str or "rate limit" in err_str):
                wait_time = (2 ** attempt) + 1
                logger.warning(
                    "Gemini Pro rate limit on explain_change; retry in %ds",
                    wait_time,
                )
                await asyncio.sleep(wait_time)
                continue
            raise
    # Defensive — loop above either returns or re-raises.
    if last_exc is not None:
        raise last_exc
    return ""


async def explain_change(commit_sha: str, repo: Optional[str] = None) -> str:
    """Cross-references a commit's diff with Decision/Problem nodes linked
    to the affected files and returns a Gemini-Pro-synthesised explanation.

    Returns a Markdown string under ~2000 tokens. Never raises into the MCP
    protocol — all failure modes degrade to a graceful string.
    """
    if not commit_sha or not commit_sha.strip():
        return "Error: commit_sha is required"

    commit_sha = commit_sha.strip()

    # S1 hardening: validate the SHA shape before invoking git. A prompt-
    # injected MCP caller could otherwise smuggle git flags here.
    if not _COMMIT_SHA_RE.match(commit_sha):
        return (
            f"Error: '{commit_sha}' is not a valid commit SHA "
            "(expected 4–40 hex characters)"
        )

    # Resolve repo for the git invocation
    repo_path = repo
    if not repo_path:
        try:
            config = get_config()
            repo_path = config.repo_root
        except Exception:
            repo_path = None

    diff_text = await _git_show(commit_sha, repo_path=repo_path)
    if diff_text is None:
        return f"could not find commit {commit_sha} — check the SHA and that the repo is a git working tree"

    files = _extract_changed_files(diff_text)
    context_nodes = await _query_linked_decisions_and_problems(files, repo=repo)

    prompt = _build_prompt(commit_sha, diff_text, context_nodes)

    try:
        synthesis = await _call_gemini_pro(prompt)
        if not synthesis.strip():
            result = _truncate(
                f"# explain_change\n\ncommit: {commit_sha}\n"
                f"synthesis returned empty response — check Gemini Pro availability"
            )
        else:
            result = _truncate(synthesis)
    except Exception as e:
        logger.error("Gemini Pro call failed in explain_change", exc_info=True)
        # Graceful fallback: surface the raw context so the agent still gets value
        ctx_text = "\n".join(
            f"- [{n.get('node_type','Node')}] {n.get('text','')}" for n in context_nodes
        ) or "- no linked context"
        result = _truncate(
            f"# explain_change [synthesis unavailable]\n\n"
            f"commit: {commit_sha}\n"
            f"changed files: {len(files)}\n\n"
            f"## Linked context (graph)\n{ctx_text}\n\n"
            f"_synthesis failed: {e}_"
        )

    try:
        from memex.graph.telemetry import record_tool_call
        await record_tool_call("explain_change", len(result) // 4, repo)
    except Exception:
        pass
    return result
