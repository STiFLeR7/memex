import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from memex.graph.decay import DecayScheduler

@pytest.mark.asyncio
async def test_decay_task_calls_cypher():
    """
    Assert that the decay task calls the expected Cypher query.
    """
    scheduler = DecayScheduler()
    
    # Mock the graph client and driver
    mock_client = AsyncMock()
    mock_driver = AsyncMock()
    mock_client.driver = mock_driver
    
    with patch('memex.graph.decay.get_graph_client', return_value=mock_client):
        await scheduler.decay_task()
        
        # Verify execute_query was called
        assert mock_driver.execute_query.called
        call_args = mock_driver.execute_query.call_args
        query_str = call_args[0][0]
        
        assert "MATCH ()-[r]->()" in query_str
        assert "r.confidence - 0.01" in query_str
        assert "r.stale = r.confidence < 0.3" in query_str
