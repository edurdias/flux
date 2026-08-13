"""Flux observability package — OpenTelemetry metrics, tracing, and logging."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Imported lazily at runtime: ObservabilityConfig is a pydantic model,
    # and task.py imports this package on every task call for get_metrics —
    # a top-level import dragged pydantic into runner children whose config
    # arrives as a plain snapshot (issue #241).
    from flux.observability.config import ObservabilityConfig

_enabled = False


def is_enabled() -> bool:
    """Check if observability is active."""
    return _enabled


def setup(config: ObservabilityConfig) -> None:
    """Initialize observability if enabled. Idempotent — subsequent calls are no-ops."""
    global _enabled
    if _enabled:
        return
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
    """Get an OTel Meter. Returns None if disabled."""
    if not _enabled:
        return None
    from opentelemetry import metrics

    return metrics.get_meter(name)


def get_tracer(name: str):
    """Get an OTel Tracer. Returns None if disabled."""
    if not _enabled:
        return None
    from opentelemetry import trace

    return trace.get_tracer(name)


def get_metrics():
    """Get the FluxMetrics singleton. Returns None if disabled."""
    if not _enabled:
        return None
    from flux.observability.provider import get_flux_metrics

    return get_flux_metrics()
