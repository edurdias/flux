"""OTel log handler that attaches to existing logger hierarchy."""

from __future__ import annotations

import logging


class OTelTraceLogHandler(logging.Handler):
    """Injects trace/span IDs into log records."""

    def emit(self, record: logging.LogRecord) -> None:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.trace_id:
            record.otelTraceID = format(ctx.trace_id, "032x")
            record.otelSpanID = format(ctx.span_id, "016x")
        else:
            record.otelTraceID = "0" * 32
            record.otelSpanID = "0" * 16


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


def setup_log_handler(logger: logging.Logger) -> OTelTraceLogHandler:
    """Add OTel trace context handler to a logger."""
    handler = OTelTraceLogHandler()
    handler.addFilter(OTelTraceLogFilter())
    logger.addHandler(handler)
    return handler


def teardown_log_handler(logger: logging.Logger, handler: OTelTraceLogHandler | None) -> None:
    """Remove OTel handler from a logger."""
    if handler:
        logger.removeHandler(handler)
