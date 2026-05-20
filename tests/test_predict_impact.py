"""Phase 9 — predict_impact MCP tool tests.

By contract this tool is PURE GRAPH TRAVERSAL — no Gemini/genai call.
Tests assert both functional correctness AND that no LLM is invoked.
"""

import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from memex.mcp_server.tools_impact import predict_impact, _format_impact_report


def _row(module, call=0, imp=0, dec=0):
    return {
        "module": module,
        "call_count": call,
        "import_count": imp,
        "decision_count": dec,
        "total_score": call + imp + dec,
    }


@pytest.mark.asyncio
async def test_predict_impact_returns_ranked_modules():
    """Fixture rows with known coupling — assert the report names every
    module and orders them by total coupling strength (descending)."""
    rows = [
        _row("memex/auth.py", call=5, imp=2, dec=1),  # 8
        _row("memex/api.py", call=2, imp=3, dec=0),   # 5
        _row("memex/cli.py", call=1, imp=0, dec=0),   # 1
    ]

    with patch(
        "memex.mcp_server.tools_impact._query_coupled_modules",
        new=AsyncMock(return_value=rows),
    ):
        result = await predict_impact("memex/core.py")

    # All three modules present
    assert "memex/auth.py" in result
    assert "memex/api.py" in result
    assert "memex/cli.py" in result
    # auth.py (score 8) ranked before api.py (score 5) ranked before cli.py (score 1)
    assert result.index("memex/auth.py") < result.index("memex/api.py") < result.index("memex/cli.py")


@pytest.mark.asyncio
async def test_predict_impact_cpu_bound_not_llm():
    """Verify NO Gemini/genai call is made. We mock the genai module to
    track any access and assert it was never used during predict_impact."""
    rows = [_row("memex/auth.py", call=3, imp=1, dec=0)]

    # Build a sentinel that records attribute access
    sentinel = MagicMock()
    sentinel.Client = MagicMock(side_effect=AssertionError(
        "predict_impact must NOT instantiate a Gemini client"
    ))

    with patch.dict(
        sys.modules,
        {"google": MagicMock(genai=sentinel), "google.genai": sentinel},
    ), patch(
        "memex.mcp_server.tools_impact._query_coupled_modules",
        new=AsyncMock(return_value=rows),
    ):
        result = await predict_impact("memex/core.py")

    # Result returned (proof we ran end-to-end)
    assert "memex/auth.py" in result
    # And genai was never instantiated
    assert not sentinel.Client.called, "predict_impact must not call Gemini"

    # Belt-and-braces: the tools_impact module must not import genai at
    # module load time either (so even if an LLM call were *added* it
    # wouldn't sneak in via implicit module init).
    import memex.mcp_server.tools_impact as ti
    src_attrs = dir(ti)
    assert "genai" not in src_attrs, "tools_impact must not import genai at module level"


@pytest.mark.asyncio
async def test_predict_impact_includes_basis_per_prediction():
    """Each ranked module must include a 'based on N calls, M imports, K
    decision links' explanation so the agent understands the coupling
    strength rather than just trusting the score."""
    rows = [
        _row("memex/auth.py", call=5, imp=2, dec=1),
        _row("memex/api.py", call=0, imp=3, dec=2),
    ]

    with patch(
        "memex.mcp_server.tools_impact._query_coupled_modules",
        new=AsyncMock(return_value=rows),
    ):
        result = await predict_impact("memex/core.py")

    # Per-row basis present
    assert "5 calls" in result
    assert "2 imports" in result
    assert "1 decision links" in result
    assert "3 imports" in result
    assert "2 decision links" in result
    # Standard "based on" keyword for the explanation
    assert "based on" in result


@pytest.mark.asyncio
async def test_predict_impact_empty_returns_helpful_message():
    """No coupled modules in the graph → return a helpful 'no coupling
    found' message rather than an empty string."""
    with patch(
        "memex.mcp_server.tools_impact._query_coupled_modules",
        new=AsyncMock(return_value=[]),
    ):
        result = await predict_impact("memex/brand_new.py")

    assert "no historically-coupled" in result.lower() or "no coupling" in result.lower()


@pytest.mark.asyncio
async def test_predict_impact_rejects_empty_path():
    result = await predict_impact("")
    assert "file_path" in result.lower()
    assert "required" in result.lower()


def test_format_impact_report_truncates_at_budget():
    """_format_impact_report must respect the char budget (~8000 chars)."""
    huge_rows = [_row(f"module_{i}.py", call=i) for i in range(500)]
    out = _format_impact_report("source.py", huge_rows)
    assert len(out) <= 2000 * 4  # CHAR_BUDGET


# ---------------------------------------------------------------------------
# Integration — exercises the REAL cypher (audit T1).
#
# Every unit test above mocks `_query_coupled_modules`, so the cypher — the
# component that returned empty for every file in the v0.3.6 dead-tool bug —
# had ZERO execution coverage. These tests write structured Symbol + CALLS
# nodes (LLM-free) under an isolated repo_path, run the real predict_impact,
# and clean up. Requires a live Neo4j (`-m integration`).
# ---------------------------------------------------------------------------

_TEST_REPO = "__memex_predict_impact_it__"


@pytest.fixture
async def fresh_client():
    """A graph client reset around each integration test. Without this the
    cached Neo4j driver is reused across tests on a stale event loop and the
    bolt write-future goes None ('NoneType has no attribute send'). Mirrors the
    autouse cleanup in test_corroboration.py / test_mcp_queries_integration.py."""
    from memex.graph.client import get_graph_client, reset_graph_client
    await reset_graph_client()
    client = await get_graph_client()
    yield client
    await reset_graph_client()


async def _seed_two_files_with_call(client, repo):
    """foo() in a.py calls bar() in b.py — one cross-file CALLS edge."""
    from datetime import datetime, UTC
    from memex.graph.schema import Symbol
    from memex.extractor.treesitter import CallEdge
    from memex.graph.writer import _merge_structured_symbol, write_call_edges

    now = datetime.now(UTC)
    foo = Symbol(name="foo", kind="fn", signature="def foo()", file="a.py", line=1)
    bar = Symbol(name="bar", kind="fn", signature="def bar()", file="b.py", line=1)
    await _merge_structured_symbol(client, foo, repo, now, None)
    await _merge_structured_symbol(client, bar, repo, now, None)
    return await write_call_edges(
        [CallEdge(caller="foo", callee="bar", file="a.py", line=2)],
        repo_root=repo,
    )


async def _purge(client, repo):
    await client.driver.execute_query(
        "MATCH (n:Entity {repo_path: $r}) DETACH DELETE n", params={"r": repo}
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_predict_impact_real_cypher_returns_coupling(fresh_client):
    """Real cypher returns coupling AND the repo_path join survives a
    write/read spelling mismatch (audit T1 + B1): we seed under the canonical
    repo_path but query with the raw spelling, which predict_impact
    canonicalizes — they must still match."""
    from memex.config import canonical_repo_path

    repo_canon = canonical_repo_path(_TEST_REPO)
    client = fresh_client
    await _purge(client, repo_canon)
    try:
        written = await _seed_two_files_with_call(client, repo_canon)
        assert written == 1, "expected one resolved CALLS edge"

        # Query with the RAW spelling — predict_impact must canonicalize it.
        out = await predict_impact("a.py", repo=_TEST_REPO)

        assert "b.py" in out, f"predict_impact should surface b.py, got:\n{out}"
        assert "no historically-coupled" not in out.lower()
        assert "1 calls" in out  # the basis explanation
    finally:
        await _purge(client, repo_canon)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_predict_impact_import_dimension(fresh_client):
    """Audit Q3 — pin the IMPORTS coupling dimension. `__mxit_a.py` IMPORTS
    `__mxit_b.py`, so predict_impact('__mxit_a.py') must surface b via imports."""
    from memex.config import canonical_repo_path

    repo_canon = canonical_repo_path(_TEST_REPO)
    client = fresh_client
    await _purge(client, repo_canon)
    try:
        await client.driver.execute_query(
            """
            CREATE (a:Entity {name:'__mxit_a.py', type:'Module', repo_path:$r})
            CREATE (b:Entity {name:'__mxit_b.py', type:'Module', repo_path:$r})
            CREATE (a)-[:IMPORTS]->(b)
            """,
            params={"r": repo_canon},
        )
        out = await predict_impact("__mxit_a.py", repo=_TEST_REPO)
        assert "__mxit_b.py" in out, out
        assert "imports" in out
    finally:
        await _purge(client, repo_canon)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_predict_impact_decision_dimension(fresh_client):
    """Audit Q3 — pin the decision-linkage dimension. A symbol in `__mxit_d.py`
    is MENTIONED by a Decision that also MENTIONS module `__mxit_e.py`."""
    from memex.config import canonical_repo_path

    repo_canon = canonical_repo_path(_TEST_REPO)
    client = fresh_client
    await _purge(client, repo_canon)
    try:
        await client.driver.execute_query(
            """
            CREATE (s:Entity {name:'fn', type:'Symbol', file:'__mxit_d.py', repo_path:$r})
            CREATE (dec:Entity {name:'Decision X', type:'Decision', repo_path:$r})
            CREATE (mod:Entity {name:'__mxit_e.py', type:'Module', repo_path:$r})
            CREATE (s)-[:MENTIONS]->(dec)
            CREATE (dec)-[:MENTIONS]->(mod)
            """,
            params={"r": repo_canon},
        )
        out = await predict_impact("__mxit_d.py", repo=_TEST_REPO)
        assert "__mxit_e.py" in out, out
        assert "decision links" in out
    finally:
        await _purge(client, repo_canon)
