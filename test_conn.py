import asyncio
from memex.graph.client import get_graph_client

async def test():
    try:
        client = await get_graph_client()
        res = await client.driver.execute_query('RETURN 1')
        print(f"Connection successful: {res.records[0].data()}")
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(test())
