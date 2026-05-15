import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from memex.watcher.events import CommitEvent
from memex.config import get_config

logger = logging.getLogger(__name__)

class CommitPoller:
    """
    Polls .memex/pending_commit.json.
    When found: reads, deletes, puts CommitEvent onto the queue.
    """
    def __init__(self, repo_root: str, queue: asyncio.Queue):
        self.repo_root = os.path.abspath(repo_root)
        self.queue = queue
        self.pending_file = Path(self.repo_root) / ".memex" / "pending_commit.json"
        config = get_config()
        self.poll_interval = config.poll_interval

    async def run(self) -> None:
        logger.info("CommitPoller started (interval: %.1fs)", self.poll_interval)
        while True:
            try:
                if self.pending_file.exists():
                    try:
                        content = self.pending_file.read_text()
                        if not content.strip():
                            # Atomic write might still be in progress or empty
                            await asyncio.sleep(0.1)
                            continue
                            
                        data = json.loads(content)
                        
                        event = CommitEvent(
                            sha=data["sha"],
                            repo_root=self.repo_root,
                            message=data["message"],
                            diff=data["diff"],
                            files_changed=data["files_changed"],
                            timestamp=datetime.fromisoformat(data["timestamp"])
                        )
                        
                        # Delete with missing_ok=True for Windows compatibility/TOCTOU
                        self.pending_file.unlink(missing_ok=True)
                        
                        await self.queue.put(event)
                        logger.debug("Pushed CommitEvent %s to queue", event.sha)
                    except json.JSONDecodeError:
                        logger.warning("Malformed JSON in %s, skipping", self.pending_file)
                        self.pending_file.unlink(missing_ok=True)
                
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("Error in CommitPoller", exc_info=True)
                await asyncio.sleep(1.0)
