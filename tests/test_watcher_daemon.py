import pytest
import asyncio
import os
from unittest.mock import AsyncMock, patch, MagicMock
from memex.watcher import daemon

@pytest.fixture
def mock_pid_exists():
    with patch("psutil.pid_exists") as mock:
        yield mock

@pytest.fixture
def mock_process():
    with patch("psutil.Process") as mock:
        yield mock

def test_write_pid_already_running(tmp_path, mock_pid_exists, mock_process):
    pid_file = tmp_path / ".memex" / "daemon.pid"
    pid_file.parent.mkdir()
    pid_file.write_text("1234")
    
    mock_pid_exists.return_value = True
    mock_proc = MagicMock()
    mock_proc.name.return_value = "memex"
    mock_process.return_value = mock_proc
    
    with pytest.raises(RuntimeError) as exc:
        daemon._write_pid(tmp_path)
    assert "already running" in str(exc.value)

def test_write_pid_stale_pid(tmp_path, mock_pid_exists):
    pid_file = tmp_path / ".memex" / "daemon.pid"
    pid_file.parent.mkdir()
    pid_file.write_text("1234")
    
    mock_pid_exists.return_value = False
    
    returned_pid_file = daemon._write_pid(tmp_path)
    assert returned_pid_file == pid_file
    assert pid_file.read_text() == str(os.getpid())

@pytest.mark.asyncio
async def test_run_daemon_pid_error():
    with patch("memex.watcher.daemon._write_pid", side_effect=RuntimeError("Already running")):
        with patch("builtins.print") as mock_print:
            await daemon.run_daemon("/fake/repo")
            mock_print.assert_any_call("CRITICAL: Already running")

@pytest.mark.asyncio
async def test_run_daemon_config_error():
    with patch("memex.watcher.daemon._write_pid") as mock_pid:
        mock_pid.return_value = MagicMock(exists=MagicMock(return_value=True))
        with patch("memex.watcher.daemon.get_config", side_effect=ValueError("Bad config")):
            with patch("builtins.print") as mock_print:
                await daemon.run_daemon("/fake/repo")
                mock_print.assert_any_call("CRITICAL: Bad config")

@pytest.mark.asyncio
async def test_run_daemon_neo4j_error():
    with patch("memex.watcher.daemon._write_pid") as mock_pid:
        mock_pid.return_value = MagicMock(exists=MagicMock(return_value=True))
        with patch("memex.watcher.daemon.get_config"):
            with patch("memex.watcher.daemon.get_graph_client", side_effect=Exception("Neo4j down")):
                with patch("builtins.print") as mock_print:
                    await daemon.run_daemon("/fake/repo")
                    mock_print.assert_any_call("CRITICAL: Could not connect to Neo4j backend.")

@pytest.mark.asyncio
async def test_run_daemon_registry_no_repos():
    with patch("memex.watcher.daemon._write_pid") as mock_pid:
        mock_pid_file = MagicMock()
        mock_pid_file.exists.return_value = True
        mock_pid.return_value = mock_pid_file
        with patch("memex.watcher.daemon.get_config"):
            with patch("memex.watcher.daemon.get_graph_client", new_callable=AsyncMock):
                with patch("memex.watcher.daemon.get_active_repositories", return_value=[]):
                    with patch("memex.watcher.daemon.EventRouter") as mock_router:
                        mock_router_instance = mock_router.return_value
                        mock_router_instance.run = AsyncMock()
                        
                        # We want it to exit after starting
                        mock_router_instance.run.side_effect = asyncio.CancelledError()
                        
                        with patch("builtins.print") as mock_print:
                            await daemon.run_daemon(None)
                            mock_print.assert_any_call("Warning: No active repositories found in registry.")

@pytest.mark.asyncio
async def test_run_daemon_paused_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".memex").mkdir()
    (repo / ".memex" / "paused").touch()
    
    with patch("memex.watcher.daemon._write_pid") as mock_pid:
        mock_pid_file = MagicMock()
        mock_pid_file.exists.return_value = True
        mock_pid.return_value = mock_pid_file
        with patch("memex.watcher.daemon.get_config"):
            with patch("memex.watcher.daemon.get_graph_client", new_callable=AsyncMock):
                with patch("memex.watcher.daemon.FSObserver") as mock_obs:
                    with patch("memex.watcher.daemon.EventRouter") as mock_router:
                        mock_router_instance = mock_router.return_value
                        mock_router_instance.run = AsyncMock(side_effect=asyncio.CancelledError())
                        
                        await daemon.run_daemon(str(repo))
                        assert not mock_obs.called

@pytest.mark.asyncio
async def test_run_daemon_exception_in_loop():
    with patch("memex.watcher.daemon._write_pid") as mock_pid:
        mock_pid_file = MagicMock()
        mock_pid_file.exists.return_value = True
        mock_pid.return_value = mock_pid_file
        with patch("memex.watcher.daemon.get_config"):
            with patch("memex.watcher.daemon.get_graph_client", new_callable=AsyncMock):
                with patch("memex.watcher.daemon.FSObserver") as mock_obs:
                    with patch("memex.watcher.daemon.CommitPoller") as mock_poller:
                        mock_poller.return_value.run = AsyncMock()
                        with patch("memex.watcher.daemon.EventRouter") as mock_router:
                            mock_router_instance = mock_router.return_value
                            mock_router_instance.run = AsyncMock(side_effect=Exception("Boom"))
                            
                            await daemon.run_daemon("/fake/repo")
                            assert mock_pid_file.unlink.called
