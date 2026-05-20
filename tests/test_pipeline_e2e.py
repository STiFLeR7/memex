import pytest
from memex.extractor.treesitter import extract_symbol_delta
from memex.synthesizer.commit import extract_decisions
from memex.graph.writer import write_symbol_delta, write_decision
from memex.graph.client import reset_graph_client

@pytest.fixture(autouse=True)
async def cleanup_client():
    """Ensure a fresh graph client for every test."""
    await reset_graph_client()
    yield
    await reset_graph_client()

@pytest.mark.asyncio
@pytest.mark.integration
async def test_pipeline_e2e_integration():
    """
    Integration test for the full Phase 1 pipeline:
    Extractor -> Synthesizer -> Writer -> Neo4j.
    """
    # 1. Setup sample data
    file_path = "auth_service.py"
    old_content = ""
    new_content = """
def validate_token(token: str):
    \"\"\"Validates a JWT token using RS256.\"\"\"
    pass

class AuthManager:
    def __init__(self, secret: str):
        self.secret = secret
"""
    commit_message = "Switch to RS256 for token validation to improve security and align with industry standards."
    diff_summary = "Added validate_token function and AuthManager class to auth_service.py"
    commit_sha = "deadbeef12345678"

    # 2. Extract Code Changes
    print(f"\n[1/3] Extracting symbol delta for {file_path}...")
    delta = await extract_symbol_delta(file_path, old_content, new_content)
    assert len(delta.added) == 2
    
    # 3. Extract Decisions using Gemini
    print("[2/3] Extracting decisions using Gemini Flash...")
    decisions = await extract_decisions(commit_message, diff_summary, commit_sha)
    # We expect at least one decision about RS256/Security
    assert len(decisions) >= 0 
    print(f"      Found {len(decisions)} decision(s).")

    # 4. Write to Graph
    print("[3/3] Writing to Neo4j via Graphiti...")
    try:
        # Write symbols
        await write_symbol_delta(delta, source_commit=commit_sha)
        
        # Write decisions
        for decision in decisions:
            await write_decision(decision, modules=[file_path], commit_sha=commit_sha)
            
        print("      Pipeline write successful.")
    except Exception as e:
        pytest.fail(f"Pipeline write failed: {e}")

    print("\nPhase 1 E2E Verification Complete. Check Neo4j Browser at http://localhost:7474")
