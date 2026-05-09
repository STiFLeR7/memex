import asyncio
import os
from memex.graph.client import get_graph_client
from memex.config import get_config

async def main():
    config = get_config()
    client = await get_graph_client()
    print(f"Connecting to database: {config.neo4j_database}")
    
    # Query all nodes and their labels
    query = "MATCH (n) RETURN labels(n) as labels, count(n) as count"
    res = await client.driver.execute_query(query)
    print("\nGraph Node Summary:")
    for r in res.records:
        print(f" - {r['labels']}: {r['count']}")
        
    # List all Entities
    res = await client.driver.execute_query("MATCH (n:Entity) RETURN n.name as name LIMIT 20")
    print("\nRecent Entities:")
    for r in res.records:
        print(f" - {r['name']}")

if __name__ == "__main__":
    asyncio.run(main())
