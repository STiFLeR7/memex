import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from memex.graph.client import get_graph_client
from memex.config import get_config

logger = logging.getLogger(__name__)

class DecayScheduler:
    """
    Runs nightly via APScheduler to decay confidence on untouched edges.
    """
    def __init__(self):
        self.scheduler = AsyncIOScheduler()

    async def decay_task(self):
        """
        Executes the decay Cypher query.
        """
        config = get_config()
        logger.info("Starting graph decay task...")
        client = await get_graph_client()
        
        query = f"""
        MATCH ()-[r]->()
        WHERE r.last_touched IS NOT NULL
          AND r.confidence > 0.0
          AND r.last_touched < datetime() - duration({{hours: {config.decay_hours_threshold}}})
        SET r.confidence = max(0.0, r.confidence - 0.01),
            r.stale = r.confidence < 0.3
        RETURN count(r) as updated_count
        """
        
        try:
            result = await client.driver.execute_query(query)
            count = 0
            if hasattr(result, "records") and result.records:
                count = result.records[0].get("updated_count", 0)
            
            logger.info("Decay task complete. Updated %d edges.", count)
        except Exception:
            logger.error("Decay task failed", exc_info=True)

    def start(self) -> None:
        config = get_config()
        # Run nightly at configured time
        self.scheduler.add_job(
            self.decay_task, 
            'cron', 
            hour=config.decay_hour, 
            minute=config.decay_minute
        )
        self.scheduler.start()
        logger.info(
            "DecayScheduler started (scheduled for %02d:%02d nightly)", 
            config.decay_hour, config.decay_minute
        )

    def stop(self) -> None:
        self.scheduler.shutdown()
        logger.info("DecayScheduler stopped")
