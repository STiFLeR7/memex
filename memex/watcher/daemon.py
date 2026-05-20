import asyncio
import logging
import os
import psutil
from pathlib import Path
from memex.config import get_config
from memex.graph.client import get_graph_client
from memex.watcher.fs_observer import FSObserver
from memex.watcher.commit_poller import CommitPoller
from memex.watcher.event_router import EventRouter
from memex.watcher.handlers import (
    handle_file_change, handle_commit, handle_lockfile_change, initial_lockfile_index,
)
from memex.graph.decay import DecayScheduler
from memex.watcher.registry import get_active_repositories, DEFAULT_REGISTRY_DIR
from memex.watcher.git_hook import install_hooks

logger = logging.getLogger(__name__)

def _write_pid(repo_root: Path | None) -> Path:
    if repo_root:
        memex_dir = repo_root / ".memex"
        memex_dir.mkdir(exist_ok=True)
        pid_file = memex_dir / "daemon.pid"
    else:
        DEFAULT_REGISTRY_DIR.mkdir(exist_ok=True)
        pid_file = DEFAULT_REGISTRY_DIR / "daemon.pid"
    
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

async def run_daemon(repo_root: str | None = None) -> None:
    """
    Starts all components and runs until cancelled.
    If repo_root is None, watches all active repositories from the registry.
    """
    repo_root_path = Path(repo_root).resolve() if repo_root else None
    
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
    if repo_root_path:
        logger.info("  Mode: Single Repo (%s)", repo_root_path)
    else:
        logger.info("  Mode: Multi-Repo (Registry)")
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

    # Shared event queue
    queue = asyncio.Queue()
    
    # Components
    router = EventRouter(queue)
    router.on_file_change(handle_file_change)
    router.on_file_change(handle_lockfile_change)
    router.on_commit(handle_commit)
    decay = DecayScheduler()

    observers = []
    pollers = []
    tasks = []

    # Determine which repos to watch
    if repo_root_path:
        repos_to_watch = [repo_root_path]
    else:
        repos_to_watch = [Path(r.path) for r in get_active_repositories()]
        if not repos_to_watch:
            logger.warning("No active repositories found in registry. Daemon will idle.")
            print("Warning: No active repositories found in registry.")

    for repo in repos_to_watch:
        # Install git hooks
        try:
            install_hooks(str(repo))
            logger.info("Installed git hooks in %s", repo)
        except Exception as e:
            logger.warning("Failed to install git hooks in %s: %s", repo, e)

        # Check for initial paused state
        pause_file = repo / ".memex" / "paused"
        if pause_file.exists():
            logger.info("memex is currently PAUSED for %s. Delete %s or run 'memex resume' to start watching.", repo, pause_file)
            continue

        observer = FSObserver(str(repo), queue)
        poller = CommitPoller(str(repo), queue)
        observers.append(observer)
        pollers.append(poller)
        
        poller_task = asyncio.create_task(poller.run())
        tasks.append(poller_task)
        observer.start()
        logger.info("memex watching %s", repo)

        # B3: build IMPORTS/Dependency edges once at startup (background, so a
        # slow scan doesn't delay the watch loop). Without this a quiescent
        # repo never gets import edges and predict_impact's import dim is 0.
        tasks.append(asyncio.create_task(initial_lockfile_index(str(repo))))

    try:
        # Start shared router
        router_task = asyncio.create_task(router.run())
        tasks.append(router_task)
        
        # Start shared decay
        decay.start()

        if repo_root_path:
            print(f"memex is watching {repo_root_path} (PID {os.getpid()})")
        else:
            print(f"memex is watching {len(observers)} repositories (PID {os.getpid()})")

        # Main Loop: wait forever (or until tasks fail)
        await asyncio.gather(*tasks)

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
        for observer in observers:
            try:
                observer.stop()
            except Exception:
                logger.error("Error stopping FSObserver", exc_info=True)

        try:
            decay.stop()
        except Exception:
            logger.error("Error stopping DecayScheduler", exc_info=True)

        logger.info("memex stopped")
