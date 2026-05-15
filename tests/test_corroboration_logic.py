import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from memex.watcher.handlers import corroborate_decisions

@pytest.mark.asyncio
async def test_corroborate_decisions_match_by_message():
    mock_client = AsyncMock()
    mock_driver = MagicMock()
    mock_client.driver = mock_driver
    
    # Mocking the Neo4j query result for the initial fetch
    mock_record = {
        "uuid": "test-uuid",
        "id": "test-uuid",
        "eid": "4:0",
        "text": "Implementing feature X",
        "related_entities": ["main.py"]
    }
    mock_res = MagicMock()
    mock_res.records = [mock_record]
    
    # Setup AsyncMock for execute_query
    mock_driver.execute_query = AsyncMock(side_effect=[mock_res, MagicMock()])

    with patch("memex.watcher.handlers.get_graph_client", return_value=mock_client):
        count = await corroborate_decisions(
            repo_root="/fake/repo",
            sha="sha123",
            message="Feature X is now implemented",
            files_changed=["main.py"]
        )
        assert count == 1
        # Verify update query was called on the driver
        # We look for the call that contains the SET clause
        found_update = False
        for call in mock_driver.execute_query.call_args_list:
            if "SET d.confidence = 1.0" in call.args[0]:
                found_update = True
                break
        assert found_update

@pytest.mark.asyncio
async def test_corroborate_decisions_no_match():
    mock_client = AsyncMock()
    mock_driver = MagicMock()
    mock_client.driver = mock_driver
    
    mock_record = {
        "uuid": "test-uuid",
        "id": "test-uuid",
        "eid": "4:0",
        "text": "Implementing feature X",
        "related_entities": ["main.py"]
    }
    mock_res = MagicMock()
    mock_res.records = [mock_record]
    mock_driver.execute_query = AsyncMock(return_value=mock_res)

    with patch("memex.watcher.handlers.get_graph_client", return_value=mock_client):
        count = await corroborate_decisions(
            repo_root="/fake/repo",
            sha="sha123",
            message="Fixing typo in README",
            files_changed=["README.md"]
        )
        assert count == 0
        # Verify update query was NOT called
        for call in mock_driver.execute_query.call_args_list:
            assert "SET d.confidence = 1.0" not in call.args[0]
