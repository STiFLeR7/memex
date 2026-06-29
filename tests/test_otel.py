import os
import pytest
from unittest.mock import MagicMock, patch

@pytest.fixture(autouse=True)
def reset_otel_state():
    import memex.graph.otel
    def _clear():
        memex.graph.otel._tracer = None
        memex.graph.otel._meter = None
        memex.graph.otel._initialized = False
        if hasattr(memex.graph.otel.record_token_metrics, "_counters"):
            delattr(memex.graph.otel.record_token_metrics, "_counters")
        if hasattr(memex.graph.otel.record_validated_ratio, "_gauge"):
            delattr(memex.graph.otel.record_validated_ratio, "_gauge")
    _clear()
    yield
    _clear()


def test_tool_span_yields_none_without_sdk():
    with patch.dict("sys.modules", {"opentelemetry": None}):
        import memex.graph.otel
        with memex.graph.otel.tool_span("get_project_context", "/fake/repo", "claude-code") as span:
            assert span is None


def test_tool_span_creates_span_with_sdk():
    mock_tracer = MagicMock()
    mock_span = MagicMock()
    mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span
    
    with patch("opentelemetry.trace.get_tracer", return_value=mock_tracer):
        import memex.graph.otel
        with memex.graph.otel.tool_span("get_project_context", "/fake/repo", "claude-code") as span:
            assert span == mock_span
            
        mock_tracer.start_as_current_span.assert_called_once_with(
            "mcp.tool.get_project_context",
            attributes={
                "gen_ai.system": "memex",
                "mcp.tool.name": "get_project_context",
                "mcp.server.name": "memex",
                "memex.repo_path": "/fake/repo",
                "memex.agent": "claude-code",
            }
        )


def test_record_token_metrics_noop_without_sdk():
    with patch.dict("sys.modules", {"opentelemetry": None}):
        import memex.graph.otel
        # Should run without raising any exceptions
        memex.graph.otel.record_token_metrics("get_project_context", 100, 200, 100)


def test_record_token_metrics_creates_counters():
    mock_meter = MagicMock()
    mock_counter = MagicMock()
    mock_meter.create_counter.return_value = mock_counter
    
    with patch("opentelemetry.metrics.get_meter", return_value=mock_meter):
        import memex.graph.otel
        memex.graph.otel.record_token_metrics("get_project_context", 100, 200, 100)
        
        # Verify counters created
        assert mock_meter.create_counter.call_count == 3
        mock_meter.create_counter.assert_any_call(
            "memex.tokens.returned",
            description="Tokens returned by memex MCP tools",
            unit="token"
        )
        mock_meter.create_counter.assert_any_call(
            "memex.tokens.naive",
            description="Estimated naive token cost without memex",
            unit="token"
        )
        mock_meter.create_counter.assert_any_call(
            "memex.tokens.saved",
            description="Tokens saved by memex context compression",
            unit="token"
        )
        
        # Verify metric updates
        assert mock_counter.add.call_count == 3
        mock_counter.add.assert_any_call(100, {"mcp.tool.name": "get_project_context"})
        mock_counter.add.assert_any_call(200, {"mcp.tool.name": "get_project_context"})
        mock_counter.add.assert_any_call(100, {"mcp.tool.name": "get_project_context"})


@patch("memex.graph.telemetry.TelemetryDB")
@patch("memex.graph.otel.record_token_metrics")
@pytest.mark.asyncio
async def test_record_tool_call_emits_otel(mock_record_otel, mock_db_class):
    mock_db = MagicMock()
    mock_db_class.return_value = mock_db
    
    from memex.graph.telemetry import record_tool_call
    await record_tool_call("search_context", 100, repo_path="/fake/repo")
    
    # Verify both SQLite and OTel metric emissions were called
    mock_db.record_call.assert_called_once()
    mock_record_otel.assert_called_once_with(
        "search_context",
        100,
        1200,  # search_context naive multiplier is 12 (100 * 12 = 1200)
        1100   # saved = 1200 - 100 = 1100
    )


def test_set_decision_confidence_noop_without_sdk():
    with patch.dict("sys.modules", {"opentelemetry": None}):
        import memex.graph.otel
        # Should run without raising when the SDK is absent.
        memex.graph.otel.set_decision_confidence(0.73)


def test_set_decision_confidence_sets_attribute_on_span():
    mock_tracer = MagicMock()
    mock_span = MagicMock()

    with patch("opentelemetry.trace.get_tracer", return_value=mock_tracer), \
         patch("opentelemetry.trace.get_current_span", return_value=mock_span):
        import memex.graph.otel
        memex.graph.otel.set_decision_confidence(0.73)

        mock_span.set_attribute.assert_called_once_with("memex.decision.confidence", 0.73)


def test_record_validated_ratio_noop_without_sdk():
    with patch.dict("sys.modules", {"opentelemetry": None}):
        import memex.graph.otel
        memex.graph.otel.record_validated_ratio(0.5)


def test_record_validated_ratio_creates_and_sets_gauge():
    mock_meter = MagicMock()
    mock_gauge = MagicMock()
    mock_meter.create_gauge.return_value = mock_gauge

    with patch("opentelemetry.metrics.get_meter", return_value=mock_meter):
        import memex.graph.otel
        memex.graph.otel.record_validated_ratio(0.42)
        # second call must reuse the gauge, not recreate it
        memex.graph.otel.record_validated_ratio(0.84)

        mock_meter.create_gauge.assert_called_once_with(
            "memex.decision.validated_ratio",
            description="Fraction of surfaced decisions that are validated or corroborated",
            unit="1",
        )
        assert mock_gauge.set.call_count == 2
        mock_gauge.set.assert_any_call(0.42)
        mock_gauge.set.assert_any_call(0.84)


def test_init_otel_idempotent():
    with patch("opentelemetry.trace.get_tracer") as mock_get_tracer:
        import memex.graph.otel
        memex.graph.otel._init_otel()
        memex.graph.otel._init_otel()
        assert mock_get_tracer.call_count == 1
