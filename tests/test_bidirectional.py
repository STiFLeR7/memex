import pytest
import re
from datetime import datetime, UTC
from memex.mcp_server.tools_write import record_decision, resolve_problem
from memex.mcp_server.tools_read import get_open_problems, get_recent_decisions
from memex.graph.client import get_graph_client, reset_graph_client

def extract_id(result_text: str) -> str:
    """Helper to extract [id: <uuid>] from tool responses."""
    match = re.search(r"\[id: ([^\]]+)\]", result_text)
    if not match:
        raise ValueError(f"Could not find ID in response: {result_text}")
    return match.group(1)

@pytest.fixture(autouse=True)
async def cleanup():
    """Start with a clean client state."""
    await reset_graph_client()
    yield
    await reset_graph_client()

@pytest.mark.asyncio
@pytest.mark.integration
async def test_agent_session_compounds_graph():
    """
    Integration test proving that agent sessions contribute to and resolve 
    information in the compounding graph.
    """
    client = await get_graph_client()
    now = datetime.now(UTC)

    # --- Session 1: Observation ---
    print("\n[Session 1] Agent recording decision and problem...")

    # 1. Record a decision normally (LLM based)
    dec_text = "Switched EventRouter debounce from threading.Timer to asyncio.create_task for event loop safety"
    dec_result = await record_decision(
        text=dec_text,
        module="memex/watcher/event_router.py"
    )
    assert "decision recorded" in dec_result

    # 2. SEED a problem manually to ensure it's available for resolution immediately
    # We create a node that looks exactly like what our tools expect
    import uuid
    problem_id = f"test-prob-{uuid.uuid4().hex[:8]}"
    await client.driver.execute_query(
        "CREATE (n:Entity {uuid: $id, name: 'CommitPoller concurrency', type: 'Problem', status: 'open', created_at: datetime($now)})",
        params={"id": problem_id, "now": now.isoformat()}
    )
    print(f"      Seeded Problem ID: {problem_id}")

    # --- Session 2: Action ---
    print("[Session 2] Different agent resolving the problem...")
    res_text = "Added file lock via portalocker around pending_commit.json read-delete cycle"
    res_result = await resolve_problem(
        problem_id=problem_id,
        resolution_text=res_text
    )
    assert "problem resolved" in res_result
    
    # --- Verification ---
    print("[Verification] Checking graph consistency...")
    
    # 1. Verify problem is no longer open
    open_problems = await get_open_problems()
    # We assert that the specific seeded problem name is GONE from the open list
    assert "CommitPoller concurrency" not in open_problems
    
    # 2. Verify decision persists
    decisions = await get_recent_decisions(days=1)
    assert "EventRouter" in decisions
    assert "asyncio.create_task" in decisions
    
    print("\nBidirectionality Verified: Graph successfully compounded across sessions.")
