"""Tests for observability logging integration."""

from __future__ import annotations

import logging

import opentelemetry.trace as trace_api
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from flux.observability.logging import setup_log_filter, teardown_log_filter


@pytest.fixture
def tracer_setup():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace_api._TRACER_PROVIDER_SET_ONCE._done = False
    trace_api._TRACER_PROVIDER = None
    trace.set_tracer_provider(provider)
    yield provider, exporter
    exporter.shutdown()
    provider.shutdown()


class TestLoggingIntegration:
    def test_filter_attaches_to_logger(self):
        test_logger = logging.getLogger("flux.test_attach")
        initial_count = len(test_logger.filters)

        setup_log_filter(test_logger)
        assert len(test_logger.filters) == initial_count + 1

        teardown_log_filter(test_logger)
        assert len(test_logger.filters) == initial_count

    def test_log_includes_trace_context(self, tracer_setup):
        provider, _ = tracer_setup
        test_logger = logging.getLogger("flux.test_trace_ctx")
        test_logger.setLevel(logging.DEBUG)

        setup_log_filter(test_logger)

        records = []
        capture_handler = logging.Handler()
        capture_handler.emit = lambda record: records.append(record)
        test_logger.addHandler(capture_handler)

        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("test.span") as span:
            test_logger.info("test message")
            expected_trace_id = format(span.get_span_context().trace_id, "032x")

        assert len(records) >= 1
        record = records[0]
        assert hasattr(record, "otelTraceID")
        assert record.otelTraceID == expected_trace_id

        teardown_log_filter(test_logger)
        test_logger.removeHandler(capture_handler)

    def test_log_without_span_has_empty_trace(self):
        test_logger = logging.getLogger("flux.test_no_span")
        test_logger.setLevel(logging.DEBUG)

        setup_log_filter(test_logger)

        records = []
        capture_handler = logging.Handler()
        capture_handler.emit = lambda record: records.append(record)
        test_logger.addHandler(capture_handler)

        test_logger.info("no span message")

        assert len(records) >= 1
        record = records[0]
        assert hasattr(record, "otelTraceID")
        assert record.otelTraceID == "0" * 32

        teardown_log_filter(test_logger)
        test_logger.removeHandler(capture_handler)

    def test_teardown_without_setup_is_safe(self):
        test_logger = logging.getLogger("flux.test_safe_teardown")
        teardown_log_filter(test_logger)
