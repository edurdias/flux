from __future__ import annotations

from pydantic import BaseModel, Field


class ObservabilityConfig(BaseModel):
    """Configuration for OpenTelemetry observability."""

    enabled: bool = Field(default=False, description="Enable observability")
    service_name: str = Field(default="flux", description="OTel service name")

    otlp_endpoint: str | None = Field(
        default=None,
        description="OTLP collector endpoint (e.g. http://localhost:4317)",
    )
    prometheus_enabled: bool = Field(
        default=True,
        description="Enable Prometheus /metrics endpoint",
    )

    trace_sample_rate: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Trace sampling rate (0.0 to 1.0)",
    )

    metric_export_interval: int = Field(
        default=60,
        description="OTLP metric export interval in seconds",
    )

    resource_attributes: dict[str, str] = Field(
        default_factory=dict,
        description="Additional OTel resource attributes",
    )
