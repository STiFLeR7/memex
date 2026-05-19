"""Helper that surfaces the retrieval-tracing weekly summary inside
``memex doctor`` output (Phase 10).

Kept in its own module so ``cli.py`` stays small and so the test suite
can patch ``memex.cli_doctor_tracing.get_retrieval_trace`` directly
without poking at the entire CLI module's imports.
"""

from __future__ import annotations

import logging
from typing import Optional

from memex.mcp_server.tracing import get_retrieval_trace

logger = logging.getLogger(__name__)


def format_tracing_summary(repo_root: str) -> str:
    """Render a single ``[INFO] retrieval tracing`` line.

    Never raises. On a fresh repo (or any failure) returns the
    ``no queries traced yet`` form so the line is still informative
    in the doctor output."""
    try:
        trace = get_retrieval_trace(repo_root)
        summary = trace.weekly_summary()
    except Exception:
        logger.debug("doctor: tracing summary failed", exc_info=True)
        summary = {"total_queries": 0, "avg_tokens": 0, "last_query_at": None}

    total = int(summary.get("total_queries") or 0)
    if total == 0:
        return "[INFO] retrieval tracing      no queries traced yet"

    avg_tokens = int(summary.get("avg_tokens") or 0)
    last_at: Optional[object] = summary.get("last_query_at")
    last_str = ""
    if last_at is not None:
        try:
            # datetime → "YYYY-MM-DD HH:MM UTC" style; tolerate strings.
            from datetime import datetime as _dt
            if isinstance(last_at, _dt):
                last_str = last_at.strftime(" (last %Y-%m-%d %H:%M UTC)")
            else:
                last_str = f" (last {last_at})"
        except Exception:
            last_str = ""

    return (
        f"[INFO] retrieval tracing      "
        f"{total} queries in last 7d, avg {avg_tokens} tokens{last_str}"
    )


def print_tracing_summary(repo_root: str) -> None:
    """Print the summary line. Used by ``memex doctor``."""
    print(format_tracing_summary(repo_root))
