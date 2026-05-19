import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from memex.graph.writer import write_symbol_delta, write_decision, MemexSchemaError
from memex.extractor.treesitter import SymbolDelta, Symbol as ExtractedSymbol
from memex.graph.schema import DecisionNode

@pytest.fixture
def mock_client():
    with patch("memex.graph.writer.get_graph_client", new_callable=AsyncMock) as mock:
        client = MagicMock()
        client.add_episode = AsyncMock()
        client.driver = MagicMock()
        client.driver.execute_query = AsyncMock()
        mock.return_value = client
        yield client

@pytest.mark.asyncio
async def test_write_symbol_delta_added(mock_client):
    sym = ExtractedSymbol(name="test_fn", kind="fn", signature="def test_fn()", file="test.py", line=10)
    delta = SymbolDelta(added=[sym], removed=[], modified=[])
    
    await write_symbol_delta(delta, source_commit="abc")
    
    mock_client.add_episode.assert_called_once()
    assert "test_fn" in mock_client.add_episode.call_args[1]["name"]
    assert "abc" in mock_client.add_episode.call_args[1]["source_description"]

@pytest.mark.asyncio
async def test_write_symbol_delta_removed(mock_client):
    sym = ExtractedSymbol(name="old_fn", kind="fn", signature="", file="test.py", line=0)
    delta = SymbolDelta(added=[], removed=[sym], modified=[])
    
    await write_symbol_delta(delta)
    
    mock_client.driver.execute_query.assert_called_once()
    query = mock_client.driver.execute_query.call_args[0][0]
    params = mock_client.driver.execute_query.call_args[1]["params"]
    assert "old_fn" == params["name"]
    assert "SET s.valid_until" in query

@pytest.mark.asyncio
async def test_write_symbol_delta_validation_error(mock_client):
    # Use model_construct to bypass initial validation and trigger it in write_symbol_delta
    from memex.graph.schema import Symbol
    sym = Symbol.model_construct(name="test_fn", kind="invalid", signature="def test_fn()", file="test.py", line=10)
    delta = SymbolDelta(added=[sym], removed=[], modified=[])
    
    with pytest.raises(MemexSchemaError) as excinfo:
        await write_symbol_delta(delta)
    assert "SymbolNode" in str(excinfo.value)

@pytest.mark.asyncio
async def test_write_decision_success(mock_client):
    decision = MagicMock()
    decision.text = "New decision"
    decision.rationale = "Because"
    decision.scope = "local"
    
    await write_decision(decision, modules=["a.py"], commit_sha="12345678")
    
    mock_client.add_episode.assert_called_once()
    assert "decision_12345678" in mock_client.add_episode.call_args[1]["name"]

@pytest.mark.asyncio
async def test_write_decision_validation_error(mock_client):
    # Empty text
    decision = MagicMock()
    decision.text = ""
    decision.rationale = "Because"
    decision.scope = "local"
    
    with pytest.raises(MemexSchemaError) as excinfo:
        await write_decision(decision, modules=["a.py"], commit_sha="12345678")
    assert "DecisionNode" in str(excinfo.value)

def test_memex_schema_error_init():
    err = MemexSchemaError("TestModel", [{"msg": "error"}])
    assert err.model_name == "TestModel"
    assert err.errors == [{"msg": "error"}]
    assert "Validation failed for TestModel" in str(err)


@pytest.mark.asyncio
async def test_write_decision_persists_v030_fields_via_post_hoc_cypher(mock_client):
    """v0.3.0 fields (validated, base_confidence, last_reinforced_at, source,
    write_policy) must reach Neo4j as queryable properties — not only the NL
    episode_body. ARCHITECTURE-v0.3.0 §4 Q1. This is the test that would have
    caught B2."""
    # Return a result with an episode.uuid the post-hoc SET can target.
    mock_episode = MagicMock(uuid="episode-uuid-abc")
    mock_result = MagicMock(episode=mock_episode)
    mock_client.add_episode.return_value = mock_result

    decision = MagicMock()
    decision.text = "switch to EdDSA"
    decision.rationale = "key rotation simplicity"
    decision.scope = "module"
    decision.validated = False
    decision.base_confidence = 0.6
    decision.source = "watcher"

    await write_decision(decision, modules=["auth.py"], commit_sha="deadbeef")

    # The episode was written AND a post-hoc Cypher SET fired with all the
    # v0.3.0 fields.
    mock_client.add_episode.assert_awaited_once()
    mock_client.driver.execute_query.assert_awaited_once()

    set_query, set_kwargs = (
        mock_client.driver.execute_query.call_args.args,
        mock_client.driver.execute_query.call_args.kwargs,
    )
    query_text = set_query[0]
    params = set_kwargs.get("params", {})

    # Cypher must contain a SET for each v0.3.0 field.
    for prop in (
        "n.validated",
        "n.base_confidence",
        "n.last_reinforced_at",
        "n.source",
        "n.write_policy",
    ):
        assert prop in query_text, f"post-hoc SET missing {prop}"

    # Parameters must carry the actual values, not the defaults.
    assert params["validated"] is False
    assert params["base_confidence"] == 0.6
    assert params["source"] == "watcher"
    assert params["commit_sha"] == "deadbeef"
    # Post-pass-2: the SET must target by uuid (name-fallback was removed to
    # avoid mis-targeting sibling nodes with colliding short-SHA names).
    assert params["uuid"] == "episode-uuid-abc"
    assert "n.uuid = $uuid" in query_text


@pytest.mark.asyncio
async def test_write_decision_skips_post_hoc_set_when_uuid_missing(mock_client):
    """When Graphiti returns no episode.uuid, the post-hoc SET must NOT fire —
    skipping is safer than name-matching, which can hit sibling nodes with
    colliding short-SHA names (e.g. another `decision_deadbeef` from another
    repo)."""
    # Episode object with NO uuid attribute.
    mock_episode = MagicMock(spec=[])
    mock_result = MagicMock(episode=mock_episode)
    mock_client.add_episode.return_value = mock_result

    decision = MagicMock()
    decision.text = "any decision"
    decision.rationale = "any"
    decision.scope = "local"

    await write_decision(decision, modules=["x.py"], commit_sha="abcd1234")

    mock_client.add_episode.assert_awaited_once()
    # The post-hoc SET must NOT have been called when uuid is missing.
    mock_client.driver.execute_query.assert_not_called()


@pytest.mark.asyncio
async def test_write_decision_logs_warning_when_post_hoc_set_fails(mock_client):
    """If Neo4j rejects the post-hoc SET, the write does not crash — the
    episode is already in the graph; missing flags can be backfilled."""
    mock_result = MagicMock(episode=MagicMock(uuid="uuid-x"))
    mock_client.add_episode.return_value = mock_result
    mock_client.driver.execute_query.side_effect = Exception("transient")

    decision = MagicMock()
    decision.text = "do a thing"
    decision.rationale = "reason"
    decision.scope = "local"

    # Must not raise — Pydantic validates, episode is written, SET fails silently.
    await write_decision(decision, modules=["x.py"], commit_sha="abcd1234")
