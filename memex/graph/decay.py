"""Nightly graph maintenance scheduler (Phase 8 / ARCHITECTURE §9).

Despite the historical name ``DecayScheduler``, this job NO LONGER decays
confidence. v0.2.0's stored-confidence decay was a latent no-op (the writer for
``r.last_touched`` was never wired up). v0.3.0 makes confidence a *computed*
value (see :mod:`memex.graph.confidence`), so the nightly job only needs to:

    (a) refresh the cached ``stale`` boolean on nodes so dashboard / list
        queries don't have to recompute on every read, and
    (b) tombstone nodes that meet :func:`memex.graph.confidence.is_cold` —
        delegated to :mod:`memex.graph.archive` (Phase 6, parallel agent).

The class name is preserved for backward compatibility with the daemon wiring;
the docstring and behaviour are the only things that changed.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from memex.config import get_config
from memex.graph.client import get_graph_client
from memex.graph.confidence import (
    LAMBDA_UNVALIDATED,
    LAMBDA_VALIDATED,
    LAMBDA_UNVALIDATED_OLD,
    UNVALIDATED_OLD_THRESHOLD_DAYS,
    UNVALIDATED_OLD_CAP,
    VALIDATED_FLOOR,
    STALENESS_THRESHOLD,
)

logger = logging.getLogger(__name__)


# Cypher expression that mirrors :func:`memex.graph.confidence.current_confidence`.
# Kept inline (rather than UNWINDing rows back to Python) so the refresh stays a
# single round-trip. ``coalesce`` is used to provide v0.2.0 fallback defaults.
_STALE_REFRESH_QUERY = f"""
MATCH (n:Entity)
WHERE coalesce(n.base_confidence, n.confidence, 1.0) IS NOT NULL
WITH n,
     coalesce(n.base_confidence, n.confidence, 1.0) AS base,
     coalesce(n.last_reinforced_at, n.created_at) AS anchor,
     coalesce(n.validated, false) AS validated
WHERE anchor IS NOT NULL
WITH n, base, validated,
     duration.inSeconds(anchor, datetime()).seconds / 86400.0 AS days
WITH n, base, validated, days,
     CASE
       WHEN validated THEN {LAMBDA_VALIDATED}
       WHEN days > {UNVALIDATED_OLD_THRESHOLD_DAYS} THEN {LAMBDA_UNVALIDATED_OLD}
       ELSE {LAMBDA_UNVALIDATED}
     END AS lam
WITH n,
     CASE
       WHEN validated THEN
         CASE WHEN base * exp(-lam * days) < {VALIDATED_FLOOR} THEN {VALIDATED_FLOOR} ELSE base * exp(-lam * days) END
       WHEN days > {UNVALIDATED_OLD_THRESHOLD_DAYS} THEN
         CASE WHEN base * exp(-lam * days) > {UNVALIDATED_OLD_CAP} THEN {UNVALIDATED_OLD_CAP} ELSE base * exp(-lam * days) END
       ELSE base * exp(-lam * days)
     END AS computed
SET n.stale = computed < {STALENESS_THRESHOLD}
RETURN count(n) AS updated_count
"""


class DecayScheduler:
    """Nightly maintenance job.

    Historically called ``DecayScheduler`` because v0.2.0 ran a confidence-
    decay query here. v0.3.0 confidence is computed at query time (see
    :mod:`memex.graph.confidence`), so this job NO LONGER mutates
    confidence. It refreshes the cached ``stale`` boolean and tombstones
    cold nodes via :mod:`memex.graph.archive`.

    The name is preserved so the watcher daemon's wiring (``daemon.py``) is
    untouched. Rename in a future refactor if/when callers can be updated.
    """

    def __init__(self) -> None:
        self.scheduler = AsyncIOScheduler()

    async def decay_task(self) -> None:
        """Run nightly maintenance.

        Steps:
            1. Recompute ``stale`` on every node using the same TempValid
               two-regime formula as ``current_confidence`` (kept inline as
               Cypher for round-trip efficiency).
            2. Sweep cold nodes to the SQLite archive (Phase 6 module).

        The maintenance job is best-effort: failures in either step are
        logged but do not raise, so a transient Neo4j blip cannot kill the
        scheduler thread.
        """
        logger.info("Starting nightly graph maintenance task...")
        client = await get_graph_client()

        # Step 1 — refresh stale boolean cache.
        try:
            result = await client.driver.execute_query(_STALE_REFRESH_QUERY)
            count = 0
            if hasattr(result, "records") and result.records:
                count = result.records[0].get("updated_count", 0) or 0
            logger.info("Stale cache refreshed on %d nodes.", count)
        except Exception:
            logger.error("Stale-cache refresh failed", exc_info=True)

        # Step 2 — tombstone cold nodes. Sweep every registered active repo
        # (memex is multi-repo-aware since v0.2.0; the global daemon's
        # responsibility is to maintain *all* repos).
        try:
            from memex.graph.archive import tombstone_cold_nodes
            from memex.watcher.registry import get_active_repositories

            total_archived = 0
            for repo in get_active_repositories():
                try:
                    archived = await tombstone_cold_nodes(repo.path)
                    total_archived += archived or 0
                except Exception:
                    logger.error(
                        "Cold-node sweep failed for repo %s", repo.path, exc_info=True
                    )
            logger.info("Tombstoned %d cold nodes across all active repos.", total_archived)
        except ImportError:
            logger.error(
                "memex.graph.archive or watcher.registry not importable; "
                "cold-node sweep skipped this run",
                exc_info=True,
            )
        except Exception:
            logger.error("Cold-node sweep failed at top level", exc_info=True)

        logger.info("Nightly graph maintenance task complete.")

    def start(self) -> None:
        config = get_config()
        self.scheduler.add_job(
            self.decay_task,
            "cron",
            hour=config.decay_hour,
            minute=config.decay_minute,
        )
        self.scheduler.start()
        logger.info(
            "DecayScheduler started (nightly maintenance scheduled for %02d:%02d)",
            config.decay_hour,
            config.decay_minute,
        )

    def stop(self) -> None:
        self.scheduler.shutdown()
        logger.info("DecayScheduler stopped")
