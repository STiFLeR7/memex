"""Repo-scoped watcher health counters (audit Q1 + B7).

The watcher daemon swallows per-event errors so one bad file can't kill the
process — but that made indexing failures invisible. This persists a small
counter file at ``<repo>/.memex/health.json`` that the (separate-process)
``memex status`` / ``memex doctor`` commands read, so degradation is observable:

- ``handler_errors``      — exceptions caught in the watcher event handlers
- ``episodes_skipped``    — NL ``add_episode`` calls skipped (e.g. Gemini quota);
                            structured graph data is still written (B7)
- ``last_indexed_at``     — last successful symbol index

Writes are best-effort and atomic; failure to record health never affects
indexing. The daemon is a single async process, so read-modify-write here is
serialized (no locking needed).
"""

import json
import logging
import os
from datetime import datetime, UTC
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _health_path(repo_root: str) -> Path:
    return Path(repo_root) / ".memex" / "health.json"


def read_health(repo_root: Optional[str]) -> dict:
    """Return the current health dict, or ``{}`` if absent/unreadable."""
    if not repo_root:
        return {}
    try:
        return json.loads(_health_path(repo_root).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(repo_root: str, data: dict) -> None:
    path = _health_path(repo_root)
    try:
        path.parent.mkdir(exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)  # atomic on same filesystem
    except Exception:
        logger.debug("health: write failed for %s", repo_root, exc_info=True)


def record(
    repo_root: Optional[str],
    *,
    handler: Optional[str] = None,
    errors: int = 0,
    episodes_skipped: int = 0,
    indexed_ok: bool = False,
) -> None:
    """Increment counters. No-op when ``repo_root`` is falsy."""
    if not repo_root:
        return
    data = read_health(repo_root)
    now = datetime.now(UTC).isoformat()
    if errors:
        data["handler_errors"] = data.get("handler_errors", 0) + errors
        if handler:
            by = data.setdefault("handler_errors_by", {})
            by[handler] = by.get(handler, 0) + errors
        data["last_error_at"] = now
    if episodes_skipped:
        data["episodes_skipped"] = data.get("episodes_skipped", 0) + episodes_skipped
        data["last_episode_skip_at"] = now
    if indexed_ok:
        data["last_indexed_at"] = now
    _write(repo_root, data)
