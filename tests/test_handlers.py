import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, UTC
from memex.watcher.handlers import handle_file_change
from memex.watcher.events import FileChangeEvent

@pytest.mark.asyncio
async def test_handler_error_logs_traceback_not_crashes():
    """
    Mock the extractor to raise, assert the daemon continues 
    and the error was logged with exc_info=True.
    """
    event = FileChangeEvent(
        path="fake_file.py",
        repo_root=".",
        kind="modified",
        timestamp=datetime.now(UTC)
    )
    
    # We need to mock Path and git commands to get past the initial setup
    with patch("memex.watcher.handlers.Path") as mock_path:
        # Mock repo root resolution (the while loop)
        mock_repo = MagicMock()
        mock_repo.__truediv__.return_value.exists.return_value = True
        mock_path.return_value.parent = mock_repo
        
        # Mock relpath
        with patch("os.path.relpath", return_value="fake_file.py"):
            # Mock extract_symbol_delta to raise
            with patch("memex.watcher.handlers.extract_symbol_delta", side_effect=Exception("Simulated error")):
                # Patch the logger in the handler module
                with patch("memex.watcher.handlers.logger") as mock_logger:
                    # This should not raise
                    await handle_file_change(event)
                    
                    # Verify error was logged
                    assert mock_logger.error.called
                    args, kwargs = mock_logger.error.call_args
                    assert "unhandled error in handle_file_change" in args[0]
                    assert kwargs.get("exc_info") is True
