import asyncio
import pytest
import shutil
import tempfile
import logging
from pathlib import Path
from git import Repo
from dotenv import load_dotenv

# Explicitly load .env from the project root
project_root = Path(__file__).parent.parent
load_dotenv(dotenv_path=project_root / ".env")

from memex.watcher.daemon import run_daemon
from memex.graph.client import get_graph_client, reset_graph_client

logger = logging.getLogger(__name__)

@pytest.fixture(autouse=True)
async def cleanup_client():
    """Ensure a fresh graph client for every test."""
    await reset_graph_client()
    yield
    await reset_graph_client()

@pytest.mark.integration
@pytest.mark.asyncio
async def test_watcher_reacts_to_file_change():
    """
    Assert that writing a file to a watched repo creates Episodic nodes in Neo4j.
    """
    tmp_dir = tempfile.mkdtemp()
    tmp_dir_path = Path(tmp_dir).resolve()
    repo = Repo.init(str(tmp_dir_path))
    
    daemon_task = asyncio.create_task(run_daemon(str(tmp_dir_path)))
    
    try:
        await asyncio.sleep(5.0)
        
        test_file = tmp_dir_path / "app_smoke.py"
        test_file.write_text("def smoke_signal_function():\n    pass")
        
        client = await get_graph_client()
        found = False
        for i in range(20):
            await asyncio.sleep(1.0)
            try:
                result = await client.driver.execute_query(
                    "MATCH (n:Episodic) WHERE n.name CONTAINS 'symbol_added_smoke_signal_function' RETURN count(n) as count"
                )
                if result.records and result.records[0].get("count", 0) > 0:
                    found = True
                    break
            except Exception as e:
                logger.warning(f"Neo4j query attempt {i} failed: {e}")
        
        assert found, "Episodic node for 'smoke_signal_function' did not appear in Neo4j"
        
    finally:
        daemon_task.cancel()
        try:
            await asyncio.wait_for(daemon_task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        repo.close()
        await asyncio.sleep(1.0)
        try:
            shutil.rmtree(tmp_dir)
        except Exception:
            pass

@pytest.mark.integration
@pytest.mark.asyncio
async def test_watcher_reacts_to_commit():
    """
    Assert that making a commit in a watched repo creates Decision nodes in Neo4j.
    """
    tmp_dir = tempfile.mkdtemp()
    tmp_dir_path = Path(tmp_dir).resolve()
    repo = Repo.init(str(tmp_dir_path))
    
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Test User")
        cw.set_value("user", "email", "test@example.com")
    
    initial_file = tmp_dir_path / "README.md"
    initial_file.write_text("# Test Repo")
    repo.index.add([str(initial_file)])
    repo.index.commit("Initial commit")

    daemon_task = asyncio.create_task(run_daemon(str(tmp_dir_path)))
    
    try:
        await asyncio.sleep(5.0)
        
        second_file = tmp_dir_path / "auth_v2.py"
        second_file.write_text("import os\ndef login_v2(): pass")
        repo.index.add([str(second_file)])
        commit_msg = "refactor: switched auth to EdDSA for key rotation to meet security compliance"
        # Skip hooks to avoid bash/WSL issues on Windows tests
        repo.index.commit(commit_msg, skip_hooks=True)
        
        # Manually trigger the emitter to simulate what the hook would do
        from memex.watcher.git_hook import emit_commit_event
        emit_commit_event(str(tmp_dir_path))
        
        client = await get_graph_client()
        found = False
        for i in range(30):
            await asyncio.sleep(1.0)
            try:
                result = await client.driver.execute_query(
                    "MATCH (n:Episodic) WHERE n.name CONTAINS 'decision_' RETURN count(n) as count"
                )
                if result.records and result.records[0].get("count", 0) > 0:
                    found = True
                    break
            except Exception as e:
                logger.warning(f"Neo4j query attempt {i} failed: {e}")
        
        assert found, "Decision Episodic node did not appear in Neo4j"
        
    finally:
        daemon_task.cancel()
        try:
            await asyncio.wait_for(daemon_task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        repo.close()
        await asyncio.sleep(1.0)
        try:
            shutil.rmtree(tmp_dir)
        except Exception:
            pass
