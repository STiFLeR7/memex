import pytest
import os
from memex.extractor.treesitter import extract_symbol_delta
from memex.synthesizer.commit import extract_decisions
from memex.graph.writer import write_symbol_delta, write_decision
from neo4j import GraphDatabase

@pytest.mark.asyncio
@pytest.mark.integration
async def test_phase1_e2e_smoke():
    """
    Final smoke test for Phase 1:
    1. Extract Symbol Delta
    2. Write Symbol Delta to Graphiti
    3. Extract Decisions via Gemini
    4. Write Decisions to Graphiti
    5. Verify nodes exist in Neo4j via direct Cypher query
    """
    # Configuration
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "memex-local")
    commit_sha = "smoke_test_sha_999"

    # 1. Extractor
    file_path = "smoke_test.py"
    old_content = ""
    new_content = "def smoke_fn():\n    pass\n\nclass SmokeClass:\n    pass"
    delta = await extract_symbol_delta(file_path, old_content, new_content)
    assert len(delta.added) == 2

    # 2. Graph Writer (Symbols)
    await write_symbol_delta(delta, source_commit=commit_sha)

    # 3. Synthesizer
    commit_msg = "Initial smoke test commit. Decided to use Gemini Flash for synthesis."
    diff_sum = "Added smoke_test.py with smoke_fn and SmokeClass."
    decisions = await extract_decisions(commit_msg, diff_sum, commit_sha)
    
    # 4. Graph Writer (Decisions)
    for decision in decisions:
        await write_decision(decision, [file_path], commit_sha)

    # 5. Direct Neo4j Verification
    # We wait a brief moment for Graphiti's background indexing if necessary
    # (Though add_episode is usually sufficient for node creation)
    
    driver = GraphDatabase.driver(uri, auth=(user, password))
    with driver.session() as session:
        # Check for Entities created by Graphiti
        # Graphiti creates 'Entity' nodes with a 'name' property
        result = session.run(
            "MATCH (n:Entity) WHERE n.name IN ['smoke_test.py', 'smoke_fn', 'SmokeClass'] RETURN n.name as name"
        )
        found_names = [record["name"] for record in result]
        
        print(f"Nodes found in Neo4j: {found_names}")
        
        # Verify at least the symbols/files we added are there
        # Note: Graphiti might name the file node slightly differently depending on extraction
        assert "smoke_fn" in found_names
        assert "SmokeClass" in found_names
        
    driver.close()
    print("\nPhase 1 Smoke Test Passed.")
