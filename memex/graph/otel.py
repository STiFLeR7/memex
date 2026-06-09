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
