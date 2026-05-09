import asyncio
import logging
import signal
import os
from pathlib import Path
from memex.config import get_config
from memex.graph.client import get_graph_client
from memex.graph.decay import DecayScheduler
from memex.watcher.fs_observer import FSObserver
from memex.watcher.commit_poller import CommitPoller
from memex.watcher.event_router import EventRouter
from memex.watcher.handlers import handle_file_change, handle_commit
from memex.watcher.git_hook import install_hooks

logger = logging.getLogger(__name__)

async def run_daemon(repo_root: str) -> None:
    """
    Starts all components and runs until cancelled.
    """
    repo_root_path = Path(repo_root).resolve()
    
    # 1. Config validation
    try:
        config = get_config()
    except ValueError as e:
        logger.error("Configuration error: %s", e)
        print(f"CRITICAL: {e}")
        return

    # 2. Startup Log & Connection Check
    logger.info("memex daemon starting...")
    logger.info("  Repo Root: %s", repo_root_path)
    logger.info("  Neo4j URI: %s", config.neo4j_uri) # Password is not in URI by default in our config
    logger.info("  Gemini Model: %s", config.gemini_model)

    try:
        client = await get_graph_client()
        # Verify connectivity
        await client.driver.execute_query("RETURN 1")
        logger.info("Connected to Neo4j successfully")
    except Exception:
        logger.error("Failed to connect to Neo4j. Ensure Neo4j is running and credentials are correct.", exc_info=True)
        print("CRITICAL: Could not connect to Neo4j backend.")
        return

    # Install git hooks
    try:
        install_hooks(str(repo_root_path))
        logger.info("Installed git hooks in %s", repo_root_path)
    except Exception as e:
        logger.warning("Failed to install git hooks: %s", e)

    # 3. Check for initial paused state
    pause_file = repo_root_path / ".memex" / "paused"
    if pause_file.exists():
        logger.info("memex is currently PAUSED. Delete %s or run 'memex resume' to start watching.", pause_file)

    # Shared event queue
    queue = asyncio.Queue()

    # Components
    observer = FSObserver(str(repo_root_path), queue)
    poller = CommitPoller(str(repo_root_path), queue)
    router = EventRouter(queue)
    router.on_file_change(handle_file_change)
    router.on_commit(handle_commit)
    decay = DecayScheduler()

    tasks = []
    
    try:
        # Start background tasks
        poller_task = asyncio.create_task(poller.run())
        router_task = asyncio.create_task(router.run())
        tasks.extend([poller_task, router_task])

        # Start non-async components
        observer.start()
        decay.start()

        logger.info("memex watching %s", repo_root_path)

        # Main Loop: wait forever (or until tasks fail)
        await asyncio.gather(poller_task, router_task)

    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Shutdown signal received...")
    except Exception:
        logger.error("Unexpected error in daemon loop", exc_info=True)
    finally:
        logger.info("Cleaning up resources...")
        
        # 1. Cancel all async tasks
        for task in tasks:
            if not task.done():
                task.cancel()
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # 2. Stop non-async components
        try:
            observer.stop()
        except Exception:
            logger.error("Error stopping FSObserver", exc_info=True)

        try:
            decay.stop()
        except Exception:
            logger.error("Error stopping DecayScheduler", exc_info=True)

        logger.info("memex stopped")
