"""Flux observability package — OpenTelemetry metrics, tracing, and logging."""

from __future__ import annotations

from flux.observability.config import ObservabilityConfig

_enabled = False


def is_enabled() -> bool:
    """Check if observability is active."""
    return _enabled


def setup(config: ObservabilityConfig) -> None:
    """Initialize observability if enabled."""
    global _enabled
    if not config.enabled:
        return

    from flux.observability.provider import setup_providers

    setup_providers(config)
    _enabled = True


def shutdown() -> None:
    """Shut down observability providers."""
    global _enabled
    if not _enabled:
        return

    from flux.observability.provider import shutdown_providers

    shutdown_providers()
    _enabled = False


def get_meter(name: str):
    """Get an OTel Meter. Returns a no-op meter if disabled."""
    if not _enabled:
        from opentelemetry import metrics

        return metrics.get_meter(name)
    from opentelemetry import metrics

    return metrics.get_meter(name)


def get_tracer(name: str):
    """Get an OTel Tracer. Returns a no-op tracer if disabled."""
    if not _enabled:
        from opentelemetry import trace

        return trace.get_tracer(name)
    from opentelemetry import trace

    return trace.get_tracer(name)
