"""OTel log handler that attaches trace/span IDs to log records."""

from __future__ import annotations

import logging


class OTelTraceLogFilter(logging.Filter):
    """Adds trace/span IDs to log records as attributes."""

    def filter(self, record: logging.LogRecord) -> bool:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.trace_id:
            record.otelTraceID = format(ctx.trace_id, "032x")
            record.otelSpanID = format(ctx.span_id, "016x")
        else:
            record.otelTraceID = "0" * 32
            record.otelSpanID = "0" * 16
        return True


_filter: OTelTraceLogFilter | None = None


def setup_log_filter(logger: logging.Logger) -> OTelTraceLogFilter:
    """Add OTel trace context filter to a logger."""
    global _filter
    _filter = OTelTraceLogFilter()
    logger.addFilter(_filter)
    return _filter


def teardown_log_filter(logger: logging.Logger) -> None:
    """Remove OTel filter from a logger."""
    global _filter
    if _filter:
        logger.removeFilter(_filter)
        _filter = None
