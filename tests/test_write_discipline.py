import pytest
import os
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, UTC
from memex.graph.writer import write_decision, MemexWriteError
from memex.mcp_server.tools_write import record_decision
from memex.watcher.handlers import corroborate_decisions
from memex.mcp_server.queries import get_recent_decisions_raw

@pytest.fixture
def mock_client():
    with patch("memex.graph.writer.get_graph_client", new_callable=AsyncMock) as mock_writer_client, \
         patch("memex.mcp_server.tools_write.get_graph_client", new_callable=AsyncMock) as mock_tools_client, \
         patch("memex.mcp_server.queries.get_graph_client", new_callable=AsyncMock) as mock_queries_client, \
         patch("memex.watcher.handlers.get_graph_client", new_callable=AsyncMock) as mock_handlers_client:
        client = MagicMock()
        client.add_episode = AsyncMock()
        client.driver = MagicMock()
        client.driver.execute_query = AsyncMock()
        client.search = AsyncMock()
        
        mock_writer_client.return_value = client
        mock_tools_client.return_value = client
        mock_queries_client.return_value = client
        mock_handlers_client.return_value = client
        yield client

@pytest.mark.asyncio
async def test_uuid_fallback_success(mock_client):
    """
    If add_episode returns uuid=None, fallback Cypher lookup is triggered.
    If the fallback lookup succeeds, the post-hoc SET is executed.
    """
    decision = MagicMock()
    decision.text = "Switch auth to JWT"
    decision.rationale = "easier scaling"
    decision.scope = "auth.py"
    decision.validated = False
    decision.base_confidence = 0.6
    decision.source = "watcher"
    
    # 1. Mock add_episode returning None for uuid
    episode_resp = MagicMock()
    episode_resp.episode = MagicMock()
    episode_resp.episode.uuid = None
    mock_client.add_episode.return_value = episode_resp

    # 2. Mock fallback Cypher query finding the node
    record = MagicMock()
    record.__getitem__.side_effect = lambda k: "fallback-uuid-123" if k == "uuid" else None
    mock_res = MagicMock()
    mock_res.records = [record]
    mock_client.driver.execute_query.return_value = mock_res

    await write_decision(decision, ["auth.py"], "commit-sha-123")

    # Verify add_episode was called
    mock_client.add_episode.assert_called_once()
    # Verify two Cypher queries: 1. fallback lookup, 2. post-hoc SET
    assert mock_client.driver.execute_query.call_count == 2
    
    # Check that fallback lookup occurred
    call_args_fallback = mock_client.driver.execute_query.call_args_list[0]
    assert "MATCH (n:Entity) WHERE n.name = $name" in call_args_fallback[0][0]
    assert call_args_fallback[1]["params"]["name"] == "decision_commit-s"

    # Check that post-hoc SET occurred with fallback UUID
    call_args_set = mock_client.driver.execute_query.call_args_list[1]
    assert "MATCH (n:Entity)" in call_args_set[0][0]
    assert "SET n.validated =" in call_args_set[0][0]
    assert call_args_set[1]["params"]["uuid"] == "fallback-uuid-123"

@pytest.mark.asyncio
async def test_uuid_fallback_failure_raises_error(mock_client):
    """
    If add_episode returns uuid=None, and fallback Cypher lookup also returns None,
    MemexWriteError is raised.
    """
    decision = MagicMock()
    decision.text = "Switch auth to JWT"
    decision.rationale = "easier scaling"
    decision.scope = "auth.py"
    decision.validated = False
    decision.base_confidence = 0.6
    decision.source = "watcher"
    
    # Mock add_episode returning None for uuid
    episode_resp = MagicMock()
    episode_resp.episode = MagicMock()
    episode_resp.episode.uuid = None
    mock_client.add_episode.return_value = episode_resp

    # Mock fallback Cypher query returning no records
    mock_res = MagicMock()
    mock_res.records = []
    mock_client.driver.execute_query.return_value = mock_res

    with pytest.raises(MemexWriteError) as exc_info:
        await write_decision(decision, ["auth.py"], "commit-sha-123")

    assert "write_decision: episode 'decision_commit-s' not found" in str(exc_info.value)

@pytest.mark.asyncio
async def test_supersedes_nonexistent_node_returns_error(mock_client):
    """
    If supersedes points to a nonexistent node, record_decision returns an error.
    """
    mock_res = MagicMock()
    mock_res.records = []
    mock_client.driver.execute_query.return_value = mock_res

    res = await record_decision(
        text="Switch authentication to OAuth2",
        repo=".",
        supersedes="nonexistent-uuid-999"
    )

    assert "Error: supersedes target 'nonexistent-uuid-999' not found" in res
    mock_client.add_episode.assert_not_called()

@pytest.mark.asyncio
async def test_contradiction_check_scopes_to_same_module(mock_client):
    """
    Contradiction check requires both high similarity AND matching module scope.
    """
    # Configure config mock to avoid absolute path scoping issues
    with patch("memex.mcp_server.tools_write.get_config") as mock_cfg:
        cfg = MagicMock()
        cfg.repo_root = "."
        cfg.retrieval = None  # Force fallback threshold of 0.85 rather than 1.0 (float(MagicMock))
        mock_cfg.return_value = cfg

        # 1. Mock search returning a candidate
        candidate = MagicMock()
        candidate.type = "Decision"
        candidate.repo_path = os.path.abspath(".")
        candidate.score = 0.9
        candidate.uuid = "similar-uuid-jwt"
        candidate.name = "Switch auth to JWT"
        mock_client.search.return_value = [candidate]

        # 2. Mock related modules query returning ["auth.py"] for this candidate
        m_rec = MagicMock()
        m_rec.__getitem__.side_effect = lambda k: ["auth.py"] if k == "modules" else None
        m_res = MagicMock()
        m_res.records = [m_rec]
        
        # 3. Handle double execute_query (exist check for supersedes/modules check)
        mock_client.driver.execute_query.return_value = m_res

        # Call record_decision with matching text but DIFFERENT module -> should NOT match (write succeeds)
        mock_client.add_episode.reset_mock()
        mock_client.add_episode.return_value = MagicMock(episode=MagicMock(uuid="new-uuid"))
        
        res = await record_decision(
            text="Switch authentication to JWT",
            module="payments.py",
            repo="."
        )
        assert "decision recorded" in res
        mock_client.add_episode.assert_called()

        # Call record_decision with matching text and SAME module -> should MATCH (returns intent prompt)
        mock_client.add_episode.reset_mock()
        res_conflict = await record_decision(
            text="Switch authentication to JWT",
            module="auth.py",
            repo="."
        )
        assert "similar decision already exists" in res_conflict
        mock_client.add_episode.assert_not_called()

@pytest.mark.asyncio
async def test_corroboration_two_pass_embedding_similarity(mock_client):
    """
    corroborate_decisions matches files in changed set, then gates on semantic similarity >= 0.6.
    """
    # 1. Fetch uncorroborated decisions mock query return
    dec_rec = MagicMock()
    dec_rec.__getitem__.side_effect = lambda k: {
        "id": "dec-uuid-1",
        "eid": "dec-eid-1",
        "text": "Auth: switched from RS256 to EdDSA",
        "related_entities": ["auth/service.py"]
    }.get(k)
    dec_res = MagicMock()
    dec_res.records = [dec_rec]
    mock_client.driver.execute_query.return_value = dec_res

    # 2. Mock _embed_text calls
    # Let's say:
    # commit message: "refactor: use EdDSA for key rotation"
    # decision text: "Auth: switched from RS256 to EdDSA"
    # similarity >= 0.6
    with patch("memex.graph.cluster_summary._embed_text") as mock_embed:
        # First call: commit message embedding
        # Second call: decision text embedding
        mock_embed.side_effect = [
            [0.8, 0.6, 0.0],  # commit
            [0.8, 0.55, 0.0]  # decision
        ]

        count = await corroborate_decisions(
            repo_root=".",
            sha="sha-eddsa",
            message="refactor: use EdDSA for key rotation",
            files_changed=["auth/service.py"]
        )

        assert count == 1
        # Verify Neo4j was updated
        assert mock_client.driver.execute_query.call_count == 2  # 1st: query, 2nd: update SET

@pytest.mark.asyncio
async def test_get_recent_decisions_corroborated_only(mock_client):
    """
    get_recent_decisions_raw uses $corroborated_only in parameters.
    """
    mock_res = MagicMock()
    mock_res.records = []
    mock_client.driver.execute_query.return_value = mock_res

    await get_recent_decisions_raw(
        since_days=7,
        module=None,
        limit=10,
        repo=".",
        corroborated_only=True
    )

    # Check execute_query parameters passed corroborated_only=True
    call_args = mock_client.driver.execute_query.call_args
    assert call_args[1]["params"]["corroborated_only"] is True
    assert "$corroborated_only = false OR d.corroborated = true OR d.validated = true" in call_args[0][0]


# --- Signal Pillar A: per-harness initial confidence wiring -----------------

def test_initial_confidence_for_resolves_by_harness():
    """The config resolver keys initial confidence by harness, falling back to
    the `default` entry and finally to the HarnessConfig default (0.6)."""
    from memex.config import Config, HarnessConfig

    cfg = Config(
        neo4j_uri="x", neo4j_user="x", neo4j_password="x", gemini_api_key="x",
        harnesses={
            "claude-code": HarnessConfig(initial_decision_confidence=0.7),
            "default": HarnessConfig(initial_decision_confidence=0.6),
        },
    )
    assert cfg.initial_confidence_for("claude-code") == 0.7
    assert cfg.initial_confidence_for("codex") == 0.6   # unknown harness -> default
    assert cfg.initial_confidence_for(None) == 0.6      # watcher synthesis -> default

    # No harness config at all -> HarnessConfig field default, never an
    # implicit 1.0.
    bare = Config(neo4j_uri="x", neo4j_user="x", neo4j_password="x", gemini_api_key="x")
    assert bare.initial_confidence_for("anything") == 0.6


@pytest.mark.asyncio
async def test_record_decision_sets_configured_initial_confidence(mock_client):
    """Agent-recorded decisions must carry the configured initial base_confidence
    (Signal Pillar A) rather than the implicit coalesce(..., 1.0) over-trust
    fallback that current_confidence applies when base_confidence is unset."""
    mock_client.add_episode.return_value = MagicMock(episode=MagicMock(uuid="agent-dec-1"))
    mock_client.driver.execute_query.return_value = MagicMock(records=[])

    with patch("memex.mcp_server.tools_write.get_config") as mock_cfg:
        cfg = MagicMock()
        cfg.initial_confidence_for.return_value = 0.55
        mock_cfg.return_value = cfg

        res = await record_decision(
            text="Adopt EdDSA token signing for rotation simplicity",
            module="auth.py",
            repo=".",
            force=True,  # skip Layer B intent-confirmation
        )

    assert "decision recorded" in res
    set_calls = [
        c for c in mock_client.driver.execute_query.call_args_list
        if "base_confidence" in c[0][0]
    ]
    assert set_calls, "expected a SET writing base_confidence on the new Decision node"
    params = set_calls[0][1]["params"]
    assert params["base_confidence"] == 0.55
    assert params["validated"] is False
    # The configured harness default (None -> default) must have been consulted.
    cfg.initial_confidence_for.assert_called()
