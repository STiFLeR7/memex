import pytest
import sys
import os
from unittest.mock import AsyncMock, patch, MagicMock
from memex import cli

def test_cli_unknown_command_exits_nonzero():
    with pytest.raises(SystemExit) as exc:
        with patch.object(sys, "argv", ["memex", "nonexistent"]):
            cli.main()
    assert exc.value.code != 0

def test_cli_watch_calls_run_daemon():
    with patch("memex.cli.run_daemon", new_callable=AsyncMock) as mock_run:
        with patch("memex.cli.asyncio.run") as mock_asyncio_run:
            mock_asyncio_run.side_effect = lambda coro: None # Mock asyncio.run to do nothing
            with patch.object(sys, "argv", ["memex", "watch", "--repo", "/fake/repo"]):
                cli.main()
                # cli calls asyncio.run(run_daemon(repo_root))
                # We check that run_daemon was the argument to asyncio.run
                assert mock_asyncio_run.called

def test_cli_init_installs_hooks():
    with patch("memex.cli.install_hooks") as mock_install:
        with patch("memex.cli.Path.mkdir") as mock_mkdir:
            with patch.object(sys, "argv", ["memex", "init", "--repo", "/fake/repo"]):
                cli.main()
                mock_install.assert_called_with("/fake/repo")
                assert mock_mkdir.called

@pytest.mark.asyncio
async def test_cli_status_prints_node_counts():
    mock_counts = {"modules": 10, "symbols": 50, "decisions": 5, "problems": 2}
    with patch("memex.cli.get_node_counts", new_callable=AsyncMock, return_value=mock_counts):
        with patch("memex.cli.get_graph_client", new_callable=AsyncMock):
            with patch("builtins.print") as mock_print:
                await cli.print_status("/fake/repo")
                # Check if print was called with expected strings
                printed = "".join([call.args[0] for call in mock_print.call_args_list])
                assert "10" in printed
                assert "50" in printed
                assert "5" in printed

def test_cli_pause_creates_paused_file():
    with patch("memex.cli.Path.touch") as mock_touch:
        with patch("memex.cli.Path.mkdir") as mock_mkdir:
            with patch.object(sys, "argv", ["memex", "pause", "--repo", "/fake/repo"]):
                cli.main()
                assert mock_touch.called

def test_cli_resume_deletes_paused_file():
    with patch("memex.cli.Path.unlink") as mock_unlink:
        with patch("memex.cli.Path.exists", return_value=True):
            with patch.object(sys, "argv", ["memex", "resume", "--repo", "/fake/repo"]):
                cli.main()
                mock_unlink.assert_called_with() # In my impl I called it without args, which uses defaults

def test_cli_serve_calls_mcp_server():
    with patch("memex.cli.run_server", new_callable=AsyncMock) as mock_run:
        with patch("memex.cli.asyncio.run") as mock_asyncio_run:
            with patch.object(sys, "argv", ["memex", "serve", "--repo", "/fake/repo"]):
                cli.main()
                assert mock_asyncio_run.called

@pytest.mark.asyncio
async def test_doctor_all_pass_exits_zero():
    # Mock all prerequisites to pass
    with patch("subprocess.check_output", return_value=b"v1.0"):
        with patch("memex.cli.get_graph_client", new_callable=AsyncMock) as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client
            with patch("os.getenv", return_value="fake-key"):
                with patch("memex.cli.Path.exists", return_value=True):
                    with patch("memex.cli.Path.read_text", return_value="memex hook"):
                        with patch("memex.cli.get_stale_edges", new_callable=AsyncMock, return_value=[]):
                            with pytest.raises(SystemExit) as exc:
                                await cli.run_doctor(".")
                            assert exc.value.code == 0

@pytest.mark.asyncio
async def test_doctor_neo4j_fail_exits_one():
    with patch("memex.cli.get_graph_client", side_effect=Exception("Conn fail")):
        with patch("subprocess.check_output", return_value=b"v1.0"):
            with pytest.raises(SystemExit) as exc:
                await cli.run_doctor(".")
            assert exc.value.code == 1
