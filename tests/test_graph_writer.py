import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from memex.graph.writer import (
    write_symbol_delta, write_decision, write_call_edges, MemexSchemaError, MemexWriteError,
)
from memex.extractor.treesitter import SymbolDelta, Symbol as ExtractedSymbol, CallEdge

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
async def test_write_symbol_delta_added_materializes_structured_node(mock_client):
    """v0.3.7 Layer 1 — an added symbol must reach Neo4j as a *structured*,
    queryable node (type='Symbol' with `file`, `kind`, `line`, `repo_path`),
    not only as Graphiti NL prose.

    This is the test that would have caught the predict_impact dead-tool bug:
    `predict_impact` does `MATCH (src:Entity) WHERE src.file=$file
    AND src.repo_path=$repo`, but the writer only ever called add_episode,
    so no node carried a `file` prop and the tool returned empty for every
    file. See docs/BUG_0.3.6.md."""
    sym = ExtractedSymbol(
        name="login", kind="fn", signature="def login(user)",
        file="memex/auth.py", line=42,
    )
    delta = SymbolDelta(added=[sym], removed=[], modified=[])

    await write_symbol_delta(delta, source_commit="abc1234", repo_root="D:/memex")

    # NL episode still written (Graphiti search surface preserved).
    mock_client.add_episode.assert_awaited_once()

    # AND a post-hoc Cypher MERGE materialized the structured node.
    mock_client.driver.execute_query.assert_awaited()
    query_text = mock_client.driver.execute_query.call_args.args[0]
    params = mock_client.driver.execute_query.call_args.kwargs["params"]

    assert "MERGE" in query_text
    assert "Symbol" in query_text  # type set to 'Symbol'
    for prop in ("file", "kind", "line"):
        assert prop in query_text, f"structured MERGE missing {prop}"

    assert params["name"] == "login"
    assert params["file"] == "memex/auth.py"
    assert params["kind"] == "fn"
    assert params["line"] == 42
    assert params["repo"] == "D:/memex"


@pytest.mark.asyncio
async def test_write_symbol_delta_materializes_node_even_if_episode_fails(mock_client):
    """The structured Symbol node feeds predict_impact (CPU-only) and must NOT
    be hostage to the Gemini quota. If add_episode raises (e.g. 429 spend cap),
    the deterministic MERGE must still land and write_symbol_delta must not
    raise. Regression guard for the v0.3.7 live-verify finding."""
    mock_client.add_episode.side_effect = Exception("429 RESOURCE_EXHAUSTED")

    sym = ExtractedSymbol(
        name="login", kind="fn", signature="def login(user)",
        file="memex/auth.py", line=42,
    )
    delta = SymbolDelta(added=[sym], removed=[], modified=[])

    # Must not raise despite the LLM failure.
    await write_symbol_delta(delta, repo_root="D:/memex")

    # The structured MERGE still fired.
    mock_client.driver.execute_query.assert_awaited()
    query_text = mock_client.driver.execute_query.call_args.args[0]
    assert "MERGE" in query_text and "Symbol" in query_text


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

@pytest.mark.asyncio
async def test_write_call_edges_merges_calls_relationship(mock_client):
    """v0.3.7 Layer 2 — each resolved call-site becomes a CALLS edge between
    structured Symbol nodes, scoped to the repo. These edges are what
    predict_impact traverses to find coupled modules."""
    edges = [CallEdge(caller="m", callee="foo", file="memex/auth.py", line=6)]

    # Driver returns a single resolved edge.
    rec = MagicMock()
    rec.get.return_value = 1
    mock_client.driver.execute_query.return_value = MagicMock(records=[rec])

    written = await write_call_edges(edges, repo_root="D:/memex")

    mock_client.driver.execute_query.assert_awaited()
    query_text = mock_client.driver.execute_query.call_args.args[0]
    params = mock_client.driver.execute_query.call_args.kwargs["params"]

    assert "CALLS" in query_text and "MERGE" in query_text
    assert params["caller"] == "m"
    assert params["callee"] == "foo"
    assert params["file"] == "memex/auth.py"
    assert params["repo"] == "D:/memex"
    assert written == 1  # one resolved edge


@pytest.mark.asyncio
async def test_write_call_edges_empty_is_noop(mock_client):
    written = await write_call_edges([], repo_root="D:/memex")
    mock_client.driver.execute_query.assert_not_called()
    assert written == 0


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
async def test_write_decision_raises_memex_write_error_when_uuid_missing(mock_client):
    """When Graphiti returns no episode.uuid and fallback name lookup also fails,
    write_decision must raise MemexWriteError."""
    # Episode object with NO uuid attribute.
    mock_episode = MagicMock(spec=[])
    mock_result = MagicMock(episode=mock_episode)
    mock_client.add_episode.return_value = mock_result
    
    # Mock fallback query to return empty records
    mock_client.driver.execute_query.return_value = MagicMock(records=[])

    decision = MagicMock()
    decision.text = "any decision"
    decision.rationale = "any"
    decision.scope = "local"

    with pytest.raises(MemexWriteError, match="not found after add_episode"):
        await write_decision(decision, modules=["x.py"], commit_sha="abcd1234")

    mock_client.add_episode.assert_awaited_once()


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
