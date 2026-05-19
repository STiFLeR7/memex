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
