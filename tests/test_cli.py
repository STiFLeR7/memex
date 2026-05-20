import pytest
import sys
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
            mock_asyncio_run.side_effect = lambda coro: None
            with patch.object(sys, "argv", ["memex", "watch", "--repo", "/fake/repo"]):
                cli.main()
                assert mock_asyncio_run.called

def test_cli_init_installs_hooks():
    with patch("memex.cli.install_hooks") as mock_install:
        with patch("memex.cli.add_repository") as mock_add:
            with patch("memex.cli.Path.mkdir") as mock_mkdir:
                with patch.object(sys, "argv", ["memex", "init", "--repo", "/fake/repo"]):
                    cli.main()
                    mock_install.assert_called_with("/fake/repo")
                    mock_add.assert_called_with("/fake/repo")

@pytest.mark.asyncio
async def test_cli_status_prints_node_counts():
    mock_counts = {"modules": 10, "symbols": 50, "decisions": 5, "problems": 2}
    with patch("memex.cli.get_node_counts", new_callable=AsyncMock, return_value=mock_counts):
        with patch("memex.cli.get_graph_client", new_callable=AsyncMock):
            with patch("builtins.print") as mock_print:
                await cli.print_status("/fake/repo")
                printed = "".join([str(call.args[0]) for call in mock_print.call_args_list])
                assert "10" in printed
                assert "50" in printed

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
                mock_unlink.assert_called_with()

def test_cli_serve_calls_mcp_server():
    with patch("memex.cli.run_server", new_callable=AsyncMock) as mock_run:
        with patch("memex.cli.asyncio.run") as mock_asyncio_run:
            with patch.object(sys, "argv", ["memex", "serve", "--repo", "/fake/repo"]):
                cli.main()
                assert mock_asyncio_run.called

@pytest.mark.asyncio
async def test_doctor_all_pass_exits_zero():
    with patch("subprocess.check_output", return_value=b"v1.0"):
        with patch("memex.cli.get_graph_client", new_callable=AsyncMock):
            with patch("os.getenv", return_value="fake-key"):
                with patch("memex.cli.Path.exists", return_value=True):
                    with patch("memex.cli.Path.read_text", return_value="memex hook"):
                        with patch("memex.cli.get_stale_edges", new_callable=AsyncMock, return_value=[]):
                            with pytest.raises(SystemExit) as exc:
                                await cli.run_doctor(".")
                            assert exc.value.code == 0

def test_cli_list_repos():
    mock_repos = [MagicMock(path="/fake/repo", active=True)]
    mock_repos[0].name = "fake"
    with patch("memex.cli.get_repositories", return_value=mock_repos):
        with patch("builtins.print") as mock_print:
            # Note: /mcp/list is NOT a command, but 'list' is. 
            # The issue was list parser didn't have parents=[parent_parser] so it lacked --repo attribute in namespace
            with patch.object(sys, "argv", ["memex", "list"]):
                cli.main()
                printed = "".join([str(call.args[0]) for call in mock_print.call_args_list])
                assert "/fake/repo" in printed

def test_cli_remove_repo():
    with patch("memex.cli.remove_repository") as mock_remove:
        with patch.object(sys, "argv", ["memex", "remove", "--repo", "/fake/repo"]):
            cli.main()
            mock_remove.assert_called_with("/fake/repo")

def test_cli_keys_add():
    with patch("memex.cli.add_key", return_value="mx_test") as mock_add:
        with patch("builtins.print") as mock_print:
            with patch.object(sys, "argv", ["memex", "keys", "add", "test-user"]):
                cli.main()
                mock_add.assert_called_with("test-user")
                printed = "".join([str(call.args[0]) for call in mock_print.call_args_list])
                assert "mx_test" in printed

def test_cli_keys_list():
    mock_keys = [{"name": "test", "key": "mx_...", "created_at": "now"}]
    with patch("memex.cli.list_keys", return_value=mock_keys):
        with patch("builtins.print") as mock_print:
            with patch.object(sys, "argv", ["memex", "keys", "list"]):
                cli.main()
                printed = "".join([str(call.args[0]) for call in mock_print.call_args_list])
                assert "test" in printed

def test_cli_keys_revoke():
    with patch("memex.cli.revoke_key", return_value=True) as mock_revoke:
        with patch.object(sys, "argv", ["memex", "keys", "revoke", "test"]):
            cli.main()
            mock_revoke.assert_called_with("test")
