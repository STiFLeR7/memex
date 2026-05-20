import os
import pytest
from memex.graph.client import get_graph_client

@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("NEO4J_URI") or not os.getenv("GEMINI_API_KEY"),
    reason="NEO4J_URI and GEMINI_API_KEY must be set to run integration tests"
)
async def test_graph_client_initialization():
    """
    Test that the Graphiti client initializes and can connect to Neo4j.
    """
    try:
        client = await get_graph_client()
        assert client is not None
        # Basic check to see if we can talk to the driver
        assert client.driver is not None
        print("Graphiti client initialized and connected successfully.")
    except Exception as e:
        pytest.fail(f"Graphiti client failed to initialize: {e}")
