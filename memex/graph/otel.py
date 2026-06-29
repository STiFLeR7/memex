"""OpenTelemetry instrumentation for memex MCP server.

Emits spans and metrics using gen_ai.* semantic conventions.
Activated only when opentelemetry-api is installed and an exporter
is configured via standard OTEL_* environment variables.
"""

from __future__ import annotations
import logging
from contextlib import contextmanager
from typing import Optional, Generator

logger = logging.getLogger(__name__)

# Lazy-loaded OTel handles — None when SDK not installed
_tracer = None
_meter = None
_initialized = False


def _init_otel() -> None:
    """One-shot initializer. Safe to call multiple times."""
    global _tracer, _meter, _initialized
    if _initialized:
        return
    _initialized = True
    try:
        from opentelemetry import trace, metrics
        _tracer = trace.get_tracer("memex", schema_url="https://opentelemetry.io/schemas/1.28.0")
        _meter = metrics.get_meter("memex", schema_url="https://opentelemetry.io/schemas/1.28.0")
        logger.info("OpenTelemetry instrumentation activated")
    except ImportError:
        logger.debug("opentelemetry-api not installed; OTel instrumentation disabled")


@contextmanager
def tool_span(
    tool_name: str,
    repo_path: str,
    agent: str,
) -> Generator[Optional[object], None, None]:
    """Context manager that creates an OTel span for an MCP tool call.

    Yields the span (or None if OTel is not available).
    Caller should set token attributes on the span before exiting.
    """
    _init_otel()
    if _tracer is None:
        yield None
        return

    with _tracer.start_as_current_span(
        f"mcp.tool.{tool_name}",
        attributes={
            "gen_ai.system": "memex",
            "mcp.tool.name": tool_name,
            "mcp.server.name": "memex",
            "memex.repo_path": repo_path,
            "memex.agent": agent,
        },
    ) as span:
        yield span


def set_decision_confidence(confidence: float) -> None:
    """Annotate the active span with ``memex.decision.confidence`` (Signal D3).

    Called by Decision-returning tools so confidence becomes visible in the same
    trace that already carries token-saving attributes — confidence and savings
    as two axes of one span. No-op when the SDK isn't installed or no span is
    active. ``confidence`` is expected in [0.0, 1.0].
    """
    _init_otel()
    if _tracer is None:
        return
    try:
        from opentelemetry import trace
        span = trace.get_current_span()
        # get_current_span never returns None, but an unrecording default span
        # ignores set_attribute — which is exactly the no-op we want.
        if span is not None:
            span.set_attribute("memex.decision.confidence", float(confidence))
    except Exception:
        logger.debug("failed to set memex.decision.confidence", exc_info=True)


def record_validated_ratio(ratio: float) -> None:
    """Update the ``memex.decision.validated_ratio`` gauge (Signal D3).

    The fraction of recently-surfaced decisions that are validated or
    corroborated, emitted on each get_context_briefing call so Signal's effect
    rides the same metrics pipeline as token savings. No-op without the SDK.
    ``ratio`` is expected in [0.0, 1.0].
    """
    _init_otel()
    if _meter is None:
        return
    if not hasattr(record_validated_ratio, "_gauge"):
        try:
            record_validated_ratio._gauge = _meter.create_gauge(
                "memex.decision.validated_ratio",
                description="Fraction of surfaced decisions that are validated or corroborated",
                unit="1",
            )
        except Exception:
            logger.debug("failed to create validated_ratio gauge", exc_info=True)
            record_validated_ratio._gauge = None
    gauge = record_validated_ratio._gauge
    if gauge is not None:
        try:
            gauge.set(float(ratio))
        except Exception:
            logger.debug("failed to set validated_ratio gauge", exc_info=True)


def record_token_metrics(
    tool_name: str,
    tokens_returned: int,
    tokens_naive: Optional[int],
    tokens_saved: Optional[int],
) -> None:
    """Record token usage as OTel metrics (counters/histograms)."""
    _init_otel()
    if _meter is None:
        return

    # Lazy-create instruments on first call
    if not hasattr(record_token_metrics, "_counters"):
        record_token_metrics._counters = {
            "returned": _meter.create_counter(
                "memex.tokens.returned",
                description="Tokens returned by memex MCP tools",
                unit="token",
            ),
            "naive": _meter.create_counter(
                "memex.tokens.naive",
                description="Estimated naive token cost without memex",
                unit="token",
            ),
            "saved": _meter.create_counter(
                "memex.tokens.saved",
                description="Tokens saved by memex context compression",
                unit="token",
            ),
        }

    c = record_token_metrics._counters
    attrs = {"mcp.tool.name": tool_name}
    c["returned"].add(tokens_returned, attrs)
    if tokens_naive is not None:
        c["naive"].add(tokens_naive, attrs)
    if tokens_saved is not None and tokens_saved > 0:
        c["saved"].add(tokens_saved, attrs)
