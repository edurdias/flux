"""Span decorators, context managers, and W3C propagation helpers."""

from __future__ import annotations

import asyncio
import functools
from typing import Any, Callable


def traced(span_name: str, attributes: dict[str, Any] | None = None) -> Callable:
    """Decorator that wraps a function in an OTel span."""

    def decorator(func: Callable) -> Callable:
        if asyncio.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                from opentelemetry import trace

                tracer = trace.get_tracer("flux")
                with tracer.start_as_current_span(span_name) as span:
                    if attributes:
                        for k, v in attributes.items():
                            span.set_attribute(k, v)
                    return await func(*args, **kwargs)

            return async_wrapper
        else:

            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                from opentelemetry import trace

                tracer = trace.get_tracer("flux")
                with tracer.start_as_current_span(span_name) as span:
                    if attributes:
                        for k, v in attributes.items():
                            span.set_attribute(k, v)
                    return func(*args, **kwargs)

            return sync_wrapper

    return decorator


def inject_trace_context() -> dict[str, str]:
    """Inject current trace context into a carrier dict (W3C format)."""
    from opentelemetry.propagate import inject

    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier


def extract_trace_context(carrier: dict[str, str]):
    """Extract trace context from a carrier dict."""
    from opentelemetry.propagate import extract

    return extract(carrier)
