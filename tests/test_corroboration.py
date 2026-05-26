import pytest
from datetime import datetime, UTC
from unittest.mock import patch
from memex.watcher.handlers import handle_commit
from memex.watcher.events import CommitEvent
from memex.graph.client import get_graph_client, reset_graph_client

@pytest.fixture(autouse=True)
async def cleanup_client():
    """Ensure a fresh graph client for every test."""
    await reset_graph_client()
    yield
    await reset_graph_client()

@pytest.mark.asyncio
async def test_decision_corroboration_by_message():
    """
    Test that a decision is corroborated when the commit message contains significant words.
    """
    client = await get_graph_client()
    
    # 1. Clean up existing test decisions
    await client.driver.execute_query("MATCH (d:Entity) WHERE d.source = 'agent' OR d.name = 'Implement JWT auth' DETACH DELETE d")
    
    # 2. Record a decision manually with agent source and low confidence, and link to Module
    decision_text = "Implement JWT auth for the API"
    await client.driver.execute_query("""
    CREATE (d:Entity {
        name: $text,
        type: 'Decision',
        source: 'agent',
        confidence: 0.6,
        corroborated: false,
        uuid: 'test-uuid-msg'
    })
    CREATE (m:Entity {
        name: 'auth.py',
        type: 'Module'
    })
    CREATE (d)-[:MOTIVATES]->(m)
    """, params={"text": decision_text})
    
    # 3. Simulate a CommitEvent that matches keywords
    event = CommitEvent(
        sha="sha_msg_123",
        repo_root=".",
        message="Auth: implemented JWT token logic",
        diff="--- a/auth.py\n+++ b/auth.py\n",
        files_changed=["auth.py"],
        timestamp=datetime.now(UTC)
    )
    
    # 4. Call handle_commit (mocking extraction to avoid Gemini calls, and mocking _embed_text for similarity)
    with patch("memex.watcher.handlers.extract_decisions", return_value=[]), \
         patch("memex.graph.cluster_summary._embed_text") as mock_embed:
        # Mock matching embeddings to yield cosine similarity of 1.0 (>= 0.6)
        mock_embed.side_effect = [
            [1.0, 0.0],  # commit
            [1.0, 0.0]   # decision
        ]
        await handle_commit(event)
    
    # 5. Verify in Neo4j
    res = await client.driver.execute_query(
        "MATCH (d:Entity {uuid: 'test-uuid-msg'}) RETURN d.corroborated as corroborated, d.confidence as confidence, d.corroboration_commit as sha, d.last_reinforced_at as lra, d.validated as validated"
    )
    assert len(res.records) > 0
    rec = res.records[0]
    assert rec["corroborated"] is True
    assert rec["sha"] == "sha_msg_123"
    # confidence stays at its original stored value (0.6) — corroboration no
    # longer bumps it to 1.0 in v0.3.0.
    assert rec["confidence"] == 0.6
    # validated is not flipped by corroboration; only `memex review` does that.
    assert rec["validated"] in (False, None)
    # last_reinforced_at must have been set
    assert rec["lra"] is not None

@pytest.mark.asyncio
async def test_decision_corroboration_by_file():
    """
    Test that a decision is corroborated when the commit changes a related file.
    """
    client = await get_graph_client()
    
    # 1. Clean up
    await client.driver.execute_query("MATCH (d:Entity) WHERE d.source = 'agent' OR d.name = 'Refactor DB' DETACH DELETE d")
    
    # 2. Record a decision and a related module
    decision_text = "Refactor DB connection"
    await client.driver.execute_query("""
    CREATE (d:Entity {
        name: $text,
        type: 'Decision',
        source: 'agent',
        confidence: 0.6,
        corroborated: false,
        uuid: 'test-uuid-file'
    })
    CREATE (m:Entity {
        name: 'database.py',
        type: 'Module'
    })
    CREATE (d)-[:MOTIVATES]->(m)
    """, params={"text": decision_text})
    
    # 3. Simulate a CommitEvent touching the related file
    event = CommitEvent(
        sha="sha_file_456",
        repo_root=".",
        message="minor updates",
        diff="--- a/database.py\n+++ b/database.py\n",
        files_changed=["database.py"],
        timestamp=datetime.now(UTC)
    )

    # 4. Call handle_commit
    with patch("memex.watcher.handlers.extract_decisions", return_value=[]), \
         patch("memex.graph.cluster_summary._embed_text") as mock_embed:
        # Mock matching embeddings to yield cosine similarity of 1.0 (>= 0.6)
        mock_embed.side_effect = [
            [1.0, 0.0],  # commit
            [1.0, 0.0]   # decision
        ]
        await handle_commit(event)

    # 5. Verify — v0.3.0: confidence is no longer mutated by corroboration.
    res = await client.driver.execute_query(
        "MATCH (d:Entity {uuid: 'test-uuid-file'}) RETURN d.corroborated as corroborated, d.confidence as confidence, d.last_reinforced_at as lra"
    )
    assert len(res.records) > 0
    rec = res.records[0]
    assert rec["corroborated"] is True
    # stored confidence stays at its original value (0.6); not bumped to 1.0.
    assert rec["confidence"] == 0.6
    assert rec["lra"] is not None

@pytest.mark.asyncio
async def test_decision_no_corroboration_no_match():
    """
    Test that a decision is NOT corroborated if there is no match.
    """
    client = await get_graph_client()
    await client.driver.execute_query("MATCH (d:Entity) WHERE d.source = 'agent' DETACH DELETE d")

    await client.driver.execute_query("""
    CREATE (d:Entity {
        name: 'Something unrelated',
        type: 'Decision',
        source: 'agent',
        confidence: 0.6,
        corroborated: false,
        uuid: 'test-uuid-no-match'
    })
    """)

    event = CommitEvent(
        sha="sha_no_789",
        repo_root=".",
        message="totally different thing",
        diff="--- a/other.py\n+++ b/other.py\n",
        files_changed=["other.py"],
        timestamp=datetime.now(UTC)
    )    
    with patch("memex.watcher.handlers.extract_decisions", return_value=[]):
        await handle_commit(event)
            
    res = await client.driver.execute_query(
        "MATCH (d:Entity {uuid: 'test-uuid-no-match'}) RETURN d.corroborated as corroborated"
    )
    assert len(res.records) > 0
    # Should be False or NULL (depending on how it was created, here it's false)
    assert res.records[0]["corroborated"] is False
