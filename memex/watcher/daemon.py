import asyncio
import logging
import os
import psutil
from pathlib import Path
from memex.config import get_config
from memex.graph.client import get_graph_client
from memex.watcher.registry import (
    FSObserver,
    CommitPoller,
    EventRouter,
    handle_file_change,
    handle_commit,
    DecayScheduler,
)
from memex.watcher.git_hook import install_hooks

logger = logging.getLogger(__name__)

def _write_pid(repo_root: Path) -> Path:
    memex_dir = repo_root / ".memex"
    memex_dir.mkdir(exist_ok=True)
    pid_file = memex_dir / "daemon.pid"
    
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            if psutil.pid_exists(old_pid):
                # Check if it's actually another memex process
                try:
                    proc = psutil.Process(old_pid)
                    if "memex" in proc.name().lower() or "python" in proc.name().lower():
                        raise RuntimeError(f"memex daemon is already running with PID {old_pid}")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except (ValueError, psutil.Error):
            pass
            
    pid_file.write_text(str(os.getpid()))
    return pid_file

async def run_daemon(repo_root: str) -> None:
    """
    Starts all components and runs until cancelled.
    """
    repo_root_path = Path(repo_root).resolve()
    
    # 1. PID management
    try:
        pid_file = _write_pid(repo_root_path)
    except RuntimeError as e:
        logger.error(str(e))
        print(f"CRITICAL: {e}")
        return

    # 2. Config validation
    try:
        config = get_config()
    except ValueError as e:
        logger.error("Configuration error: %s", e)
        print(f"CRITICAL: {e}")
        if pid_file.exists(): pid_file.unlink()
        return

    # 3. Startup Log & Connection Check
    logger.info("memex daemon starting...")
    logger.info("  Repo Root: %s", repo_root_path)
    logger.info("  Neo4j URI: %s", config.neo4j_uri)
    logger.info("  Gemini Model: %s", config.gemini_model)

    try:
        client = await get_graph_client()
        # Verify connectivity
        await client.driver.execute_query("RETURN 1")
        logger.info("Connected to Neo4j successfully")
    except Exception:
        logger.error("Failed to connect to Neo4j. Ensure Neo4j is running and credentials are correct.", exc_info=True)
        print("CRITICAL: Could not connect to Neo4j backend.")
        if pid_file.exists(): pid_file.unlink()
        return

    # Install git hooks
    try:
        install_hooks(str(repo_root_path))
        logger.info("Installed git hooks in %s", repo_root_path)
    except Exception as e:
        logger.warning("Failed to install git hooks: %s", e)

    # 4. Check for initial paused state
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
        print(f"memex is watching {repo_root_path} (PID {os.getpid()})")

        # Main Loop: wait forever (or until tasks fail)
        await asyncio.gather(poller_task, router_task)

    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Shutdown signal received...")
    except Exception:
        logger.error("Unexpected error in daemon loop", exc_info=True)
    finally:
        logger.info("Cleaning up resources...")
        
        # Remove PID file
        if pid_file.exists():
            pid_file.unlink()

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
