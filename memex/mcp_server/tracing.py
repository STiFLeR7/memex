"""Retrieval-tracing harness — append-only JSONL log of every MCP retrieval.

Per the v0.3.0 plan (Phase 10, §"Retrieval-tracing harness"):

    Logs per query: timestamp, query string, modality counts retrieved,
    prompt-token estimate, top result UUIDs, composite-score components.

The file lives at ``<repo>/.memex/retrieval_trace.jsonl`` and rotates at
10 MB to keep individual log files bounded. ``memex doctor`` consumes
:meth:`RetrievalTrace.weekly_summary` to surface a weekly aggregate.

Why this lives in ``mcp_server`` and not ``graph``:
    The data being logged is *what the MCP layer chose to return to an
    agent*, not anything about the graph itself. Future Phase 7 reranker
    tuning will read this file to validate the composite-score weights —
    keeping the harness next to the server keeps the call site obvious.

Why synchronous file I/O:
    JSONL append is microseconds; an async wrapper would add complexity
    without measurable benefit. The MCP read path is not so hot that an
    occasional disk write hurts it.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Module-level constants — monkey-patchable in tests.
MAX_BYTES = 10 * 1024 * 1024   # 10 MB before rotation
MAX_ROTATIONS = 9              # keep .1 through .9
WEEKLY_WINDOW = timedelta(days=7)
SUMMARY_CACHE_TTL = timedelta(seconds=60)  # weekly_summary memoization window


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class RetrievalTrace:
    """Per-repo singleton that owns an append-only JSONL trace file.

    Reuse the same instance per repo via :func:`get_retrieval_trace`. The
    class is thread-safe; a coarse lock guards file rotation + append.

    The file format is one JSON object per line::

        {"ts": "...", "query": "...", "modality_counts": {...},
         "tokens": int, "top_uuids": [...], "composite": {...}}
    """

    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.trace_dir = self.repo_root / ".memex"
        self.trace_path = self.trace_dir / "retrieval_trace.jsonl"
        self._lock = threading.Lock()
        # weekly_summary cache: invalidated whenever log() appends OR after
        # SUMMARY_CACHE_TTL seconds. memex doctor calls this on every run.
        self._summary_cache: Optional[Dict[str, Any]] = None
        self._summary_cached_at: Optional[datetime] = None

    # -- internal helpers --------------------------------------------------

    def _ensure_dir(self) -> None:
        try:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            # Don't let a missing directory crash the MCP call path.
            logger.debug("tracing: could not create %s", self.trace_dir, exc_info=True)

    def _maybe_rotate(self) -> None:
        """Rotate ``.jsonl`` → ``.jsonl.1`` → ``.jsonl.2`` … up to
        ``MAX_ROTATIONS``. Oldest beyond that is dropped."""
        try:
            if not self.trace_path.exists():
                return
            if self.trace_path.stat().st_size < MAX_BYTES:
                return
        except OSError:
            return

        # Shift existing rotations down: .N -> .N+1 (drop the very oldest).
        for n in range(MAX_ROTATIONS, 0, -1):
            src = self.trace_path.with_suffix(self.trace_path.suffix + f".{n}")
            dst = self.trace_path.with_suffix(self.trace_path.suffix + f".{n + 1}")
            if src.exists():
                if n == MAX_ROTATIONS:
                    try:
                        src.unlink()
                    except OSError:
                        pass
                    continue
                try:
                    if dst.exists():
                        dst.unlink()
                    src.rename(dst)
                except OSError:
                    pass

        # Current → .1
        rotated = self.trace_path.with_suffix(self.trace_path.suffix + ".1")
        try:
            if rotated.exists():
                rotated.unlink()
            self.trace_path.rename(rotated)
        except OSError:
            logger.debug("tracing: rotation failed", exc_info=True)

    # -- public methods ----------------------------------------------------

    def log(
        self,
        query: str,
        modality_counts: Dict[str, int],
        tokens: int,
        top_uuids: List[str],
        composite_components: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Append one record. Never raises — tracing must not break retrieval."""
        record = {
            "ts":              datetime.now(timezone.utc).isoformat(),
            "query":           query,
            "modality_counts": dict(modality_counts or {}),
            "tokens":          int(tokens or 0),
            "top_uuids":       list(top_uuids or []),
            "composite":       composite_components or {},
        }
        line = json.dumps(record, ensure_ascii=False, default=str)

        with self._lock:
            self._ensure_dir()
            self._maybe_rotate()
            try:
                with open(self.trace_path, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.write("\n")
            except OSError:
                logger.debug("tracing: append failed", exc_info=True)
            # Fresh data → drop cached summary so the next caller recomputes.
            self._summary_cache = None
            self._summary_cached_at = None

    def weekly_summary(self) -> Dict[str, Any]:
        """Aggregate the trailing 7 days of records.

        Returns ``{'total_queries', 'avg_tokens', 'top_results',
        'last_query_at'}``. Sensible empty defaults on a fresh repo —
        callers (notably ``memex doctor``) should not need to special-case
        an empty trace.
        """
        empty: Dict[str, Any] = {
            "total_queries": 0,
            "avg_tokens":    0,
            "top_results":   [],
            "last_query_at": None,
        }

        # Serve cached summary if fresh. doctor surfaces call this on every
        # invocation; the full JSONL scan is wasted IO at scale.
        with self._lock:
            if (
                self._summary_cache is not None
                and self._summary_cached_at is not None
                and datetime.now(timezone.utc) - self._summary_cached_at < SUMMARY_CACHE_TTL
            ):
                return self._summary_cache

        if not self.trace_path.exists():
            with self._lock:
                self._summary_cache = empty
                self._summary_cached_at = datetime.now(timezone.utc)
            return empty

        cutoff = datetime.now(timezone.utc) - WEEKLY_WINDOW
        total = 0
        token_sum = 0
        last_ts: Optional[datetime] = None
        uuid_counts: Dict[str, int] = {}

        try:
            with open(self.trace_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = _parse_iso(rec.get("ts"))
                    if ts is None or ts < cutoff:
                        continue
                    total += 1
                    token_sum += int(rec.get("tokens") or 0)
                    if last_ts is None or ts > last_ts:
                        last_ts = ts
                    for uid in rec.get("top_uuids") or []:
                        uuid_counts[uid] = uuid_counts.get(uid, 0) + 1
        except OSError:
            return empty

        if total == 0:
            with self._lock:
                self._summary_cache = empty
                self._summary_cached_at = datetime.now(timezone.utc)
            return empty

        top_results = sorted(
            uuid_counts.items(), key=lambda kv: kv[1], reverse=True
        )[:10]

        summary = {
            "total_queries": total,
            "avg_tokens":    int(token_sum / total) if total else 0,
            "top_results":   [{"uuid": u, "count": c} for u, c in top_results],
            "last_query_at": last_ts,
        }
        with self._lock:
            self._summary_cache = summary
            self._summary_cached_at = datetime.now(timezone.utc)
        return summary


# ---------------------------------------------------------------------------
# Singleton-per-repo (matches the GraphClient pattern in graph/client.py)
# ---------------------------------------------------------------------------


_INSTANCES: Dict[str, RetrievalTrace] = {}
_INSTANCES_LOCK = threading.Lock()


def get_retrieval_trace(repo_root: str) -> RetrievalTrace:
    """Return the per-repo :class:`RetrievalTrace` singleton.

    Use the resolved absolute path as the cache key so callers can pass
    ``.`` or relative paths interchangeably."""
    key = str(Path(repo_root).expanduser().resolve())
    with _INSTANCES_LOCK:
        inst = _INSTANCES.get(key)
        if inst is None:
            inst = RetrievalTrace(key)
            _INSTANCES[key] = inst
        return inst


def reset_retrieval_traces() -> None:
    """Clear the singleton cache. Test-helper only."""
    with _INSTANCES_LOCK:
        _INSTANCES.clear()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
