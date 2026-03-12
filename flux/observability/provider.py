"""OTel provider initialization and shutdown."""

from __future__ import annotations

import logging

from flux.observability.config import ObservabilityConfig

logger = logging.getLogger("flux.observability")

_meter_provider = None
_tracer_provider = None
_logger_provider = None


def _check_dependencies():
    """Verify OTel packages are installed."""
    try:
        import opentelemetry  # noqa: F401
    except ImportError:
        raise ImportError(
            "OpenTelemetry packages not installed. "
            "Install with: pip install flux-core[observability]"
        )


def setup_providers(config: ObservabilityConfig):
    """Initialize MeterProvider, TracerProvider, and LoggerProvider."""
    global _meter_provider, _tracer_provider, _logger_provider

    _check_dependencies()

    from opentelemetry import metrics, trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

    resource_attrs = {"service.name": config.service_name}
    resource_attrs.update(config.resource_attributes)
    resource = Resource.create(resource_attrs)

    # Metrics
    readers = []
    if config.prometheus_enabled:
        from opentelemetry.exporter.prometheus import PrometheusMetricReader

        readers.append(PrometheusMetricReader())

    if config.otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        otlp_exporter = OTLPMetricExporter(endpoint=config.otlp_endpoint)
        readers.append(
            PeriodicExportingMetricReader(
                otlp_exporter,
                export_interval_millis=config.metric_export_interval * 1000,
            )
        )

    _meter_provider = MeterProvider(resource=resource, metric_readers=readers)
    metrics.set_meter_provider(_meter_provider)

    # Tracing
    sampler = TraceIdRatioBased(config.trace_sample_rate)
    _tracer_provider = TracerProvider(resource=resource, sampler=sampler)

    span_exporters = []
    if config.otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )

        span_exporters.append(OTLPSpanExporter(endpoint=config.otlp_endpoint))

    if span_exporters:
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        for exporter in span_exporters:
            _tracer_provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(_tracer_provider)

    # Logging
    if config.otlp_endpoint:
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
            OTLPLogExporter,
        )
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

        _logger_provider = LoggerProvider(resource=resource)
        log_exporter = OTLPLogExporter(endpoint=config.otlp_endpoint)
        _logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
        set_logger_provider(_logger_provider)

    logger.info(
        f"Observability initialized (prometheus={config.prometheus_enabled}, "
        f"otlp={'enabled' if config.otlp_endpoint else 'disabled'}, "
        f"sample_rate={config.trace_sample_rate})"
    )


def shutdown_providers():
    """Flush and shut down all providers."""
    global _meter_provider, _tracer_provider, _logger_provider

    if _meter_provider:
        _meter_provider.shutdown()
        _meter_provider = None

    if _tracer_provider:
        _tracer_provider.shutdown()
        _tracer_provider = None

    if _logger_provider:
        _logger_provider.shutdown()
        _logger_provider = None

    logger.info("Observability shut down")


def get_meter_provider():
    return _meter_provider


def get_tracer_provider():
    return _tracer_provider
