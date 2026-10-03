from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from memex.extractor.treesitter import SymbolDelta
from memex.watcher.events import FileChangeEvent
from memex.watcher.handlers import handle_file_change


@pytest.mark.asyncio
async def test_calls_refresh_without_symbol_delta(tmp_path):
    path = tmp_path / "client.py"
    path.write_text("def send():\n    return new_api()\n", encoding="utf-8")
    event = FileChangeEvent(str(path), str(tmp_path), "modified", datetime.now(UTC))
    with (
        patch("memex.watcher.handlers.run_git_command", new=AsyncMock(
            return_value="def send():\n    return old_api()\n")),
        patch("memex.watcher.handlers.extract_symbol_delta", new=AsyncMock(
            return_value=SymbolDelta())),
        patch("memex.watcher.handlers.write_symbol_delta", new=AsyncMock(
            return_value={"episodes_skipped": 0})),
        patch("memex.watcher.handlers.write_call_edges", new=AsyncMock(
            return_value=1)) as write_calls,
        patch("memex.watcher.handlers.canonical_repo_path", return_value=str(tmp_path)),
        patch("memex.watcher.handlers.health"),
        patch("memex.watcher.handlers.notify_local_server"),
    ):
        await handle_file_change(event)
    write_calls.assert_awaited_once()
    assert {(edge.caller, edge.callee) for edge in write_calls.call_args.args[0]} == {
        ("send", "new_api")
    }
