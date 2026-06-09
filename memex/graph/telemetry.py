import os
import logging
import sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

NAIVE_MULTIPLIERS: Dict[str, int] = {
    "get_recent_decisions": 5,
    "search_context":       12,
    "get_stale_context":    3,
    "get_open_problems":    4,   # fallback when no module scope
    "explain_change":       6,
    "predict_impact":       6,
}

KNOWN_AGENTS = {"claude-code", "gemini-cli", "codex", "cursor", "continue"}

ENV_AGENT_HINTS: Dict[str, str] = {
    "CLAUDE_CODE":       "claude-code",
    "GEMINI_CLI":        "gemini-cli",
    "OPENAI_CODEX":      "codex",
    "CURSOR_EDITOR":     "cursor",
    "CONTINUE_DEV":      "continue",
}

def get_telemetry_db_path() -> Path:
    """Returns the user-global path to the telemetry database."""
    return Path.home() / ".memex" / "telemetry.db"

def estimate_naive_tokens(
    tool_name: str,
    tokens_returned: int,
    repo_root: Optional[str] = None,
    module_files: Optional[List[str]] = None,
) -> Optional[int]:
    """
    Returns estimated naive token count, or None if estimation is not meaningful.

    For scoped tools (get_project_context, get_symbol_context, get_open_problems
    with a module), stats the actual source files: sum(file_bytes) // 4.
    For unscoped tools, applies NAIVE_MULTIPLIERS[tool_name].
    Falls back to None for tools not in the multiplier table.

    Uses os.stat() only — never reads file content. Sub-millisecond.
    """
    try:
        if tool_name == "get_project_context":
            if not module_files or not repo_root:
                return None
            total_size = 0
            for f in module_files:
                abs_path = Path(repo_root) / f
                try:
                    total_size += os.stat(abs_path).st_size
                except OSError:
                    # Fall back to a default size for this file (e.g. 4000 bytes)
                    total_size += 4000
            return total_size // 4

        elif tool_name == "get_symbol_context":
            if not module_files or not repo_root:
                return None
            abs_path = Path(repo_root) / module_files[0]
            try:
                return os.stat(abs_path).st_size // 4
            except OSError:
                # Fall back to a default multiplier
                return tokens_returned * 8

        elif tool_name == "get_open_problems":
            if module_files and repo_root:
                abs_path = Path(repo_root) / module_files[0]
                try:
                    return os.stat(abs_path).st_size // 4
                except OSError:
                    return tokens_returned * NAIVE_MULTIPLIERS["get_open_problems"]
            else:
                return tokens_returned * NAIVE_MULTIPLIERS["get_open_problems"]

        multiplier = NAIVE_MULTIPLIERS.get(tool_name)
        if multiplier is not None:
            return tokens_returned * multiplier
    except Exception as e:
        logger.warning(f"Error during estimate_naive_tokens for {tool_name}: {e}")
    return None

def detect_agent(client_info_name: Optional[str] = None) -> str:
    """
    1. If client_info_name is set and recognised → return it.
    2. Check ENV_AGENT_HINTS for known env vars → return matched agent.
    3. Return "unknown".
    """
    if client_info_name:
        return client_info_name[:64]

    # Try context variable first if we are inside an MCP request
    try:
        from mcp.server.lowlevel.server import request_ctx
        ctx = request_ctx.get(None)
        if ctx and ctx.session and ctx.session.client_params:
            name = ctx.session.client_params.clientInfo.name
            if name:
                return name[:64]
    except Exception:
        pass

    # Check env vars
    for var, agent_name in ENV_AGENT_HINTS.items():
        if os.getenv(var):
            return agent_name

    return "unknown"

def normalize_repo_path(p: str) -> str:
    try:
        from memex.config import canonical_repo_path
        val = canonical_repo_path(p)
        if val:
            return val
    except Exception:
        pass
    return os.path.abspath(p)

class TelemetryDB:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        if db_path is None:
            db_path = get_telemetry_db_path()
        self.db_path = db_path
        self.ensure_schema()

    def get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_schema(self) -> None:
        try:
            with self.get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS tool_calls (
                        id               INTEGER PRIMARY KEY,
                        tool_name        TEXT    NOT NULL,
                        called_at        TEXT    NOT NULL,   -- ISO 8601 UTC
                        repo_path        TEXT    NOT NULL,   -- absolute path
                        agent            TEXT    NOT NULL DEFAULT 'unknown',
                        tokens_returned  INTEGER NOT NULL,
                        tokens_naive     INTEGER,            -- NULL for unscoped fallback tools
                        tokens_saved     INTEGER             -- NULL when tokens_naive IS NULL
                    );
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_tool_calls_repo_called
                        ON tool_calls (repo_path, called_at);
                """)
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to ensure schema in telemetry DB: {e}", exc_info=True)

    def record_call(
        self,
        tool_name: str,
        repo_path: str,
        agent: str,
        tokens_returned: int,
        tokens_naive: Optional[int],
    ) -> None:
        try:
            repo_path = normalize_repo_path(repo_path)
            called_at = datetime.now(timezone.utc).isoformat()
            tokens_saved = None
            if tokens_naive is not None:
                tokens_saved = tokens_naive - tokens_returned

            with self.get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO tool_calls (
                        tool_name, called_at, repo_path, agent, tokens_returned, tokens_naive, tokens_saved
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (tool_name, called_at, repo_path, agent, tokens_returned, tokens_naive, tokens_saved)
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"Failed to record call in telemetry DB: {e}", exc_info=True)

    def get_stats(self, repo_path: str, days: int) -> Dict[str, Any]:
        """
        Returns aggregate stats for the specified repo and period.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_str = cutoff.isoformat()

        # Handle canonical/normalized repo path representation
        repo_path = normalize_repo_path(repo_path)

        try:
            with self.get_connection() as conn:
                # 1. Total aggregates
                row = conn.execute(
                    """
                    SELECT
                        COUNT(*) as total_calls,
                        SUM(tokens_returned) as total_tokens_returned,
                        SUM(tokens_naive) as total_tokens_naive,
                        SUM(tokens_saved) as total_tokens_saved,
                        COUNT(tokens_naive) as naive_coverage_calls
                    FROM tool_calls
                    WHERE repo_path = ? AND called_at >= ?
                    """,
                    (repo_path, cutoff_str)
                ).fetchone()

                total_calls = row["total_calls"] or 0
                tokens_returned = row["total_tokens_returned"] or 0
                tokens_naive = row["total_tokens_naive"] or 0
                tokens_saved = row["total_tokens_saved"] or 0
                coverage_calls = row["naive_coverage_calls"] or 0

                reduction_pct = 0.0
                if tokens_naive > 0:
                    reduction_pct = (tokens_saved / tokens_naive) * 100

                naive_coverage_pct = 0.0
                if total_calls > 0:
                    naive_coverage_pct = (coverage_calls / total_calls) * 100

                cost_saved_usd = (tokens_saved / 1_000_000.0) * 0.24

                # 2. Tool breakdown
                by_tool = []
                tool_rows = conn.execute(
                    """
                    SELECT
                        tool_name,
                        COUNT(*) as calls,
                        SUM(tokens_returned) as tokens_returned,
                        SUM(tokens_naive) as tokens_naive,
                        SUM(tokens_saved) as tokens_saved
                    FROM tool_calls
                    WHERE repo_path = ? AND called_at >= ?
                    GROUP BY tool_name
                    ORDER BY tokens_saved DESC, calls DESC
                    """,
                    (repo_path, cutoff_str)
                ).fetchall()

                for r in tool_rows:
                    by_tool.append({
                        "tool_name": r["tool_name"],
                        "calls": r["calls"],
                        "tokens_returned": r["tokens_returned"] or 0,
                        "tokens_naive": r["tokens_naive"],
                        "tokens_saved": r["tokens_saved"],
                    })

                # 3. Agent breakdown
                by_agent = []
                agent_rows = conn.execute(
                    """
                    SELECT
                        agent,
                        COUNT(*) as calls,
                        SUM(tokens_returned) as tokens_returned,
                        SUM(tokens_naive) as tokens_naive,
                        SUM(tokens_saved) as tokens_saved
                    FROM tool_calls
                    WHERE repo_path = ? AND called_at >= ?
                    GROUP BY agent
                    ORDER BY calls DESC
                    """,
                    (repo_path, cutoff_str)
                ).fetchall()

                for r in agent_rows:
                    by_agent.append({
                        "agent": r["agent"],
                        "calls": r["calls"],
                        "tokens_returned": r["tokens_returned"] or 0,
                        "tokens_naive": r["tokens_naive"],
                        "tokens_saved": r["tokens_saved"],
                    })

                return {
                    "period_days": days,
                    "repo_path": repo_path,
                    "total_calls": total_calls,
                    "tokens_returned": tokens_returned,
                    "tokens_naive": tokens_naive,
                    "tokens_saved": tokens_saved,
                    "reduction_pct": round(reduction_pct, 1),
                    "naive_coverage_pct": round(naive_coverage_pct, 1),
                    "cost_saved_usd": round(cost_saved_usd, 2),
                    "by_tool": by_tool,
                    "by_agent": by_agent,
                }
        except Exception as e:
            logger.error(f"Error fetching stats from telemetry DB: {e}", exc_info=True)
            return {
                "period_days": days,
                "repo_path": repo_path,
                "total_calls": 0,
                "tokens_returned": 0,
                "tokens_naive": 0,
                "tokens_saved": 0,
                "reduction_pct": 0.0,
                "naive_coverage_pct": 0.0,
                "cost_saved_usd": 0.0,
                "by_tool": [],
                "by_agent": [],
            }

async def record_tool_call(
    tool_name: str,
    tokens_returned: int,
    repo_path: Optional[str] = None,
    module_files: Optional[List[str]] = None,
) -> None:
    """
    Asynchronously records a tool call to the global SQLite database.
    """
    try:
        import asyncio
        from memex.config import get_config
        try:
            config = get_config()
            config_repo = config.repo_root
        except Exception:
            config_repo = None
            
        actual_repo = repo_path or config_repo or os.getcwd()
        actual_repo = normalize_repo_path(actual_repo)

        agent = detect_agent()
        tokens_naive = estimate_naive_tokens(tool_name, tokens_returned, actual_repo, module_files)
        tokens_saved = (tokens_naive - tokens_returned) if tokens_naive is not None else None

        # OTel metrics (no-op if SDK not installed)
        from memex.graph.otel import record_token_metrics
        record_token_metrics(tool_name, tokens_returned, tokens_naive, tokens_saved)

        db = TelemetryDB()
        await asyncio.to_thread(db.record_call, tool_name, actual_repo, agent, tokens_returned, tokens_naive)
    except Exception as e:
        logger.warning(f"Telemetry recording failed for {tool_name}: {e}")

