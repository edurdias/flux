"""Tests for observability tracing."""

from __future__ import annotations

import opentelemetry.trace as trace_api
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from flux.observability import tracing


@pytest.fixture
def span_exporter():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # Reset global provider state so each test gets a fresh provider
    trace_api._TRACER_PROVIDER_SET_ONCE._done = False
    trace_api._TRACER_PROVIDER = None
    trace.set_tracer_provider(provider)
    yield exporter
    exporter.shutdown()
    provider.shutdown()


class TestTracing:
    def test_start_span_creates_span(self, span_exporter):
        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("test.span") as span:
            span.set_attribute("flux.workflow.name", "my_wf")

        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "test.span"
        assert spans[0].attributes["flux.workflow.name"] == "my_wf"

    def test_traced_decorator(self, span_exporter):
        @tracing.traced("test.decorated")
        def my_func():
            return 42

        result = my_func()

        assert result == 42
        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "test.decorated"

    @pytest.mark.asyncio
    async def test_traced_decorator_async(self, span_exporter):
        @tracing.traced("test.async_decorated")
        async def my_async_func():
            return 99

        result = await my_async_func()

        assert result == 99
        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "test.async_decorated"

    def test_inject_extract_roundtrip(self, span_exporter):
        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("parent"):
            carrier = tracing.inject_trace_context()

        assert "traceparent" in carrier

        ctx = tracing.extract_trace_context(carrier)
        assert ctx is not None

    def test_inject_without_span_returns_empty(self):
        carrier = tracing.inject_trace_context()
        assert carrier == {} or "traceparent" not in carrier
