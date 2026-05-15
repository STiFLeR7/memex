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
